from pathlib import Path
from uuid import UUID

import pytest

from collaboration_service import CHANGE_TYPES, CollaborationService
from database import initialize_database
from repository.person_repository import PersonRepository
from viewer import GenealogyViewer


def build_repository(tmp_path):
    database = tmp_path / "genealogy.db"
    initialize_database(database)
    return PersonRepository(database)


def test_uuid_identity_and_metadata_persistence_are_separate_from_genealogy_data(tmp_path):
    repository = build_repository(tmp_path)
    try:
        before = repository.capture_command_state()
        service = CollaborationService(repository.db_name, data_dir=tmp_path / "data", editor_identity="Editor", machine_identifier="Machine")
        identity = service.identity()
        UUID(identity.project_uuid); UUID(identity.dataset_uuid)
        change = service.record_change("create_person", references={"person": ("1",)}, summary="Created", timestamp="2026-08-03T00:00:00+00:00", operation_id="12345678-1234-5678-1234-567812345678")
        assert change.author == "Editor" and change.machine_identifier == "Machine"
        assert CollaborationService(repository.db_name, data_dir=tmp_path / "data").identity() == identity
        assert repository.capture_command_state() == before
        assert "collaboration" in str(service.metadata_path)
    finally:
        repository.close()


def test_export_import_is_deterministic_and_detects_orphans(tmp_path):
    repository = build_repository(tmp_path)
    try:
        person_id = repository.create_person({"gedcom_id": "I1", "first_name": "Ada", "last_name": "Lovelace"})
        service = CollaborationService(repository.db_name, data_dir=tmp_path / "one", editor_identity="Ada")
        service.record_change("edit_person", references={"person": (str(person_id),)}, timestamp="2026-08-03T00:00:00+00:00", operation_id="12345678-1234-5678-1234-567812345678")
        service.record_change("source_change", references={"source": ("999",)}, timestamp="2026-08-03T00:00:01+00:00", operation_id="12345678-1234-5678-1234-567812345679")
        exported = service.export_metadata(tmp_path / "metadata.json")
        assert exported.read_text(encoding="utf-8") == service.export_metadata(tmp_path / "metadata-copy.json").read_text(encoding="utf-8")
        clone = CollaborationService(repository.db_name, data_dir=tmp_path / "two")
        clone.configure_identity(editor_identity="Other")
        assert clone.import_metadata(exported) == 2
        diagnostics = service.diagnostics(repository)
        assert diagnostics.orphan_operation_ids == ("12345678-1234-5678-1234-567812345679",)
        assert diagnostics.missing_references[diagnostics.orphan_operation_ids[0]] == ("source:999",)
    finally:
        repository.close()


def test_change_type_validation_and_future_interfaces_are_explicit(tmp_path):
    service = CollaborationService(tmp_path / "genealogy.db", data_dir=tmp_path / "data")
    with pytest.raises(ValueError):
        service.record_change("unsupported")
    for name in ("merge_from_project", "detect_conflicts", "exchange_changes", "review_workflow"):
        with pytest.raises(NotImplementedError):
            getattr(service, name)()
    assert set(CHANGE_TYPES) == {"create_person", "edit_person", "delete_person", "merge", "split", "relationship_change", "source_change"}


def test_viewer_registers_collaboration_under_tools_without_opening_tkinter():
    source = Path(GenealogyViewer._create_widgets.__code__.co_filename).read_text(encoding="utf-8")
    assert 'add_cascade(label="Инструменты", menu=tools_menu)' in source
    assert 'label="Совместная работа", command=self.open_collaboration' in source