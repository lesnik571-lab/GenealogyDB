import sqlite3
import inspect
from pathlib import Path

import pytest

from batch_operations_service import BatchOperation, BatchOperationsService
from repository.person_repository import PersonRepository
from undo_manager import AppliedDeltaCommand, UndoManager
from viewer import GenealogyViewer


def build_repository(tmp_path, name="batch.db"):
    database = tmp_path / name
    connection = sqlite3.connect(database)
    connection.executescript(Path("schema.sql").read_text(encoding="utf-8"))
    connection.close()
    return PersonRepository(str(database))


def add_person(repository, number, **values):
    return repository.create_person(
        {
            "gedcom_id": f"I{number}",
            "first_name": f"Person{number}",
            "last_name": "Batch",
            **values,
        }
    )


def test_preview_is_read_only_and_lists_before_after_values(tmp_path):
    repository = build_repository(tmp_path, "preview.db")
    try:
        person_id = add_person(repository, 1, occupation="Teacher")
        service = BatchOperationsService(repository)

        preview = service.preview([person_id], BatchOperation("edit_occupation", value="Engineer"))

        assert preview.affected_records == 1
        assert [(change.field, change.before, change.after) for change in preview.changes] == [
            ("occupation", "Teacher", "Engineer")
        ]
        assert repository.get_person_record(person_id)["occupation"] == "Teacher"
    finally:
        repository.close()


@pytest.mark.parametrize(
    ("operation", "initial", "expected_field", "expected"),
    [
        (BatchOperation("edit_notes", value="New note"), {"note": "Old"}, "note", "New note"),
        (BatchOperation("add_tag", value="Research"), {"note": "Memo"}, "note", "Memo\nTags: Research"),
        (BatchOperation("remove_tag", value="Old"), {"note": "Memo\nTags: Old, Keep"}, "note", "Memo\nTags: Keep"),
        (BatchOperation("replace_text", value="old", replacement="new"), {"occupation": "old work"}, "occupation", "new work"),
        (BatchOperation("normalize_dates"), {"birth_date": " 1 jan  1900 "}, "birth_date", "1 JAN 1900"),
        (BatchOperation("normalize_places"), {"birth_place": " Moscow  ,  Russia "}, "birth_place", "Moscow, Russia"),
        (BatchOperation("merge_duplicate_values"), {"occupation": "Doctor; doctor; Writer"}, "occupation", "Doctor; Writer"),
    ],
)
def test_person_batch_operations_apply_expected_values(tmp_path, operation, initial, expected_field, expected):
    repository = build_repository(tmp_path, f"{operation.kind}.db")
    try:
        person_id = add_person(repository, 1, **initial)
        service = BatchOperationsService(repository)
        preview = service.preview([person_id], operation)

        result = service.execute(preview)

        assert result.changed_fields >= 1
        assert repository.get_person_record(person_id)[expected_field] == expected
    finally:
        repository.close()


def test_add_event_and_event_normalization_are_supported(tmp_path):
    repository = build_repository(tmp_path, "events.db")
    try:
        first = add_person(repository, 1)
        second = add_person(repository, 2)
        service = BatchOperationsService(repository)
        add_preview = service.preview(
            [first, second],
            BatchOperation(
                "add_event",
                event_type="residence",
                event_date="1920",
                event_place="Paris",
                event_notes="Moved",
            ),
        )

        result = service.execute(add_preview)

        assert result.changed_records == 2
        assert repository.list_person_events(first)[0]["place"] == "Paris"
        assert repository.list_person_events(second)[0]["description"] == "Moved"

        event_id = repository.create_person_event(
            {
                "person_id": first,
                "event_type": "custom",
                "date": " 2 feb  1930 ",
                "place": " New York  , USA ",
                "description": "alpha; alpha; beta",
            }
        )
        date_preview = service.preview([first], BatchOperation("normalize_dates"))
        service.execute(date_preview)
        place_preview = service.preview([first], BatchOperation("normalize_places"))
        service.execute(place_preview)
        duplicate_preview = service.preview([first], BatchOperation("merge_duplicate_values"))
        service.execute(duplicate_preview)

        event = repository.get_person_event(event_id)
        assert event["date"] == "2 FEB 1930"
        assert event["place"] == "New York, USA"
        assert event["description"] == "alpha; beta"
    finally:
        repository.close()


def test_execution_rolls_back_every_record_on_mid_batch_error(tmp_path, monkeypatch):
    repository = build_repository(tmp_path, "rollback.db")
    try:
        first = add_person(repository, 1)
        second = add_person(repository, 2)
        service = BatchOperationsService(repository)
        preview = service.preview([first, second], BatchOperation("add_event", event_type="custom"))
        original_create = repository.create_person_event
        calls = 0

        def fail_second(data):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("forced failure")
            return original_create(data)

        monkeypatch.setattr(repository, "create_person_event", fail_second)

        with pytest.raises(RuntimeError, match="forced failure"):
            service.execute(preview)

        assert repository.list_person_events(first) == []
        assert repository.list_person_events(second) == []
    finally:
        repository.close()


def test_stale_preview_aborts_without_applying_other_people(tmp_path):
    repository = build_repository(tmp_path, "stale.db")
    try:
        first = add_person(repository, 1, occupation="Old")
        second = add_person(repository, 2, occupation="Old")
        service = BatchOperationsService(repository)
        preview = service.preview(
            [first, second], BatchOperation("edit_occupation", value="New")
        )
        repository.update_person_fields(second, {"occupation": "External"})

        with pytest.raises(RuntimeError, match="предварительного просмотра"):
            service.execute(preview)

        assert repository.get_person_record(first)["occupation"] == "Old"
        assert repository.get_person_record(second)["occupation"] == "External"
    finally:
        repository.close()


def test_worker_delta_can_be_registered_for_undo_and_redo(tmp_path):
    database_name = "undo_batch.db"
    ui_repository = build_repository(tmp_path, database_name)
    person_id = add_person(ui_repository, 1, occupation="Teacher")
    worker_repository = PersonRepository(ui_repository.db_name)
    try:
        service = BatchOperationsService(worker_repository)
        preview = service.preview([person_id], BatchOperation("edit_occupation", value="Engineer"))
        result = service.execute(preview)
        worker_repository.close()

        manager = UndoManager()
        manager.record_applied(
            AppliedDeltaCommand("Пакетные операции", ui_repository, result.delta, result)
        )

        assert ui_repository.get_person_record(person_id)["occupation"] == "Engineer"
        assert manager.undo() is True
        assert ui_repository.get_person_record(person_id)["occupation"] == "Teacher"
        assert manager.redo() is True
        assert ui_repository.get_person_record(person_id)["occupation"] == "Engineer"
    finally:
        try:
            worker_repository.close()
        except sqlite3.ProgrammingError:
            pass
        ui_repository.close()


def test_viewer_batch_button_preview_and_execution_are_wired():
    widget_source = inspect.getsource(GenealogyViewer._create_widgets)
    dialog_source = inspect.getsource(GenealogyViewer.open_batch_operations)
    preview_source = inspect.getsource(GenealogyViewer._preview_batch_operations)
    execute_source = inspect.getsource(GenealogyViewer._execute_batch_operations)
    complete_source = inspect.getsource(GenealogyViewer._complete_batch_operations)

    assert 'text="Пакетные операции"' in widget_source
    assert "command=self.open_batch_operations" in widget_source
    assert 'selectmode="extended"' in dialog_source
    assert 'text="Предварительный просмотр"' in dialog_source
    assert 'state="disabled"' in dialog_source
    assert "_submit_repository_task" in preview_source
    assert "BatchOperationsService(repository).preview" in preview_source
    assert "_submit_repository_task" in execute_source
    assert "BatchOperationsService(repository).execute" in execute_source
    assert "record_applied" in complete_source
    assert "AppliedDeltaCommand" in complete_source


def test_viewer_preview_table_contains_record_field_before_and_after_columns():
    source = inspect.getsource(GenealogyViewer.open_batch_operations)

    assert '("person", "record", "field", "before", "after")' in source
    assert '("before", "До", 190)' in source
    assert '("after", "После", 190)' in source
