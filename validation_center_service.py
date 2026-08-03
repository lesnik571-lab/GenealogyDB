"""Unified, sidecar-backed validation and safe repair for GenealogyDB."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from audit_service import AuditService
from config import DATA_DIR
from data_quality_service import DataQualityService
from database import backup_database
from repository.person_repository import PersonRepository
from undo_manager import RepositoryDeltaCommand, TableDelta


SEVERITIES = ("Critical", "Error", "Warning", "Information")
RISKS = ("Safe", "Review required", "Dangerous")
SAFE_FIX_CATEGORIES = {
    "duplicate_child_links", "duplicate_events", "orphan_citations",
    "broken_attachment_paths", "invalid_layout_nodes",
}
QUALITY_SCORE_FORMULA = "100 minus issue penalties (Critical=15, Error=8, Warning=3, Information=1), clamped to 0..100. Component scores apply the same formula to their listed categories."


@dataclass(frozen=True)
class ValidationIssue:
    issue_id: str
    category: str
    severity: str
    object_type: str
    database_id: int | None
    gedcom_id: str
    display_name: str
    explanation: str
    evidence: dict
    recommended_action: str
    automatic_fix_available: bool
    risk_level: str
    resolved: bool = False


@dataclass(frozen=True)
class QualityScore:
    overall: int
    completeness: int
    consistency: int
    source_coverage: int
    relationship_integrity: int
    media_integrity: int


@dataclass(frozen=True)
class ValidationReport:
    issues: tuple[ValidationIssue, ...]
    score: QualityScore
    generated_at: str

    @property
    def counters(self):
        return dict(sorted(Counter(issue.category for issue in self.issues).items()))


@dataclass(frozen=True)
class ValidationFixPreview:
    issues: tuple[ValidationIssue, ...]
    changes: tuple[dict, ...]
    blockers: tuple[str, ...]

    @property
    def can_apply(self):
        return bool(self.changes) and not self.blockers


@dataclass(frozen=True)
class ValidationFixResult:
    delta: dict[str, TableDelta]
    backup_path: Path
    issue_ids: tuple[str, ...]
    changes: tuple[dict, ...]
    before_family_children: tuple[tuple, ...]
    after_family_children: tuple[tuple, ...]
    before_layouts: dict[str, str]
    after_layouts: dict[str, str]


class ValidationFixCommand:
    """Applied command that preserves duplicate family-child rows on Undo/Redo."""

    name = "Исправления проверки данных"

    def __init__(self, repository, result: ValidationFixResult):
        self.repository = repository
        self.result = result
        self.delta = result.delta

    @property
    def has_effect(self):
        return bool(self.delta or self.result.before_layouts != self.result.after_layouts)

    def undo(self):
        self._apply(True)

    def redo(self):
        self._apply(False)

    def _apply(self, use_before):
        delta = {table: change for table, change in self.delta.items() if table != "family_children"}
        self.repository.apply_command_delta(delta, use_before)
        rows = self.result.before_family_children if use_before else self.result.after_family_children
        with self.repository.transaction():
            self.repository.conn.execute("DELETE FROM family_children")
            self.repository.conn.executemany("INSERT INTO family_children (family_id, child_id) VALUES (?, ?)", rows)
        for path, contents in (self.result.before_layouts if use_before else self.result.after_layouts).items():
            Path(path).write_text(contents, encoding="utf-8")


class ValidationCenterService:
    """Analyze all supported data surfaces and apply only explicitly safe repairs."""

    def __init__(self, repository: PersonRepository, *, data_dir=None, backup_dir=None):
        self.repository = repository
        self.data_dir = Path(data_dir or DATA_DIR)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.ignores_path = self.data_dir / "validation_center_ignores.json"
        self.backup_dir = Path(backup_dir or DATA_DIR / "backups")

    def analyze(self, *, include_ignored=False, progress_callback=None, cancel_callback=None) -> ValidationReport:
        rows = self._rows()
        ignores = self._load_ignores()
        issues = []
        checks = (
            self._people_issues, self._family_issues, self._event_issues,
            self._source_issues, self._attachment_issues, self._layout_issues,
            self._audit_issues,
        )
        for index, check in enumerate(checks, 1):
            self._cancel(cancel_callback)
            issues.extend(check(rows))
            if progress_callback:
                progress_callback("Проверка данных", index, len(checks))
        frozen = tuple(sorted((issue for issue in issues if include_ignored or not self._ignored(issue, ignores)), key=lambda item: (item.category, item.issue_id)))
        return ValidationReport(frozen, self._score(rows, frozen), self._timestamp())

    def filter_issues(self, report, *, severity="", category="", object_type="", automatic_only=False, risk_level="", text=""):
        query = str(text or "").casefold()
        return tuple(issue for issue in report.issues if (
            (not severity or issue.severity == severity) and
            (not category or issue.category == category) and
            (not object_type or issue.object_type == object_type) and
            (not automatic_only or issue.automatic_fix_available) and
            (not risk_level or issue.risk_level == risk_level) and
            (not query or query in " ".join((issue.display_name, issue.explanation, issue.category, issue.gedcom_id)).casefold())
        ))

    def preview_fixes(self, issues: Iterable[ValidationIssue]) -> ValidationFixPreview:
        safe = tuple(issue for issue in issues if issue.automatic_fix_available and issue.risk_level == "Safe")
        blockers = tuple(issue.issue_id for issue in issues if issue not in safe)
        changes = tuple(self._fix_change(issue) for issue in safe if self._fix_change(issue) is not None)
        return ValidationFixPreview(safe, changes, blockers)

    def apply_fixes(self, preview: ValidationFixPreview, *, cancel_callback=None) -> ValidationFixResult:
        if preview.blockers:
            raise ValueError("Выбраны исправления, требующие ручной проверки")
        self._cancel(cancel_callback)
        backup_path = backup_database(self.repository.db_name, self.backup_dir)
        before = self.repository.capture_command_state()
        before_children = before.get("family_children", ())
        layout_paths = {change["path"] for change in preview.changes if change["kind"] == "layout"}
        before_layouts = {path: Path(path).read_text(encoding="utf-8") for path in layout_paths}
        try:
            with self.repository.transaction():
                for change in preview.changes:
                    self._cancel(cancel_callback)
                    self._apply_change(change)
        except Exception:
            for path, contents in before_layouts.items():
                Path(path).write_text(contents, encoding="utf-8")
            raise
        after = self.repository.capture_command_state()
        after_layouts = {path: Path(path).read_text(encoding="utf-8") for path in layout_paths}
        delta = RepositoryDeltaCommand._build_delta(before, after)
        AuditService.for_database(self.repository.db_name).record_delta(
            "batch_operations", delta, description=f"Validation Center applied {len(preview.changes)} safe fixes.",
            service="validation_center_service", batch_id="validation-center",
        )
        return ValidationFixResult(delta, backup_path, tuple(issue.issue_id for issue in preview.issues), preview.changes, before_children, after.get("family_children", ()), before_layouts, after_layouts)

    def ignore(self, issue: ValidationIssue, reason="", *, pattern=False):
        rules = self._load_ignores()
        key = f"category:{issue.category}" if pattern else f"id:{issue.issue_id}"
        rules[key] = {"reason": str(reason), "timestamp": self._timestamp()}
        self._save_ignores(rules)

    def restore_ignored(self, issue: ValidationIssue, *, pattern=False):
        rules = self._load_ignores()
        rules.pop(f"category:{issue.category}" if pattern else f"id:{issue.issue_id}", None)
        self._save_ignores(rules)

    def export_ignores(self, destination):
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._load_ignores(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def import_ignores(self, source):
        payload = json.loads(Path(source).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Файл правил игнорирования должен быть JSON-объектом")
        rules = self._load_ignores()
        rules.update(payload)
        self._save_ignores(rules)

    def export_report(self, report: ValidationReport, destination, export_format):
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        if export_format == "json":
            path.write_text(json.dumps({"score": asdict(report.score), "issues": [asdict(issue) for issue in report.issues]}, ensure_ascii=False, indent=2), encoding="utf-8")
        elif export_format == "csv":
            with path.open("w", encoding="utf-8", newline="") as output:
                writer = csv.DictWriter(output, fieldnames=("issue_id", "category", "severity", "object_type", "database_id", "gedcom_id", "display_name", "explanation", "recommended_action", "automatic_fix_available", "risk_level"))
                writer.writeheader()
                writer.writerows({key: getattr(issue, key) for key in writer.fieldnames} for issue in report.issues)
        elif export_format == "html":
            rows = "".join(f"<tr><td>{issue.severity}</td><td>{issue.category}</td><td>{issue.display_name}</td><td>{issue.explanation}</td></tr>" for issue in report.issues)
            path.write_text(f"<html><body><h1>GenealogyDB Validation Report</h1><p>Score: {report.score.overall}/100</p><table><tr><th>Severity</th><th>Category</th><th>Object</th><th>Explanation</th></tr>{rows}</table></body></html>", encoding="utf-8")
        else:
            raise ValueError("Неподдерживаемый формат отчёта")
        return path

    def _rows(self):
        conn = self.repository.conn
        original_factory = conn.row_factory
        try:
            conn.row_factory = __import__("sqlite3").Row
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            def read(table):
                if table not in tables:
                    return []
                cursor = conn.execute(f'SELECT * FROM "{table}" ORDER BY rowid')
                return [dict(row) for row in cursor.fetchall()] if cursor.description else []
            return {name: read(name) for name in ("people", "families", "family_children", "person_events", "sources", "citations", "person_media", "person_sources")}
        finally:
            conn.row_factory = original_factory

    def _people_issues(self, rows):
        people, families, children = rows["people"], rows["families"], rows["family_children"]
        issues, aliases = [], self._aliases(people)
        for person in people:
            if not (str(person.get("first_name") or "").strip() or str(person.get("last_name") or "").strip()):
                issues.append(self._issue("unnamed_people", "Warning", "person", person, "Person has no first or last name.", {}, "Add a known name.", False, "Review required"))
            if not str(person.get("gedcom_id") or "").strip():
                issues.append(self._issue("missing_gedcom_ids", "Error", "person", person, "Person has no GEDCOM identifier.", {}, "Assign an imported identifier manually.", False, "Review required"))
            for field in ("birth_date", "death_date"):
                parsed = DataQualityService.parse_date(person.get(field))
                if str(person.get(field) or "").strip() and (not parsed.parseable or not parsed.valid):
                    issues.append(self._issue("malformed_dates", "Warning", "person", person, f"{field} is malformed or unsupported.", {"value": person.get(field), "field": field}, "Review the original date.", False, "Review required"))
            birth, death = DataQualityService.parse_date(person.get("birth_date")), DataQualityService.parse_date(person.get("death_date"))
            if birth.year and death.year and birth.year > death.year:
                issues.append(self._issue("birth_after_death", "Error", "person", person, "Birth is after death.", {}, "Review dates.", False, "Review required"))
        for gedcom, records in self._duplicates(people, "gedcom_id").items():
            for person in records[1:]:
                issues.append(self._issue("duplicate_gedcom_ids", "Critical", "person", person, "GEDCOM identifier is duplicated.", {"gedcom_id": gedcom}, "Resolve manually.", False, "Dangerous"))
        for family in families:
            for field in ("husband_id", "wife_id"):
                value = str(family.get(field) or "")
                if value and self._resolve(value, aliases) is None:
                    issues.append(self._issue("dangling_person_references", "Critical", "family", family, f"{field} does not resolve.", {"reference": value}, "Review family link.", False, "Dangerous"))
        for link in children:
            child = str(link.get("child_id") or "")
            if child and self._resolve(child, aliases) is None:
                issues.append(self._issue("dangling_person_references", "Critical", "family_child", link, "Child reference does not resolve.", {"child_id": child}, "Review family link.", False, "Dangerous"))
        return issues

    def _family_issues(self, rows):
        people, families, children = rows["people"], rows["families"], rows["family_children"]
        aliases, issues = self._aliases(people), []
        children_by_family = defaultdict(list)
        for link in children:
            children_by_family[str(link.get("family_id") or "")].append(link)
        family_ids = {str(item.get("id")) for item in families} | {str(item.get("gedcom_id") or "") for item in families}
        for family in families:
            husband, wife = str(family.get("husband_id") or ""), str(family.get("wife_id") or "")
            family_children = children_by_family[str(family.get("id"))] + children_by_family[str(family.get("gedcom_id") or "")]
            if not husband and not wife and not family_children:
                issues.append(self._issue("empty_families", "Information", "family", family, "Family has no spouses or children.", {}, "Review family manually.", False, "Review required"))
            if not husband and not wife:
                issues.append(self._issue("families_without_spouses", "Warning", "family", family, "Family has no spouses.", {}, "Review family manually.", False, "Review required"))
            if not family_children:
                issues.append(self._issue("families_without_children", "Information", "family", family, "Family has no children.", {}, "No automatic action.", False, "Review required"))
            if husband and husband == wife:
                issues.append(self._issue("self_spouse", "Critical", "family", family, "A person is linked as both spouses.", {}, "Review relationship manually.", False, "Dangerous"))
            for link in family_children:
                if str(link.get("child_id") or "") in {husband, wife}:
                    issues.append(self._issue("self_child", "Critical", "family_child", link, "A parent is also a child in the same family.", {}, "Review parentage manually.", False, "Dangerous"))
            if husband or wife:
                for parent in (husband, wife):
                    if parent and any(str(link.get("child_id") or "") == parent for link in family_children):
                        issues.append(self._issue("self_parent", "Critical", "family", family, "Person is their own parent.", {}, "Review parentage manually.", False, "Dangerous"))
        for key, links in self._duplicates(children, ("family_id", "child_id")).items():
            for link in links[1:]:
                issues.append(self._issue("duplicate_child_links", "Warning", "family_child", link, "Exact duplicate child link.", {"duplicate": key}, "Remove duplicate link.", True, "Safe"))
        for key, records in self._duplicates(families, ("husband_id", "wife_id", "relationship_type")).items():
            if key[0] or key[1]:
                for family in records[1:]:
                    issues.append(self._issue("duplicate_spouse_links", "Warning", "family", family, "Exact duplicate spouse link.", {"duplicate": key}, "Review duplicate family.", False, "Review required"))
        for link in children:
            if str(link.get("family_id") or "") not in family_ids:
                issues.append(self._issue("dangling_family_references", "Critical", "family_child", link, "Family-child link references a missing family.", {}, "Review family link.", False, "Dangerous"))
        issues.extend(self._pedigree_and_age_issues(people, families, children, aliases))
        return issues

    def _event_issues(self, rows):
        people, events, issues = {item["id"]: item for item in rows["people"]}, rows["person_events"], []
        seen = {}
        for event in events:
            person = people.get(event.get("person_id"))
            if not person:
                issues.append(self._issue("orphan_events", "Error", "event", event, "Event references a missing person.", {}, "Review event manually.", False, "Review required"))
                continue
            parsed = DataQualityService.parse_date(event.get("event_date"))
            if str(event.get("event_date") or "").strip() and (not parsed.parseable or not parsed.valid):
                issues.append(self._issue("malformed_dates", "Warning", "event", event, "Event date is malformed or unsupported.", {"value": event.get("event_date")}, "Review the original date.", False, "Review required"))
            birth, death = DataQualityService.parse_date(person.get("birth_date")), DataQualityService.parse_date(person.get("death_date"))
            if parsed.year and birth.year and parsed.year < birth.year:
                issues.append(self._issue("event_before_birth", "Warning", "event", event, "Event is before birth.", {}, "Review event date.", False, "Review required"))
            if parsed.year and death.year and parsed.year > death.year:
                issues.append(self._issue("event_after_death", "Warning", "event", event, "Event is after death.", {}, "Review event date.", False, "Review required"))
            key = (event.get("person_id"), event.get("event_type"), event.get("event_date"), event.get("event_place"), event.get("description"))
            if key in seen:
                issues.append(self._issue("duplicate_events", "Warning", "event", event, "Exact duplicate event.", {"duplicate_of": seen[key]}, "Remove duplicate event.", True, "Safe"))
            else:
                seen[key] = event.get("id")
        return issues

    def _source_issues(self, rows):
        issues, sources = [], rows["sources"]
        source_ids = {item["id"] for item in sources}
        for citation in rows["citations"]:
            if citation.get("source_id") not in source_ids:
                issues.append(self._issue("orphan_citations", "Warning", "citation", citation, "Citation references a missing source.", {}, "Detach orphan citation.", True, "Safe"))
            if str(citation.get("quality") or "") not in {"", "low", "medium", "high", "primary", "secondary"}:
                issues.append(self._issue("invalid_evidence_confidence", "Warning", "citation", citation, "Citation confidence is unsupported.", {"quality": citation.get("quality")}, "Review confidence manually.", False, "Review required"))
        for _key, records in self._duplicates(sources, ("title", "author", "publication", "repository_name", "call_number")).items():
            for source in records[1:]:
                issues.append(self._issue("duplicate_sources", "Warning", "source", source, "Exact duplicate source metadata.", {}, "Review sources; do not merge automatically.", False, "Review required"))
        return issues

    def _attachment_issues(self, rows):
        issues = []
        for media in rows["person_media"]:
            if not Path(str(media.get("file_path") or "")).expanduser().exists():
                issues.append(self._issue("broken_attachment_paths", "Warning", "attachment", media, "Attachment path does not exist.", {"path": media.get("file_path")}, "Remove broken attachment reference.", True, "Safe"))
        return issues

    def _layout_issues(self, _rows):
        issues = []
        people_ids = {item["id"] for item in self.repository.list_people_full()}
        for path in (self.data_dir / "tree_layouts").glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                positions = payload.get("positions", payload)
            except (OSError, ValueError):
                continue
            for person_id in positions:
                if not str(person_id).isdigit() or int(person_id) not in people_ids:
                    record = {"id": None, "path": str(path), "person_id": str(person_id)}
                    issues.append(self._issue("invalid_layout_nodes", "Information", "layout", record, "Saved layout references a missing person.", record, "Remove stale layout node.", True, "Safe"))
        return issues

    def _audit_issues(self, _rows):
        issues = []
        try:
            for record in AuditService.for_database(self.repository.db_name).list_records():
                for value in str(record.database_id or "").split(","):
                    if value.isdigit() and not self.repository.get_person_record(int(value)):
                        issues.append(self._issue("stale_audit_references", "Information", "audit", {"id": record.id}, "Audit record references a deleted person.", {"audit_id": record.id}, "Audit records are immutable.", False, "Review required"))
        except Exception:
            pass
        return issues

    def _pedigree_and_age_issues(self, people, families, children, aliases):
        issues, people_by_id = [], {item["id"]: item for item in people}
        children_by_family = defaultdict(list)
        for link in children:
            children_by_family[str(link.get("family_id") or "")].append(str(link.get("child_id") or ""))
        graph = defaultdict(set)
        for family in families:
            family_children = children_by_family[str(family.get("id"))] + children_by_family[str(family.get("gedcom_id") or "")]
            for parent_ref in (family.get("husband_id"), family.get("wife_id")):
                parent_id = self._resolve(parent_ref, aliases)
                for child_ref in family_children:
                    child_id = self._resolve(child_ref, aliases)
                    if parent_id and child_id:
                        graph[parent_id].add(child_id)
                        parent, child = people_by_id[parent_id], people_by_id[child_id]
                        parent_birth, child_birth = DataQualityService.parse_date(parent.get("birth_date")), DataQualityService.parse_date(child.get("birth_date"))
                        if parent_birth.year and child_birth.year:
                            age = child_birth.year - parent_birth.year
                            if age < 0:
                                issues.append(self._issue("child_older_than_parent", "Error", "person", child, "Child is older than parent.", {"parent_id": parent_id}, "Review dates or parentage.", False, "Dangerous"))
                            elif age < 12:
                                issues.append(self._issue("parent_under_12", "Warning", "person", parent, "Parent was younger than 12 at child birth.", {"child_id": child_id, "age": age}, "Review dates.", False, "Review required"))
                            elif age > 80:
                                issues.append(self._issue("parent_over_80", "Warning", "person", parent, "Parent was older than 80 at child birth.", {"child_id": child_id, "age": age}, "Review dates.", False, "Review required"))
        for start in sorted(graph):
            stack = [(start, {start})]
            while stack:
                current, path = stack.pop()
                for child in graph[current]:
                    if child == start:
                        issues.append(self._issue("pedigree_cycles", "Critical", "person", people_by_id[start], "Pedigree cycle detected.", {"path": sorted(path)}, "Review parentage manually.", False, "Dangerous"))
                    elif child not in path:
                        stack.append((child, path | {child}))
        return issues

    def _fix_change(self, issue):
        evidence = issue.evidence
        if issue.category == "duplicate_events": return {"kind": "delete", "table": "person_events", "id": issue.database_id}
        if issue.category == "orphan_citations": return {"kind": "delete", "table": "citations", "id": issue.database_id}
        if issue.category == "broken_attachment_paths": return {"kind": "delete", "table": "person_media", "id": issue.database_id}
        if issue.category == "duplicate_child_links": return {"kind": "duplicate_child", "family_id": evidence["duplicate"][0], "child_id": evidence["duplicate"][1]}
        if issue.category == "invalid_layout_nodes": return {"kind": "layout", "path": evidence["path"], "person_id": evidence["person_id"]}
        return None

    def _apply_change(self, change):
        if change["kind"] == "delete":
            self.repository.conn.execute(f'DELETE FROM "{change["table"]}" WHERE id = ?', (change["id"],))
        elif change["kind"] == "duplicate_child":
            self.repository.conn.execute("DELETE FROM family_children WHERE rowid IN (SELECT rowid FROM family_children WHERE family_id = ? AND child_id = ? LIMIT 1)", (change["family_id"], change["child_id"]))
        elif change["kind"] == "layout":
            path = Path(change["path"])
            payload = json.loads(path.read_text(encoding="utf-8"))
            positions = payload.get("positions", payload)
            positions.pop(change["person_id"], None)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _aliases(people):
        result = {}
        for person in people:
            for value in (str(person.get("id")), str(person.get("gedcom_id") or "")):
                if value: result[value] = person["id"]
        return result

    @staticmethod
    def _resolve(value, aliases): return aliases.get(str(value or ""))

    @staticmethod
    def _duplicates(rows, keys):
        keys = (keys,) if isinstance(keys, str) else keys
        groups = defaultdict(list)
        for row in rows:
            value = tuple(str(row.get(key) or "") for key in keys)
            if any(value): groups[value].append(row)
        return {key: value for key, value in groups.items() if len(value) > 1}

    def _issue(self, category, severity, object_type, record, explanation, evidence, action, automatic, risk):
        database_id = record.get("id") if isinstance(record, dict) else None
        gedcom_id = str(record.get("gedcom_id") or "") if isinstance(record, dict) else ""
        name = " ".join(value for value in (str(record.get("first_name") or ""), str(record.get("last_name") or "")) if value).strip() if isinstance(record, dict) else ""
        display = name or gedcom_id or f"{object_type} {database_id if database_id is not None else ''}".strip()
        token = json.dumps([category, object_type, database_id, gedcom_id, evidence], sort_keys=True, default=str, ensure_ascii=False)
        issue_id = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
        return ValidationIssue(issue_id, category, severity, object_type, database_id, gedcom_id, display, explanation, evidence, action, automatic, risk)

    def _score(self, rows, issues):
        """Apply QUALITY_SCORE_FORMULA deterministically to the current issue set."""
        weights = {"Critical": 15, "Error": 8, "Warning": 3, "Information": 1}
        penalty = sum(weights[issue.severity] for issue in issues)
        overall = max(0, min(100, 100 - penalty))
        def component(categories): return max(0, min(100, 100 - sum(weights[issue.severity] for issue in issues if issue.category in categories)))
        return QualityScore(overall, component({"unnamed_people", "missing_gedcom_ids"}), component({"birth_after_death", "malformed_dates", "duplicate_events"}), component({"orphan_citations", "duplicate_sources"}), component({"pedigree_cycles", "self_parent", "self_child", "self_spouse", "duplicate_child_links", "duplicate_spouse_links"}), component({"broken_attachment_paths"}))

    def _load_ignores(self):
        try: return json.loads(self.ignores_path.read_text(encoding="utf-8"))
        except (OSError, ValueError): return {}
    def _save_ignores(self, rules): self.ignores_path.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
    @staticmethod
    def _ignored(issue, rules): return f"id:{issue.issue_id}" in rules or f"category:{issue.category}" in rules
    @staticmethod
    def _timestamp(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
    @staticmethod
    def _cancel(callback):
        if callback: callback()
