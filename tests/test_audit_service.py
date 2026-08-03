import inspect
import json
import sqlite3
from pathlib import Path

from audit_service import AUDIT_OPERATION_TYPES, AuditService
from batch_operations_service import BatchOperation, BatchOperationsService
from merge_service import MergeService
from repository.person_repository import PersonRepository
from undo_manager import AddPersonCommand, TableDelta, UndoManager
from viewer import GenealogyViewer


def build_repository(tmp_path, name):
    database = tmp_path / name
    connection = sqlite3.connect(database)
    connection.executescript(Path("schema.sql").read_text(encoding="utf-8"))
    connection.close()
    return PersonRepository(str(database))


def test_history_creation_and_snapshot_correctness(tmp_path):
    service = AuditService(tmp_path / "audit.sqlite3")
    delta = {
        "people": TableDelta(
            ((7, "I7", "Before", "Person"),),
            ((7, "I7", "After", "Person"),),
        ),
        "person_events": TableDelta((), ((2, 7, "birth"),)),
    }

    record = service.record_delta(
        "person_edit",
        delta,
        description="Изменена карточка человека",
        service="viewer",
    )

    assert record.database_id == "7"
    assert record.gedcom_id == "I7"
    assert record.affected_tables == ("people", "person_events")
    assert record.before_snapshot["people"] == [[7, "I7", "Before", "Person"]]
    assert record.after_snapshot["people"] == [[7, "I7", "After", "Person"]]
    assert record.after_snapshot["person_events"] == [[2, 7, "birth"]]
    assert record.description == "Изменена карточка человека"


def test_filtering_and_stable_sorting(tmp_path):
    service = AuditService(tmp_path / "audit.sqlite3")
    service.record(
        "merge", database_id=1, gedcom_id="I1", affected_tables=("people",),
        description="Merge", service="merge_service", batch_id="batch-a",
        timestamp="2026-08-01T12:00:00+00:00",
    )
    service.record(
        "batch_operations", database_id="1,2", gedcom_id="I1,I2",
        affected_tables=("people",), description="Batch",
        service="batch_operations_service", batch_id="batch-b",
        timestamp="2026-08-02T12:00:00+00:00",
    )
    service.record(
        "undo", database_id=1, gedcom_id="I1", affected_tables=("people",),
        description="Undo", service="undo_manager",
        timestamp="2026-08-03T12:00:00+00:00",
    )

    assert [item.operation_type for item in service.list_records()] == [
        "undo", "batch_operations", "merge"
    ]
    assert [item.operation_type for item in service.list_records(sort_order="asc")] == [
        "merge", "batch_operations", "undo"
    ]
    assert [item.operation_type for item in service.list_records(person="I1")] == [
        "undo", "batch_operations", "merge"
    ]
    assert [item.operation_type for item in service.list_records(operation="merge")] == ["merge"]
    assert [item.operation_type for item in service.list_records(service="merge_service")] == ["merge"]
    assert [item.operation_type for item in service.list_records(batch_id="batch-b")] == ["batch_operations"]
    assert [item.operation_type for item in service.list_records(
        date_from="2026-08-02", date_to="2026-08-02"
    )] == ["batch_operations"]


def test_csv_and_json_export_include_complete_records(tmp_path):
    service = AuditService(tmp_path / "audit.sqlite3")
    service.record(
        "redo", database_id=3, gedcom_id="I3", affected_tables=("people",),
        before_snapshot={"people": [[3, "I3", "Before"]]},
        after_snapshot={"people": [[3, "I3", "After"]]},
        description="Повторено изменение", service="undo_manager", batch_id="redo-1",
    )
    records = service.list_records()

    json_path = service.export_json(records, tmp_path / "audit.json")
    csv_path = service.export_csv(records, tmp_path / "audit.csv")

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload[0]["operation_type"] == "redo"
    assert payload[0]["before_snapshot"]["people"][0][2] == "Before"
    csv_text = csv_path.read_text(encoding="utf-8-sig")
    assert "operation_type" in csv_text
    assert "Повторено изменение" in csv_text


def test_merge_logging_contains_surviving_identity_and_complete_delta(tmp_path):
    repository = build_repository(tmp_path, "merge.db")
    try:
        primary_id = repository.create_person({"gedcom_id": "I1", "first_name": "Primary", "last_name": "One"})
        duplicate_id = repository.create_person({"gedcom_id": "I2", "first_name": "Duplicate", "last_name": "Two"})

        MergeService(repository, tmp_path / "backups").execute(
            MergeService(repository, tmp_path / "backups").plan_merge(primary_id, duplicate_id)
        )

        records = AuditService.for_database(repository.db_name).list_records(operation="merge")
        assert len(records) == 1
        record = records[0]
        assert record.database_id == str(primary_id)
        assert record.gedcom_id == "I1"
        assert "people" in record.affected_tables
        assert any(row[0] == duplicate_id for row in record.before_snapshot["people"])
        assert all(row[0] != duplicate_id for row in record.after_snapshot["people"])
    finally:
        repository.close()


def test_batch_logging_contains_people_service_and_batch_id(tmp_path):
    repository = build_repository(tmp_path, "batch.db")
    try:
        first_id = repository.create_person({"gedcom_id": "I1", "first_name": "One", "last_name": "Person"})
        second_id = repository.create_person({"gedcom_id": "I2", "first_name": "Two", "last_name": "Person"})
        service = BatchOperationsService(repository)
        preview = service.preview((first_id, second_id), BatchOperation("edit_occupation", value="Engineer"))

        service.execute(preview)

        records = AuditService.for_database(repository.db_name).list_records(operation="batch_operations")
        assert len(records) == 1
        assert records[0].database_id == f"{first_id},{second_id}"
        assert records[0].gedcom_id == "I1,I2"
        assert records[0].batch_id
        assert records[0].service == "batch_operations_service"
    finally:
        repository.close()


def test_viewer_undo_and_redo_logging_have_correct_snapshot_direction(tmp_path):
    repository = build_repository(tmp_path, "undo.db")
    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer.repository = repository
    viewer.audit_service = AuditService.for_database(repository.db_name)
    viewer.undo_manager = UndoManager()
    viewer.refresh_views = lambda: None
    viewer._refresh_person_card = lambda: None
    try:
        command = AddPersonCommand(repository, {"gedcom_id": "I1", "first_name": "Undo", "last_name": "Person"})
        person_id = viewer.undo_manager.execute(command)

        assert viewer._undo_command() == "break"
        assert viewer._redo_command() == "break"

        undo_record = viewer.audit_service.list_records(operation="undo")[0]
        redo_record = viewer.audit_service.list_records(operation="redo")[0]
        assert undo_record.before_snapshot["people"][0][0] == person_id
        assert undo_record.after_snapshot["people"] == []
        assert redo_record.before_snapshot["people"] == []
        assert redo_record.after_snapshot["people"][0][0] == person_id
    finally:
        repository.close()


def test_all_required_operation_types_include_future_split():
    assert set(AUDIT_OPERATION_TYPES) == {
        "person_create", "person_edit", "person_delete", "merge", "split",
        "relationship_change", "batch_operations", "import", "recovery_wizard",
        "placeholder_repair", "undo", "redo",
    }


def test_viewer_audit_history_is_read_only_service_driven_and_sql_free():
    widget_source = inspect.getsource(GenealogyViewer._create_widgets)
    window_source = inspect.getsource(GenealogyViewer.open_audit_history)
    load_source = inspect.getsource(GenealogyViewer._load_audit_history)
    comparison_source = inspect.getsource(GenealogyViewer._set_audit_comparison)
    export_source = inspect.getsource(GenealogyViewer._export_audit_history)
    audit_ui_source = "\n".join((window_source, load_source, comparison_source, export_source))

    assert 'text="История изменений"' in widget_source
    assert "command=self.open_audit_history" in widget_source
    for filter_name in ("person", "operation", "date_from", "date_to", "service", "batch_id"):
        assert f'(\"{filter_name}\"' in window_source
    assert 'text="До  ↓  После"' in window_source
    assert "list_records" in load_source
    assert 'state="disabled"' in comparison_source
    assert "export_csv" in export_source
    assert "export_json" in export_source
    assert "CREATE TABLE" not in audit_ui_source
    assert "INSERT INTO" not in audit_ui_source
    assert "SELECT " not in audit_ui_source
    assert "sqlite3" not in audit_ui_source