import json
import inspect
import zipfile

from database import initialize_database
from release_center_service import ReleaseCenterService, UpdateChecker
from viewer import GenealogyViewer


def build_service(tmp_path):
    root = tmp_path / "project"; root.mkdir()
    for name, content in {
        "GenealogyDB.spec": "spec", "schema.sql": "schema", "USER_MANUAL.md": "# Manual", "CHANGELOG.md": "# Release\n- Fixed search",
    }.items():
        (root / name).write_text(content, encoding="utf-8")
    (root / "tests").mkdir()
    for name in ("assets/app_icon.svg", "assets/app.ico", "resources/default_config.json", "plugins/statistics.py"):
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text("payload", encoding="utf-8")
    data = tmp_path / "data"; logs = tmp_path / "logs"; plugins = tmp_path / "plugins"; config = tmp_path / "config.json"
    initialize_database(data / "genealogy.db")
    config.write_text('{"geocoding_provider": "offline"}', encoding="utf-8")
    return ReleaseCenterService(project_root=root, data_dir=data, log_dir=logs, plugin_dir=plugins, config_path=config, database_path=data / "genealogy.db")


def test_diagnostics_self_check_environment_and_report_exports_are_read_only(tmp_path):
    service = build_service(tmp_path)
    before = (tmp_path / "data" / "genealogy.db").read_bytes()
    environment = service.environment_summary(plugins=(type("P", (), {"name": "Stats", "version": "1"})(),), diagnostics=("Performance",))
    checks = service.self_check()
    markdown = service.export_report(tmp_path / "report.md", "markdown")
    html = service.export_report(tmp_path / "report.html", "html")
    archive = service.export_report(tmp_path / "report.zip", "zip")

    assert environment["application_version"]
    assert any(check.name == "Repository integrity" and check.passed for check in checks)
    assert "Release Report" in markdown.read_text(encoding="utf-8")
    assert "<html>" in html.read_text(encoding="utf-8")
    with zipfile.ZipFile(archive) as package:
        assert {"report.md", "report.html"}.issubset(package.namelist())
    assert (tmp_path / "data" / "genealogy.db").read_bytes() == before


def test_package_manifest_crash_report_notes_and_update_checker_are_deterministic(tmp_path):
    service = build_service(tmp_path)
    manifest = service.export_release_package(tmp_path / "release")
    first = json.loads(manifest.read_text(encoding="utf-8"))
    second = json.loads(service.export_release_package(tmp_path / "release").read_text(encoding="utf-8"))
    crash = service.crash_report(tmp_path / "crash.zip", RuntimeError("broken"), include_database_checksum=True)
    pdf = service.export_release_notes_pdf(tmp_path / "notes.pdf")

    assert first == second and "diagnostics_summary.md" in first["files"]
    with zipfile.ZipFile(crash) as archive:
        payload = json.loads(archive.read("crash.json"))
        assert "RuntimeError" in payload["traceback"] and payload["database_checksum"]
    assert pdf.read_bytes().startswith(b"%PDF")
    assert service.search_release_notes("fixed") == ["- Fixed search"]
    assert UpdateChecker().check() == {"enabled": False, "checked": False, "update": None}
    assert UpdateChecker(True, lambda: {"version": "2.0"}).check()["update"]["version"] == "2.0"


def test_invalid_configuration_is_reported_without_mutating_it(tmp_path):
    service = build_service(tmp_path)
    service.config_path.write_text("not-json", encoding="utf-8")
    result = next(check for check in service.self_check() if check.name == "Configuration")
    assert not result.passed
    assert service.config_path.read_text(encoding="utf-8") == "not-json"


def test_help_menu_registers_release_center_without_opening_tkinter():
    source = inspect.getsource(GenealogyViewer._create_widgets)
    assert 'label="Центр релиза"' in source
    assert "self.open_release_center" in source
