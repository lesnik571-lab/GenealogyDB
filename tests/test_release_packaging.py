import sqlite3
import sys
from pathlib import Path

import config
import viewer


def test_prepare_user_environment_creates_and_seeds_directories(tmp_path, monkeypatch):
    resource_dir = tmp_path / "bundle"
    plugin_dir = resource_dir / "plugins"
    resource_config = resource_dir / "resources" / "default_config.json"
    plugin_dir.mkdir(parents=True)
    resource_config.parent.mkdir(parents=True)
    (plugin_dir / "statistics.py").write_text("plugin_name = 'Statistics'", encoding="utf-8")
    resource_config.write_text('{"geocoding_provider": "opencage"}', encoding="utf-8")

    app_home = tmp_path / "user"
    directories = tuple(app_home / name for name in ("data", "backups", "exports", "logs", "plugins"))
    monkeypatch.setattr(config, "RESOURCE_DIR", resource_dir)
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", resource_config)
    monkeypatch.setattr(config, "USER_CONFIG_PATH", app_home / "config.json")
    monkeypatch.setattr(config, "DATA_DIR", directories[0])
    monkeypatch.setattr(config, "BACKUP_DIR", directories[1])
    monkeypatch.setattr(config, "EXPORT_DIR", directories[2])
    monkeypatch.setattr(config, "LOG_DIR", directories[3])
    monkeypatch.setattr(config, "PLUGIN_DIR", directories[4])

    config.prepare_user_environment()

    assert all(path.is_dir() for path in directories)
    assert (app_home / "config.json").is_file()
    assert (app_home / "plugins" / "statistics.py").is_file()


def test_about_reports_release_and_runtime_versions(monkeypatch):
    captured = {}
    application = viewer.GenealogyViewer.__new__(viewer.GenealogyViewer)
    application.root = object()
    monkeypatch.setattr(viewer.messagebox, "showinfo", lambda title, body, **kwargs: captured.update(title=title, body=body))

    application._show_about()

    assert viewer.APP_VERSION in captured["body"]
    assert viewer.BUILD_DATE in captured["body"]
    assert sys.version.split()[0] in captured["body"]
    assert sqlite3.sqlite_version in captured["body"]


def test_release_build_uses_disposable_pyinstaller_workpath():
    source = Path("build_release.ps1").read_text(encoding="utf-8")

    assert '$PyInstallerWorkPath = Join-Path $env:TEMP' in source
    assert "--workpath $PyInstallerWorkPath" in source
    assert "Remove-Item -Recurse -Force $PyInstallerWorkPath" in source
