"""Local-only collaboration metadata stored outside genealogy records."""

from __future__ import annotations

import hashlib
import json
import platform
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from config import DATA_DIR


CHANGE_TYPES = (
    "create_person", "edit_person", "delete_person", "merge", "split",
    "relationship_change", "source_change",
)
FUTURE_INTERFACES = (
    "merge_from_project", "detect_conflicts", "exchange_changes", "review_workflow",
)


@dataclass(frozen=True)
class CollaborationIdentity:
    project_uuid: str
    dataset_uuid: str
    editor_identity: str
    machine_identifier: str


@dataclass(frozen=True)
class CollaborationChange:
    operation_id: str
    change_type: str
    author: str
    timestamp: str
    session_id: str
    machine_identifier: str
    references: dict[str, tuple[str, ...]]
    summary: str


@dataclass(frozen=True)
class CollaborationDiagnostics:
    project_identity: CollaborationIdentity
    change_count: int
    orphan_operation_ids: tuple[str, ...]
    missing_references: dict[str, tuple[str, ...]]
    consistency_issues: tuple[str, ...]


class CollaborationService:
    """Persist collaboration metadata locally without changing genealogy data."""

    def __init__(self, database_path, *, data_dir=None, editor_identity="", machine_identifier=""):
        self.database_path = Path(database_path).expanduser().resolve()
        self.data_dir = Path(data_dir or DATA_DIR) / "collaboration"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(str(self.database_path).encode("utf-8")).hexdigest()[:24]
        self.metadata_path = self.data_dir / f"{digest}.json"
        self.session_id = str(uuid4())
        self._default_editor = str(editor_identity).strip()
        self._default_machine = str(machine_identifier).strip()
        self._ensure_metadata()

    def identity(self) -> CollaborationIdentity:
        payload = self._read()
        identity = payload["identity"]
        return CollaborationIdentity(**identity)

    def configure_identity(self, *, editor_identity=None, machine_identifier=None) -> CollaborationIdentity:
        payload = self._read()
        if editor_identity is not None:
            payload["identity"]["editor_identity"] = str(editor_identity).strip()
        if machine_identifier is not None:
            payload["identity"]["machine_identifier"] = str(machine_identifier).strip()
        self._write(payload)
        return self.identity()

    def record_change(self, change_type, *, references=None, summary="", author="", timestamp=None, operation_id=None) -> CollaborationChange:
        if change_type not in CHANGE_TYPES:
            raise ValueError(f"Unsupported collaboration change type: {change_type}")
        identity = self.identity()
        change = CollaborationChange(
            operation_id=str(operation_id or uuid4()),
            change_type=change_type,
            author=str(author or identity.editor_identity),
            timestamp=str(timestamp or self._now()),
            session_id=self.session_id,
            machine_identifier=identity.machine_identifier,
            references=self._references(references),
            summary=str(summary),
        )
        self._require_uuid(change.operation_id, "operation_id")
        payload = self._read()
        if any(item["operation_id"] == change.operation_id for item in payload["changes"]):
            raise ValueError(f"Duplicate collaboration operation: {change.operation_id}")
        payload["changes"].append(asdict(change))
        self._write(payload)
        return change

    def changes(self) -> tuple[CollaborationChange, ...]:
        return tuple(
            CollaborationChange(
                **{**item, "references": {key: tuple(value) for key, value in item.get("references", {}).items()}},
            )
            for item in sorted(self._read()["changes"], key=lambda item: (item["timestamp"], item["operation_id"]))
        )

    def export_metadata(self, destination) -> Path:
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self._serialized(self._read()), encoding="utf-8")
        return path

    def import_metadata(self, source) -> int:
        incoming = json.loads(Path(source).read_text(encoding="utf-8"))
        self._validate_payload(incoming)
        current = self._read()
        if current["changes"] and incoming["identity"]["dataset_uuid"] != current["identity"]["dataset_uuid"]:
            raise ValueError("Collaboration metadata belongs to another dataset")
        if not current["changes"]:
            current["identity"]["dataset_uuid"] = incoming["identity"]["dataset_uuid"]
        known = {item["operation_id"] for item in current["changes"]}
        additions = [item for item in incoming["changes"] if item["operation_id"] not in known]
        current["changes"].extend(additions)
        self._write(current)
        return len(additions)

    def diagnostics(self, repository=None) -> CollaborationDiagnostics:
        payload = self._read()
        issues = self._consistency_issues(payload)
        missing = {}
        orphan = []
        for change in self.changes():
            if not change.references:
                orphan.append(change.operation_id)
                continue
            absent = self._missing_references(repository, change.references) if repository is not None else {}
            if absent:
                missing[change.operation_id] = tuple(f"{kind}:{value}" for kind, values in absent.items() for value in values)
                orphan.append(change.operation_id)
        return CollaborationDiagnostics(self.identity(), len(payload["changes"]), tuple(sorted(orphan)), missing, tuple(issues))

    def merge_from_project(self, *_args, **_kwargs):
        raise NotImplementedError("Future local project merge interface; networking is not implemented.")

    def detect_conflicts(self, *_args, **_kwargs):
        raise NotImplementedError("Future conflict detection interface; synchronization is not implemented.")

    def exchange_changes(self, *_args, **_kwargs):
        raise NotImplementedError("Future change exchange interface; networking is not implemented.")

    def review_workflow(self, *_args, **_kwargs):
        raise NotImplementedError("Future review workflow interface; networking is not implemented.")

    def _ensure_metadata(self):
        if self.metadata_path.exists():
            self._validate_payload(self._read())
            return
        identity = {
            "project_uuid": str(uuid4()),
            "dataset_uuid": str(uuid4()),
            "editor_identity": self._default_editor,
            "machine_identifier": self._default_machine or platform.node(),
        }
        self._write({"format_version": 1, "identity": identity, "changes": []})

    def _read(self):
        return json.loads(self.metadata_path.read_text(encoding="utf-8"))

    def _write(self, payload):
        self._validate_payload(payload)
        temporary = self.metadata_path.with_suffix(".tmp")
        temporary.write_text(self._serialized(payload), encoding="utf-8")
        temporary.replace(self.metadata_path)

    @staticmethod
    def _serialized(payload):
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def _validate_payload(self, payload):
        if payload.get("format_version") != 1 or not isinstance(payload.get("changes"), list):
            raise ValueError("Unsupported collaboration metadata format")
        identity = payload.get("identity", {})
        for field in ("project_uuid", "dataset_uuid"):
            self._require_uuid(identity.get(field, ""), field)
        for item in payload["changes"]:
            self._require_uuid(item.get("operation_id", ""), "operation_id")
            if item.get("change_type") not in CHANGE_TYPES:
                raise ValueError("Unsupported collaboration change type")

    @staticmethod
    def _require_uuid(value, field):
        try:
            UUID(str(value))
        except (TypeError, ValueError) as error:
            raise ValueError(f"Invalid {field}") from error

    @staticmethod
    def _references(references):
        return {
            str(kind): tuple(sorted({str(value) for value in values if str(value)}))
            for kind, values in sorted((references or {}).items())
        }

    def _consistency_issues(self, payload):
        operation_ids = [item["operation_id"] for item in payload["changes"]]
        issues = []
        if len(operation_ids) != len(set(operation_ids)):
            issues.append("duplicate operation identifiers")
        return issues

    @staticmethod
    def _missing_references(repository, references):
        lookups = {
            "person": "get_person_record", "family": "get_family", "source": "get_source_record",
        }
        missing = {}
        for kind, values in references.items():
            lookup = getattr(repository, lookups.get(kind, ""), None)
            if lookup is None:
                missing[kind] = tuple(values)
                continue
            absent = tuple(value for value in values if not lookup(int(value)))
            if absent:
                missing[kind] = absent
        return missing

    @staticmethod
    def _now():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")