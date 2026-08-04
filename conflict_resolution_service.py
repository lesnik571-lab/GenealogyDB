"""Read-only conflict review and conservative local resolution for project merges."""

from __future__ import annotations

import csv
import html
import json
import re
import shutil
import sqlite3
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from uuid import uuid4

from audit_service import AuditService
from collaboration_service import CollaborationService
from config import DATA_DIR
from database import backup_database, validate_database_file
from undo_manager import AppliedDeltaCommand, RepositoryDeltaCommand
from operation_correlation import OperationContext

KEEP_CURRENT = "Keep current"
TAKE_INCOMING = "Take incoming"
KEEP_BOTH = "Keep both"
MERGE_COMPATIBLE = "Merge compatible values"
CUSTOM_VALUE = "Custom value"
SKIP = "Skip"
MARK_UNRESOLVED = "Mark unresolved"
RESOLUTION_CHOICES = (KEEP_CURRENT, TAKE_INCOMING, KEEP_BOTH, MERGE_COMPATIBLE, CUSTOM_VALUE, SKIP, MARK_UNRESOLVED)
RELATIONSHIP_CATEGORIES = {"parentage_conflict", "spouse_partner_conflict", "family_structure_conflict"}
SAFE_CUSTOM_FIELDS = {"first_name", "last_name", "birth_date", "birth_place", "death_date", "death_place", "occupation", "note", "description", "event_date", "event_place", "notes", "page", "transcription", "comment"}


@dataclass(frozen=True)
class Conflict:
    conflict_id: str
    category: str
    entity_type: str
    current_entity_id: str
    incoming_entity_id: str
    field_name: str
    base_value: str
    current_value: str
    incoming_value: str
    confidence: int
    risk: str
    explanation: str
    provenance: dict
    affected_related_records: tuple[str, ...]
    resolution: str = MARK_UNRESOLVED
    custom_value: str = ""


@dataclass(frozen=True)
class ResolutionPlan:
    plan_id: str
    name: str
    current_path: str
    incoming_path: str
    baseline_path: str
    conflicts: tuple[Conflict, ...]


@dataclass(frozen=True)
class ResolutionPreview:
    plan: ResolutionPlan
    additions: tuple[str, ...]
    updates: tuple[str, ...]
    preserved_duplicates: tuple[str, ...]
    unresolved_conflicts: tuple[str, ...]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    affected_relationships: tuple[str, ...]
    resulting_record_counts: dict[str, int]


@dataclass(frozen=True)
class ResolutionResult:
    plan_id: str
    target_path: str
    backup_path: str
    delta: dict


class ConflictResolutionService:
    """Persist review plans outside databases and apply only explicit safe changes."""

    def __init__(self, target_repository, *, data_dir=None, backup_dir=None):
        self.repository = target_repository
        self.target_path = Path(target_repository.db_name).resolve()
        self.data_dir = Path(data_dir or DATA_DIR)
        self.plan_dir = self.data_dir / "collaboration" / "resolution_plans"
        self.report_dir = self.data_dir / "collaboration" / "resolution_reports"
        self.backup_dir = Path(backup_dir or self.data_dir / "backups")
        self.collaboration = CollaborationService(self.target_path, data_dir=self.data_dir)

    def review(self, incoming_path, *, baseline_path=None, merge_preview=None, incoming_metadata=None, cancel_callback=None, progress_callback=None):
        incoming = Path(incoming_path).expanduser().resolve(); validate_database_file(incoming)
        current = self._snapshot(self.target_path, cancel_callback, progress_callback, "current")
        incoming_data = self._snapshot(incoming, cancel_callback, progress_callback, "incoming")
        baseline = self._snapshot(Path(baseline_path), cancel_callback, progress_callback, "base") if baseline_path else None
        conflicts = self._compare(current, incoming_data, baseline, cancel_callback)
        conflicts.extend(self._metadata_conflicts(incoming, incoming_metadata))
        conflicts.extend(self._missing_reference_conflicts(incoming_data))
        if merge_preview:
            for item in merge_preview.items:
                if item.risk_level == "blocker":
                    conflicts.append(self._conflict(f"merge:{item.item_id}", item.category, item.entity_type, item.target_entity_id, item.source_entity_id, "", "", "", "", item.confidence, "blocker", item.explanation, {"merge_item": item.item_id}, ()))
        return ResolutionPlan(str(uuid4()), "Untitled resolution plan", str(self.target_path), str(incoming), str(Path(baseline_path).resolve()) if baseline_path else "", tuple(sorted(conflicts, key=lambda item: item.conflict_id)))

    def save_plan(self, plan):
        self.plan_dir.mkdir(parents=True, exist_ok=True)
        path = self.plan_dir / f"{plan.plan_id}.json"
        self._atomic_json(path, asdict(plan)); return path

    def load_plan(self, plan_id):
        payload = json.loads((self.plan_dir / f"{plan_id}.json").read_text(encoding="utf-8"))
        return ResolutionPlan(**{**payload, "conflicts": tuple(Conflict(**{**item, "affected_related_records": tuple(item["affected_related_records"])} ) for item in payload["conflicts"])})

    def list_plans(self):
        return tuple(self.load_plan(path.stem) for path in sorted(self.plan_dir.glob("*.json"))) if self.plan_dir.exists() else ()

    def rename_plan(self, plan, name): return replace(plan, name=str(name).strip() or "Untitled resolution plan")
    def duplicate_plan(self, plan, name=None): return replace(plan, plan_id=str(uuid4()), name=name or f"{plan.name} copy")
    def delete_plan(self, plan_id):
        path = self.plan_dir / f"{plan_id}.json"
        if path.exists(): path.unlink()

    def resolve(self, plan, conflict_id, choice, custom_value=""):
        if choice not in RESOLUTION_CHOICES: raise ValueError("Unsupported resolution choice")
        changed = []
        for conflict in plan.conflicts:
            if conflict.conflict_id != conflict_id: changed.append(conflict); continue
            self._validate_choice(conflict, choice, custom_value)
            changed.append(replace(conflict, resolution=choice, custom_value=str(custom_value)))
        return replace(plan, conflicts=tuple(changed))

    def batch_resolve(self, plan, conflict_ids, choice, *, confirmed=False):
        selected = [item for item in plan.conflicts if item.conflict_id in set(conflict_ids)]
        if not confirmed: raise ValueError("Batch resolution requires confirmation")
        if not selected: return plan, 0
        structures = {(item.category, item.entity_type, item.field_name) for item in selected}
        if len(structures) != 1 or any(item.category in RELATIONSHIP_CATEGORIES for item in selected):
            raise ValueError("Only identical non-relationship conflicts can be batch-resolved")
        for item in selected: self._validate_choice(item, choice, "")
        return replace(plan, conflicts=tuple(replace(item, resolution=choice) if item.conflict_id in {value.conflict_id for value in selected} else item for item in plan.conflicts)), len(selected)

    def preview(self, plan, *, cancel_callback=None):
        current = self._snapshot(Path(plan.current_path), cancel_callback, None, "preview")
        updates = tuple(item.conflict_id for item in plan.conflicts if item.resolution in {TAKE_INCOMING, MERGE_COMPATIBLE, CUSTOM_VALUE})
        kept = tuple(item.conflict_id for item in plan.conflicts if item.resolution == KEEP_BOTH)
        unresolved = tuple(item.conflict_id for item in plan.conflicts if item.resolution in {MARK_UNRESOLVED, SKIP})
        relationships = tuple(item.conflict_id for item in plan.conflicts if item.category in RELATIONSHIP_CATEGORIES)
        blockers = tuple(item.conflict_id for item in plan.conflicts if item.category in RELATIONSHIP_CATEGORIES or item.category == "gedcom_id_conflict" or item.resolution == MARK_UNRESOLVED)
        warnings = tuple(item.conflict_id for item in plan.conflicts if item.risk in {"high", "blocker"})
        return ResolutionPreview(plan, (), updates, kept, unresolved, blockers, warnings, relationships, {table: len(rows) for table, rows in current.items()})

    def relationship_preview(self, plan, conflict_id):
        """Return visual-review data and relationship blockers without mutating either project."""
        conflict = next(item for item in plan.conflicts if item.conflict_id == conflict_id)
        if conflict.category not in RELATIONSHIP_CATEGORIES: raise ValueError("Conflict is not a relationship conflict")
        snapshot = self._snapshot(Path(plan.incoming_path), None, None, "relationship preview")
        families = snapshot["families"]
        members = {str(row["id"]): row for row in snapshot["people"]}
        graph = {}
        duplicate_links = set(); seen_links = set(); self_links = set(); partial_families = set()
        for family in families:
            family_id = str(family["id"]); parents = [str(value) for value in (family.get("husband_id"), family.get("wife_id")) if value]
            if len(parents) == 1: partial_families.add(family_id)
            family_references = {family_id, str(family.get("gedcom_id") or "")}
            children = [str(row["child_id"]) for row in snapshot["family_children"] if str(row["family_id"]) in family_references]
            for parent in parents:
                for child in children:
                    link = (parent, child)
                    if link in seen_links: duplicate_links.add(link)
                    seen_links.add(link); graph.setdefault(parent, set()).add(child)
                    if parent == child: self_links.add(parent)
        def cyclic(node, visiting, visited):
            if node in visiting: return True
            if node in visited: return False
            visiting.add(node); result = any(cyclic(child, visiting, visited) for child in graph.get(node, ())); visiting.remove(node); visited.add(node); return result
        cycles = any(cyclic(node, set(), set()) for node in graph)
        referenced_people = {str(value) for family in families for value in (family.get("husband_id"), family.get("wife_id")) if value} | {str(row["child_id"]) for row in snapshot["family_children"]}
        return {
            "conflict_id": conflict_id,
            "parents": tuple(sorted({str(value) for family in families for value in (family.get("husband_id"), family.get("wife_id")) if value})),
            "spouses_partners": tuple(sorted({str(value) for family in families for value in (family.get("husband_id"), family.get("wife_id")) if value})),
            "children": tuple(sorted({str(row["child_id"]) for row in snapshot["family_children"]})),
            "families": tuple(sorted(str(row["id"]) for row in families)),
            "missing_people": tuple(sorted(value for value in referenced_people if value not in members)),
            "cycle_detected": cycles,
            "self_links": tuple(sorted(self_links)),
            "duplicate_links": tuple(sorted(duplicate_links)),
            "partial_families": tuple(sorted(partial_families)),
            "can_apply": False,
        }

    def apply(self, preview, *, mode="Apply to current project", destination=None, confirmed_overwrite=False, cancel_callback=None, progress_callback=None):
        if preview.blockers: raise ValueError("Resolution preview has unresolved or unsafe blockers")
        if mode not in {"Apply to current project", "Create resolved project copy"}: raise ValueError("Unsupported apply mode")
        if mode == "Create resolved project copy":
            if not destination: raise ValueError("A destination path is required")
            copy_path = Path(destination).expanduser().resolve()
            if copy_path.exists() and not confirmed_overwrite: raise FileExistsError("Resolved project destination already exists")
            copy_path.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(self.target_path, copy_path)
            from repository.person_repository import PersonRepository
            copied = PersonRepository(copy_path)
            try: return ConflictResolutionService(copied, data_dir=self.data_dir, backup_dir=self.backup_dir)._apply(preview, copy_path, cancel_callback, progress_callback)
            finally: copied.close()
        return self._apply(preview, self.target_path, cancel_callback, progress_callback)

    def _apply(self, preview, target_path, cancel_callback, progress_callback):
        if cancel_callback: cancel_callback()
        backup = backup_database(target_path, self.backup_dir); before = self.repository.capture_command_state()
        incoming = self._snapshot(Path(preview.plan.incoming_path), cancel_callback, progress_callback, "apply")
        people = {str(row["id"]): row for row in incoming["people"]}
        try:
            with self.repository.transaction():
                for index, conflict in enumerate(preview.plan.conflicts, 1):
                    if cancel_callback: cancel_callback()
                    if conflict.resolution not in {TAKE_INCOMING, MERGE_COMPATIBLE, CUSTOM_VALUE}: continue
                    if conflict.category != "person_field_conflict": raise ValueError("Only reviewed scalar person fields can be applied")
                    value = conflict.custom_value if conflict.resolution == CUSTOM_VALUE else people[conflict.incoming_entity_id][conflict.field_name]
                    self.repository.update_person_fields(int(conflict.current_entity_id), {conflict.field_name: value})
                    if progress_callback: progress_callback("Applying resolutions", index, len(preview.plan.conflicts))
        except Exception: raise
        delta = RepositoryDeltaCommand._build_delta(before, self.repository.capture_command_state())
        identity = self.collaboration.identity()
        context = OperationContext.create(operation_type="merge", project_uuid=identity.project_uuid, dataset_uuid=identity.dataset_uuid, operation_uuid=preview.plan.plan_id, author=identity.editor_identity, session_uuid=self.collaboration.session_id, source_module="conflict_resolution_service", affected_entity_types=delta.keys(), provenance={"incoming_path": preview.plan.incoming_path}).transition("running").complete()
        AuditService.for_database(target_path).record_delta("merge", delta, description=f"Conflict resolution plan {preview.plan.plan_id}.", service="conflict_resolution_service", operation_context=context)
        self.collaboration.record_change("merge", references={}, summary=f"Conflict resolution provenance: plan {preview.plan.plan_id}; incoming {preview.plan.incoming_path}.", operation_context=context)
        return ResolutionResult(preview.plan.plan_id, str(target_path), str(backup), delta)

    def undo_command(self, result): return AppliedDeltaCommand("Conflict Resolution", self.repository, result.delta, result)

    def export(self, preview, destination, report_format):
        path = Path(destination); path.parent.mkdir(parents=True, exist_ok=True); temporary = path.with_suffix(path.suffix + ".tmp")
        if report_format == "json": temporary.write_text(json.dumps(asdict(preview), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        elif report_format == "markdown": temporary.write_text(self._markdown(preview), encoding="utf-8")
        elif report_format == "html": temporary.write_text("<!doctype html><meta charset=\"utf-8\"><pre>" + html.escape(self._markdown(preview)) + "</pre>", encoding="utf-8")
        elif report_format == "csv":
            with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream); writer.writerow(("conflict_id", "category", "entity", "field", "resolution", "risk")); writer.writerows((item.conflict_id, item.category, item.entity_type, item.field_name, item.resolution, item.risk) for item in preview.plan.conflicts)
        else: raise ValueError("Unsupported resolution report format")
        temporary.replace(path); return path

    def export_all(self, preview):
        self.report_dir.mkdir(parents=True, exist_ok=True)
        return tuple(self.export(preview, self.report_dir / f"resolution.{suffix}", report_format) for report_format, suffix in (("json", "json"), ("markdown", "md"), ("html", "html"), ("csv", "csv")))

    def _compare(self, current, incoming, baseline, cancel_callback):
        conflicts = []; current_people = {row.get("gedcom_id") or self._person_key(row): row for row in current["people"]}
        for row in incoming["people"]:
            if cancel_callback: cancel_callback()
            match = current_people.get(row.get("gedcom_id")) or current_people.get(self._person_key(row))
            if not match: continue
            base = self._matching(baseline, "people", row) if baseline else None
            for field in ("first_name", "last_name", "sex", "birth_date", "birth_place", "death_date", "death_place", "occupation", "note"):
                if str(match.get(field, "")) != str(row.get(field, "")):
                    category = "gedcom_id_conflict" if field == "gedcom_id" else "person_field_conflict"
                    conflicts.append(self._conflict(f"person:{match['id']}:{row['id']}:{field}", category, "person", match["id"], row["id"], field, base.get(field, "") if base else "", match.get(field, ""), row.get(field, ""), 100, "blocker" if field == "sex" else "medium", "Person fields diverge.", {"current": str(self.target_path), "incoming": ""}, ()))
            if row.get("gedcom_id") == match.get("gedcom_id") and self._person_key(row) != self._person_key(match):
                conflicts.append(self._conflict(f"gedcom:{match['id']}:{row['id']}", "gedcom_id_conflict", "person", match["id"], row["id"], "gedcom_id", "", match.get("gedcom_id", ""), row.get("gedcom_id", ""), 100, "blocker", "Same GEDCOM ID has incompatible identity data.", {}, ()))
        conflicts.extend(self._relationship_conflicts(current, incoming))
        conflicts.extend(self._row_conflicts("person_events", "event_conflict", current, incoming, ("event_type", "event_date", "event_place", "description")))
        conflicts.extend(self._row_conflicts("sources", "source_conflict", current, incoming, ("title", "author", "publication", "source_url", "notes")))
        conflicts.extend(self._row_conflicts("citations", "citation_conflict", current, incoming, ("source_id", "target_type", "target_id", "page", "quality", "transcription", "comment")))
        conflicts.extend(self._row_conflicts("person_media", "attachment_metadata_conflict", current, incoming, ("person_id", "media_type", "title", "file_path", "description")))
        return conflicts

    def _relationship_conflicts(self, current, incoming):
        conflicts = []
        for family in incoming["families"]:
            match = next((row for row in current["families"] if row.get("gedcom_id") and row.get("gedcom_id") == family.get("gedcom_id")), None)
            if not match: continue
            members = tuple(sorted({str(value) for value in (match.get("husband_id"), match.get("wife_id"), family.get("husband_id"), family.get("wife_id")) if value}))
            if (match.get("husband_id"), match.get("wife_id")) != (family.get("husband_id"), family.get("wife_id")):
                conflicts.append(self._conflict(f"parentage:{match['id']}:{family['id']}", "parentage_conflict", "family", match["id"], family["id"], "parents", "", str(match), str(family), 100, "blocker", "Parentage conflict requires visual review.", {}, members))
                conflicts.append(self._conflict(f"spouse:{match['id']}:{family['id']}", "spouse_partner_conflict", "family", match["id"], family["id"], "partners", "", str(match), str(family), 100, "blocker", "Spouse or partner identity conflict requires visual review.", {}, members))
            if match.get("relationship_type") != family.get("relationship_type"):
                conflicts.append(self._conflict(f"family:{match['id']}:{family['id']}", "family_structure_conflict", "family", match["id"], family["id"], "relationship_type", "", match.get("relationship_type", ""), family.get("relationship_type", ""), 100, "blocker", "Family structure conflict requires visual review.", {}, members))
        return conflicts

    def _missing_reference_conflicts(self, incoming):
        people = {str(row["id"]) for row in incoming["people"]}; sources = {str(row["id"]) for row in incoming["sources"]}; output = []
        for table, field, known, entity in (("person_events", "person_id", people, "event"), ("person_media", "person_id", people, "attachment"), ("person_sources", "person_id", people, "person_source"), ("citations", "source_id", sources, "citation")):
            for row in incoming[table]:
                if str(row.get(field, "")) not in known:
                    output.append(self._conflict(f"missing:{table}:{row['id']}", "missing_referenced_record", entity, "", row["id"], field, "", "", row.get(field, ""), 100, "blocker", "Incoming record references a missing entity.", {}, (str(row.get(field, "")),)))
        return output

    def _row_conflicts(self, table, category, current, incoming, fields):
        output = []
        for row in incoming.get(table, []):
            related = next((candidate for candidate in current.get(table, []) if candidate.get(fields[0]) == row.get(fields[0]) and candidate is not row), None)
            if not related: continue
            differences = [field for field in fields if str(related.get(field, "")) != str(row.get(field, ""))]
            if not differences:
                if table != "citations": continue
                output.append(self._conflict(f"duplicate:{table}:{related['id']}:{row['id']}", "duplicate_citation", "citation", related["id"], row["id"], "", "", "", "", 100, "low", "Equivalent citations are preserved as duplicates.", {"attribution": "preserved"}, ()))
                continue
            label = category
            for field in differences:
                output.append(self._conflict(f"{table}:{related['id']}:{row['id']}:{field}", label, table[:-1], related["id"], row["id"], field, "", related.get(field, ""), row.get(field, ""), 90, "high" if table in {"sources", "citations"} else "medium", "Incoming record conflicts with current record.", {"attribution": "preserved"}, ()))
        return output

    def _metadata_conflicts(self, incoming_path, metadata_path):
        incoming = CollaborationService(incoming_path, data_dir=self.data_dir); output = []
        if incoming.identity().dataset_uuid != self.collaboration.identity().dataset_uuid:
            output.append(self._conflict("dataset-identity", "dataset_identity_conflict", "metadata", "", "", "dataset_uuid", "", self.collaboration.identity().dataset_uuid, incoming.identity().dataset_uuid, 100, "blocker", "Dataset identities differ.", {}, ()))
        path = Path(metadata_path) if metadata_path else incoming.metadata_path
        if path.exists():
            known = {item.operation_id for item in self.collaboration.changes()}
            for change in incoming.changes():
                if change.operation_id in known: output.append(self._conflict(f"operation:{change.operation_id}", "collaboration_operation_collision", "metadata", change.operation_id, change.operation_id, "operation_id", "", change.operation_id, change.operation_id, 100, "blocker", "Collaboration operation identifier collides.", {}, ()))
        return output

    def _snapshot(self, path, cancel_callback, progress_callback, label):
        tables = ("people", "families", "family_children", "person_events", "person_media", "person_sources", "sources", "citations")
        connection = sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True); connection.row_factory = sqlite3.Row
        try:
            result = {}
            for index, table in enumerate(tables, 1):
                if cancel_callback: cancel_callback()
                order = "rowid" if table == "family_children" else "id"
                result[table] = [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY {order}")]
                if progress_callback: progress_callback(f"Reading {label} {table}", index, len(tables))
            return result
        finally: connection.close()

    def _validate_choice(self, conflict, choice, custom_value):
        if conflict.category in RELATIONSHIP_CATEGORIES and choice not in {SKIP, MARK_UNRESOLVED}:
            raise ValueError("Relationship conflicts require visual preview and cannot be auto-resolved")
        if conflict.category == "gedcom_id_conflict" and choice not in {SKIP, MARK_UNRESOLVED}:
            raise ValueError("GEDCOM ID conflicts require manual review")
        if choice == CUSTOM_VALUE:
            if conflict.field_name not in SAFE_CUSTOM_FIELDS: raise ValueError("Custom values are not supported for this field")
            if "date" in conflict.field_name and not re.fullmatch(r"(?:[0-9]{4}|[0-9]{1,2}[ ./-][0-9]{1,2}[ ./-][0-9]{4}|[A-Za-z]{3,9} [0-9]{4})", str(custom_value).strip()): raise ValueError("Unsupported date syntax")

    @staticmethod
    def _person_key(row): return tuple(str(row.get(field, "")).casefold() for field in ("first_name", "last_name", "birth_date", "death_date"))
    def _matching(self, snapshot, table, row): return next((item for item in (snapshot or {}).get(table, []) if item.get("gedcom_id") == row.get("gedcom_id")), None)
    @staticmethod
    def _conflict(conflict_id, category, entity_type, current_id, incoming_id, field_name, base_value, current_value, incoming_value, confidence, risk, explanation, provenance, related):
        return Conflict(str(conflict_id), str(category), str(entity_type), str(current_id), str(incoming_id), str(field_name), str(base_value), str(current_value), str(incoming_value), int(confidence), str(risk), str(explanation), dict(provenance), tuple(related))
    @staticmethod
    def _atomic_json(path, payload):
        temporary = path.with_suffix(path.suffix + ".tmp"); temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"); temporary.replace(path)
    @staticmethod
    def _markdown(preview):
        lines = ["# Conflict Resolution Preview", "", f"Plan: {preview.plan.name}", f"Blockers: {len(preview.blockers)}", "", "| Conflict | Category | Field | Resolution | Risk |", "| --- | --- | --- | --- | --- |"]
        lines.extend(f"| {item.conflict_id} | {item.category} | {item.field_name} | {item.resolution} | {item.risk} |" for item in preview.plan.conflicts)
        return "\n".join(lines) + "\n"