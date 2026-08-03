from pathlib import Path

from database import initialize_database
from rc_validation_service import BLOCKED, PASS, WARNING, RCValidationCheck, RCValidationService


def test_rc_validation_uses_temporary_workflows_and_preserves_configured_database(tmp_path):
    configured = tmp_path / "configured.db"
    initialize_database(configured)
    before = RCValidationService._checksum(configured)
    service = RCValidationService(configured, project_root=Path.cwd())
    report = service.validate()

    assert report.checksum_before == before == report.checksum_after
    assert report.recommendation in {"READY FOR RC1", "READY FOR RC1 WITH WARNINGS"}
    assert all(
        check.cleanup_result == "temporary root removed"
        or check.cleanup_result.startswith("cleanup failed:")
        or check.cleanup_result == "not applicable"
        for check in report.checks
    )
    assert not report.blockers
    assert {"Startup", "Database", "Import", "CRUD", "Relationships", "Sources", "Attachments", "Undo/Redo", "Backup/Restore", "Analysis", "Visualization", "Persistence", "Export", "Packaging"} <= {check.category for check in report.checks}


def test_rc_recommendation_and_deterministic_report_generation(tmp_path):
    service = RCValidationService(tmp_path / "missing.db", project_root=tmp_path)
    checks = (RCValidationCheck("safety", "Database", "safe", PASS, 0, "ok"),)
    assert service.recommendation(checks) == "READY FOR RC1"
    assert service.recommendation((*checks, RCValidationCheck("blocked", "Database", "blocked", BLOCKED, 0, "", "failure"))) == "NOT READY FOR RC1"
    report = service.validate()
    first = service.export_all(report)
    second = service.export_all(report)
    assert first == second
    assert all(path.exists() and path.stat().st_size > 0 for path in first)


def test_rc_validation_orders_complete_workflow_and_checks_exports_and_resources(tmp_path):
    configured = tmp_path / "configured.db"
    initialize_database(configured)
    report = RCValidationService(configured, project_root=Path.cwd()).validate()
    identifiers = [check.check_id for check in report.checks]
    assert identifiers.index("database.initialize") < identifiers.index("import.confirmed") < identifiers.index("crud.person-create-edit") < identifiers.index("backup.restore") < identifiers.index("startup.restart")
    assert next(check for check in report.checks if check.check_id == "export.formats").status in {PASS, WARNING}
    assert next(check for check in report.checks if check.check_id == "packaging.resources").status in {PASS, WARNING}


def test_missing_configured_database_is_warning_and_cleanup_failure_is_warning(tmp_path, monkeypatch):
    service = RCValidationService(tmp_path / "missing.db", project_root=Path.cwd())
    report = service.validate()
    assert next(check for check in report.checks if check.check_id == "database.configured-availability").status == WARNING

    monkeypatch.setattr("rc_validation_service.shutil.rmtree", lambda _path: (_ for _ in ()).throw(OSError("locked")))
    report = service.validate()
    assert any(check.status == WARNING and "cleanup failed" in check.reason for check in report.checks)