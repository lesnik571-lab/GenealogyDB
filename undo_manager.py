from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class TableDelta:
    """Before-and-after row state for one database table."""
    before_rows: tuple[tuple[Any, ...], ...]
    after_rows: tuple[tuple[Any, ...], ...]


class RepositoryDeltaCommand:
    """Store only rows changed by one repository-backed write operation."""

    def __init__(self, name: str, repository: Any, operation: Callable[[], Any]) -> None:
        self.name = name
        self.repository = repository
        self._operation: Callable[[], Any] | None = operation
        self.delta: dict[str, TableDelta] = {}
        self.result: Any = None

    @property
    def has_effect(self) -> bool:
        return bool(self.delta)

    @property
    def changed_row_count(self) -> int:
        return sum(
            max(len(change.before_rows), len(change.after_rows))
            for change in self.delta.values()
        )

    def execute(self) -> Any:
        if self._operation is None:
            raise RuntimeError("Command has already been executed.")
        before = self.repository.capture_command_state()
        self.result = self._operation()
        after = self.repository.capture_command_state()
        self.delta = self._build_delta(before, after)
        self._operation = None
        return self.result

    def undo(self) -> None:
        self.repository.apply_command_delta(self.delta, use_before=True)

    def redo(self) -> None:
        self.repository.apply_command_delta(self.delta, use_before=False)

    @staticmethod
    def _build_delta(
        before: dict[str, tuple[tuple[Any, ...], ...]],
        after: dict[str, tuple[tuple[Any, ...], ...]],
    ) -> dict[str, TableDelta]:
        delta = {}
        for table in sorted(set(before) | set(after)):
            before_rows = before.get(table, ())
            after_rows = after.get(table, ())
            if table == "family_children":
                before_only = tuple((Counter(before_rows) - Counter(after_rows)).elements())
                after_only = tuple((Counter(after_rows) - Counter(before_rows)).elements())
            else:
                before_by_id = {row[0]: row for row in before_rows}
                after_by_id = {row[0]: row for row in after_rows}
                changed_ids = sorted(
                    key for key in set(before_by_id) | set(after_by_id)
                    if before_by_id.get(key) != after_by_id.get(key)
                )
                before_only = tuple(before_by_id[key] for key in changed_ids if key in before_by_id)
                after_only = tuple(after_by_id[key] for key in changed_ids if key in after_by_id)
            if before_only or after_only:
                delta[table] = TableDelta(before_only, after_only)
        return delta


class AddPersonCommand(RepositoryDeltaCommand):
    """Undoable command for creating a person."""
    def __init__(self, repository: Any, data: dict[str, Any]) -> None:
        super().__init__("Add person", repository, lambda: repository.create_person(data))


class EditPersonCommand(RepositoryDeltaCommand):
    """Undoable command for editing a person."""
    def __init__(self, repository: Any, person_id: int, data: dict[str, Any]) -> None:
        super().__init__("Edit person", repository, lambda: repository.update_person(person_id, data))


class DeletePersonCommand(RepositoryDeltaCommand):
    """Undoable command for deleting a person."""
    def __init__(
        self,
        repository: Any,
        person_id: int,
        *,
        on_delete: Callable[[], None] | None = None,
        on_restore: Callable[[], None] | None = None,
    ) -> None:
        super().__init__("Delete person", repository, lambda: repository.delete_person(person_id))
        self._on_delete = on_delete
        self._on_restore = on_restore

    def execute(self) -> Any:
        result = super().execute()
        if self.has_effect and self._on_delete is not None:
            self._on_delete()
        return result

    def undo(self) -> None:
        super().undo()
        if self._on_restore is not None:
            self._on_restore()

    def redo(self) -> None:
        super().redo()
        if self._on_delete is not None:
            self._on_delete()


class RelationshipEditCommand(RepositoryDeltaCommand):
    """Undoable command for relationship changes."""
    def __init__(self, repository: Any, operation: Callable[[], Any]) -> None:
        super().__init__("Relationship edit", repository, operation)


class MergePeopleCommand(RepositoryDeltaCommand):
    """Undoable command for merging duplicate people."""
    def __init__(self, repository: Any, target_person_id: int, source_person_id: int) -> None:
        super().__init__(
            "Merge people",
            repository,
            lambda: repository.merge_people(target_person_id, source_person_id),
        )


class RecoveryUpdateCommand(RepositoryDeltaCommand):
    """Undoable command for recovery wizard updates."""
    def __init__(self, repository: Any, operation: Callable[[], Any]) -> None:
        super().__init__("Recovery Wizard change", repository, operation)


class AppliedDeltaCommand:
    """Undo an operation that was already committed by a worker repository."""

    def __init__(
        self,
        name: str,
        repository: Any,
        delta: dict[str, TableDelta],
        result: Any = None,
        *,
        on_undo: Callable[[], None] | None = None,
        on_redo: Callable[[], None] | None = None,
    ) -> None:
        self.name = name
        self.repository = repository
        self.delta = delta
        self.result = result
        self._on_undo = on_undo
        self._on_redo = on_redo

    @property
    def has_effect(self) -> bool:
        return bool(self.delta)

    def undo(self) -> None:
        self.repository.apply_command_delta(self.delta, use_before=True)
        if self._on_undo is not None:
            self._on_undo()

    def redo(self) -> None:
        self.repository.apply_command_delta(self.delta, use_before=False)
        if self._on_redo is not None:
            self._on_redo()


class UndoManager:
    """Execute commands and maintain bounded undo and redo stacks."""
    def __init__(self, on_change: Callable[[], None] | None = None) -> None:
        self._undo_stack: list[RepositoryDeltaCommand] = []
        self._redo_stack: list[RepositoryDeltaCommand] = []
        self._on_change = on_change

    @property
    def can_undo(self) -> bool:
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo_stack)

    @property
    def undo_name(self) -> str:
        return self._undo_stack[-1].name if self._undo_stack else ""

    @property
    def redo_name(self) -> str:
        return self._redo_stack[-1].name if self._redo_stack else ""

    def execute(self, command: RepositoryDeltaCommand) -> Any:
        result = command.execute()
        if command.has_effect:
            self._undo_stack.append(command)
            self._redo_stack.clear()
            self._notify()
        return result

    def record_applied(self, command: AppliedDeltaCommand) -> Any:
        if command.has_effect:
            self._undo_stack.append(command)
            self._redo_stack.clear()
            self._notify()
        return command.result

    def undo(self) -> bool:
        if not self._undo_stack:
            return False
        command = self._undo_stack.pop()
        command.undo()
        self._redo_stack.append(command)
        self._notify()
        return True

    def redo(self) -> bool:
        if not self._redo_stack:
            return False
        command = self._redo_stack.pop()
        command.redo()
        self._undo_stack.append(command)
        self._notify()
        return True

    def _notify(self) -> None:
        if self._on_change is not None:
            self._on_change()
