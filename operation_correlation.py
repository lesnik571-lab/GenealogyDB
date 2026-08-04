"""Shared operation identity and validation for local 2.1 sidecars."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4


STATUSES = ("preview", "planned", "running", "completed", "failed", "cancelled", "rolled_back")
TRANSITIONS = {
    "planned": {"running", "cancelled", "failed"},
    "running": {"completed", "failed", "cancelled", "rolled_back"},
    "preview": set(), "completed": set(), "failed": set(), "cancelled": set(), "rolled_back": set(),
}


@dataclass(frozen=True)
class OperationContext:
    operation_uuid: str
    operation_type: str
    project_uuid: str
    dataset_uuid: str
    author: str = ""
    session_uuid: str = ""
    started_at: str = ""
    completed_at: str = ""
    status: str = "planned"
    source_module: str = ""
    affected_entity_types: tuple[str, ...] = ()
    affected_entity_ids: tuple[str, ...] = ()
    parent_operation_uuid: str = ""
    provenance: dict | None = None
    preview: bool = False
    reason: str = ""

    @classmethod
    def create(cls, *, operation_type, project_uuid, dataset_uuid, operation_uuid=None, author="", session_uuid="", source_module="", parent_operation_uuid="", affected_entity_types=(), affected_entity_ids=(), provenance=None, preview=False, now=None):
        timestamp = now or datetime.now(timezone.utc).isoformat(timespec="microseconds")
        context = cls(str(operation_uuid or uuid4()), str(operation_type), str(project_uuid), str(dataset_uuid), str(author), str(session_uuid), timestamp, "" if not preview else timestamp, "preview" if preview else "planned", str(source_module), tuple(sorted({str(value) for value in affected_entity_types if str(value)})), tuple(sorted({str(value) for value in affected_entity_ids if str(value)})), str(parent_operation_uuid), dict(provenance or {}), bool(preview), "")
        context.validate()
        return context

    def complete(self):
        return self.transition("completed")

    def transition(self, status, *, reason="", now=None):
        status = str(status)
        if status not in STATUSES or status not in TRANSITIONS[self.status]:
            raise ValueError(f"Invalid operation status transition: {self.status} -> {status}")
        completed = now or datetime.now(timezone.utc).isoformat(timespec="microseconds")
        result = OperationContext(**{**self.__dict__, "status": status, "completed_at": completed, "reason": str(reason)})
        result.validate()
        return result

    def validate(self):
        for field, value in (("operation_uuid", self.operation_uuid), ("project_uuid", self.project_uuid), ("dataset_uuid", self.dataset_uuid)):
            self._uuid(value, field)
        if self.session_uuid:
            self._uuid(self.session_uuid, "session_uuid")
        if self.parent_operation_uuid:
            self._uuid(self.parent_operation_uuid, "parent_operation_uuid")
        if self.status not in STATUSES:
            raise ValueError("Unsupported operation status")
        if self.preview != (self.status == "preview"):
            raise ValueError("Preview flag and operation status disagree")

    @staticmethod
    def _uuid(value, field):
        try:
            UUID(str(value))
        except (TypeError, ValueError) as error:
            raise ValueError(f"Invalid {field}") from error


def validate_correlation(audit_records, collaboration_changes):
    """Return sidecar correlation findings without rewriting legacy records."""
    audit = {}
    collaboration = {}
    legacy = []
    for record in audit_records:
        operation_uuid = getattr(record, "operation_uuid", "")
        if not operation_uuid:
            legacy.append(f"audit:{record.id}"); continue
        audit.setdefault(operation_uuid, []).append(record)
    for change in collaboration_changes:
        operation_uuid = getattr(change, "operation_uuid", "") or getattr(change, "operation_id", "")
        if not operation_uuid:
            legacy.append("collaboration:unknown"); continue
        collaboration.setdefault(operation_uuid, []).append(change)
    missing = sorted(set(audit) ^ set(collaboration))
    duplicates = sorted(operation_uuid for operation_uuid, records in {**audit, **collaboration}.items() if len(records) != 1)
    mismatched = []
    identity_mismatches = []
    orphan_children = []
    for operation_uuid in sorted(set(audit) & set(collaboration)):
        record, change = audit[operation_uuid][0], collaboration[operation_uuid][0]
        if getattr(record, "status", "completed") != getattr(change, "status", "completed"):
            mismatched.append(operation_uuid)
        if getattr(record, "project_uuid", "") and getattr(change, "project_uuid", "") and (record.project_uuid != change.project_uuid or record.dataset_uuid != change.dataset_uuid):
            identity_mismatches.append(operation_uuid)
    known_operations = set(audit) | set(collaboration)
    for operation_uuid, records in {**audit, **collaboration}.items():
        for record in records:
            parent = getattr(record, "parent_operation_uuid", "")
            if parent and parent not in known_operations:
                orphan_children.append(operation_uuid)
    return {"complete": not missing and not duplicates and not mismatched and not identity_mismatches and not orphan_children, "missing_counterparts": tuple(missing), "duplicate_counterparts": tuple(duplicates), "mismatched_status": tuple(mismatched), "mismatched_identity": tuple(identity_mismatches), "orphan_child_operations": tuple(sorted(set(orphan_children))), "legacy_uncorrelated": tuple(sorted(legacy))}