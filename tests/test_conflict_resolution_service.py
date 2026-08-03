from pathlib import Path

import pytest

from collaboration_service import CollaborationService
from audit_service import AuditService
from conflict_resolution_service import (
    CUSTOM_VALUE, KEEP_CURRENT, MARK_UNRESOLVED, TAKE_INCOMING,
    ConflictResolutionService,
)
from database import initialize_database
from repository.person_repository import PersonRepository
from undo_manager import UndoManager


def repository(tmp_path, name):
    path = tmp_path / name; initialize_database(path); return PersonRepository(path)


def add_person(repo, gedcom, first="Ada", last="Lovelace", **values):
    return repo.create_person({"gedcom_id": gedcom, "first_name": first, "last_name": last, **values})


def test_three_way_person_resolution_custom_validation_and_plan_persistence(tmp_path):
    current = repository(tmp_path, "current.db"); incoming = repository(tmp_path, "incoming.db"); base = repository(tmp_path, "base.db")
    try:
        add_person(base, "I1", occupation="Writer", birth_date="1900"); current_id = add_person(current, "I1", occupation="Programmer", birth_date="1901"); add_person(incoming, "I1", occupation="Mathematician", birth_date="1902")
        service = ConflictResolutionService(current, data_dir=tmp_path / "data")
        plan = service.review(incoming.db_name, baseline_path=base.db_name)
        conflict = next(item for item in plan.conflicts if item.field_name == "occupation")
        assert conflict.base_value == "Writer" and conflict.current_value == "Programmer" and conflict.incoming_value == "Mathematician"
        plan = service.resolve(plan, conflict.conflict_id, CUSTOM_VALUE, "Scientist")
        path = service.save_plan(plan); assert path.exists() and service.load_plan(plan.plan_id) == plan
        duplicate_plan = service.duplicate_plan(plan)
        assert service.rename_plan(plan, "Reviewed").name == "Reviewed" and duplicate_plan.plan_id != plan.plan_id
        service.save_plan(duplicate_plan); service.delete_plan(duplicate_plan.plan_id); assert not (service.plan_dir / f"{duplicate_plan.plan_id}.json").exists()
        date_conflict = next(item for item in plan.conflicts if item.field_name == "birth_date")
        with pytest.raises(ValueError): service.resolve(plan, date_conflict.conflict_id, CUSTOM_VALUE, "not-a-date")
        preview = service.preview(plan); assert current.get_person_record(current_id)["occupation"] == "Programmer" and preview.updates
    finally: current.close(); incoming.close(); base.close()


def test_gedcom_and_relationship_conflicts_block_batch_and_apply(tmp_path):
    current = repository(tmp_path, "current.db"); incoming = repository(tmp_path, "incoming.db")
    try:
        current_person = add_person(current, "I1", "Ada", "Lovelace"); incoming_person = add_person(incoming, "I1", "Grace", "Hopper")
        current.create_family({"gedcom_id": "F1", "husband": "I1", "wife": "", "children": [], "relationship_type": "marriage"})
        incoming.create_family({"gedcom_id": "F1", "husband": "", "wife": "I1", "children": [], "relationship_type": "civil_partner"})
        service = ConflictResolutionService(current, data_dir=tmp_path / "data")
        plan = service.review(incoming.db_name); categories = {item.category for item in plan.conflicts}
        assert {"gedcom_id_conflict", "parentage_conflict", "spouse_partner_conflict", "family_structure_conflict"} <= categories
        relationship = next(item for item in plan.conflicts if item.category == "parentage_conflict")
        with pytest.raises(ValueError): service.resolve(plan, relationship.conflict_id, TAKE_INCOMING)
        with pytest.raises(ValueError): service.batch_resolve(plan, [relationship.conflict_id], KEEP_CURRENT, confirmed=True)
        preview = service.preview(plan); assert preview.blockers
        with pytest.raises(ValueError): service.apply(preview)
        assert current.get_person_record(current_person)["first_name"] == "Ada" and incoming.get_person_record(incoming_person)["first_name"] == "Grace"
    finally: current.close(); incoming.close()


def test_apply_backup_undo_provenance_copy_reports_and_cancellation(tmp_path):
    current = repository(tmp_path, "current.db"); incoming = repository(tmp_path, "incoming.db")
    try:
        current_id = add_person(current, "I1", occupation="Writer"); add_person(incoming, "I1", occupation="Mathematician")
        service = ConflictResolutionService(current, data_dir=tmp_path / "data", backup_dir=tmp_path / "backups")
        plan = service.review(incoming.db_name); conflict = next(item for item in plan.conflicts if item.field_name == "occupation")
        plan = service.resolve(plan, conflict.conflict_id, TAKE_INCOMING)
        for item in list(plan.conflicts):
            if item.resolution == MARK_UNRESOLVED: plan = service.resolve(plan, item.conflict_id, KEEP_CURRENT)
        preview = service.preview(plan); result = service.apply(preview)
        assert Path(result.backup_path).exists() and current.get_person_record(current_id)["occupation"] == "Mathematician"
        manager = UndoManager(); manager.record_applied(service.undo_command(result)); assert manager.undo() and current.get_person_record(current_id)["occupation"] == "Writer" and manager.redo()
        copy_preview = service.preview(plan); copied = service.apply(copy_preview, mode="Create resolved project copy", destination=tmp_path / "resolved.db")
        assert Path(copied.target_path).exists() and current.get_person_record(current_id)["occupation"] == "Mathematician"
        assert CollaborationService(current.db_name, data_dir=tmp_path / "data").changes()
        reports = service.export_all(preview); first_report = {path: path.read_bytes() for path in reports}
        assert all(path.exists() for path in reports) and first_report == {path: path.read_bytes() for path in service.export_all(preview)}
        assert AuditService.for_database(current.db_name).list_records(service="conflict_resolution_service")
        with pytest.raises(RuntimeError): service.review(incoming.db_name, cancel_callback=lambda: (_ for _ in ()).throw(RuntimeError("cancelled")))
        with pytest.raises(RuntimeError): service.apply(preview, cancel_callback=lambda: (_ for _ in ()).throw(RuntimeError("cancelled")))
    finally: current.close(); incoming.close()


def test_citation_provenance_duplicates_and_relationship_visual_blockers(tmp_path):
    current = repository(tmp_path, "current.db"); incoming = repository(tmp_path, "incoming.db")
    try:
        current_person = add_person(current, "I1"); incoming_person = add_person(incoming, "I1")
        current_source = current.create_source_record({"title": "Archive"}); incoming_source = incoming.create_source_record({"title": "Archive"})
        current.create_citation_record({"source_id": current_source, "target_type": "person", "target_id": current_person, "page": "1"})
        incoming.create_citation_record({"source_id": incoming_source, "target_type": "person", "target_id": incoming_person, "page": "1"})
        current.create_family({"gedcom_id": "F1", "husband": "I1", "wife": "", "children": [], "relationship_type": "marriage"})
        incoming.create_family({"gedcom_id": "F1", "husband": "", "wife": "I1", "children": ["I1"], "relationship_type": "marriage"})
        service = ConflictResolutionService(current, data_dir=tmp_path / "data"); plan = service.review(incoming.db_name)
        duplicate = next(item for item in plan.conflicts if item.category == "duplicate_citation")
        assert duplicate.provenance["attribution"] == "preserved"
        relationship = next(item for item in plan.conflicts if item.category == "parentage_conflict")
        visual = service.relationship_preview(plan, relationship.conflict_id)
        assert not visual["can_apply"] and "I1" in visual["parents"] and visual["cycle_detected"]
    finally: current.close(); incoming.close()


def test_apply_rolls_back_when_a_resolution_write_fails(tmp_path, monkeypatch):
    current = repository(tmp_path, "current.db"); incoming = repository(tmp_path, "incoming.db")
    try:
        current_id = add_person(current, "I1", occupation="Writer"); add_person(incoming, "I1", occupation="Mathematician")
        service = ConflictResolutionService(current, data_dir=tmp_path / "data", backup_dir=tmp_path / "backups")
        plan = service.review(incoming.db_name); conflict = next(item for item in plan.conflicts if item.field_name == "occupation")
        plan = service.resolve(plan, conflict.conflict_id, TAKE_INCOMING)
        for item in plan.conflicts:
            if item.conflict_id != conflict.conflict_id:
                plan = service.resolve(plan, item.conflict_id, KEEP_CURRENT)
        preview = service.preview(plan); original = current.update_person_fields
        def fail_after_write(*args, **kwargs):
            original(*args, **kwargs); raise RuntimeError("forced failure")
        monkeypatch.setattr(current, "update_person_fields", fail_after_write)
        with pytest.raises(RuntimeError): service.apply(preview)
        assert current.get_person_record(current_id)["occupation"] == "Writer"
    finally: current.close(); incoming.close()