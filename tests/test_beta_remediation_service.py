import json
import inspect
import sqlite3
from pathlib import Path

import pytest

from beta_remediation_service import BetaRemediationService, RemediationCheck
from database import initialize_database
from repository.person_repository import PersonRepository
from viewer import GenealogyViewer


def build_service(tmp_path, *, initialized=True):
    tmp_path.mkdir(parents=True, exist_ok=True)
    database = tmp_path / "genealogy.db"
    if initialized:
        initialize_database(database)
    else:
        sqlite3.connect(database).close()
    root = tmp_path / "project"
    root.mkdir()
    for relative, content in {
        "build_info.py": 'APP_VERSION = "1.0.0"\n',
        "installer/GenealogyDB.iss": '#define MyAppVersion "1.0.0"\n',
        "USER_MANUAL.md": "# GenealogyDB User Manual\n",
        "CHANGELOG.md": "# Changelog\n",
        "viewer.py": "from build_info import APP_VERSION\n",
        "release_center_service.py": "from build_info import APP_VERSION\n",
        "GenealogyDB.spec": "name='GenealogyDB'\n",
    }.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    service = BetaRemediationService(database, project_root=root, data_dir=tmp_path / "data", log_dir=tmp_path / "logs", config_path=tmp_path / "config.json")
    return database, service, root


def test_database_state_classification_and_required_table_reporting(tmp_path):
    database, service, _root = build_service(tmp_path, initialized=True)
    assert service.diagnose_database().classification == "valid GenealogyDB database"
    assert not service.diagnose_database().missing_mandatory

    missing = BetaRemediationService(tmp_path / "missing.db", data_dir=tmp_path / "data")
    assert missing.diagnose_database().classification == "missing database"

    empty_database, empty_service, _root = build_service(tmp_path / "empty", initialized=False)
    empty = empty_service.diagnose_database()
    assert empty.classification == "empty SQLite file"
    assert "people" in empty.missing_mandatory

    corrupt_path = tmp_path / "corrupt.db"
    corrupt_path.write_text("not sqlite", encoding="utf-8")
    assert BetaRemediationService(corrupt_path, data_dir=tmp_path / "data").diagnose_database().classification == "corrupt database"

    unsupported_path = tmp_path / "unsupported.db"
    with sqlite3.connect(unsupported_path) as connection:
        for table in ("people", "families", "family_children", "person_events"):
            connection.execute(f"CREATE TABLE {table} (id INTEGER)")
    assert BetaRemediationService(unsupported_path, data_dir=tmp_path / "data").diagnose_database().classification == "unsupported schema"

    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE person_events")
    uninitialized = service.diagnose_database()
    assert uninitialized.classification == "uninitialized database"
    assert "person_events" in uninitialized.missing_mandatory


def test_supported_uploaded_core_schema_is_not_classified_as_empty():
    database = Path(__file__).parents[1] / "tests" / "data" / "genealogy.db"
    diagnostic = BetaRemediationService(database).diagnose_database()
    assert diagnostic.classification == "valid GenealogyDB database"
    assert set(diagnostic.tables) == {"people", "families", "family_children", "person_events"}
    assert not diagnostic.missing_mandatory


def test_integrity_and_temporary_backup_are_read_only_and_cleaned_up(tmp_path):
    database, service, _root = build_service(tmp_path)
    repository = PersonRepository(database)
    try:
        before = repository.capture_command_state()
        temporary_before = set(Path(__import__("tempfile").gettempdir()).glob("genealogydb-beta-backup-*.sqlite"))
        assert service.verify_integrity().integrity_result == "ok"
        service.verify_temporary_backup()
        assert repository.capture_command_state() == before
        temporary_after = set(Path(__import__("tempfile").gettempdir()).glob("genealogydb-beta-backup-*.sqlite"))
        assert temporary_after == temporary_before
    finally:
        repository.close()


def test_selection_cancel_keeps_configuration_and_rejects_wrong_database(tmp_path):
    database, service, _root = build_service(tmp_path)
    before = service.config_path.read_text(encoding="utf-8") if service.config_path.exists() else ""
    service.select_working_database(database, confirmed=False)
    after = service.config_path.read_text(encoding="utf-8") if service.config_path.exists() else ""
    assert after == before
    with pytest.raises(ValueError):
        service.select_working_database(tmp_path / "wrong.db", confirmed=True)
    service.select_working_database(database, confirmed=True)
    assert json.loads(service.config_path.read_text(encoding="utf-8"))["database_path"] == str(database.resolve())


def test_version_sync_and_drift_detection(tmp_path):
    _database, service, root = build_service(tmp_path)
    assert service._version_check().status == "warning"
    changed = service.synchronize_version_metadata()
    assert changed
    assert service._version_check().status == "passed"
    assert BetaRemediationService.VERSION in (root / "build_info.py").read_text(encoding="utf-8")
    assert BetaRemediationService.VERSION in (root / "installer/GenealogyDB.iss").read_text(encoding="utf-8")


def test_log_archive_baseline_scaling_and_report_generation(tmp_path):
    _database, service, _root = build_service(tmp_path)
    service.log_dir.mkdir(parents=True)
    log = service.log_dir / "genealogydb.log"
    log.write_text("Traceback (most recent call last)\nold failure", encoding="utf-8")
    assert service._logs_check().status == "warning"
    archived = service.archive_logs()
    assert archived and all(path.parent == service.data_dir / "logs" / "archive" for path in archived)
    baseline = service.create_performance_baseline()
    assert baseline.exists()
    for scale in (125, 150, 175):
        service.record_scaling_check(scale, toolbar_fits=True, menus_readable=True, dialogs_usable=True, notes="checked")
    report = service.analyze()
    assert report.recommendation in {"READY", "READY WITH WARNINGS"}
    for report_format in ("markdown", "html", "json"):
        assert service.export(report, report_format).exists()


def test_blocker_and_warning_classification():
    assert BetaRemediationService.classify((RemediationCheck("log", "warning", "", ""),)) == "READY WITH WARNINGS"
    assert BetaRemediationService.classify((RemediationCheck("database", "blocker", "", ""),)) == "NOT READY"


def test_remediation_actions_are_wired_without_opening_tkinter():
    source = inspect.getsource(GenealogyViewer.open_beta_readiness)
    for label in (
        "Выбрать рабочую базу", "Проверить целостность", "Проверить временный backup",
        "Синхронизировать версию", "Архивировать старые логи", "Создать базовую линию",
        "Записать scaling check", "Экспорт remediation",
    ):
        assert label in source
    assert "repository.conn.execute" not in Path(__file__).parents[1].joinpath("viewer.py").read_text(encoding="utf-8")
