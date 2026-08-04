import sys
from pathlib import Path
from types import SimpleNamespace

from database import initialize_database
from rc_validation_service import BLOCKED, PASS, SKIPPED, WARNING, RCValidationCheck, RCValidationReport, RCValidationService


def test_rc_validation_uses_temporary_workflows_and_preserves_configured_database(tmp_path):
    configured = tmp_path / "configured.db"
    initialize_database(configured)
    before = RCValidationService._checksum(configured)
    service = RCValidationService(configured, project_root=Path.cwd())
    service.report_dir = tmp_path / "reports"
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
    service = RCValidationService(configured, project_root=Path.cwd())
    service.report_dir = tmp_path / "reports"
    report = service.validate()
    identifiers = [check.check_id for check in report.checks]
    assert identifiers.index("database.initialize") < identifiers.index("import.confirmed") < identifiers.index("crud.person-create-edit") < identifiers.index("backup.restore") < identifiers.index("startup.restart")
    assert next(check for check in report.checks if check.check_id == "export.formats").status in {PASS, WARNING}
    assert next(check for check in report.checks if check.check_id == "packaging.resources").status in {PASS, WARNING}


def test_missing_configured_database_is_warning_and_cleanup_failure_is_warning(tmp_path, monkeypatch):
    service = RCValidationService(tmp_path / "missing.db", project_root=Path.cwd())
    service.report_dir = tmp_path / "reports"
    report = service.validate()
    assert next(check for check in report.checks if check.check_id == "database.configured-availability").status == WARNING

    monkeypatch.setattr("rc_validation_service.shutil.rmtree", lambda _path: (_ for _ in ()).throw(OSError("locked")))
    report = service.validate()
    assert any(check.status == WARNING and "cleanup failed" in check.reason for check in report.checks)

def test_rc_startup_check_accepts_existing_application_root(tmp_path, monkeypatch):
    existing_root = object()
    monkeypatch.setitem(sys.modules, "tkinter", SimpleNamespace(_default_root=existing_root))
    monkeypatch.setattr("rc_validation_service.importlib.import_module", lambda _name: None)
    (tmp_path / "viewer.py").write_text("", encoding="utf-8")
    service = RCValidationService(tmp_path / "configured.db", project_root=tmp_path)

    checks = service._startup_checks()
    headless = next(check for check in checks if check.check_id == "startup.headless-import")

    assert headless.status == PASS
    assert headless.evidence == "tk._default_root remained unchanged"



def test_rc_validation_supports_frozen_installed_executable(tmp_path, monkeypatch):
    runtime_files = (
        "assets/app_icon.svg",
        "USER_MANUAL.md",
        "schema.sql",
        "resources/default_config.json",
        "plugins/statistics.py",
    )
    for relative_path in runtime_files:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("runtime resource", encoding="utf-8")
    executable = tmp_path / "GenealogyDB.exe"
    executable.write_bytes(b"packaged executable")
    existing_root = object()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setitem(sys.modules, "tkinter", SimpleNamespace(_default_root=existing_root))
    service = RCValidationService(tmp_path / "configured.db", project_root=tmp_path)

    startup = {check.check_id: check for check in service._startup_checks()}
    packaging = {check.check_id: check for check in service._packaging_checks()}

    assert startup["startup.headless-import"].status == PASS
    assert startup["startup.viewer-safety"].status == SKIPPED
    assert packaging["packaging.resources"].status == PASS
    assert packaging["packaging.version"].status == PASS

def test_rc_markdown_localizes_recommendation_headers_and_standard_values():
    check = RCValidationCheck(
        "database.initialize",
        "Database",
        "initialize",
        PASS,
        0.125,
        "completed",
        cleanup_result="temporary root removed",
    )
    report = RCValidationReport(
        "2.1.0-rc1-dev",
        (check,),
        "READY FOR RC1",
        "configured.db",
        "before",
        "after",
    )

    text = RCValidationService._markdown(report)

    assert "# Проверка GenealogyDB RC1" in text
    assert "Рекомендация: **ГОТОВО К RC1**" in text
    assert "| ID | Категория | Статус | Время | Результат | Причина | Очистка |" in text
    assert "| База данных | ПРОЙДЕНО | 0.125 с | выполнено |" in text
    assert "временная папка удалена" in text

