"""Safe beta-readiness remediation actions without genealogy-record changes."""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import sqlite3
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from build_info import APP_VERSION
from config import DATA_DIR, LOG_DIR, PROJECT_ROOT, USER_CONFIG_PATH
from database import supported_schema_requirements
from performance_service import PerformanceService


@dataclass(frozen=True)
class DatabaseDiagnostic:
    path: str
    classification: str
    exists: bool
    size_bytes: int
    writable: bool
    tables: tuple[str, ...]
    missing_mandatory: tuple[str, ...]
    missing_optional: tuple[str, ...]
    integrity_result: str
    detail: str


@dataclass(frozen=True)
class RemediationCheck:
    name: str
    status: str
    detail: str
    data_effect: str


@dataclass(frozen=True)
class RemediationReport:
    database: DatabaseDiagnostic
    checks: tuple[RemediationCheck, ...]
    recommendation: str

    @property
    def blockers(self):
        return tuple(item for item in self.checks if item.status == "blocker")

    @property
    def warnings(self):
        return tuple(item for item in self.checks if item.status == "warning")


class BetaRemediationService:
    """Diagnose and remediate readiness only through explicit safe actions."""

    VERSION = APP_VERSION

    def __init__(
        self,
        database_path: str | Path,
        *,
        project_root: str | Path = PROJECT_ROOT,
        data_dir: str | Path = DATA_DIR,
        log_dir: str | Path = LOG_DIR,
        config_path: str | Path = USER_CONFIG_PATH,
    ) -> None:
        self.database_path = Path(database_path).expanduser()
        self.project_root = Path(project_root)
        self.data_dir = Path(data_dir)
        self.log_dir = Path(log_dir)
        self.config_path = Path(config_path)
        self.report_dir = self.project_root / "release" / "beta-readiness" / "remediation"
        self.scaling_path = self.data_dir / "ui_state" / "scaling_check.json"
        self.mandatory_tables, self.optional_tables = supported_schema_requirements()

    def diagnose_database(self, database_path: str | Path | None = None) -> DatabaseDiagnostic:
        path = Path(database_path or self.database_path).expanduser()
        if not path.exists():
            return DatabaseDiagnostic(str(path), "missing database", False, 0, False, (), tuple(sorted(self.mandatory_tables)), tuple(sorted(self.optional_tables)), "not run", "Configured database file does not exist.")
        if not path.is_file():
            return DatabaseDiagnostic(str(path), "unsupported schema", True, 0, False, (), tuple(sorted(self.mandatory_tables)), tuple(sorted(self.optional_tables)), "not run", "Configured database path is not a file.")
        size = path.stat().st_size
        writable = os.access(path, os.W_OK)
        try:
            uri = f"file:{path.resolve().as_posix()}?mode=ro"
            connection = sqlite3.connect(uri, uri=True)
            try:
                integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
                tables = tuple(sorted(row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'") if not row[0].startswith("sqlite_")))
            finally:
                connection.close()
        except sqlite3.DatabaseError as error:
            return DatabaseDiagnostic(str(path), "corrupt database", True, size, writable, (), tuple(sorted(self.mandatory_tables)), tuple(sorted(self.optional_tables)), "failed", str(error))
        missing_mandatory = tuple(sorted(set(self.mandatory_tables) - set(tables)))
        missing_optional = tuple(sorted(set(self.optional_tables) - set(tables)))
        if integrity.upper() != "OK":
            classification, detail = "corrupt database", f"SQLite integrity_check: {integrity}"
        elif not tables:
            classification, detail = "empty SQLite file", "SQLite file contains no application tables."
        elif missing_mandatory:
            classification, detail = "uninitialized database", f"Missing mandatory tables: {', '.join(missing_mandatory)}"
        elif any(not columns.issubset(self._columns(path, table)) for table, columns in self.mandatory_tables.items()):
            classification, detail = "unsupported schema", "Mandatory tables do not contain the required columns."
        elif missing_optional:
            classification, detail = "valid GenealogyDB database", f"Optional feature tables unavailable: {', '.join(missing_optional)}"
        else:
            classification, detail = "valid GenealogyDB database", "All mandatory and optional application tables are present."
        return DatabaseDiagnostic(str(path), classification, True, size, writable, tables, missing_mandatory, missing_optional, integrity, detail)

    def analyze(self) -> RemediationReport:
        database = self.diagnose_database()
        checks = [
            self._database_check(database), self._integrity_check(database), self._backup_check(database),
            self._version_check(), self._logs_check(), self._baseline_check(), self._scaling_check(),
        ]
        checks = tuple(sorted(checks, key=lambda item: item.name))
        return RemediationReport(database, checks, self.classify(checks))

    @staticmethod
    def classify(checks) -> str:
        if any(item.status == "blocker" for item in checks):
            return "NOT READY"
        if any(item.status == "warning" for item in checks):
            return "READY WITH WARNINGS"
        return "READY"

    def validate_working_database(self, selected_path: str | Path) -> DatabaseDiagnostic:
        diagnostic = self.diagnose_database(selected_path)
        if diagnostic.classification != "valid GenealogyDB database" or diagnostic.missing_mandatory:
            raise ValueError(diagnostic.detail)
        return diagnostic

    def select_working_database(self, selected_path: str | Path, *, confirmed: bool) -> DatabaseDiagnostic:
        diagnostic = self.validate_working_database(selected_path)
        if not confirmed:
            return diagnostic
        config = self._load_config()
        config["database_path"] = str(Path(selected_path).expanduser().resolve())
        self._atomic_json(self.config_path, config)
        return diagnostic

    def verify_integrity(self) -> DatabaseDiagnostic:
        return self.diagnose_database()

    def verify_temporary_backup(self) -> None:
        diagnostic = self.diagnose_database()
        if diagnostic.classification != "valid GenealogyDB database" or diagnostic.missing_mandatory:
            raise ValueError(diagnostic.detail)
        temporary = None
        try:
            descriptor, name = tempfile.mkstemp(prefix="genealogydb-beta-backup-", suffix=".sqlite")
            os.close(descriptor)
            temporary = Path(name)
            source = sqlite3.connect(self.database_path)
            target = sqlite3.connect(temporary)
            try:
                source.backup(target)
            finally:
                target.close()
                source.close()
            result = self.diagnose_database(temporary)
            if result.classification != "valid GenealogyDB database" or result.integrity_result.upper() != "OK":
                raise ValueError(result.detail)
        finally:
            if temporary:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def synchronize_version_metadata(self) -> tuple[Path, ...]:
        replacements = {
            self.project_root / "build_info.py": (r'APP_VERSION = "[^"]+"', f'APP_VERSION = "{self.VERSION}"'),
            self.project_root / "installer" / "GenealogyDB.iss": (r'#define MyAppVersion "[^"]+"', f'#define MyAppVersion "{self.VERSION}"'),
        }
        changed = []
        for path, (pattern, replacement) in replacements.items():
            text = path.read_text(encoding="utf-8")
            updated, count = re.subn(pattern, replacement, text, count=1)
            if not count:
                raise ValueError(f"Version anchor not found: {path.name}")
            self._atomic_text(path, updated)
            changed.append(path)
        for path, marker in ((self.project_root / "USER_MANUAL.md", "# GenealogyDB User Manual"), (self.project_root / "CHANGELOG.md", "# Changelog")):
            text = path.read_text(encoding="utf-8")
            if self.VERSION not in text:
                self._atomic_text(path, text.replace(marker, f"{marker}\n\nVersion: {self.VERSION}", 1))
                changed.append(path)
        return tuple(changed)

    def archive_logs(self) -> tuple[Path, ...]:
        archive_dir = self.data_dir / "logs" / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        archived = []
        for source in sorted(self.log_dir.glob("*.log*")) if self.log_dir.exists() else ():
            destination = archive_dir / f"{source.stem}-{timestamp}{source.suffix}"
            shutil.copy2(source, destination)
            archived.append(destination)
        return tuple(archived)

    def create_performance_baseline(self) -> Path:
        service = PerformanceService(self.data_dir)
        service.run_quick_benchmarks()
        return service.save_baseline()

    def record_scaling_check(self, scale: int, *, toolbar_fits: bool, menus_readable: bool, dialogs_usable: bool, notes: str = "") -> Path:
        records = self._load_scaling()
        records[str(int(scale))] = {
            "scale": int(scale), "tested_at": datetime.now(timezone.utc).isoformat(),
            "main_toolbar_fits": bool(toolbar_fits), "menus_readable": bool(menus_readable),
            "dialogs_usable": bool(dialogs_usable), "notes": str(notes),
        }
        self._atomic_json(self.scaling_path, records)
        return self.scaling_path

    def export(self, report: RemediationReport, report_format: str) -> Path:
        suffix = {"markdown": "md", "html": "html", "json": "json"}.get(report_format)
        if not suffix:
            raise ValueError(f"Unsupported report format: {report_format}")
        self.report_dir.mkdir(parents=True, exist_ok=True)
        path = self.report_dir / f"remediation.{suffix}"
        payload = {"recommendation": report.recommendation, "database": asdict(report.database), "checks": [asdict(item) for item in report.checks]}
        if report_format == "json":
            content = json.dumps(payload, ensure_ascii=False, indent=2)
        else:
            markdown = self._markdown(report)
            content = markdown if report_format == "markdown" else f"<!doctype html><html><meta charset=\"utf-8\"><body><pre>{html.escape(markdown)}</pre></body></html>"
        self._atomic_text(path, content)
        return path

    def _database_check(self, database):
        status = "passed" if database.classification == "valid GenealogyDB database" else "blocker"
        if database.missing_optional and status == "passed":
            status = "warning"
        return RemediationCheck("Database classification", status, database.detail, "Reads genealogy data only.")

    def _integrity_check(self, database):
        status = "passed" if database.integrity_result.upper() == "OK" else "blocker"
        return RemediationCheck("Read-only integrity check", status, f"integrity_check: {database.integrity_result}", "Reads genealogy data only.")

    def _backup_check(self, database):
        try:
            self.verify_temporary_backup()
            return RemediationCheck("Verified temporary backup", "passed", "SQLite online backup validated in a temporary directory.", "Reads genealogy data and creates/deletes a temporary diagnostic file.")
        except (OSError, ValueError, sqlite3.DatabaseError) as error:
            return RemediationCheck("Verified temporary backup", "blocker", str(error), "Reads genealogy data only when backup cannot be verified.")

    def _version_check(self):
        paths = {
            "build_info.py": self.project_root / "build_info.py",
            "installer": self.project_root / "installer" / "GenealogyDB.iss",
            "user manual": self.project_root / "USER_MANUAL.md",
            "release notes": self.project_root / "CHANGELOG.md",
            "application title": self.project_root / "viewer.py",
            "release center": self.project_root / "release_center_service.py",
            "PyInstaller specification": self.project_root / "GenealogyDB.spec",
        }
        missing = []
        for label, path in paths.items():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                missing.append(f"{label}: missing")
                continue
            if label in {"application title", "release center"} and "APP_VERSION" not in text:
                missing.append(f"{label}: shared version not referenced")
            elif label == "PyInstaller specification" and "GenealogyDB" not in text:
                missing.append(f"{label}: product metadata missing")
            elif label not in {"application title", "release center", "PyInstaller specification"} and self.VERSION not in text:
                missing.append(f"{label}: {self.VERSION} not found")
        return RemediationCheck("Version metadata", "passed" if not missing else "warning", "Version metadata is synchronized." if not missing else "; ".join(missing), "Reads source/version files only.")

    def _logs_check(self):
        historical = [path.name for path in self.log_dir.glob("*.log*") if "Traceback (most recent call last)" in path.read_text(encoding="utf-8", errors="replace")]
        return RemediationCheck("Historical crash logs", "warning" if historical else "passed", "Historical tracebacks: " + ", ".join(historical) if historical else "No historical tracebacks found.", "Reads diagnostic logs only.")

    def _baseline_check(self):
        path = self.data_dir / "performance" / "baseline.json"
        return RemediationCheck("Performance baseline", "passed" if path.is_file() else "warning", "Baseline is available." if path.is_file() else "No saved synthetic performance baseline.", "Reads diagnostic sidecars only.")

    def _scaling_check(self):
        records = self._load_scaling()
        complete = all(str(scale) in records and all(records[str(scale)].get(key) for key in ("main_toolbar_fits", "menus_readable", "dialogs_usable")) for scale in (125, 150, 175))
        return RemediationCheck("Manual scaling checklist", "passed" if complete else "warning", "Scaling checklist is complete." if complete else "Record 125%, 150%, and 175% manual checks.", "Reads UI-state sidecar only.")

    @staticmethod
    def _columns(path, table):
        uri = f"file:{Path(path).resolve().as_posix()}?mode=ro"
        connection = sqlite3.connect(uri, uri=True)
        try:
            return {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
        finally:
            connection.close()

    def _load_config(self):
        try:
            value = json.loads(self.config_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, json.JSONDecodeError):
            return {}

    def _load_scaling(self):
        try:
            value = json.loads(self.scaling_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, json.JSONDecodeError):
            return {}

    @staticmethod
    def _atomic_text(path, content):
        path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)

    def _atomic_json(self, path, value):
        self._atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2))

    @staticmethod
    def _markdown(report):
        lines = ["# GenealogyDB Beta Readiness Remediation", "", f"## Recommendation\n\n{report.recommendation}", "", "## Database", f"- Path: {report.database.path}", f"- Classification: {report.database.classification}", f"- Tables: {', '.join(report.database.tables) or '-'}", "", "## Checks"]
        lines.extend(f"- [{item.status.upper()}] **{item.name}**: {item.detail} ({item.data_effect})" for item in report.checks)
        return "\n".join(lines) + "\n"
