"""Secure local ZIP exchange of selected collaboration changes without networking."""

from __future__ import annotations

import csv
import hashlib
import html
import json
import shutil
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

from collaboration_service import CHANGE_TYPES, CollaborationService
from config import DATA_DIR
from database import validate_database_file
from project_merge_service import ProjectMergeService


FORMAT_VERSION = 1
MAX_FILES = 100
MAX_TOTAL_SIZE = 100 * 1024 * 1024
EXECUTABLE_SUFFIXES = {".py", ".pyc", ".dll", ".exe", ".bat", ".cmd", ".ps1", ".js", ".vbs", ".sh", ".com", ".msi"}
ALLOWED_ENTITY_TYPES = {"person", "family", "event", "source", "citation", "relationship"}


@dataclass(frozen=True)
class ExchangePreview:
    package_uuid: str
    path: str
    status: str
    manifest: dict
    operations: tuple[dict, ...]
    attachments: tuple[dict, ...]
    warnings: tuple[str, ...]
    blockers: tuple[str, ...]
    missing_references: tuple[str, ...]
    already_applied: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()


class ChangeExchangeService:
    """Create and inspect deterministic, untrusted local change packages."""

    def __init__(self, repository, *, data_dir=None):
        self.repository = repository
        self.target_path = Path(repository.db_name).resolve()
        self.data_dir = Path(data_dir or DATA_DIR)
        self.root = self.data_dir / "collaboration" / "change_exchange"
        self.collaboration = CollaborationService(self.target_path, data_dir=self.data_dir)

    def export_preview(self, *, operation_ids=None, date_from="", date_to="", author="", session="", entity_type="", references=(), include_snapshot=None, include_attachments=False):
        selected = []
        wanted = set(operation_ids or ())
        for change in self.collaboration.changes():
            if wanted and change.operation_id not in wanted: continue
            if date_from and change.timestamp < date_from or date_to and change.timestamp > date_to: continue
            if author and change.author != author or session and change.session_id != session: continue
            if entity_type and entity_type not in change.references: continue
            if references and not set(map(str, references)) & {value for values in change.references.values() for value in values}: continue
            selected.append(asdict(change))
        selected.sort(key=lambda item: (item["timestamp"], item["operation_id"]))
        dependencies = self._dependencies(selected)
        missing = self._missing_references(dependencies)
        warnings = tuple(["Attachments were not selected" if not include_attachments else "Attachment selection requires explicit paths"])
        blockers = tuple(["Missing referenced entities"] if missing else [])
        return {"operations": tuple(selected), "dependencies": dependencies, "missing_references": tuple(missing), "warnings": warnings, "blockers": blockers, "snapshot": str(include_snapshot or "")}

    def export(self, destination, *, operation_ids=None, include_snapshot=None, attachment_paths=(), author_approved_machine_username=False, cancel_callback=None, progress_callback=None, **filters):
        preview = self.export_preview(operation_ids=operation_ids, include_snapshot=include_snapshot, **filters)
        if preview["blockers"]: raise ValueError("; ".join(preview["blockers"]))
        destination = Path(destination); destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        if cancel_callback: cancel_callback()
        identity = self.collaboration.identity(); package_id = str(uuid4())
        files = {"changes.json": self._json_bytes(preview["operations"]), "attachments.json": self._json_bytes([])}
        attachments = self._attachment_entries(attachment_paths)
        for attachment in attachments:
            files[attachment["package_path"]] = Path(attachment["source_path"]).read_bytes()
        if attachments: files["attachments.json"] = self._json_bytes([{key: value for key, value in item.items() if key != "source_path"} for item in attachments])
        if include_snapshot:
            snapshot = Path(include_snapshot).resolve(); validate_database_file(snapshot); files["snapshots/incoming.db"] = snapshot.read_bytes()
        portable_attachments = [{key: value for key, value in item.items() if key != "source_path"} for item in attachments]
        manifest = {
            "format_version": FORMAT_VERSION, "package_uuid": package_id, "created_at": self._now(),
            "project_identity": identity.project_uuid, "dataset_identity": identity.dataset_uuid,
            "source_project_uuid": identity.project_uuid, "source_dataset_uuid": identity.dataset_uuid,
            "author": identity.editor_identity, "machine_identity": identity.machine_identifier if author_approved_machine_username else "",
            "base_snapshot_reference": "included_snapshot" if include_snapshot else "", "operations_file": "changes.json",
            "affected_references": preview["dependencies"], "warnings": list(preview["warnings"]), "attachments": portable_attachments,
            "files": {name: self._sha256(content) for name, content in sorted(files.items())}, "lifecycle": "Exported",
        }
        manifest["digital_integrity"] = {"package_checksum": self._sha256(self._json_bytes(manifest["files"])), "manifest_checksum": self._sha256(self._json_bytes(manifest))}
        files["manifest.json"] = self._json_bytes(manifest)
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                for index, name in enumerate(sorted(files), 1):
                    if cancel_callback: cancel_callback()
                    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0)); info.compress_type = zipfile.ZIP_DEFLATED; archive.writestr(info, files[name])
                    if progress_callback: progress_callback("Packaging change exchange", index, len(files))
            temporary.replace(destination)
        except Exception:
            if temporary.exists(): temporary.unlink()
            raise
        self._lifecycle(package_id, "Exported", destination, manifest); return destination

    def inspect(self, package_path, *, cancel_callback=None, max_files=MAX_FILES, max_total_size=MAX_TOTAL_SIZE):
        path = Path(package_path); warnings = []; blockers = []; operations = []; attachments = []; manifest = {}; missing = []
        try:
            with zipfile.ZipFile(path) as archive:
                infos = sorted(archive.infolist(), key=lambda item: item.filename)
                if len(infos) > max_files: raise ValueError("Package has too many files")
                if len({item.filename for item in infos}) != len(infos): raise ValueError("Package contains duplicate file names")
                if sum(item.file_size for item in infos) > max_total_size: raise ValueError("Package exceeds decompression limit")
                for info in infos:
                    if cancel_callback: cancel_callback()
                    self._safe_member(info)
                names = {item.filename for item in infos}
                if "manifest.json" not in names or "changes.json" not in names: raise ValueError("Package is missing required files")
                manifest = json.loads(archive.read("manifest.json").decode("utf-8")); self._validate_manifest(manifest)
                if names != set(manifest["files"]) | {"manifest.json"}: raise ValueError("Package contains unlisted files")
                for name, checksum in manifest["files"].items():
                    if name not in names or self._sha256(archive.read(name)) != checksum: raise ValueError(f"Checksum mismatch: {name}")
                operations = json.loads(archive.read("changes.json").decode("utf-8")); self._validate_operations(operations)
                attachments = json.loads(archive.read("attachments.json").decode("utf-8")) if "attachments.json" in names else []
                for attachment in attachments:
                    package_path = attachment.get("package_path", "")
                    if package_path not in names or self._sha256(archive.read(package_path)) != attachment.get("checksum", ""):
                        raise ValueError("Attachment checksum mismatch")
                missing = self._missing_references(manifest.get("affected_references", {}), repository=False)
        except (OSError, zipfile.BadZipFile, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            return ExchangePreview(manifest.get("package_uuid", ""), str(path), "Rejected", manifest, (), (), (), (str(error),), (), ())
        known = {change.operation_id for change in self.collaboration.changes()}; operation_ids = [item["operation_id"] for item in operations]
        already = tuple(sorted(set(operation_ids) & known)); conflicts = tuple(sorted([f"operation:{value}" for value in already]))
        if manifest["dataset_identity"] != self.collaboration.identity().dataset_uuid: blockers.append("Dataset UUID mismatch")
        if missing: blockers.append("Missing dependencies")
        result = ExchangePreview(manifest["package_uuid"], str(path), "Inspected" if not blockers else "Rejected", manifest, tuple(operations), tuple(attachments), tuple(warnings), tuple(blockers), tuple(missing), already, conflicts)
        self._lifecycle(result.package_uuid, result.status, path, manifest); return result

    def preview_against_current(self, inspected, *, cancel_callback=None):
        if inspected.status == "Rejected": raise ValueError("Rejected package cannot be previewed")
        if cancel_callback: cancel_callback()
        conflicts = list(inspected.conflicts); known = {change.operation_id for change in self.collaboration.changes()}
        for operation in inspected.operations:
            if operation["operation_id"] in known: conflicts.append(f"already-applied:{operation['operation_id']}")
        return ExchangePreview(**{**asdict(inspected), "status": "Previewed", "conflicts": tuple(sorted(set(conflicts)))})

    def create_incoming_copy(self, inspected, destination, *, confirmed_overwrite=False):
        if inspected.status == "Rejected": raise ValueError("Rejected package cannot be converted")
        destination = Path(destination).resolve()
        if destination.exists() and not confirmed_overwrite: raise FileExistsError("Incoming project destination already exists")
        with zipfile.ZipFile(inspected.path) as archive:
            if "snapshots/incoming.db" not in archive.namelist(): raise ValueError("Package has no supported incoming snapshot")
            payload = archive.read("snapshots/incoming.db")
        temporary = destination.with_suffix(destination.suffix + ".tmp"); destination.parent.mkdir(parents=True, exist_ok=True); temporary.write_bytes(payload); validate_database_file(temporary); temporary.replace(destination); return destination

    def to_project_merge_input(self, inspected, destination, *, cancel_callback=None):
        if cancel_callback: cancel_callback()
        path = self.create_incoming_copy(inspected, destination)
        self._lifecycle(inspected.package_uuid, "Accepted for merge", path, inspected.manifest); return path

    def reject(self, inspected, reason):
        self._lifecycle(inspected.package_uuid, "Rejected", inspected.path, {**inspected.manifest, "rejection_reason": str(reason)})

    def export_report(self, inspected, destination, report_format):
        path = Path(destination); path.parent.mkdir(parents=True, exist_ok=True); temporary = path.with_suffix(path.suffix + ".tmp")
        if report_format == "json": temporary.write_text(json.dumps(asdict(inspected), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        elif report_format == "markdown": temporary.write_text(self._markdown(inspected), encoding="utf-8")
        elif report_format == "html": temporary.write_text("<!doctype html><meta charset=\"utf-8\"><pre>" + html.escape(self._markdown(inspected)) + "</pre>", encoding="utf-8")
        elif report_format == "csv":
            with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream); writer.writerow(("package_uuid", "operation_id", "type", "author", "timestamp")); writer.writerows((inspected.package_uuid, item["operation_id"], item["change_type"], item.get("author", ""), item["timestamp"]) for item in inspected.operations)
        else: raise ValueError("Unsupported exchange report format")
        temporary.replace(path); return path

    def export_all_reports(self, inspected):
        directory = self.root / "reports"; directory.mkdir(parents=True, exist_ok=True)
        return tuple(self.export_report(inspected, directory / f"{inspected.package_uuid}.{suffix}", kind) for kind, suffix in (("json", "json"), ("markdown", "md"), ("html", "html"), ("csv", "csv")))

    def _attachment_entries(self, paths):
        output = []; seen = set()
        for raw in paths:
            path = Path(raw).resolve()
            if not path.is_file(): raise ValueError(f"Missing attachment: {path}")
            digest = self._sha256(path.read_bytes())
            if digest in seen: continue
            safe_name = "".join(character if character.isalnum() or character in ".-_" else "_" for character in path.name)
            output.append({"name": safe_name, "package_path": f"attachments/{digest[:16]}-{safe_name}", "source_path": str(path), "checksum": digest, "size": path.stat().st_size})
        return sorted(output, key=lambda item: (item["checksum"], item["name"]))
    def _dependencies(self, operations):
        result = {}
        for operation in operations:
            for kind, values in operation.get("references", {}).items(): result.setdefault(kind, set()).update(map(str, values))
        return {key: sorted(values) for key, values in sorted(result.items())}
    def _missing_references(self, dependencies, repository=True):
        if not repository: return []
        lookups = {"person": "get_person_record", "family": "get_family", "source": "get_source_record"}; missing = []
        for kind, values in dependencies.items():
            lookup = getattr(self.repository, lookups.get(kind, ""), None)
            if lookup is None: missing.extend(f"{kind}:{value}" for value in values); continue
            missing.extend(f"{kind}:{value}" for value in values if not lookup(int(value)))
        return sorted(missing)
    def _validate_manifest(self, manifest):
        if manifest.get("format_version") != FORMAT_VERSION: raise ValueError("Unsupported package format")
        for field in ("package_uuid", "project_identity", "dataset_identity", "source_project_uuid", "source_dataset_uuid"):
            UUID(str(manifest.get(field, "")))
        datetime.fromisoformat(manifest["created_at"]); files = manifest.get("files")
        if not isinstance(files, dict) or not files: raise ValueError("Invalid package file checksums")
        integrity = manifest.get("digital_integrity", {}).get("manifest_checksum", "")
        unsigned = dict(manifest); unsigned.pop("digital_integrity", None)
        if integrity != self._sha256(self._json_bytes(unsigned)): raise ValueError("Manifest checksum mismatch")
        if manifest.get("digital_integrity", {}).get("package_checksum") != self._sha256(self._json_bytes(files)): raise ValueError("Package checksum mismatch")
    def _validate_operations(self, operations):
        ids = []
        for item in operations:
            UUID(str(item.get("operation_id", ""))); datetime.fromisoformat(item["timestamp"])
            if item.get("change_type") not in CHANGE_TYPES: raise ValueError("Unsupported operation type")
            if any(kind not in ALLOWED_ENTITY_TYPES for kind in item.get("references", {})): raise ValueError("Unsupported entity type")
            ids.append(item["operation_id"])
        if len(ids) != len(set(ids)): raise ValueError("Duplicate operation IDs")
    def _safe_member(self, info):
        path = PurePosixPath(info.filename)
        if path.is_absolute() or ".." in path.parts or not info.filename or Path(info.filename).suffix.lower() in EXECUTABLE_SUFFIXES: raise ValueError("Unsafe package path or executable content")
        if info.external_attr >> 16 & 0o170000 == 0o120000: raise ValueError("Symbolic links are not allowed")
    def _lifecycle(self, package_id, state, path, manifest):
        subdirectory = {"Exported": "exported", "Rejected": "rejected", "Inspected": "received", "Accepted for merge": "received"}.get(state, "drafts")
        destination = self.root / subdirectory / f"{package_id}.json"; destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(".tmp"); temporary.write_text(json.dumps({"package_uuid": package_id, "state": state, "path": str(path), "manifest": manifest}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"); temporary.replace(destination)
    @staticmethod
    def _sha256(data): return hashlib.sha256(data).hexdigest()
    @staticmethod
    def _json_bytes(value): return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    @staticmethod
    def _now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
    @staticmethod
    def _markdown(item): return f"# Offline Change Exchange\n\nPackage: {item.package_uuid}\nStatus: {item.status}\nOperations: {len(item.operations)}\nWarnings: {', '.join(item.warnings) or '-'}\nBlockers: {', '.join(item.blockers) or '-'}\n"