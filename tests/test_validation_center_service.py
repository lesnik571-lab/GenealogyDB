import json

import pytest

from audit_service import AuditService
from database import initialize_database
from repository.person_repository import PersonRepository
from undo_manager import UndoManager
from validation_center_service import ValidationCenterService, ValidationFixCommand


def repository(tmp_path):
    path = tmp_path / "validation.db"
    initialize_database(path)
    return PersonRepository(path)


def seed(repo, tmp_path):
    for index, values in enumerate((
        ("I1", "Imported", "Blank", "2000", ""), ("I2", "Parent", "One", "1995", ""),
        ("I3", "Child", "One", "2000", ""), ("I4", "Dead", "One", "1900", "1950"),
    ), 1):
        repo.create_person({"gedcom_id": values[0], "first_name": values[1], "last_name": values[2], "birth_date": values[3], "death_date": values[4]})
    repo.create_family({"gedcom_id": "F1", "husband": "I2", "wife": "", "children": ["I3"], "relationship_type": "unknown"})
    repo.conn.execute("UPDATE people SET first_name = '', last_name = '' WHERE id = 1")
    repo.conn.execute("INSERT INTO family_children (family_id, child_id) VALUES ('F1', 'I3')")
    repo.conn.execute("INSERT INTO person_events (person_id, event_type, event_date, event_place, description) VALUES (3, 'Birth', '2000', '', '')")
    repo.conn.execute("INSERT INTO person_events (person_id, event_type, event_date, event_place, description) VALUES (3, 'Birth', '2000', '', '')")
    repo.conn.execute("INSERT INTO person_media (person_id, media_type, title, file_path, description) VALUES (3, 'document', 'missing', ?, '')", (str(tmp_path / "missing.pdf"),))
    repo.conn.commit()


def test_validation_analysis_is_deterministic_read_only_and_classifies_issues(tmp_path):
    repo = repository(tmp_path)
    try:
        seed(repo, tmp_path)
        service = ValidationCenterService(repo, data_dir=tmp_path / "sidecar")
        before = repo.capture_command_state()
        first = service.analyze()
        second = service.analyze()
        categories = {issue.category for issue in first.issues}

        assert first == second
        assert {"unnamed_people", "duplicate_child_links", "duplicate_events", "broken_attachment_paths", "parent_under_12"} <= categories
        assert all(len(issue.issue_id) == 16 and issue.severity in {"Critical", "Error", "Warning", "Information"} for issue in first.issues)
        assert all(issue.risk_level in {"Safe", "Review required", "Dangerous"} for issue in first.issues)
        assert first.score == service.analyze().score
        assert repo.capture_command_state() == before
    finally:
        repo.close()


def test_safe_fix_preview_apply_backup_audit_undo_and_cancellation(tmp_path):
    repo = repository(tmp_path)
    try:
        seed(repo, tmp_path)
        service = ValidationCenterService(repo, data_dir=tmp_path / "sidecar", backup_dir=tmp_path / "backups")
        report = service.analyze()
        safe = [issue for issue in report.issues if issue.automatic_fix_available]
        preview = service.preview_fixes(safe)
        before = repo.capture_command_state()
        with pytest.raises(RuntimeError):
            service.apply_fixes(preview, cancel_callback=lambda: (_ for _ in ()).throw(RuntimeError("cancelled")))
        assert repo.capture_command_state() == before

        result = service.apply_fixes(preview)
        assert result.backup_path.exists() and result.delta
        assert not any(issue.category in {"duplicate_events", "duplicate_child_links", "broken_attachment_paths"} for issue in service.analyze().issues)
        assert AuditService.for_database(repo.db_name).list_records(service="validation_center_service")
        manager = UndoManager()
        manager.record_applied(ValidationFixCommand(repo, result))
        manager.undo()
        assert repo.capture_command_state() == before
        manager.redo()
        assert repo.capture_command_state() != before
    finally:
        repo.close()


def test_ignores_and_reports_are_sidecar_only(tmp_path):
    repo = repository(tmp_path)
    try:
        seed(repo, tmp_path)
        service = ValidationCenterService(repo, data_dir=tmp_path / "sidecar")
        report = service.analyze()
        issue = next(item for item in report.issues if item.category == "unnamed_people")
        before = repo.capture_command_state()
        service.ignore(issue, "Known import gap")
        assert issue.issue_id not in {item.issue_id for item in service.analyze().issues}
        export = service.export_ignores(tmp_path / "ignores.json")
        service.restore_ignored(issue)
        service.import_ignores(export)
        assert issue.issue_id not in {item.issue_id for item in service.analyze().issues}
        for extension in ("csv", "json", "html"):
            path = service.export_report(report, tmp_path / f"report.{extension}", extension)
            assert path.exists() and path.stat().st_size > 0
        assert json.loads(export.read_text(encoding="utf-8"))
        assert repo.capture_command_state() == before
    finally:
        repo.close()