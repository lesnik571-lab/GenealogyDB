from pathlib import Path
import sqlite3

import pytest

from collaboration_service import CollaborationService
from database import initialize_database
from project_merge_service import KEEP_BOTH, MANUAL_REVIEW, ProjectMergeService
from repository.person_repository import PersonRepository
from undo_manager import UndoManager


def repository(tmp_path, name):
    path = tmp_path / name; initialize_database(path); return PersonRepository(path)


def person(repo, gedcom, first="Ada", last="Lovelace", **values):
    return repo.create_person({"gedcom_id": gedcom, "first_name": first, "last_name": last, **values})


def test_identical_duplicates_conflicts_and_preview_do_not_change_projects(tmp_path):
    target = repository(tmp_path, "target.db"); source = repository(tmp_path, "source.db")
    try:
        target_id = person(target, "I1", occupation="Writer"); person(source, "I1", occupation="Mathematician"); person(source, "I2", "Grace", "Hopper")
        before_target, before_source = target.capture_command_state(), source.capture_command_state()
        preview = ProjectMergeService(target, data_dir=tmp_path / "data").analyze(source.db_name)
        assert any(item.category == "conflicting_person_fields" for item in preview.items)
        assert any(item.category == "people_addition" for item in preview.items)
        assert target.capture_command_state() == before_target and source.capture_command_state() == before_source
        assert target_id == 1
    finally: target.close(); source.close()


def test_family_event_source_citation_conflicts_and_reports_are_deterministic(tmp_path):
    target = repository(tmp_path, "target.db"); source = repository(tmp_path, "source.db")
    try:
        first = person(target, "I1"); second = person(source, "I1")
        target.create_family({"gedcom_id": "F1", "husband": "I1", "wife": "", "children": [], "relationship_type": "marriage"}); source.create_family({"gedcom_id": "F2", "husband": "I1", "wife": "", "children": [], "relationship_type": "civil_partner"})
        target.create_person_event({"person_id": first, "event_type": "birth", "date": "1900"}); source.create_person_event({"person_id": second, "event_type": "birth", "date": "1901"})
        target_source = target.create_source_record({"title": "Archive", "author": "A"}); source_source = source.create_source_record({"title": "Archive", "author": "B"})
        target.create_citation_record({"source_id": target_source, "target_type": "person", "target_id": first, "page": "1"}); source.create_citation_record({"source_id": source_source, "target_type": "person", "target_id": second, "page": "2"})
        service = ProjectMergeService(target, data_dir=tmp_path / "data"); preview = service.analyze(source.db_name)
        assert {"conflicting_family_structure", "conflicting_event", "conflicting_source", "conflicting_citation"} <= {item.category for item in preview.items}
        first_reports = service.export_all(preview); assert first_reports == service.export_all(preview)
        assert all(path.exists() and path.stat().st_size > 0 for path in first_reports)
    finally: target.close(); source.close()


def test_apply_copy_backup_undo_audit_provenance_and_cancellation(tmp_path):
    target = repository(tmp_path, "target.db"); source = repository(tmp_path, "source.db")
    try:
        person(target, "I1"); person(source, "I2", "Grace", "Hopper")
        target_state, source_state = target.capture_command_state(), source.capture_command_state()
        service = ProjectMergeService(target, data_dir=tmp_path / "data", backup_dir=tmp_path / "backups")
        preview = service.analyze(source.db_name, mode="Merge into current project")
        result = service.apply(preview)
        assert Path(result.backup_path).exists() and target.get_person_by_gedcom_id("I2") and source.capture_command_state() == source_state
        manager = UndoManager(); manager.record_applied(service.undo_command(result)); assert manager.undo() and target.capture_command_state() == target_state
        copy_preview = service.analyze(source.db_name, mode="Create new merged project copy")
        copied = service.apply(copy_preview, destination=tmp_path / "merged.db")
        assert Path(copied.target_path).exists() and target.capture_command_state() == target_state
        with pytest.raises(RuntimeError): service.analyze(source.db_name, cancel_callback=lambda: (_ for _ in ()).throw(RuntimeError("cancelled")))
        with pytest.raises(RuntimeError): service.apply(preview, cancel_callback=lambda: (_ for _ in ()).throw(RuntimeError("cancelled")))
        assert CollaborationService(target.db_name, data_dir=tmp_path / "data").changes()
    finally: target.close(); source.close()