"""Local, read-only history indexing and validated historical project recovery."""

from __future__ import annotations

import csv
import html
import json
import shutil
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from audit_service import AuditService
from collaboration_service import CollaborationService
from config import DATA_DIR
from database import backup_database, validate_database_file
from undo_manager import AppliedDeltaCommand, RepositoryDeltaCommand
from operation_correlation import OperationContext
from operation_correlation import validate_correlation


@dataclass(frozen=True)
class HistoryEntry:
    entry_id: str
    operation_uuid: str
    timestamp: str
    author: str
    machine_id: str
    session_id: str
    category: str
    operation_type: str
    entity_type: str
    affected_entity_ids: tuple[str, ...]
    summary: str
    before_snapshot_reference: str
    after_snapshot_reference: str
    provenance: dict
    undo_redo_boundary: str
    related_operation: str
    risk_level: str


@dataclass(frozen=True)
class HistoricalPreview:
    entry_id: str
    snapshot_path: str
    temporary_path: str
    counts: dict[str, int]
    read_only: bool = True


@dataclass(frozen=True)
class RestorePreview:
    entry_id: str
    snapshot_path: str
    target_path: str
    dataset_compatible: bool
    blockers: tuple[str, ...]


@dataclass(frozen=True)
class RestoreResult:
    operation_uuid: str
    target_path: str
    backup_path: str
    delta: dict


class HistoryBrowserService:
    """Index local history without changing genealogy until an explicit restore."""

    def __init__(self, repository, *, data_dir=None, backup_dir=None):
        self.repository = repository
        self.target_path = Path(repository.db_name).resolve()
        self.data_dir = Path(data_dir or DATA_DIR)
        self.root = self.data_dir / "collaboration"
        self.metadata_path = self.root / "history_metadata" / f"{self.target_path.name}.json"
        self.snapshot_dir = self.root / "history_metadata" / "snapshots"
        self.report_dir = self.root / "history_reports"
        self.backup_dir = Path(backup_dir or self.data_dir / "backups")
        self.collaboration = CollaborationService(self.target_path, data_dir=self.data_dir)

    def entries(self, *, filters=None, group_by=None, cancel_callback=None, progress_callback=None):
        filters = filters or {}; entries = self._audit_entries() + self._collaboration_entries()
        unique = {entry.entry_id: entry for entry in entries}
        filtered = [entry for entry in unique.values() if self._matches(entry, filters)]
        filtered.sort(key=lambda entry: (entry.timestamp, entry.entry_id), reverse=True)
        if progress_callback: progress_callback("Indexing history", len(filtered), len(filtered))
        if cancel_callback: cancel_callback()
        if group_by:
            groups = {}
            for entry in filtered:
                key = self._group_key(entry, group_by); groups.setdefault(key, []).append(entry)
            return {key: tuple(value) for key, value in sorted(groups.items())}
        return tuple(filtered)

    def correlation_diagnostics(self):
        """Validate Audit/Collaboration operation counterparts without mutation."""
        return validate_correlation(AuditService.for_database(self.target_path).list_records(), self.collaboration.changes())

    def bookmark(self, entry_id, *, bookmarked=True, tags=(), note=""):
        metadata = self._metadata(); item = metadata.setdefault("entries", {}).setdefault(str(entry_id), {})
        item.update({"bookmarked": bool(bookmarked), "tags": sorted({str(tag).strip() for tag in tags if str(tag).strip()}), "note": str(note)})
        self._write_metadata(metadata)

    def entry_metadata(self, entry_id):
        return self._metadata().get("entries", {}).get(str(entry_id), {"bookmarked": False, "tags": [], "note": ""})

    def create_snapshot(self, *, label="", retention_count=20, retention_days=90, retention_bytes=2_000_000_000, cancel_callback=None):
        if cancel_callback: cancel_callback()
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        snapshot_id = str(uuid4()); path = backup_database(self.target_path, self.snapshot_dir / f"{snapshot_id}.db")
        metadata = self._metadata(); metadata.setdefault("snapshots", {})[snapshot_id] = {"path": str(path), "created_at": self._now(), "label": str(label), "dataset_uuid": self.collaboration.identity().dataset_uuid}
        self._write_metadata(metadata); self.enforce_retention(retention_count, retention_days, retention_bytes)
        return snapshot_id, path

    def enforce_retention(self, count, days, total_bytes):
        metadata = self._metadata(); snapshots = metadata.get("snapshots", {})
        now = datetime.now(timezone.utc); ordered = sorted(snapshots.items(), key=lambda item: (item[1].get("created_at", ""), item[0]), reverse=True); retained = []; size = 0
        for snapshot_id, item in ordered:
            path = Path(item["path"]); age = (now - datetime.fromisoformat(item["created_at"])).days if item.get("created_at") else days + 1; candidate_size = path.stat().st_size if path.exists() else 0
            if len(retained) < count and age <= days and size + candidate_size <= total_bytes:
                retained.append(snapshot_id); size += candidate_size; continue
            if path.parent == self.snapshot_dir and path.exists(): path.unlink()
        metadata["snapshots"] = {key: snapshots[key] for key in sorted(retained)}; self._write_metadata(metadata)

    def compare(self, first, second, *, cancel_callback=None, progress_callback=None):
        left = self._state_for(first); right = self._state_for(second)
        result = {"added": {}, "removed": {}, "modified": {}, "relationships": {}, "events": {}, "sources_citations": {}, "attachments": {}, "collaboration": {}}
        for index, table in enumerate(sorted(set(left) | set(right)), 1):
            if cancel_callback: cancel_callback()
            left_rows, right_rows = {self._row_key(row): row for row in left.get(table, [])}, {self._row_key(row): row for row in right.get(table, [])}
            result["added"][table] = tuple(sorted(set(right_rows) - set(left_rows)))
            result["removed"][table] = tuple(sorted(set(left_rows) - set(right_rows)))
            result["modified"][table] = {key: self._field_differences(left_rows[key], right_rows[key]) for key in sorted(set(left_rows) & set(right_rows)) if left_rows[key] != right_rows[key]}
            if progress_callback: progress_callback("Comparing history points", index, len(set(left) | set(right)))
        result["relationships"] = {table: result["modified"].get(table, {}) for table in ("families", "family_children")}
        result["events"] = result["modified"].get("person_events", {})
        result["sources_citations"] = {table: result["modified"].get(table, {}) for table in ("sources", "citations")}
        result["attachments"] = result["modified"].get("person_media", {})
        return result

    def historical_preview(self, entry, *, cancel_callback=None):
        snapshot = self._snapshot_path(entry); validate_database_file(snapshot)
        if cancel_callback: cancel_callback()
        temporary = Path(tempfile.mkdtemp(prefix="genealogy-history-")) / "historical.db"; shutil.copy2(snapshot, temporary)
        return HistoricalPreview(entry.entry_id, str(snapshot), str(temporary), self._counts(temporary))

    def close_preview(self, preview):
        path = Path(preview.temporary_path)
        if path.exists(): path.unlink()
        if path.parent.exists(): path.parent.rmdir()

    def restore_preview(self, entry):
        snapshot = self._snapshot_path(entry); validate_database_file(snapshot)
        snapshot_metadata = next((item for item in self._metadata().get("snapshots", {}).values() if Path(item.get("path", "")).resolve() == snapshot.resolve()), {})
        compatible = not snapshot_metadata or snapshot_metadata.get("dataset_uuid") == self.collaboration.identity().dataset_uuid
        blockers = [] if compatible else ["Snapshot belongs to another dataset"]
        blockers.extend(self._relationship_blockers(snapshot))
        return RestorePreview(entry.entry_id, str(snapshot), str(self.target_path), compatible, tuple(blockers))

    def restore(self, preview, *, mode="Restore current project", destination=None, confirmed_overwrite=False, confirmed=False, cancel_callback=None):
        if not confirmed: raise ValueError("Restore requires explicit confirmation")
        if preview.blockers and mode != "Create historical project copy": raise ValueError("Restore preview has safety blockers")
        target = self.target_path if mode == "Restore current project" else Path(destination or "").expanduser().resolve()
        if mode == "Create historical project copy":
            if not destination: raise ValueError("A destination path is required")
            if target.exists() and not confirmed_overwrite: raise FileExistsError("Historical project destination already exists")
            target.parent.mkdir(parents=True, exist_ok=True); shutil.copy2(preview.snapshot_path, target); validate_database_file(target)
            return RestoreResult(str(uuid4()), str(target), "", {})
        if cancel_callback: cancel_callback()
        backup = backup_database(self.target_path, self.backup_dir); before = self.repository.capture_command_state()
        try:
            source = sqlite3.connect(f"file:{Path(preview.snapshot_path).resolve().as_posix()}?mode=ro", uri=True)
            try:
                source.backup(self.repository.conn)
            finally:
                source.close()
            validate_database_file(self.target_path)
            blockers = self._relationship_blockers(self.target_path)
            if blockers: raise ValueError("; ".join(blockers))
        except Exception:
            source = sqlite3.connect(f"file:{backup.resolve().as_posix()}?mode=ro", uri=True)
            try:
                source.backup(self.repository.conn)
            finally:
                source.close()
            raise
        delta = RepositoryDeltaCommand._build_delta(before, self.repository.capture_command_state())
        identity = self.collaboration.identity()
        context = OperationContext.create(operation_type="restore", project_uuid=identity.project_uuid, dataset_uuid=identity.dataset_uuid, author=identity.editor_identity, session_uuid=self.collaboration.session_id, source_module="history_browser_service", affected_entity_types=delta.keys(), provenance={"snapshot_path": preview.snapshot_path, "backup_path": str(backup)}).transition("running").complete()
        AuditService.for_database(self.target_path).record_delta("restore", delta, description=f"History restore {context.operation_uuid} from {preview.snapshot_path}.", service="history_browser_service", operation_context=context)
        self.collaboration.record_change("merge", references={}, summary=f"History restore provenance: {context.operation_uuid}; snapshot {preview.snapshot_path}.", operation_context=context)
        return RestoreResult(context.operation_uuid, str(self.target_path), str(backup), delta)

    def undo_command(self, result): return AppliedDeltaCommand("History Restore", self.repository, result.delta, result)

    def export(self, entries, destination, report_format):
        path = Path(destination); path.parent.mkdir(parents=True, exist_ok=True); temporary = path.with_suffix(path.suffix + ".tmp")
        if report_format == "json": temporary.write_text(json.dumps([asdict(entry) for entry in entries], ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        elif report_format == "markdown": temporary.write_text("# History Browser\n\n" + "\n".join(f"- {entry.timestamp} | {entry.category} | {entry.summary}" for entry in entries) + "\n", encoding="utf-8")
        elif report_format == "html": temporary.write_text("<!doctype html><meta charset=\"utf-8\"><pre>" + html.escape("\n".join(f"{entry.timestamp} | {entry.category} | {entry.summary}" for entry in entries)) + "</pre>", encoding="utf-8")
        elif report_format == "csv":
            with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream); writer.writerow(("entry_id", "timestamp", "category", "operation", "summary", "risk")); writer.writerows((entry.entry_id, entry.timestamp, entry.category, entry.operation_type, entry.summary, entry.risk_level) for entry in entries)
        else: raise ValueError("Unsupported history report format")
        temporary.replace(path); return path

    def export_all(self, entries):
        self.report_dir.mkdir(parents=True, exist_ok=True)
        return tuple(self.export(entries, self.report_dir / f"history.{suffix}", kind) for kind, suffix in (("json", "json"), ("markdown", "md"), ("html", "html"), ("csv", "csv")))

    def _audit_entries(self):
        result = []
        for record in AuditService.for_database(self.target_path).list_records(sort_order="asc"):
            entity_ids = tuple(value for value in (record.database_id, record.gedcom_id) if value)
            operation_uuid = record.operation_uuid or f"audit-{record.id}"
            result.append(HistoryEntry(f"audit:{record.id}", operation_uuid, record.timestamp, "", "", record.session_uuid, self._category(record.operation_type, record.service), record.operation_type, self._entity_type(record.affected_tables), entity_ids, record.description, f"audit:{record.id}:before", f"audit:{record.id}:after", {"service": record.service, "audit_id": record.id, "status": record.status, "project_uuid": record.project_uuid, "dataset_uuid": record.dataset_uuid, "parent_operation_uuid": record.parent_operation_uuid, "preview": record.preview}, record.operation_type if record.operation_type in {"undo", "redo"} else "", record.parent_operation_uuid or record.batch_id, "high" if record.operation_type in {"merge", "restore", "delete_person"} else "medium"))
        return result

    def _collaboration_entries(self):
        audit_operations = {record.operation_uuid for record in AuditService.for_database(self.target_path).list_records() if record.operation_uuid}
        return [HistoryEntry(f"collaboration:{change.operation_id}", change.operation_uuid or change.operation_id, change.timestamp, change.author, change.machine_identifier, change.session_id, "collaboration", change.change_type, next(iter(change.references), ""), tuple(value for values in change.references.values() for value in values), change.summary, "", "", {"references": change.references, "status": change.status, "project_uuid": change.project_uuid, "dataset_uuid": change.dataset_uuid, "parent_operation_uuid": change.parent_operation_uuid, "preview": change.preview}, "", change.parent_operation_uuid, "medium") for change in self.collaboration.changes() if (change.operation_uuid or change.operation_id) not in audit_operations]

    def _state_for(self, point):
        if isinstance(point, HistoricalPreview): return self._read_state(point.temporary_path)
        if isinstance(point, (str, Path)): return self._read_state(point)
        reference = getattr(point, "after_snapshot_reference", "")
        if reference.startswith("snapshot:"): return self._read_state(reference.split(":", 1)[1])
        record_id = int(point.entry_id.split(":", 1)[1]) if isinstance(point, HistoryEntry) and point.entry_id.startswith("audit:") else None
        if record_id: return AuditService.for_database(self.target_path).get(record_id).after_snapshot
        return {}

    def _snapshot_path(self, entry):
        if isinstance(entry, HistoricalPreview): return Path(entry.snapshot_path)
        reference = getattr(entry, "after_snapshot_reference", str(entry))
        if reference.startswith("snapshot:"): return Path(reference.split(":", 1)[1])
        raise ValueError("History entry does not have a restorable snapshot")

    def _metadata(self):
        if not self.metadata_path.exists(): return {"format_version": 1, "entries": {}, "snapshots": {}}
        return json.loads(self.metadata_path.read_text(encoding="utf-8"))
    def _write_metadata(self, metadata):
        self.metadata_path.parent.mkdir(parents=True, exist_ok=True); temporary = self.metadata_path.with_suffix(".tmp"); temporary.write_text(json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"); temporary.replace(self.metadata_path)
    def _matches(self, entry, filters):
        metadata = self.entry_metadata(entry.entry_id); haystack = " ".join((entry.summary, entry.author, entry.machine_id, entry.operation_type, entry.entity_type, *entry.affected_entity_ids, *metadata.get("tags", []), metadata.get("note", ""))).casefold()
        return (not filters.get("date_from") or entry.timestamp >= filters["date_from"]) and (not filters.get("date_to") or entry.timestamp <= filters["date_to"]) and (not filters.get("author") or entry.author == filters["author"]) and (not filters.get("machine") or entry.machine_id == filters["machine"]) and (not filters.get("operation_type") or entry.operation_type == filters["operation_type"]) and (not filters.get("entity_type") or entry.entity_type == filters["entity_type"]) and (not filters.get("entity_id") or filters["entity_id"] in entry.affected_entity_ids) and (not filters.get("merge") or entry.category == "merge") and (not filters.get("conflict_resolution") or "conflict" in entry.summary.casefold()) and (not filters.get("undoable") or bool(entry.before_snapshot_reference)) and (not filters.get("bookmarked") or metadata.get("bookmarked")) and (not filters.get("search") or str(filters["search"]).casefold() in haystack)
    @staticmethod
    def _group_key(entry, group_by): return {"session": entry.session_id or "unspecified", "author": entry.author or "unspecified", "entity": entry.entity_type or "unspecified", "category": entry.category}.get(group_by, "timeline")
    @staticmethod
    def _category(operation, service): return "conflict_resolution" if "conflict" in service else "merge" if "merge" in service else "backup_restore" if operation == "restore" else "import" if operation == "import" else "audit"
    @staticmethod
    def _entity_type(tables): return "person" if "people" in tables else "family" if "families" in tables else "source" if "sources" in tables else ""
    @staticmethod
    def _row_key(row): return str(row[0]) if isinstance(row, (list, tuple)) and row else str(row.get("id", ""))
    @staticmethod
    def _field_differences(left, right): return {str(index): (left[index], right[index]) for index in range(min(len(left), len(right))) if left[index] != right[index]} if isinstance(left, (list, tuple)) else {key: (left.get(key), right.get(key)) for key in set(left) | set(right) if left.get(key) != right.get(key)}
    def _read_state(self, path):
        connection = sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)
        try:
            tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            return {table: connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid').fetchall() for table in sorted(tables)}
        finally:
            connection.close()
    def _counts(self, path): return {table: len(rows) for table, rows in self._read_state(path).items()}
    def _relationship_blockers(self, path):
        connection = sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)
        try:
            people = {str(row[0]) for row in connection.execute("SELECT id FROM people")}; people.update(str(row[0]) for row in connection.execute("SELECT gedcom_id FROM people WHERE gedcom_id <> ''")); links = connection.execute("SELECT family_id, child_id FROM family_children").fetchall()
            missing = [str(child) for _, child in links if str(child) not in people]; self_links = [str(child) for family, child in links if str(family) == str(child)]
            families = {str(row[0]): (str(row[1] or ""), str(row[2] or ""), str(row[3] or "")) for row in connection.execute("SELECT id, gedcom_id, husband_id, wife_id FROM families")}
            graph = {}
            for family_reference, child in links:
                family = families.get(str(family_reference)) or next((value for value in families.values() if value[0] == str(family_reference)), None)
                if family:
                    for parent in family[1:]:
                        if parent: graph.setdefault(parent, set()).add(str(child))
        finally:
            connection.close()
        def cyclic(node, visiting, visited):
            if node in visiting: return True
            if node in visited: return False
            visiting.add(node); result = any(cyclic(child, visiting, visited) for child in graph.get(node, ())); visiting.remove(node); visited.add(node); return result
        cycles = any(cyclic(node, set(), set()) for node in graph)
        blockers = []
        if missing: blockers.append("missing relationship references")
        if self_links: blockers.append("self relationship links")
        if cycles: blockers.append("relationship cycle")
        return tuple(blockers)
    @staticmethod
    def _now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")