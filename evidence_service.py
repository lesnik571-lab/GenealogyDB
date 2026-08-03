"""Evidence management, diagnostics, export, and reversible batch execution."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from audit_service import AuditService
from repository.person_repository import PersonRepository
from source_service import CITATION_FIELDS, SOURCE_FIELDS, SourceService
from undo_manager import TableDelta


CONFIDENCE_LEVELS = ("Proven", "Strong", "Probable", "Weak", "Disputed", "Unknown")
PROOF_STATUSES = ("Unreviewed", "Supported", "Contradicted")
OPERATION_KINDS = (
    "create_source", "edit_source", "duplicate_source", "merge_sources",
    "attach_citation", "edit_citation", "detach_citation",
)
_METADATA_MARKER = "[EVIDENCE_META]"


@dataclass(frozen=True)
class EvidenceIssue:
    kind: str
    description: str
    source_ids: tuple[int, ...] = ()
    citation_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class EvidenceModel:
    sources: tuple[Mapping[str, Any], ...]
    citations: tuple[Mapping[str, Any], ...]
    usages: tuple[Mapping[str, Any], ...]
    issues: tuple[EvidenceIssue, ...]
    read_only: bool = False


@dataclass(frozen=True)
class EvidenceOperation:
    kind: str
    source_id: int = 0
    citation_id: int = 0
    target_type: str = ""
    target_id: str = ""
    source_ids: tuple[int, ...] = ()
    data: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidencePreview:
    operations: tuple[EvidenceOperation, ...]
    descriptions: tuple[str, ...]
    blockers: tuple[str, ...]
    source_fingerprint: tuple[Any, ...]

    @property
    def can_execute(self) -> bool:
        return not self.blockers


@dataclass(frozen=True)
class EvidenceExecutionResult:
    before_state: Mapping[str, tuple[tuple[Any, ...], ...]]
    after_state: Mapping[str, tuple[tuple[Any, ...], ...]]
    operations: tuple[EvidenceOperation, ...]
    descriptions: tuple[str, ...]


class EvidenceSafetyError(ValueError):
    """Raised when an evidence mutation is invalid or disabled."""


class EvidenceAppliedCommand:
    """Undo or redo an already-applied evidence batch without changing UndoManager."""

    def __init__(self, repository: PersonRepository, result: EvidenceExecutionResult) -> None:
        self.name = "Источники и доказательства"
        self.repository = repository
        self.result = result
        self.delta = {
            table: TableDelta(
                tuple(result.before_state.get(table, ())),
                tuple(result.after_state.get(table, ())),
            )
            for table in sorted(set(result.before_state) | set(result.after_state))
            if tuple(result.before_state.get(table, ())) != tuple(result.after_state.get(table, ()))
        }

    @property
    def has_effect(self) -> bool:
        return bool(self.delta)

    def undo(self) -> None:
        EvidenceService(self.repository)._restore_state(self.result.before_state)

    def redo(self) -> None:
        EvidenceService(self.repository)._restore_state(self.result.after_state)


class EvidenceService:
    """Manage evidence using the existing source and citation schema."""

    SOURCE_COLUMNS = (
        "id", "title", "author", "publication", "repository_name", "call_number",
        "source_url", "notes", "created_at", "updated_at",
    )
    CITATION_COLUMNS = (
        "id", "source_id", "target_type", "target_id", "page", "quality",
        "transcription", "comment", "created_at",
    )

    def __init__(self, repository: PersonRepository, *, read_only: bool = False) -> None:
        self.repository = repository
        self.sources = SourceService(repository)
        self.read_only = bool(read_only)

    def build_model(self) -> EvidenceModel:
        sources = tuple(dict(source) for source in self.sources.list_sources())
        citations = tuple(self._evidence_citation(item) for item in self.sources.list_citations())
        usages = tuple(self._usage(citation) for citation in citations)
        return EvidenceModel(sources, citations, usages, tuple(self._diagnose(sources, citations, usages)), self.read_only)

    def preview(self, operations) -> EvidencePreview:
        normalized = tuple(self._normalize_operation(operation) for operation in operations)
        if not normalized:
            raise ValueError("Не выбрана операция с доказательствами")
        model = self.build_model()
        source_ids = {int(source["id"]) for source in model.sources}
        citations = {int(citation["id"]): citation for citation in model.citations}
        blockers = []
        descriptions = []
        if self.read_only:
            blockers.append("В режиме диагностики изменения запрещены.")
        for operation in normalized:
            if operation.kind not in OPERATION_KINDS:
                blockers.append(f"Неподдерживаемая операция: {operation.kind}.")
                continue
            try:
                descriptions.append(self._validate_operation(operation, source_ids, citations, model))
            except (EvidenceSafetyError, ValueError, TypeError) as error:
                blockers.append(str(error))
        return EvidencePreview(
            normalized,
            tuple(descriptions),
            tuple(dict.fromkeys(blockers)),
            self._fingerprint(model),
        )

    def execute(self, preview: EvidencePreview) -> EvidenceExecutionResult:
        if self.read_only:
            raise EvidenceSafetyError("В режиме диагностики изменения запрещены.")
        if preview.blockers:
            raise EvidenceSafetyError("; ".join(preview.blockers))
        if self._fingerprint(self.build_model()) != preview.source_fingerprint:
            raise RuntimeError("Данные доказательств изменились после проверки")
        before_state = self._capture_state()
        with self.repository.transaction():
            for operation in preview.operations:
                self._execute_operation(operation)
        after_state = self._capture_state()
        result = EvidenceExecutionResult(
            before_state, after_state, preview.operations, preview.descriptions,
        )
        AuditService.for_database(self.repository.db_name).record_state_change(
            "evidence_change",
            before_state,
            after_state,
            description=" ".join(preview.descriptions),
            service="evidence_service",
            batch_id="batch" if len(preview.operations) > 1 else "",
        )
        return result

    def export_csv(self, destination_path) -> Path:
        destination = Path(destination_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        model = self.build_model()
        sources = {int(source["id"]): source for source in model.sources}
        with destination.open("w", encoding="utf-8-sig", newline="") as csv_file:
            fields = (
                *SOURCE_FIELDS, "citation_id", "target_type", "target_id", "target",
                "page", "confidence", "proof_status", "media_reference", "transcription", "comment",
            )
            writer = csv.DictWriter(csv_file, fieldnames=fields)
            writer.writeheader()
            for usage in model.usages:
                source = sources[int(usage["source_id"])]
                writer.writerow({
                    **{field: source.get(field, "") for field in SOURCE_FIELDS},
                    **{field: usage.get(field, "") for field in fields if field not in SOURCE_FIELDS},
                })
        return destination

    def export_json(self, destination_path) -> Path:
        destination = Path(destination_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        model = self.build_model()
        payload = {
            "sources": list(model.sources),
            "citations": list(model.citations),
            "usages": list(model.usages),
            "issues": [issue.__dict__ for issue in model.issues],
        }
        destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return destination

    def _validate_operation(self, operation, source_ids, citations, model) -> str:
        if operation.kind == "create_source":
            self.sources._source_payload(operation.data)
            return f"Создать источник «{operation.data.get('title', '')}»."
        if operation.kind in {"edit_source", "duplicate_source"}:
            self._require_source(operation.source_id, source_ids)
            if operation.kind == "edit_source":
                self.sources._source_payload(operation.data)
                return f"Изменить источник ID {operation.source_id}."
            return f"Дублировать источник ID {operation.source_id}."
        if operation.kind == "merge_sources":
            self._require_source(operation.source_id, source_ids)
            duplicate_ids = tuple(dict.fromkeys(int(value) for value in operation.source_ids))
            if not duplicate_ids or operation.source_id in duplicate_ids:
                raise EvidenceSafetyError("Выберите отдельные источники для объединения.")
            for source_id in duplicate_ids:
                self._require_source(source_id, source_ids)
            return f"Объединить источники {', '.join(map(str, duplicate_ids))} с ID {operation.source_id}."
        if operation.kind == "attach_citation":
            self._require_source(operation.source_id, source_ids)
            details = self._citation_payload(operation.data)
            self.sources.resolve_target(operation.target_type, operation.target_id)
            signature = self._citation_signature({
                "source_id": operation.source_id,
                "target_type": operation.target_type,
                "target_id": str(operation.target_id),
                **details,
            })
            if any(self._citation_signature(citation) == signature for citation in model.citations):
                raise EvidenceSafetyError("Такая цитата уже существует.")
            return f"Прикрепить цитату к {operation.target_type} ID {operation.target_id}."
        citation = citations.get(operation.citation_id)
        if citation is None:
            raise EvidenceSafetyError(f"Цитата ID {operation.citation_id} не найдена.")
        if operation.kind == "edit_citation":
            details = self._citation_payload(operation.data)
            target_type = operation.target_type or citation["target_type"]
            target_id = operation.target_id or citation["target_id"]
            self.sources.resolve_target(target_type, target_id)
            signature = self._citation_signature({
                **citation, **details, "target_type": target_type, "target_id": str(target_id),
            })
            if any(
                int(other["id"]) != operation.citation_id
                and self._citation_signature(other) == signature
                for other in model.citations
            ):
                raise EvidenceSafetyError("Такая цитата уже существует.")
            return f"Изменить цитату ID {operation.citation_id}."
        return f"Открепить цитату ID {operation.citation_id}."

    def _execute_operation(self, operation: EvidenceOperation) -> None:
        if operation.kind == "create_source":
            self.sources.create_source(operation.data)
            return
        if operation.kind == "edit_source":
            self.sources.update_source(operation.source_id, operation.data)
            return
        if operation.kind == "duplicate_source":
            source = self.sources.get_source(operation.source_id)
            data = {field: source.get(field, "") for field in SOURCE_FIELDS}
            data.update(operation.data)
            data["title"] = data.get("title") or f"{source['title']} (копия)"
            self.sources.create_source(data)
            return
        if operation.kind == "merge_sources":
            self._merge_sources(operation.source_id, operation.source_ids)
            return
        if operation.kind == "attach_citation":
            self.sources.create_citation(
                operation.source_id, operation.target_type, operation.target_id,
                **self._citation_payload(operation.data),
            )
            return
        citation = next(
            item for item in self.sources.list_citations()
            if int(item["id"]) == operation.citation_id
        )
        if operation.kind == "edit_citation":
            self.sources.update_citation(
                operation.citation_id,
                operation.source_id or citation["source_id"],
                operation.target_type or citation["target_type"],
                operation.target_id or citation["target_id"],
                **self._citation_payload(operation.data),
            )
            return
        self.sources.delete_citation(operation.citation_id)

    def _merge_sources(self, target_id: int, duplicate_ids) -> None:
        target = self.sources.get_source(target_id)
        merged = {field: target.get(field, "") for field in SOURCE_FIELDS}
        existing = {
            self._citation_signature(self._evidence_citation(citation))
            for citation in self.sources.list_citations(target_id)
        }
        for duplicate_id in dict.fromkeys(int(value) for value in duplicate_ids):
            duplicate = self.sources.get_source(duplicate_id)
            for field in SOURCE_FIELDS:
                if not merged[field] and duplicate.get(field):
                    merged[field] = duplicate[field]
            for citation in self.sources.list_citations(duplicate_id):
                evidence = self._evidence_citation(citation)
                signature = self._citation_signature({**evidence, "source_id": target_id})
                if signature in existing:
                    self.sources.delete_citation(citation["id"])
                    continue
                self.sources.update_citation(
                    citation["id"], target_id, citation["target_type"], citation["target_id"],
                    **{field: citation[field] for field in CITATION_FIELDS},
                )
                existing.add(signature)
            self.sources.delete_source(duplicate_id)
        self.sources.update_source(target_id, merged)

    def _diagnose(self, sources, citations, usages):
        source_groups = {}
        for source in sources:
            key = tuple(str(source.get(field) or "").strip().casefold() for field in (
                "title", "author", "publication", "repository", "call_number",
            ))
            source_groups.setdefault(key, []).append(int(source["id"]))
            if not str(source.get("repository") or "").strip():
                yield EvidenceIssue(
                    "missing_repository", f"У источника «{source['title']}» не указан репозиторий.",
                    (int(source["id"]),),
                )
        for ids in source_groups.values():
            if len(ids) > 1:
                yield EvidenceIssue("duplicate_source", "Возможные дубликаты источников.", tuple(ids))
        citation_groups = {}
        for citation in citations:
            citation_groups.setdefault(self._citation_signature(citation), []).append(int(citation["id"]))
        for ids in citation_groups.values():
            if len(ids) > 1:
                yield EvidenceIssue("duplicate_citation", "Повторяющиеся цитаты.", citation_ids=tuple(ids))
        for usage in usages:
            citation_id = int(usage["id"])
            if usage.get("broken_target"):
                yield EvidenceIssue(
                    "orphan_citation", f"Цитата ID {citation_id} ссылается на отсутствующий объект.",
                    (int(usage["source_id"]),), (citation_id,),
                )
            if usage.get("broken_media"):
                yield EvidenceIssue(
                    "broken_media", f"Цитата ID {citation_id} содержит недоступную медиассылку.",
                    (int(usage["source_id"]),), (citation_id,),
                )

    def _usage(self, citation):
        try:
            target = self.sources.resolve_target(citation["target_type"], citation["target_id"])
            broken_target = False
        except (ValueError, TypeError):
            target = {"target_label": "Недоступный объект", "linked_person_id": None}
            broken_target = True
        media_reference = citation.get("media_reference", "")
        return {
            **citation,
            **target,
            "target": target["target_label"],
            "broken_target": broken_target,
            "broken_media": bool(media_reference) and not self._media_exists(media_reference, target.get("linked_person_id")),
        }

    def _media_exists(self, reference, person_id) -> bool:
        if person_id is None:
            return False
        records = self.repository.list_person_media(person_id)
        reference = str(reference).strip()
        for media in records:
            if reference in {str(media.get("id", "")), str(media.get("file_path", ""))}:
                return bool(media.get("file_path")) and Path(media["file_path"]).expanduser().exists()
        return False

    def _citation_payload(self, data):
        confidence = str(data.get("confidence") or data.get("quality") or "Unknown").strip()
        if confidence not in CONFIDENCE_LEVELS:
            raise ValueError("Недопустимый уровень достоверности")
        proof_status = str(data.get("proof_status") or "Unreviewed").strip()
        if proof_status not in PROOF_STATUSES:
            raise ValueError("Недопустимый статус доказательства")
        comment = self._encode_comment(
            str(data.get("comment") or "").strip(),
            proof_status,
            str(data.get("media_reference") or "").strip(),
        )
        return {
            "page": str(data.get("page") or "").strip(),
            "quality": confidence,
            "transcription": str(data.get("transcription") or "").strip(),
            "comment": comment,
        }

    @staticmethod
    def _encode_comment(comment, proof_status, media_reference):
        metadata = json.dumps(
            {"proof_status": proof_status, "media_reference": media_reference},
            ensure_ascii=False, separators=(",", ":"),
        )
        separator = "\n" if comment else ""
        return f"{comment}{separator}{_METADATA_MARKER}{metadata}"

    @staticmethod
    def _decode_comment(value):
        comment = str(value or "")
        if _METADATA_MARKER not in comment:
            return comment, "Unreviewed", ""
        plain, encoded = comment.rsplit(_METADATA_MARKER, 1)
        try:
            metadata = json.loads(encoded)
        except (TypeError, ValueError, json.JSONDecodeError):
            return comment, "Unreviewed", ""
        return plain.rstrip(), str(metadata.get("proof_status") or "Unreviewed"), str(metadata.get("media_reference") or "")

    def _evidence_citation(self, citation):
        comment, proof_status, media_reference = self._decode_comment(citation.get("comment"))
        confidence = citation.get("quality") if citation.get("quality") in CONFIDENCE_LEVELS else "Unknown"
        return {
            **citation,
            "confidence": confidence,
            "proof_status": proof_status,
            "media_reference": media_reference,
            "comment": comment,
        }

    @staticmethod
    def _citation_signature(citation):
        return tuple(str(citation.get(field) or "").strip().casefold() for field in (
            "source_id", "target_type", "target_id", "page", "transcription",
        ))

    @staticmethod
    def _require_source(source_id, source_ids):
        if int(source_id or 0) not in source_ids:
            raise EvidenceSafetyError(f"Источник ID {source_id} не найден.")

    @staticmethod
    def _normalize_operation(operation):
        if isinstance(operation, EvidenceOperation):
            return operation
        if isinstance(operation, Mapping):
            return EvidenceOperation(**operation)
        raise TypeError("Операция должна быть EvidenceOperation или mapping")

    @staticmethod
    def _fingerprint(model):
        source_rows = tuple(
            tuple(str(source.get(field) or "") for field in ("id", *SOURCE_FIELDS, "updated_at"))
            for source in model.sources
        )
        citation_rows = tuple(
            tuple(str(citation.get(field) or "") for field in (
                "id", "source_id", "target_type", "target_id", "page", "quality",
                "transcription", "comment", "created_at",
            ))
            for citation in model.citations
        )
        return source_rows, citation_rows

    def _capture_state(self):
        source_rows = tuple(
            tuple(row)
            for row in self.repository.conn.execute(
                f"SELECT {', '.join(self.SOURCE_COLUMNS)} FROM sources ORDER BY id"
            ).fetchall()
        )
        citation_rows = tuple(
            tuple(row)
            for row in self.repository.conn.execute(
                f"SELECT {', '.join(self.CITATION_COLUMNS)} FROM citations ORDER BY id"
            ).fetchall()
        )
        return {"sources": source_rows, "citations": citation_rows}

    def _restore_state(self, state) -> None:
        with self.repository.transaction():
            self.repository.conn.execute("DELETE FROM citations")
            self.repository.conn.execute("DELETE FROM sources")
            for table, columns in (
                ("sources", self.SOURCE_COLUMNS), ("citations", self.CITATION_COLUMNS),
            ):
                rows = state.get(table, ())
                if rows:
                    placeholders = ", ".join("?" for _column in columns)
                    self.repository.conn.executemany(
                        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                        rows,
                    )
