"""Preview and atomically apply bulk changes to selected people."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from logging_service import get_logger
from repository.person_repository import PersonRepository
from undo_manager import RepositoryDeltaCommand, TableDelta


OPERATION_LABELS = {
    "edit_occupation": "Изменить занятие",
    "edit_notes": "Изменить заметки",
    "add_tag": "Добавить тег",
    "remove_tag": "Удалить тег",
    "add_event": "Добавить событие",
    "replace_text": "Заменить текст",
    "normalize_dates": "Нормализовать даты",
    "normalize_places": "Нормализовать места",
    "merge_duplicate_values": "Объединить повторяющиеся значения",
}


@dataclass(frozen=True)
class BatchOperation:
    kind: str
    value: str = ""
    replacement: str = ""
    event_type: str = "custom"
    event_date: str = ""
    event_place: str = ""
    event_notes: str = ""


@dataclass(frozen=True)
class BatchChange:
    person_id: int
    person_name: str
    record_type: str
    record_id: int | None
    field: str
    before: str
    after: str


@dataclass(frozen=True)
class BatchPreview:
    operation: BatchOperation
    person_ids: tuple[int, ...]
    changes: tuple[BatchChange, ...]

    @property
    def affected_records(self) -> int:
        return len({(change.record_type, change.record_id or change.person_id) for change in self.changes})


@dataclass(frozen=True)
class BatchExecutionResult:
    changed_records: int
    changed_fields: int
    delta: dict[str, TableDelta]


class BatchOperationsService:
    """Build read-only plans and execute them in one repository transaction."""

    PERSON_TEXT_FIELDS = ("occupation", "note", "birth_place", "death_place")

    def __init__(self, repository: PersonRepository) -> None:
        self.repository = repository
        self.logger = get_logger("batch_operations")

    def preview(self, person_ids, operation: BatchOperation) -> BatchPreview:
        if operation.kind not in OPERATION_LABELS:
            raise ValueError("Неизвестная пакетная операция")
        selected_ids = tuple(dict.fromkeys(int(person_id) for person_id in person_ids))
        if not selected_ids:
            raise ValueError("Выберите хотя бы одного человека")
        changes = []
        for person_id in selected_ids:
            person = self.repository.get_person_record(person_id)
            if person is None:
                raise ValueError(f"Человек ID {person_id} не найден")
            name = " ".join(value for value in (person["first_name"], person["last_name"]) if value) or "Без имени"
            events = self.repository.list_person_events(person_id)
            changes.extend(self._person_changes(person, name, operation))
            changes.extend(self._event_changes(person_id, name, events, operation))
        return BatchPreview(operation, selected_ids, tuple(changes))

    def execute(
        self,
        preview: BatchPreview,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> BatchExecutionResult:
        before_state = self.repository.capture_command_state()
        total = max(1, len(preview.person_ids))
        self.logger.info(
            "Batch operation started: operation=%s people=%s changes=%s",
            preview.operation.kind,
            len(preview.person_ids),
            len(preview.changes),
        )
        try:
            with self.repository.transaction():
                self._assert_preview_current(preview)
                for index, person_id in enumerate(preview.person_ids, start=1):
                    self._apply_person_changes(person_id, preview)
                    if preview.operation.kind == "add_event":
                        operation = preview.operation
                        self.repository.create_person_event({
                            "person_id": person_id,
                            "event_type": operation.event_type or "custom",
                            "date": operation.event_date,
                            "place": operation.event_place,
                            "description": operation.event_notes,
                        })
                    else:
                        self._apply_event_changes(person_id, preview)
                    if progress_callback:
                        progress_callback(OPERATION_LABELS[preview.operation.kind], index, total)
        except Exception:
            self.logger.exception("Batch operation rolled back: operation=%s", preview.operation.kind)
            raise
        after_state = self.repository.capture_command_state()
        delta = RepositoryDeltaCommand._build_delta(before_state, after_state)
        result = BatchExecutionResult(preview.affected_records, len(preview.changes), delta)
        self.logger.info(
            "Batch operation completed: operation=%s records=%s fields=%s",
            preview.operation.kind,
            result.changed_records,
            result.changed_fields,
        )
        return result

    def _person_changes(self, person, name, operation):
        person_id = int(person["id"])
        if operation.kind == "edit_occupation":
            return self._change(person_id, name, "person", person_id, "occupation", person["occupation"], operation.value)
        if operation.kind == "edit_notes":
            return self._change(person_id, name, "person", person_id, "note", person["note"], operation.value)
        if operation.kind in {"add_tag", "remove_tag"}:
            after = self._update_tags(person["note"], operation.value, operation.kind == "add_tag")
            return self._change(person_id, name, "person", person_id, "note", person["note"], after)
        if operation.kind == "replace_text":
            changes = []
            for field in self.PERSON_TEXT_FIELDS:
                after = person[field].replace(operation.value, operation.replacement)
                changes.extend(self._change(person_id, name, "person", person_id, field, person[field], after))
            return changes
        if operation.kind == "normalize_dates":
            changes = []
            for field in ("birth_date", "death_date"):
                changes.extend(self._change(person_id, name, "person", person_id, field, person[field], self._normalize_date(person[field])))
            return changes
        if operation.kind == "normalize_places":
            changes = []
            for field in ("birth_place", "death_place"):
                changes.extend(self._change(person_id, name, "person", person_id, field, person[field], self._normalize_place(person[field])))
            return changes
        if operation.kind == "merge_duplicate_values":
            changes = []
            for field in ("occupation", "note"):
                changes.extend(self._change(person_id, name, "person", person_id, field, person[field], self._deduplicate(person[field])))
            return changes
        if operation.kind == "add_event":
            summary = " | ".join((operation.event_type or "custom", operation.event_date, operation.event_place, operation.event_notes))
            return [BatchChange(person_id, name, "event", None, "event", "", summary)]
        return []

    def _event_changes(self, person_id, name, events, operation):
        changes = []
        for event in events:
            event_id = int(event["id"])
            if operation.kind == "replace_text":
                for field in ("place", "description"):
                    before = event[field]
                    after = before.replace(operation.value, operation.replacement)
                    changes.extend(self._change(person_id, name, "event", event_id, field, before, after))
            elif operation.kind == "normalize_dates":
                changes.extend(self._change(person_id, name, "event", event_id, "date", event["date"], self._normalize_date(event["date"])))
            elif operation.kind == "normalize_places":
                changes.extend(self._change(person_id, name, "event", event_id, "place", event["place"], self._normalize_place(event["place"])))
            elif operation.kind == "merge_duplicate_values":
                changes.extend(self._change(person_id, name, "event", event_id, "description", event["description"], self._deduplicate(event["description"])))
        return changes

    @staticmethod
    def _change(person_id, name, record_type, record_id, field, before, after):
        before = str(before or "")
        after = str(after or "")
        return [] if before == after else [BatchChange(person_id, name, record_type, record_id, field, before, after)]

    def _assert_preview_current(self, preview):
        for change in preview.changes:
            if change.field == "event" and change.record_id is None:
                continue
            if change.record_type == "person":
                record = self.repository.get_person_record(change.person_id)
            else:
                record = self.repository.get_person_event(change.record_id)
            if record is None or str(record.get(change.field, "") or "") != change.before:
                raise RuntimeError("Данные изменились после предварительного просмотра")

    def _apply_person_changes(self, person_id, preview):
        changes = {
            change.field: change.after for change in preview.changes
            if change.person_id == person_id and change.record_type == "person"
        }
        if changes:
            self.repository.update_person_fields(person_id, changes)

    def _apply_event_changes(self, person_id, preview):
        by_event = {}
        for change in preview.changes:
            if change.person_id == person_id and change.record_type == "event" and change.record_id is not None:
                by_event.setdefault(change.record_id, {})[change.field] = change.after
        for event_id, changes in by_event.items():
            event = self.repository.get_person_event(event_id)
            event.update(changes)
            self.repository.update_person_event(event_id, event)

    @staticmethod
    def _normalize_date(value):
        return re.sub(r"\s+", " ", str(value or "").strip()).upper()

    @staticmethod
    def _normalize_place(value):
        parts = [re.sub(r"\s+", " ", part.strip()) for part in str(value or "").split(",")]
        return ", ".join(part for part in parts if part)

    @staticmethod
    def _deduplicate(value):
        values = [part.strip() for part in str(value or "").split(";")]
        result = []
        seen = set()
        for item in values:
            key = item.casefold()
            if item and key not in seen:
                seen.add(key)
                result.append(item)
        return "; ".join(result)

    @staticmethod
    def _update_tags(note, tag, add):
        tag = str(tag or "").strip()
        if not tag:
            raise ValueError("Укажите тег")
        note = str(note or "")
        match = re.search(r"(?im)^Tags:\s*(.*)$", note)
        tags = [item.strip() for item in match.group(1).split(",") if item.strip()] if match else []
        if add and tag.casefold() not in {item.casefold() for item in tags}:
            tags.append(tag)
        if not add:
            tags = [item for item in tags if item.casefold() != tag.casefold()]
        tag_line = f"Tags: {', '.join(tags)}" if tags else ""
        if match:
            updated = f"{note[:match.start()]}{tag_line}{note[match.end():]}"
        else:
            updated = f"{note.rstrip()}\n{tag_line}" if note.strip() and tag_line else tag_line or note
        return updated.strip()