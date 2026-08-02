import logging
import zipfile

import pytest

from logging_service import (
    LOG_BACKUP_COUNT,
    MAX_LOG_BYTES,
    configure_logging,
    diagnostics_snapshot,
    export_diagnostics,
    get_logger,
    install_exception_logging,
    log_operation,
)


@pytest.fixture
def isolated_logger(tmp_path):
    logger = configure_logging(tmp_path / "logs")
    yield logger, tmp_path
    for handler in tuple(logger.handlers):
        if getattr(handler, "_genealogydb_handler", False):
            logger.removeHandler(handler)
            handler.close()


def test_rotating_logger_keeps_ten_files_at_five_megabytes(isolated_logger):
    logger, tmp_path = isolated_logger
    handler = next(
        item for item in logger.handlers
        if getattr(item, "_genealogydb_handler", False)
    )
    assert handler.maxBytes == MAX_LOG_BYTES == 5 * 1024 * 1024
    assert handler.backupCount == LOG_BACKUP_COUNT == 9

    handler.maxBytes = 120
    for index in range(100):
        logger.info("rotation record %s with padding", index)

    log_files = list((tmp_path / "logs").glob("genealogydb.log*"))
    assert 1 < len(log_files) <= 10


def test_operation_logging_records_success_and_unexpected_failure(isolated_logger):
    _logger, tmp_path = isolated_logger

    @log_operation("Test action")
    def success():
        return 42

    @log_operation("Broken action")
    def failure():
        raise RuntimeError("boom")

    assert success() == 42
    with pytest.raises(RuntimeError, match="boom"):
        failure()

    for handler in get_logger().handlers:
        handler.flush()
    combined = "".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "logs").glob("genealogydb.log*")
    )
    assert "Test action started" in combined
    assert "Test action completed" in combined
    assert "Broken action failed" in combined


def test_diagnostics_export_excludes_genealogy_data(isolated_logger):
    logger, tmp_path = isolated_logger
    logger.info("diagnostic log")
    configuration = tmp_path / "config.json"
    configuration.write_text('{"geocoding_provider": "opencage"}', encoding="utf-8")
    (tmp_path / "genealogy.db").write_bytes(b"private database")
    (tmp_path / "family.ged").write_text("private GEDCOM", encoding="utf-8")
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    (backup_dir / "backup.db").write_bytes(b"private backup")
    snapshot = diagnostics_snapshot(
        plugins=(type("Plugin", (), {"name": "Statistics", "version": "1.0"})(),),
        services=("PersonRepository", "RecoveryWizardService"),
        database_path=tmp_path / "genealogy.db",
        log_dir=tmp_path / "logs",
    )

    destination = export_diagnostics(
        tmp_path / "diagnostics.zip",
        snapshot,
        tmp_path / "logs",
        configuration,
    )

    with zipfile.ZipFile(destination) as archive:
        names = set(archive.namelist())
    assert "configuration/config.json" in names
    assert {"version.json", "plugins.json", "diagnostics.json"} <= names
    assert any(name.startswith("logs/") for name in names)
    assert not any(
        name.lower().endswith((".db", ".ged")) or "backup" in name.lower()
        for name in names
    )


def test_diagnostics_snapshot_reports_runtime_and_loaded_components(tmp_path):
    plugin = type("Plugin", (), {"name": "Statistics", "version": "1.0"})()
    snapshot = diagnostics_snapshot(
        plugins=(plugin,),
        services=("PersonRepository", "IntegrityCheckService"),
        database_path=tmp_path / "genealogy.db",
        log_dir=tmp_path / "logs",
    )

    assert snapshot["application_version"]
    assert snapshot["database_path"].endswith("genealogy.db")
    assert snapshot["log_folder"].endswith("logs")
    assert snapshot["python_version"]
    assert snapshot["sqlite_version"]
    assert snapshot["plugins"] == ["Statistics 1.0"]
    assert snapshot["loaded_services"] == ["IntegrityCheckService", "PersonRepository"]


def test_unexpected_tk_callback_exception_is_logged(isolated_logger):
    _logger, tmp_path = isolated_logger
    root = type("Root", (), {})()
    install_exception_logging(root)

    try:
        raise RuntimeError("callback failed")
    except RuntimeError as error:
        root.report_callback_exception(type(error), error, error.__traceback__)

    for handler in get_logger().handlers:
        handler.flush()
    combined = "".join(
        path.read_text(encoding="utf-8")
        for path in (tmp_path / "logs").glob("genealogydb.log*")
    )
    assert "Unexpected Tk callback exception" in combined
    assert "callback failed" in combined