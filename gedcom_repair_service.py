"""Copy-only GEDCOM diagnostics, repair previews, and reversible exports."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable

from audit_service import AuditService


SEVERITIES = ("critical", "warning", "info")
SAFE_REPAIR_KINDS = {
    "broken_reference", "duplicate_spouse", "duplicate_child", "invalid_date",
    "invalid_event_tag", "encoding", "empty_record",
}
_RECORD_HEADER = re.compile(r"^0\s+(@[^@\s]+@)\s+(INDI|FAM)\s*$")
_LINE = re.compile(r"^(\d+)\s+([A-Z0-9_]+)(?:\s+(.*))?$")
_REFERENCE = re.compile(r"^@([^@]+)@$")
_VALID_EVENT_TAGS = {
    "BIRT", "DEAT", "BURI", "CHR", "BAPM", "MARR", "DIV", "OCCU", "RESI",
    "EDUC", "EMIG", "IMMI", "CENS", "EVEN", "NOTE", "SOUR", "OBJE", "FACT",
}
_KNOWN_RECORD_TAGS = _VALID_EVENT_TAGS | {
    "NAME", "SEX", "FAMC", "FAMS", "HUSB", "WIFE", "CHIL", "DATE", "PLAC",
    "TITL", "AUTH", "PUBL", "REPO", "CALN", "CHAN", "UID", "RIN", "TYPE",
}
_MONTHS = {"JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
           "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12}


@dataclass(frozen=True)
class GedcomRepairIssue:
    issue_id: str
    kind: str
    severity: str
    location: str
    description: str
    recommended_repair: str
    automatic_repair: bool
    line_numbers: tuple[int, ...] = ()


@dataclass(frozen=True)
class GedcomRepairPreview:
    source_path: Path
    source_digest: str
    decoded_text: str
    issues: tuple[GedcomRepairIssue, ...]
    selected_issue_ids: tuple[str, ...]
    repaired_text: str

    @property
    def selected_issues(self) -> tuple[GedcomRepairIssue, ...]:
        selected = set(self.selected_issue_ids)
        return tuple(issue for issue in self.issues if issue.issue_id in selected)

    @property
    def safe_issue_ids(self) -> tuple[str, ...]:
        return tuple(issue.issue_id for issue in self.issues if issue.automatic_repair)


@dataclass(frozen=True)
class GedcomRepairExecutionResult:
    source_path: Path
    repaired_path: Path
    selected_issue_ids: tuple[str, ...]
    before_snapshot: dict[str, Any]
    after_snapshot: dict[str, Any]


class GedcomRepairCommand:
    """Undo/redo a generated repair copy without changing UndoManager."""

    def __init__(self, result: GedcomRepairExecutionResult) -> None:
        self.name = "Исправление GEDCOM"
        self.result = result
        self.delta = {"gedcom_file": {"before_rows": (), "after_rows": ((str(result.repaired_path),),)}}

    @property
    def has_effect(self) -> bool:
        return self.result.repaired_path.exists()

    def undo(self) -> None:
        self.result.repaired_path.unlink(missing_ok=True)

    def redo(self) -> None:
        self.result.repaired_path.parent.mkdir(parents=True, exist_ok=True)
        self.result.repaired_path.write_text(
            str(self.result.after_snapshot["content"]), encoding="utf-8", newline="\n"
        )


class GedcomRepairService:
    """Analyze GEDCOM bytes and export only repaired copies, never imports or mutates originals."""

    def __init__(self, database_path=None, *, diagnostics_only: bool = False) -> None:
        self.database_path = database_path
        self.diagnostics_only = bool(diagnostics_only)

    def analyze(self, source_path) -> GedcomRepairPreview:
        source = Path(source_path).expanduser().resolve()
        raw = source.read_bytes()
        text, has_encoding_error = self._decode(raw)
        records = self._records(text)
        issues = list(self._issues(records, has_encoding_error))
        return self._preview(source, raw, text, issues, ())

    def preview(self, source_path, issue_ids: Iterable[str] | None = None, *, safe_only=False) -> GedcomRepairPreview:
        initial = self.analyze(source_path)
        available = {issue.issue_id: issue for issue in initial.issues}
        if safe_only:
            selected = initial.safe_issue_ids
        elif issue_ids is None:
            selected = ()
        else:
            selected = tuple(dict.fromkeys(str(value) for value in issue_ids))
        unknown = set(selected) - set(available)
        if unknown:
            raise ValueError("Выбрана несуществующая проблема GEDCOM")
        unsafe = [issue_id for issue_id in selected if not available[issue_id].automatic_repair]
        if unsafe:
            raise ValueError("Выбранная проблема не имеет безопасного автоматического исправления")
        return self._preview(initial.source_path, initial.source_path.read_bytes(), initial.decoded_text, list(initial.issues), selected)

    def repair_selected(self, source_path, issue_ids, destination_path) -> GedcomRepairExecutionResult:
        return self.execute(self.preview(source_path, issue_ids), destination_path)

    def repair_all_safe(self, source_path, destination_path) -> GedcomRepairExecutionResult:
        return self.execute(self.preview(source_path, safe_only=True), destination_path)

    def execute(self, preview: GedcomRepairPreview, destination_path) -> GedcomRepairExecutionResult:
        if self.diagnostics_only:
            raise PermissionError("В режиме диагностики исправления отключены.")
        source = preview.source_path
        if hashlib.sha256(source.read_bytes()).hexdigest() != preview.source_digest:
            raise RuntimeError("Исходный GEDCOM изменился после анализа")
        destination = Path(destination_path).expanduser().resolve()
        if destination == source:
            raise ValueError("Нельзя перезаписывать исходный GEDCOM")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(preview.repaired_text, encoding="utf-8", newline="\n")
        before = {"source": str(source), "digest": preview.source_digest, "content": None}
        after = {"path": str(destination), "content": preview.repaired_text, "issues": preview.selected_issue_ids}
        if self.database_path:
            AuditService.for_database(self.database_path).record_state_change(
                "gedcom_repair",
                {"gedcom_file": ((str(source), preview.source_digest),)},
                {"gedcom_file": ((str(destination), hashlib.sha256(preview.repaired_text.encode("utf-8")).hexdigest()),)},
                description=f"Исправлен GEDCOM: {len(preview.selected_issue_ids)} безопасных проблем.",
                service="gedcom_repair_service",
                batch_id="batch" if len(preview.selected_issue_ids) > 1 else "",
            )
        return GedcomRepairExecutionResult(source, destination, preview.selected_issue_ids, before, after)

    def export_report_csv(self, preview: GedcomRepairPreview, destination_path) -> Path:
        destination = Path(destination_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8-sig", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=(
                "id", "severity", "location", "description", "recommended_repair", "automatic_repair",
            ))
            writer.writeheader()
            for issue in preview.issues:
                writer.writerow({
                    "id": issue.issue_id, "severity": issue.severity, "location": issue.location,
                    "description": issue.description, "recommended_repair": issue.recommended_repair,
                    "automatic_repair": "yes" if issue.automatic_repair else "no",
                })
        return destination

    def export_report_json(self, preview: GedcomRepairPreview, destination_path) -> Path:
        destination = Path(destination_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps({
            "source": str(preview.source_path), "issues": [issue.__dict__ for issue in preview.issues],
            "selected_issue_ids": preview.selected_issue_ids,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        return destination

    def _preview(self, source, raw, text, issues, selected):
        selected_set = set(selected)
        repaired = self._apply_safe_repairs(text, [issue for issue in issues if issue.issue_id in selected_set])
        return GedcomRepairPreview(
            source, hashlib.sha256(raw).hexdigest(), text, tuple(issues), tuple(selected), repaired,
        )

    @staticmethod
    def _decode(raw):
        try:
            return raw.decode("utf-8-sig"), False
        except UnicodeDecodeError:
            return raw.decode("utf-8-sig", errors="replace"), True

    @staticmethod
    def _records(text):
        records = []
        current = None
        for line_number, line in enumerate(text.splitlines(), start=1):
            header = _RECORD_HEADER.match(line.strip())
            if header:
                if current:
                    records.append(current)
                current = {"id": header.group(1)[1:-1], "type": header.group(2), "line": line_number, "lines": [(line_number, line)]}
            elif current:
                current["lines"].append((line_number, line))
        if current:
            records.append(current)
        return records

    def _issues(self, records, has_encoding_error):
        issues = []
        if has_encoding_error:
            issues.append(self._issue("encoding", "warning", "Файл", "Недопустимая UTF-8 последовательность.", "Заменить недопустимые байты символом замены.", True, ()))
        by_type = {kind: [record for record in records if record["type"] == kind] for kind in ("INDI", "FAM")}
        people = {record["id"] for record in by_type["INDI"]}
        families = {record["id"] for record in by_type["FAM"]}
        for kind, items in by_type.items():
            grouped = {}
            for record in items:
                grouped.setdefault(record["id"], []).append(record)
            for record_id, copies in grouped.items():
                if len(copies) > 1:
                    issues.append(self._issue("duplicate_id", "critical", f"{kind} @{record_id}@", "Повторяющийся идентификатор записи.", "Назначить уникальный ID и обновить ссылки вручную.", False, tuple(item["line"] for item in copies)))
        family_links = {person_id: {"FAMC": [], "FAMS": []} for person_id in people}
        parent_graph = {person_id: set() for person_id in people}
        for record in records:
            tags = self._tags(record)
            nonempty = [tag for tag, _value, _line in tags if tag not in {"NAME", "SEX"}]
            if not nonempty:
                issues.append(self._issue("empty_record", "warning", f"{record['type']} @{record['id']}@", "Пустая запись.", "Удалить пустую запись.", True, (record["line"],)))
            if record["type"] == "INDI":
                name_present = any(tag == "NAME" and value.strip() for tag, value, _line in tags)
                issues.extend(self._invalid_event_tag_issues(record))
                for tag, value, line in tags:
                    reference = self._reference(value)
                    if tag in {"FAMC", "FAMS"}:
                        if reference not in families:
                            issues.append(self._issue("broken_reference", "critical", f"INDI @{record['id']}@, строка {line}", f"Ссылка {tag} ведёт к отсутствующей семье @{reference or value}@.", "Удалить битую ссылку.", True, (line,)))
                        else:
                            family_links[record["id"]][tag].append((reference, line))
                    if tag == "DATE" and not self._valid_date(value):
                        issues.append(self._issue("invalid_date", "warning", f"INDI @{record['id']}@, строка {line}", f"Некорректная дата: {value}.", "Очистить некорректное значение даты.", True, (line,)))
                    if tag == "EVEN" and value.strip() and value.strip().upper() not in _VALID_EVENT_TAGS:
                        issues.append(self._issue("invalid_event_tag", "warning", f"INDI @{record['id']}@, строка {line}", f"Недопустимый тег события: {value}.", "Заменить на EVEN.", True, (line,)))
                if not name_present and not any(tag in {"FAMC", "FAMS", "BIRT", "DEAT", "NOTE", "OCCU"} for tag, _value, _line in tags):
                    issues.append(self._issue("empty_record", "warning", f"INDI @{record['id']}@", "Запись человека не содержит полезных данных.", "Удалить пустую запись.", True, (record["line"],)))
            else:
                spouses = []
                children = []
                issues.extend(self._invalid_event_tag_issues(record))
                for tag, value, line in tags:
                    reference = self._reference(value)
                    if tag in {"HUSB", "WIFE", "CHIL"}:
                        if reference not in people:
                            issues.append(self._issue("broken_reference", "critical", f"FAM @{record['id']}@, строка {line}", f"Ссылка {tag} ведёт к отсутствующему человеку @{reference or value}@.", "Удалить битую ссылку.", True, (line,)))
                        elif tag in {"HUSB", "WIFE"}:
                            spouses.append((reference, line))
                        else:
                            children.append((reference, line))
                    if tag == "DATE" and not self._valid_date(value):
                        issues.append(self._issue("invalid_date", "warning", f"FAM @{record['id']}@, строка {line}", f"Некорректная дата: {value}.", "Очистить некорректное значение даты.", True, (line,)))
                    if tag == "EVEN" and value.strip() and value.strip().upper() not in _VALID_EVENT_TAGS:
                        issues.append(self._issue("invalid_event_tag", "warning", f"FAM @{record['id']}@, строка {line}", f"Недопустимый тег события: {value}.", "Заменить на EVEN.", True, (line,)))
                issues.extend(self._duplicate_link_issues("duplicate_spouse", record, spouses, "Повторяющийся супруг", "Удалить повторяющуюся ссылку супруга."))
                issues.extend(self._duplicate_link_issues("duplicate_child", record, children, "Повторяющийся ребёнок", "Удалить повторяющуюся ссылку ребёнка."))
                for child, _line in children:
                    parent_graph.setdefault(child, set()).update(parent for parent, _line in spouses)
                if not spouses and not children:
                    issues.append(self._issue("orphan_family", "warning", f"FAM @{record['id']}@", "Семья не содержит участников.", "Проверить или удалить семью вручную.", False, (record["line"],)))
        for person_id, links in family_links.items():
            linked_families = {reference for values in links.values() for reference, _line in values}
            if not linked_families and not any(person_id in self._family_refs(record) for record in by_type["FAM"]):
                issues.append(self._issue("orphan_individual", "info", f"INDI @{person_id}@", "Человек не связан ни с одной семьёй.", "Проверить родственные связи вручную.", False, ()))
        for record in by_type["FAM"]:
            tags = self._tags(record)
            for tag, value, line in tags:
                if tag in {"HUSB", "WIFE", "CHIL"}:
                    reference = self._reference(value)
                    if reference in people:
                        expected = "FAMS" if tag in {"HUSB", "WIFE"} else "FAMC"
                        if record["id"] not in {ref for ref, _line in family_links[reference][expected]}:
                            issues.append(self._issue("missing_family_link", "warning", f"FAM @{record['id']}@, строка {line}", f"У человека @{reference}@ отсутствует обратная ссылка {expected}.", "Добавить обратную ссылку вручную.", False, (line,)))
        for cycle in self._cycles(parent_graph):
            issues.append(self._issue("circular_reference", "critical", f"INDI @{cycle[0]}@", f"Циклическая родительская связь: {' -> '.join(cycle)}.", "Разорвать цикл вручную.", False, ()))
        return issues

    @staticmethod
    def _tags(record):
        values = []
        for line_number, raw in record["lines"][1:]:
            match = _LINE.match(raw.strip())
            if match:
                values.append((match.group(2), match.group(3) or "", line_number))
        return values

    def _invalid_event_tag_issues(self, record):
        for line_number, raw in record["lines"][1:]:
            match = _LINE.match(raw.strip())
            if match and match.group(1) == "1" and match.group(2) not in _KNOWN_RECORD_TAGS:
                tag = match.group(2)
                yield self._issue(
                    "invalid_event_tag", "warning", f"{record['type']} @{record['id']}@, строка {line_number}",
                    f"Недопустимый тег события: {tag}.", "Заменить тег на EVEN.", True, (line_number,),
                )

    @staticmethod
    def _reference(value):
        match = _REFERENCE.match(value.strip())
        return match.group(1) if match else None

    def _family_refs(self, record):
        return {self._reference(value) for tag, value, _line in self._tags(record) if tag in {"HUSB", "WIFE", "CHIL"}}

    def _duplicate_link_issues(self, kind, record, references, label, repair):
        seen = set()
        for reference, line in references:
            if reference in seen:
                yield self._issue(kind, "warning", f"FAM @{record['id']}@, строка {line}", f"{label}: @{reference}@.", repair, True, (line,))
            seen.add(reference)

    @staticmethod
    def _valid_date(value):
        value = value.strip().upper()
        if not value:
            return True
        match = re.fullmatch(r"(\d{1,2})\s+([A-Z]{3})\s+(\d{1,4})", value)
        if not match:
            return bool(re.fullmatch(r"\d{1,4}", value))
        try:
            date(int(match.group(3)), _MONTHS[match.group(2)], int(match.group(1)))
            return True
        except (KeyError, ValueError):
            return False

    @staticmethod
    def _cycles(graph):
        cycles, active, seen = set(), set(), set()
        def visit(node, path):
            if node in active:
                cycle = path[path.index(node):] + [node]
                cycles.add(tuple(cycle))
                return
            if node in seen:
                return
            active.add(node)
            for parent in graph.get(node, ()):
                visit(parent, path + [parent])
            active.remove(node)
            seen.add(node)
        for node in graph:
            visit(node, [node])
        return sorted(cycles)

    @staticmethod
    def _issue(kind, severity, location, description, repair, automatic, lines):
        digest = hashlib.sha1(f"{kind}|{location}|{description}".encode("utf-8")).hexdigest()[:12]
        return GedcomRepairIssue(digest, kind, severity, location, description, repair, automatic, tuple(lines))

    def _apply_safe_repairs(self, text, issues):
        remove_lines, blank_dates, event_lines, remove_records = set(), set(), set(), set()
        encoding = False
        for issue in issues:
            if issue.kind in {"broken_reference", "duplicate_spouse", "duplicate_child"}:
                remove_lines.update(issue.line_numbers)
            elif issue.kind == "invalid_date":
                blank_dates.update(issue.line_numbers)
            elif issue.kind == "invalid_event_tag":
                event_lines.update(issue.line_numbers)
            elif issue.kind == "empty_record":
                remove_records.update(issue.line_numbers)
            elif issue.kind == "encoding":
                encoding = True
        output, skipping = [], False
        for line_number, raw in enumerate(text.splitlines(), start=1):
            if _RECORD_HEADER.match(raw.strip()):
                skipping = line_number in remove_records
            if skipping:
                continue
            if line_number in remove_lines:
                continue
            if line_number in blank_dates:
                match = _LINE.match(raw.strip())
                output.append(f"{match.group(1)} DATE" if match else raw)
            elif line_number in event_lines:
                match = _LINE.match(raw.strip())
                output.append(
                    f"{match.group(1)} EVEN" + (f" {match.group(3)}" if match.group(3) else "")
                    if match else raw
                )
            else:
                output.append(raw)
        repaired = "\n".join(output) + ("\n" if text.endswith(("\n", "\r")) else "")
        return repaired.replace("\ufffd", "?") if encoding else repaired
