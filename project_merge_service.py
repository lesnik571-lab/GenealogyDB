"""Local, transactional project merge analysis for GenealogyDB databases."""

from __future__ import annotations

import csv
import html
import json
import shutil
import sqlite3
from dataclasses import asdict, dataclass
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
MERGE_COMPATIBLE = "Merge compatible fields"
KEEP_BOTH = "Keep both"
SKIP = "Skip"
MANUAL_REVIEW = "Manual review required"
ACTIONS = (KEEP_CURRENT, TAKE_INCOMING, MERGE_COMPATIBLE, KEEP_BOTH, SKIP, MANUAL_REVIEW)
RISK_BLOCKER = "blocker"


@dataclass(frozen=True)
class MergeItem:
    item_id: str
    category: str
    source_project: str
    target_project: str
    entity_type: str
    source_entity_id: str
    target_entity_id: str
    confidence: int
    explanation: str
    differences: dict[str, tuple[str, str]]
    suggested_action: str
    risk_level: str


@dataclass(frozen=True)
class ProjectMergePreview:
    source_path: str
    target_path: str
    mode: str
    merge_operation_id: str
    items: tuple[MergeItem, ...]
    additions: tuple[str, ...]
    updates: tuple[str, ...]
    kept_duplicates: tuple[str, ...]
    skipped_records: tuple[str, ...]
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]
    estimated_counts: dict[str, int]
    parent_operation_uuid: str = ""

    @property
    def can_apply(self):
        return not self.blockers and self.mode != "Preview only"


@dataclass(frozen=True)
class ProjectMergeResult:
    merge_operation_id: str
    target_path: str
    backup_path: str
    delta: dict
    imported_metadata_count: int


class ProjectMergeService:
    """Analyze local projects read-only and apply explicit safe additions atomically."""

    def __init__(self, target_repository, *, data_dir=None, backup_dir=None):
        self.repository = target_repository
        self.target_path = Path(target_repository.db_name).resolve()
        self.data_dir = Path(data_dir or DATA_DIR)
        self.report_dir = self.data_dir / "collaboration" / "merge_reports"
        self.backup_dir = Path(backup_dir or self.data_dir / "backups")
        self.collaboration = CollaborationService(self.target_path, data_dir=self.data_dir)

    def analyze(self, incoming_path, *, mode="Preview only", actions=None, cancel_callback=None, progress_callback=None, incoming_metadata=None, parent_operation_uuid=""):
        source = Path(incoming_path).expanduser().resolve()
        if source == self.target_path:
            raise ValueError("A project cannot be merged with itself")
        validate_database_file(source)
        if mode not in {"Preview only", "Merge into current project", "Create new merged project copy"}:
            raise ValueError("Unsupported project merge mode")
        source_data = self._snapshot(source, cancel_callback, progress_callback, "incoming")
        target_data = self._snapshot(self.target_path, cancel_callback, progress_callback, "current")
        source_identity = CollaborationService(source, data_dir=self.data_dir).identity()
        target_identity = self.collaboration.identity()
        items = self._compare(source_data, target_data, source_identity.project_uuid, target_identity.project_uuid, cancel_callback)
        metadata_path = Path(incoming_metadata) if incoming_metadata else CollaborationService(source, data_dir=self.data_dir).metadata_path
        warnings = []
        blockers = []
        if source_identity.dataset_uuid != target_identity.dataset_uuid:
            warnings.append("Dataset UUIDs differ; collaboration metadata will not be imported automatically.")
            items.append(self._item("dataset-uuid", "incompatible_dataset_uuid", "metadata", "", "", 100, "Dataset UUIDs differ.", {}, MANUAL_REVIEW, RISK_BLOCKER, source_identity.project_uuid, target_identity.project_uuid))
        if metadata_path.is_file():
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
            operation_ids = [item.get("operation_id", "") for item in payload.get("changes", [])]
            known = {change.operation_id for change in self.collaboration.changes()}
            for operation_id in sorted(set(operation_ids) & known):
                items.append(self._item(f"operation:{operation_id}", "operation_id_collision", "metadata", operation_id, operation_id, 100, "Incoming operation identifier already exists.", {}, MANUAL_REVIEW, RISK_BLOCKER, source_identity.project_uuid, target_identity.project_uuid))
            for operation_id in CollaborationService(source, data_dir=self.data_dir).diagnostics().orphan_operation_ids:
                items.append(self._item(f"orphan:{operation_id}", "orphan_collaboration_metadata", "metadata", operation_id, "", 100, "Incoming collaboration metadata has no references.", {}, MANUAL_REVIEW, RISK_BLOCKER, source_identity.project_uuid, target_identity.project_uuid))
        resolved = self._resolve(items, actions or {})
        additions = tuple(item.item_id for item in resolved if item.category.endswith("addition") and actions_or_default(actions, item) != SKIP)
        updates = tuple(item.item_id for item in resolved if actions_or_default(actions, item) in {TAKE_INCOMING, MERGE_COMPATIBLE})
        kept = tuple(item.item_id for item in resolved if item.category.startswith("identical") or actions_or_default(actions, item) == KEEP_CURRENT)
        skipped = tuple(item.item_id for item in resolved if actions_or_default(actions, item) in {SKIP, MANUAL_REVIEW})
        blocked = tuple(sorted(item.item_id for item in resolved if item.risk_level == RISK_BLOCKER and actions_or_default(actions, item) not in {SKIP, MANUAL_REVIEW}))
        estimates = {table: len(target_data[table]) + sum(1 for item in resolved if item.category == f"{table}_addition" and actions_or_default(actions, item) != SKIP) for table in ("people", "families", "family_children", "person_events", "sources", "citations")}
        return ProjectMergePreview(str(source), str(self.target_path), mode, str(uuid4()), tuple(resolved), additions, updates, kept, skipped, blocked, tuple(sorted(warnings)), estimates, str(parent_operation_uuid))

    def apply(self, preview, *, destination=None, confirmed_overwrite=False, cancel_callback=None, progress_callback=None, import_metadata=True):
        if preview.blockers:
            raise ValueError("Project merge preview has unresolved blockers")
        if preview.mode == "Preview only":
            raise ValueError("Preview-only mode cannot be applied")
        target_path = self.target_path
        copied_repository = None
        if preview.mode == "Create new merged project copy":
            if not destination:
                raise ValueError("A destination path is required for a merged project copy")
            target_path = Path(destination).expanduser().resolve()
            if target_path.exists() and not confirmed_overwrite:
                raise FileExistsError("Merged project destination already exists")
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.target_path, target_path)
            from repository.person_repository import PersonRepository
            copied_repository = PersonRepository(target_path)
            service = ProjectMergeService(copied_repository, data_dir=self.data_dir, backup_dir=self.backup_dir)
            result = service._apply_to_target(preview, target_path, cancel_callback, progress_callback, import_metadata)
            copied_repository.close()
            return result
        return self._apply_to_target(preview, target_path, cancel_callback, progress_callback, import_metadata)

    def _apply_to_target(self, preview, target_path, cancel_callback, progress_callback, import_metadata):
        if cancel_callback:
            cancel_callback()
        backup = backup_database(target_path, self.backup_dir)
        before = self.repository.capture_command_state()
        incoming = self._snapshot(Path(preview.source_path), cancel_callback, progress_callback, "apply")
        try:
            with self.repository.transaction():
                self._apply_additions(preview, incoming, cancel_callback, progress_callback)
        except Exception:
            raise
        after = self.repository.capture_command_state()
        delta = RepositoryDeltaCommand._build_delta(before, after)
        identity = self.collaboration.identity()
        context = OperationContext.create(operation_type="merge", project_uuid=identity.project_uuid, dataset_uuid=identity.dataset_uuid, operation_uuid=preview.merge_operation_id, author=identity.editor_identity, session_uuid=self.collaboration.session_id, parent_operation_uuid=preview.parent_operation_uuid, source_module="project_merge_service", affected_entity_types=delta.keys(), provenance={"source_path": preview.source_path}).transition("running").complete()
        AuditService.for_database(target_path).record_delta("merge", delta, description=f"Project merge {preview.merge_operation_id} from {preview.source_path}.", service="project_merge_service", operation_context=context)
        imported = 0
        if import_metadata:
            source_collaboration = CollaborationService(preview.source_path, data_dir=self.data_dir)
            if source_collaboration.identity().dataset_uuid == self.collaboration.identity().dataset_uuid:
                imported = self.collaboration.import_metadata(source_collaboration.metadata_path)
        self.collaboration.record_change("merge", references={}, summary=f"Project merge provenance: {preview.source_path}; operation {preview.merge_operation_id}.", operation_context=context)
        return ProjectMergeResult(preview.merge_operation_id, str(target_path), str(backup), delta, imported)

    def undo_command(self, result):
        return AppliedDeltaCommand("Project Merge", self.repository, result.delta, result)

    def export(self, preview, destination, report_format):
        path = Path(destination); path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        if report_format == "json": content = json.dumps(asdict(preview), ensure_ascii=False, indent=2, default=str)
        elif report_format == "markdown": content = self._markdown(preview)
        elif report_format == "html": content = "<!doctype html><html><meta charset=\"utf-8\"><body><pre>" + html.escape(self._markdown(preview)) + "</pre></body></html>"
        elif report_format == "csv":
            with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream); writer.writerow(("item_id", "category", "entity_type", "source_id", "target_id", "confidence", "action", "risk"))
                for item in preview.items: writer.writerow((item.item_id, item.category, item.entity_type, item.source_entity_id, item.target_entity_id, item.confidence, item.suggested_action, item.risk_level))
            temporary.replace(path)
            return path
        else: raise ValueError("Unsupported merge report format")
        temporary.write_text(content, encoding="utf-8"); temporary.replace(path); return path

    def export_all(self, preview):
        self.report_dir.mkdir(parents=True, exist_ok=True)
        return tuple(self.export(preview, self.report_dir / f"project-merge.{suffix}", kind) for kind, suffix in (("json", "json"), ("markdown", "md"), ("html", "html"), ("csv", "csv")))

    def _snapshot(self, path, cancel_callback, progress_callback, label):
        data = {table: [] for table in ("people", "families", "family_children", "person_events", "sources", "citations")}
        connection = sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            for index, table in enumerate(data, 1):
                if cancel_callback: cancel_callback()
                data[table] = [dict(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY id" if table not in {"family_children"} else "SELECT rowid AS id, family_id, child_id FROM family_children ORDER BY rowid")]
                if progress_callback: progress_callback(f"Reading {label} {table}", index, len(data))
        finally: connection.close()
        return data

    def _compare(self, source, target, source_project, target_project, cancel_callback):
        items = []
        target_people = {row.get("gedcom_id") or f"id:{row['id']}": row for row in target["people"]}
        for person in source["people"]:
            if cancel_callback: cancel_callback()
            key = person.get("gedcom_id") or f"id:{person['id']}"
            match = target_people.get(key) or next((row for row in target["people"] if self._person_key(row) == self._person_key(person)), None)
            if not match:
                items.append(self._item(f"person:{person['id']}", "people_addition", "person", person["id"], "", 100, "Incoming person has no match.", {}, KEEP_BOTH, "low", source_project, target_project)); continue
            differences = self._differences(person, match, ("gedcom_id", "first_name", "last_name", "sex", "birth_date", "birth_place", "death_date", "death_place", "occupation", "note"))
            if not differences:
                category, confidence, action, risk = "identical_person", 100, KEEP_CURRENT, "low"
            elif person.get("gedcom_id") and person.get("gedcom_id") == match.get("gedcom_id"):
                category, confidence, action, risk = "conflicting_person_fields", 100, MANUAL_REVIEW, RISK_BLOCKER
            else:
                category, confidence, action, risk = "probable_duplicate_person", 85, MANUAL_REVIEW, "high"
            items.append(self._item(f"person:{person['id']}:{match['id']}", category, "person", person["id"], match["id"], confidence, "Matching person detected.", differences, action, risk, source_project, target_project))
        items.extend(self._compare_rows("families", source["families"], target["families"], ("husband_id", "wife_id", "relationship_type"), source_project, target_project, "conflicting_family_structure"))
        items.extend(self._compare_rows("events", source["person_events"], target["person_events"], ("event_type", "event_date", "event_place", "description"), source_project, target_project, "conflicting_event"))
        items.extend(self._compare_rows("sources", source["sources"], target["sources"], ("title", "author", "publication", "repository_name", "call_number", "source_url"), source_project, target_project, "conflicting_source"))
        items.extend(self._compare_rows("citations", source["citations"], target["citations"], ("source_id", "target_type", "target_id", "page", "quality", "transcription", "comment"), source_project, target_project, "conflicting_citation"))
        return items

    def _compare_rows(self, label, source_rows, target_rows, fields, source_project, target_project, conflict_category):
        items = []
        for row in source_rows:
            match = next((candidate for candidate in target_rows if all(str(candidate.get(field, "")) == str(row.get(field, "")) for field in fields)), None)
            if match:
                items.append(self._item(f"{label}:{row['id']}:{match['id']}", f"duplicate_{label[:-1]}", label[:-1], row["id"], match["id"], 100, "Equivalent incoming record detected.", {}, KEEP_CURRENT, "low", source_project, target_project))
            else:
                related = next((candidate for candidate in target_rows if str(candidate.get(fields[0], "")) == str(row.get(fields[0], ""))), None)
                category = conflict_category if related else f"{label}_addition"
                action = MANUAL_REVIEW if related else KEEP_BOTH
                risk = RISK_BLOCKER if related and label in {"families", "sources", "citations"} else ("high" if related else "low")
                items.append(self._item(f"{label}:{row['id']}", category, label[:-1], row["id"], related["id"] if related else "", 90 if related else 100, "Incoming record comparison.", self._differences(row, related, fields) if related else {}, action, risk, source_project, target_project))
        return items

    def _apply_additions(self, preview, incoming, cancel_callback, progress_callback):
        selected = {item.source_entity_id for item in preview.items if item.category == "people_addition" and item.suggested_action == KEEP_BOTH}
        for index, person in enumerate(incoming["people"], 1):
            if cancel_callback: cancel_callback()
            if str(person["id"]) not in selected: continue
            payload = {key: person.get(key, "") for key in ("gedcom_id", "first_name", "last_name", "sex", "birth_date", "birth_place", "death_date", "death_place", "occupation", "note")}
            self.repository.create_person(payload)
            if progress_callback: progress_callback("Applying incoming people", index, len(incoming["people"]))

    @staticmethod
    def _person_key(person): return tuple(str(person.get(field, "")).casefold() for field in ("first_name", "last_name", "birth_date", "death_date"))
    @staticmethod
    def _differences(left, right, fields): return {} if right is None else {field: (str(left.get(field, "")), str(right.get(field, ""))) for field in fields if str(left.get(field, "")) != str(right.get(field, ""))}
    @staticmethod
    def _resolve(items, actions): return [item if item.item_id not in actions else MergeItem(**{**asdict(item), "suggested_action": actions[item.item_id]}) for item in sorted(items, key=lambda item: item.item_id)]
    @staticmethod
    def _item(item_id, category, entity_type, source_id, target_id, confidence, explanation, differences, action, risk, source_project, target_project): return MergeItem(item_id, category, source_project, target_project, entity_type, str(source_id), str(target_id), confidence, explanation, differences, action, risk)
    @staticmethod
    def _markdown(preview):
        lines = ["# Project Merge Preview", "", f"Mode: {preview.mode}", f"Operation: {preview.merge_operation_id}", f"Blockers: {len(preview.blockers)}", "", "| Item | Category | Source | Target | Action | Risk |", "| --- | --- | --- | --- | --- | --- |"]
        lines.extend(f"| {item.item_id} | {item.category} | {item.source_entity_id} | {item.target_entity_id} | {item.suggested_action} | {item.risk_level} |" for item in preview.items)
        return "\n".join(lines) + "\n"


def actions_or_default(actions, item):
    return (actions or {}).get(item.item_id, item.suggested_action)