"""Read-only release diagnostics, verification, and support-package export."""

from __future__ import annotations

import hashlib
import html
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import traceback
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from build_info import APP_VERSION, BUILD_DATE
from config import DATA_DIR, LOG_DIR, PLUGIN_DIR, PROJECT_ROOT, USER_CONFIG_PATH, USER_MANUAL_PATH
from database import validate_database_file
from plugin_manager import PluginManager


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


class UpdateChecker:
    """Offline-by-default abstraction for a future update provider."""

    def __init__(self, enabled: bool = False, provider: Callable[[], Mapping[str, Any]] | None = None) -> None:
        self.enabled = enabled
        self.provider = provider

    def check(self) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "checked": False, "update": None}
        return {"enabled": True, "checked": True, "update": dict(self.provider() if self.provider else {})}


class ReleaseCenterService:
    """Gather release information without changing genealogy data or configuration."""

    def __init__(
        self,
        *,
        project_root: str | Path = PROJECT_ROOT,
        data_dir: str | Path = DATA_DIR,
        log_dir: str | Path = LOG_DIR,
        plugin_dir: str | Path = PLUGIN_DIR,
        config_path: str | Path = USER_CONFIG_PATH,
        database_path: str | Path | None = None,
    ) -> None:
        self.project_root = Path(project_root)
        self.data_dir = Path(data_dir)
        self.log_dir = Path(log_dir)
        self.plugin_dir = Path(plugin_dir)
        self.config_path = Path(config_path)
        self.database_path = Path(database_path) if database_path else self.data_dir / "genealogy.db"

    def environment_summary(self, plugins: Iterable[Any] = (), diagnostics: Iterable[str] = ()) -> dict[str, Any]:
        return {
            "application_version": APP_VERSION,
            "build_date": BUILD_DATE,
            "git_commit": self._git_commit(),
            "python_version": sys.version.split()[0],
            "sqlite_version": sqlite3.sqlite_version,
            "os": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "database_version": self._database_version(),
            "enabled_plugins": sorted(f"{getattr(item, 'name', item)} {getattr(item, 'version', '')}".strip() for item in plugins),
            "enabled_diagnostics": sorted(str(item) for item in diagnostics),
        }

    def self_check(self) -> list[CheckResult]:
        checks = [
            self._check_database(),
            self._check_directories(),
            self._check_configuration(),
            self._check_plugins(),
            self._check_performance_baseline(),
            self._check_ui_state(),
            self._check_build_files(),
        ]
        return checks

    def build_report(self, plugins: Iterable[Any] = (), diagnostics: Iterable[str] = ()) -> dict[str, Any]:
        return {"environment": self.environment_summary(plugins, diagnostics), "checks": [asdict(item) for item in self.self_check()]}

    def export_report(self, destination: str | Path, report_format: str = "markdown", *, plugins: Iterable[Any] = (), diagnostics: Iterable[str] = ()) -> Path:
        path = Path(destination); path.parent.mkdir(parents=True, exist_ok=True)
        report = self.build_report(plugins, diagnostics)
        if report_format == "markdown":
            path.write_text(self._markdown(report), encoding="utf-8")
        elif report_format == "html":
            path.write_text(self._html(report), encoding="utf-8")
        elif report_format == "zip":
            self._write_zip(path, report)
        else:
            raise ValueError(f"Unsupported report format: {report_format}")
        return path

    def export_release_package(self, destination: str | Path, *, plugins: Iterable[Any] = (), diagnostics: Iterable[str] = ()) -> Path:
        folder = Path(destination); folder.mkdir(parents=True, exist_ok=True)
        report = self.build_report(plugins, diagnostics)
        files = {
            "configuration": self.config_path,
            "user_manual": self.project_root / "USER_MANUAL.md",
            "release_notes": self.project_root / "CHANGELOG.md",
        }
        for label, source in files.items():
            if source.is_file():
                shutil.copy2(source, folder / source.name)
        executable = self._find_executable()
        if executable:
            shutil.copy2(executable, folder / executable.name)
        (folder / "diagnostics_summary.md").write_text(self._markdown(report), encoding="utf-8")
        manifest = {"version": APP_VERSION, "files": sorted(path.name for path in folder.iterdir() if path.is_file() and path.name != "manifest.json")}
        (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        return folder / "manifest.json"

    def crash_report(self, destination: str | Path, error: BaseException | None = None, *, include_database_checksum: bool = False) -> Path:
        path = Path(destination); path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"environment": self.environment_summary(), "traceback": "".join(traceback.format_exception(type(error), error, error.__traceback__)) if error else "", "database_checksum": self._checksum(self.database_path) if include_database_checksum and self.database_path.is_file() else None}
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("crash.json", json.dumps(payload, ensure_ascii=False, indent=2))
            for log in sorted(self.log_dir.glob("*.log*")) if self.log_dir.exists() else ():
                archive.write(log, f"logs/{log.name}")
        return path

    def release_notes(self) -> str:
        path = self.project_root / "CHANGELOG.md"
        return path.read_text(encoding="utf-8") if path.is_file() else ""

    def search_release_notes(self, query: str) -> list[str]:
        needle = query.casefold().strip()
        return [line for line in self.release_notes().splitlines() if not needle or needle in line.casefold()]

    def export_release_notes_pdf(self, destination: str | Path) -> Path:
        """Write a small text-only PDF, sufficient for portable release notes."""
        text = self.release_notes().replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        lines = text.splitlines()[:120]
        stream = "BT /F1 10 Tf 48 780 Td " + " ".join(f"({line[:100]}) Tj 0 -13 Td" for line in lines) + " ET"
        objects = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>", "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", f"<< /Length {len(stream.encode('latin-1', 'replace'))} >>\nstream\n{stream}\nendstream"]
        content = ["%PDF-1.4\n"]; offsets = [0]
        for index, item in enumerate(objects, 1):
            offsets.append(sum(len(part.encode("latin-1", "replace")) for part in content)); content.append(f"{index} 0 obj\n{item}\nendobj\n")
        xref = sum(len(part.encode("latin-1", "replace")) for part in content); content.append(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n" + "".join(f"{offset:010d} 00000 n \n" for offset in offsets[1:]) + f"trailer << /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n")
        path = Path(destination); path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes("".join(content).encode("latin-1", "replace")); return path

    def _check_database(self) -> CheckResult:
        try:
            validate_database_file(self.database_path)
            return CheckResult("Repository integrity", True, "SQLite integrity and required tables are valid.")
        except (OSError, ValueError) as error:
            return CheckResult("Repository integrity", False, str(error))

    def _check_directories(self) -> CheckResult:
        directories = (self.data_dir, self.log_dir, self.plugin_dir)
        try:
            for directory in directories:
                directory.mkdir(parents=True, exist_ok=True)
                probe = directory / ".release-center-write-test"; probe.write_text("ok", encoding="utf-8"); probe.unlink()
            return CheckResult("Required directories", True, "Data, log, and plugin folders are writable.")
        except OSError as error:
            return CheckResult("Required directories", False, str(error))

    def _check_configuration(self) -> CheckResult:
        if not self.config_path.exists():
            return CheckResult("Configuration", True, "User configuration has not been created yet.")
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
            return CheckResult("Configuration", isinstance(payload, dict), "Configuration JSON is valid." if isinstance(payload, dict) else "Configuration root must be an object.")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            return CheckResult("Configuration", False, str(error))

    def _check_plugins(self) -> CheckResult:
        try:
            for path in sorted(self.plugin_dir.glob("*.py")) if self.plugin_dir.exists() else ():
                if not path.name.startswith("_"):
                    PluginManager._validate_source(path)
            return CheckResult("Plugins", True, "Plugin sources passed static validation.")
        except (OSError, ValueError, SyntaxError) as error:
            return CheckResult("Plugins", False, str(error))

    def _check_performance_baseline(self) -> CheckResult:
        path = self.data_dir / "performance" / "baseline.json"
        return CheckResult("Performance baseline", path.is_file(), "Baseline is available." if path.is_file() else "Performance baseline has not been saved.")

    def _check_ui_state(self) -> CheckResult:
        path = self.data_dir / "ui_state" / "workspace.json"
        if not path.exists():
            return CheckResult("UI state", True, "UI state has not been saved yet.")
        try:
            json.loads(path.read_text(encoding="utf-8")); return CheckResult("UI state", True, "UI state JSON is valid.")
        except (OSError, ValueError, json.JSONDecodeError) as error:
            return CheckResult("UI state", False, str(error))

    def _check_build_files(self) -> CheckResult:
        required = (
            self.project_root / "GenealogyDB.spec", self.project_root / "schema.sql",
            self.project_root / "USER_MANUAL.md", self.project_root / "CHANGELOG.md",
            self.project_root / "tests", self.project_root / "assets" / "app_icon.svg",
            self.project_root / "assets" / "app.ico", self.project_root / "resources" / "default_config.json",
            self.project_root / "plugins" / "statistics.py",
        )
        missing = [path.name for path in required if not path.exists()]
        return CheckResult("Build verification", not missing, "Required build files exist." if not missing else f"Missing: {', '.join(missing)}")

    def _database_version(self) -> str:
        try:
            with sqlite3.connect(self.database_path) as connection:
                return str(connection.execute("PRAGMA user_version").fetchone()[0])
        except sqlite3.Error:
            return "unavailable"

    def _git_commit(self) -> str:
        try:
            return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=self.project_root, text=True, stderr=subprocess.DEVNULL, timeout=2).strip()
        except (OSError, subprocess.SubprocessError):
            return "unavailable"

    def _find_executable(self) -> Path | None:
        candidates = tuple(self.project_root.glob("dist/**/GenealogyDB.exe")) + tuple(self.project_root.glob("GenealogyDB.exe"))
        return next((path for path in candidates if path.is_file()), None)

    def _write_zip(self, path: Path, report: Mapping[str, Any]) -> None:
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("report.md", self._markdown(report))
            archive.writestr("report.html", self._html(report))
            for log in sorted(self.log_dir.glob("*.log*")) if self.log_dir.exists() else ():
                archive.write(log, f"logs/{log.name}")

    @staticmethod
    def _checksum(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _markdown(report: Mapping[str, Any]) -> str:
        environment = report["environment"]
        lines = ["# GenealogyDB Release Report", "", "## Environment"] + [f"- **{key}**: {value}" for key, value in environment.items()] + ["", "## Self-check"]
        lines.extend(f"- [{'OK' if item['passed'] else 'FAIL'}] **{item['name']}**: {item['detail']}" for item in report["checks"])
        return "\n".join(lines) + "\n"

    def _html(self, report: Mapping[str, Any]) -> str:
        markdown = self._markdown(report)
        return "<!doctype html><html><meta charset=\"utf-8\"><title>GenealogyDB Release Report</title><body><pre>" + html.escape(markdown) + "</pre></body></html>"
