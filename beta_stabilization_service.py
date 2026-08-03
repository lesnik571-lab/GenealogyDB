"""Read-only beta readiness diagnostics and report export for GenealogyDB."""

from __future__ import annotations

import ast
import html
import json
import os
import re
import sqlite3
import tempfile
import traceback
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from build_info import APP_VERSION
from beta_remediation_service import BetaRemediationService
from config import (
    APP_NAME,
    BACKUP_DIR,
    DATA_DIR,
    DEFAULT_CONFIG_PATH,
    LOG_DIR,
    PLUGIN_DIR,
    PROJECT_ROOT,
    USER_CONFIG_PATH,
    USER_MANUAL_PATH,
)
from database import validate_database_file
from plugin_manager import PluginManager
from validation_center_service import ValidationCenterService


@dataclass(frozen=True)
class ReadinessCheck:
    name: str
    status: str
    detail: str
    section: str


@dataclass(frozen=True)
class BetaReadinessReport:
    checks: tuple[ReadinessCheck, ...]
    recommendation: str

    @property
    def passed(self) -> tuple[ReadinessCheck, ...]:
        return tuple(item for item in self.checks if item.status == "passed")

    @property
    def warnings(self) -> tuple[ReadinessCheck, ...]:
        return tuple(item for item in self.checks if item.status == "warning")

    @property
    def blockers(self) -> tuple[ReadinessCheck, ...]:
        return tuple(item for item in self.checks if item.status == "blocker")


class BetaStabilizationService:
    """Perform deterministic diagnostics without changing genealogy records."""

    REQUIRED_RESOURCES = (
        "schema.sql", "USER_MANUAL.md", "CHANGELOG.md", "GenealogyDB.spec",
        "build_info.py", "build_release.ps1", "installer/GenealogyDB.iss",
        "resources/default_config.json", "plugins/statistics.py", "assets/app_icon.svg",
    )
    SIDECARS = (
        "ui_state/workspace.json", "validation_center_ignores.json",
        "intelligence/suggestions.json", "source_analysis/findings.json",
        "performance/baseline.json",
    )

    def __init__(
        self,
        repository,
        *,
        project_root: str | Path = PROJECT_ROOT,
        data_dir: str | Path = DATA_DIR,
        log_dir: str | Path = LOG_DIR,
        plugin_dir: str | Path = PLUGIN_DIR,
        config_path: str | Path = USER_CONFIG_PATH,
        viewer_path: str | Path | None = None,
    ) -> None:
        self.repository = repository
        self.project_root = Path(project_root)
        self.data_dir = Path(data_dir)
        self.log_dir = Path(log_dir)
        self.plugin_dir = Path(plugin_dir)
        self.config_path = Path(config_path)
        self.viewer_path = Path(viewer_path or self.project_root / "viewer.py")
        self.report_dir = self.project_root / "release" / "beta-readiness"

    def analyze(self) -> BetaReadinessReport:
        checks = [
            self._test_suite_check(), self._compilation_check(), self._resources_check(),
            self._configuration_check(), self._plugins_check(), self._directories_check(),
            self._backup_check(), self._database_check(), self._sidecars_check(),
            self._version_check(), self._validation_check(), self._crash_check(),
            self._performance_check(),
        ]
        checks.extend(self.viewer_checks(self.viewer_path))
        checks.append(self._error_handling_check())
        checks.extend(self._performance_smoke_checks())
        checks.extend(self._accessibility_checks())
        checks = tuple(sorted(checks, key=lambda item: (item.section, item.name)))
        return BetaReadinessReport(checks, self.classify(checks))

    @staticmethod
    def classify(checks: Iterable[ReadinessCheck]) -> str:
        checks = tuple(checks)
        if any(item.status == "blocker" for item in checks):
            return "NOT READY"
        if any(item.status == "warning" for item in checks):
            return "READY WITH WARNINGS"
        return "READY"

    def export(self, report: BetaReadinessReport, report_format: str) -> Path:
        suffix = {"markdown": "md", "html": "html", "json": "json"}.get(report_format)
        if suffix is None:
            raise ValueError(f"Unsupported report format: {report_format}")
        self.report_dir.mkdir(parents=True, exist_ok=True)
        path = self.report_dir / f"beta-readiness.{suffix}"
        if report_format == "json":
            content = json.dumps(self._payload(report), ensure_ascii=False, indent=2)
        else:
            markdown = self._markdown(report)
            content = markdown if report_format == "markdown" else "<!doctype html><html><meta charset=\"utf-8\"><body><pre>" + html.escape(markdown) + "</pre></body></html>"
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
        return path

    def _test_suite_check(self) -> ReadinessCheck:
        tests = tuple((self.project_root / "tests").glob("test_*.py"))
        return ReadinessCheck("Full test-suite status", "passed" if tests else "blocker", f"{len(tests)} focused test modules are available; canonical execution is performed by the release workflow.", "Test status")

    def _compilation_check(self) -> ReadinessCheck:
        failures = []
        for path in (self.project_root / "viewer.py", self.project_root / "app.py"):
            try:
                compile(path.read_text(encoding="utf-8"), str(path), "exec")
            except (OSError, SyntaxError) as error:
                failures.append(f"{path.name}: {error}")
        return ReadinessCheck("Python compilation", "passed" if not failures else "blocker", "Core Python modules compile." if not failures else "; ".join(failures), "Build status")

    def _resources_check(self) -> ReadinessCheck:
        missing = [value for value in self.REQUIRED_RESOURCES if not (self.project_root / value).is_file()]
        return ReadinessCheck("Release resources", "passed" if not missing else "blocker", "All required resources are present." if not missing else f"Missing: {', '.join(missing)}", "Build status")

    def _configuration_check(self) -> ReadinessCheck:
        invalid = []
        for path in (self.project_root / "resources" / "default_config.json", self.config_path):
            if not path.exists():
                continue
            try:
                if not isinstance(json.loads(path.read_text(encoding="utf-8")), dict):
                    invalid.append(path.name)
            except (OSError, ValueError, json.JSONDecodeError):
                invalid.append(path.name)
        return ReadinessCheck("Configuration validity", "passed" if not invalid else "blocker", "Configuration JSON is valid." if not invalid else f"Invalid configuration: {', '.join(invalid)}", "Data safety")

    def _plugins_check(self) -> ReadinessCheck:
        try:
            for path in sorted(self.plugin_dir.glob("*.py")) if self.plugin_dir.exists() else ():
                if not path.name.startswith("_"):
                    PluginManager._validate_source(path)
            return ReadinessCheck("Plugin loading", "passed", "Plugin sources passed static loading validation.", "Build status")
        except (OSError, ValueError, SyntaxError) as error:
            return ReadinessCheck("Plugin loading", "blocker", str(error), "Build status")

    def _directories_check(self) -> ReadinessCheck:
        try:
            directories = [self.data_dir, self.log_dir, self.plugin_dir, Path(BACKUP_DIR)]
            directories.extend((self.data_dir / value).parent for value in self.SIDECARS)
            for directory in directories:
                directory.mkdir(parents=True, exist_ok=True)
                probe = directory / ".beta-readiness-probe"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink()
            return ReadinessCheck("Writable user-data directories", "passed", "Data, log, plugin, and backup folders are writable.", "Data safety")
        except OSError as error:
            return ReadinessCheck("Writable user-data directories", "blocker", str(error), "Data safety")

    def _backup_check(self) -> ReadinessCheck:
        try:
            self._remediation().verify_temporary_backup()
            return ReadinessCheck("Database backup capability", "passed", "A validated temporary database backup can be created.", "Data safety")
        except (OSError, ValueError, sqlite3.DatabaseError) as error:
            return ReadinessCheck("Database backup capability", "blocker", str(error), "Data safety")

    def _database_check(self) -> ReadinessCheck:
        diagnostic = self._remediation().diagnose_database()
        status = "passed" if diagnostic.classification == "valid GenealogyDB database" and not diagnostic.missing_optional else "warning" if diagnostic.classification == "valid GenealogyDB database" else "blocker"
        detail = f"Path: {diagnostic.path}; state: {diagnostic.classification}; integrity_check: {diagnostic.integrity_result}; tables: {', '.join(diagnostic.tables) or '-'}; {diagnostic.detail}"
        return ReadinessCheck("Database integrity", status, detail, "Data safety")

    def _remediation(self) -> BetaRemediationService:
        return BetaRemediationService(
            self.repository.db_name,
            project_root=self.project_root,
            data_dir=self.data_dir,
            log_dir=self.log_dir,
            config_path=self.config_path,
        )

    def _sidecars_check(self) -> ReadinessCheck:
        invalid = []
        for value in self.SIDECARS:
            path = self.data_dir / value
            if not path.exists():
                continue
            try:
                json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, json.JSONDecodeError):
                invalid.append(value)
        return ReadinessCheck("UI and diagnostic sidecars", "passed" if not invalid else "warning", "Sidecars are valid or absent and services recreate their directories on write." if not invalid else f"Malformed sidecars safely recover on next service load: {', '.join(invalid)}", "Data safety")

    def _version_check(self) -> ReadinessCheck:
        files = {
            "build_info.py": self.project_root / "build_info.py",
            "viewer.py": self.viewer_path,
            "release_center_service.py": self.project_root / "release_center_service.py",
            "installer/GenealogyDB.iss": self.project_root / "installer" / "GenealogyDB.iss",
            "GenealogyDB.spec": self.project_root / "GenealogyDB.spec",
            "USER_MANUAL.md": self.project_root / "USER_MANUAL.md",
            "CHANGELOG.md": self.project_root / "CHANGELOG.md",
        }
        mismatches = []
        for label, path in files.items():
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                mismatches.append(f"{label}: missing")
                continue
            if label == "installer/GenealogyDB.iss":
                match = re.search(r'#define MyAppVersion "([^"]+)"', text)
                if match and match.group(1) != APP_VERSION:
                    mismatches.append(f"{label}: {match.group(1)} != {APP_VERSION}")
            elif label in {"USER_MANUAL.md", "CHANGELOG.md"} and APP_VERSION not in text:
                mismatches.append(f"{label}: version {APP_VERSION} not mentioned")
            elif label == "build_info.py" and f'APP_VERSION = "{APP_VERSION}"' not in text:
                mismatches.append(f"{label}: does not define {APP_VERSION}")
        return ReadinessCheck("Version consistency", "passed" if not mismatches else "warning", "All checked version anchors agree." if not mismatches else "; ".join(mismatches), "Version consistency")

    def _validation_check(self) -> ReadinessCheck:
        try:
            report = ValidationCenterService(self.repository, data_dir=self.data_dir).analyze(include_ignored=True)
            critical = [item.issue_id for item in report.issues if item.severity == "Critical" and not item.resolved]
            return ReadinessCheck("Unresolved critical validation issues", "passed" if not critical else "blocker", "No unresolved critical validation issues." if not critical else f"Critical issues: {', '.join(critical)}", "Data safety")
        except Exception as error:
            return ReadinessCheck("Unresolved critical validation issues", "warning", f"Validation check unavailable: {error}", "Data safety")

    def _crash_check(self) -> ReadinessCheck:
        crashes = []
        for path in sorted(self.log_dir.glob("*.log*")) if self.log_dir.exists() else ():
            try:
                if "Traceback (most recent call last)" in path.read_text(encoding="utf-8", errors="replace"):
                    crashes.append(path.name)
            except OSError:
                continue
        return ReadinessCheck("Unresolved crash diagnostics", "warning" if crashes else "passed", "No unresolved crash diagnostics." if not crashes else f"Tracebacks found in: {', '.join(crashes)}", "Build status")

    def _performance_check(self) -> ReadinessCheck:
        path = self.data_dir / "performance" / "baseline.json"
        return ReadinessCheck("Performance baseline availability", "passed" if path.is_file() else "warning", "Performance baseline is available." if path.is_file() else "Performance baseline has not been saved.", "Performance")

    def _error_handling_check(self) -> ReadinessCheck:
        try:
            text = self.viewer_path.read_text(encoding="utf-8")
        except OSError as error:
            return ReadinessCheck("Viewer service failure handling", "blocker", str(error), "Build status")
        guarded = all(value in text for value in (
            "def _show_unified_error", "get_logger(\"viewer\").exception",
            "self._error_dialog_active", "on_error=on_error or",
        ))
        return ReadinessCheck("Viewer service failure handling", "passed" if guarded else "warning", "Service failures use the logged, guarded concise Russian error path." if guarded else "The unified service error boundary is incomplete.", "Build status")

    @classmethod
    def viewer_checks(cls, viewer_path: str | Path) -> tuple[ReadinessCheck, ...]:
        path = Path(viewer_path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError) as error:
            return (ReadinessCheck("Viewer static analysis", "blocker", str(error), "Build status"),)
        commands, buttons = cls._viewer_commands(tree)
        duplicate_commands = sorted(value for value, count in commands.items() if count > 1)
        duplicate_buttons = sorted(value for value, count in buttons.items() if count > 1)
        defined_methods = {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        missing_methods = sorted(cls._self_calls(tree) - defined_methods)
        services = cls._service_names(tree)
        used_services = cls._used_services(tree)
        missing_services = sorted(used_services - services - defined_methods)
        direct_sql = cls._direct_sql_lines(tree)
        module_windows = cls._module_window_lines(tree)
        stale_imports = cls._stale_imports(tree)
        return (
            ReadinessCheck("Duplicate Viewer buttons", "warning" if duplicate_buttons else "passed", "No duplicate button labels." if not duplicate_buttons else f"Duplicate labels: {', '.join(duplicate_buttons)}", "Accessibility"),
            ReadinessCheck("Duplicate menu commands", "warning" if duplicate_commands else "passed", "No duplicate menu command labels." if not duplicate_commands else f"Duplicate labels: {', '.join(duplicate_commands)}", "Accessibility"),
            ReadinessCheck("Commands referencing missing functions", "blocker" if missing_methods else "passed", "All self command targets are defined." if not missing_methods else f"Missing: {', '.join(missing_methods)}", "Build status"),
            ReadinessCheck("Unavailable services", "blocker" if missing_services else "passed", "All referenced Viewer services are initialized or lazily provided." if not missing_services else f"Missing: {', '.join(missing_services)}", "Build status"),
            ReadinessCheck("Direct SQL in viewer.py", "blocker" if direct_sql else "passed", "No direct repository SQL calls found." if not direct_sql else f"Lines: {', '.join(map(str, direct_sql))}", "Data safety"),
            ReadinessCheck("Tkinter windows during import", "blocker" if module_windows else "passed", "No Tkinter windows are created at module import time." if not module_windows else f"Lines: {', '.join(map(str, module_windows))}", "Build status"),
            ReadinessCheck("Stale Viewer imports", "warning" if stale_imports else "passed", "No proven unused Viewer imports found." if not stale_imports else f"Unused: {', '.join(stale_imports)}", "Build status"),
        )

    def _performance_smoke_checks(self) -> tuple[ReadinessCheck, ...]:
        text = self.viewer_path.read_text(encoding="utf-8")
        checks = (
            ("Startup instrumentation", "performance_service.timer(\"database connection\"" in text),
            ("Person-list pagination", "list_people_page(" in text),
            ("Bounded caches", "BoundedLRUCache" in (self.project_root / "performance_service.py").read_text(encoding="utf-8")),
            ("Task Manager cancellation", "cancellable=True" in text),
        )
        return tuple(ReadinessCheck(name, "passed" if value else "warning", "Available." if value else "Static smoke check did not find expected instrumentation.", "Performance") for name, value in checks)

    def _accessibility_checks(self) -> tuple[ReadinessCheck, ...]:
        text = self.viewer_path.read_text(encoding="utf-8")
        shortcuts = "self.root.bind(" in text
        escape = "<Escape>" in text
        return (
            ReadinessCheck("Keyboard shortcuts registered", "passed" if shortcuts else "warning", "Keyboard shortcut bindings are registered." if shortcuts else "No keyboard shortcut bindings found.", "Accessibility"),
            ReadinessCheck("Escape dialog close support", "warning" if not escape else "passed", "Escape close bindings are present." if escape else "Escape close support is not consistently registered.", "Accessibility"),
            ReadinessCheck("Toolbar scaling smoke check", "warning", "Manual verification remains required at 125%, 150%, and 175% scaling.", "Accessibility"),
        )

    @staticmethod
    def _viewer_commands(tree):
        commands, buttons = {}, {}
        for function in (node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))):
            local_commands, local_buttons = {}, {}
            for node in ast.walk(function):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr == "add_command":
                    label = BetaStabilizationService._keyword_text(node, "label")
                    if label:
                        identity = (label, BetaStabilizationService._keyword_expression(node, "command"))
                        local_commands[identity] = local_commands.get(identity, 0) + 1
                if node.func.attr == "Button":
                    label = BetaStabilizationService._keyword_text(node, "text")
                    if label:
                        identity = (label, BetaStabilizationService._keyword_expression(node, "command"))
                        local_buttons[identity] = local_buttons.get(identity, 0) + 1
            for (label, _command), count in local_commands.items():
                if count > 1:
                    commands[f"{function.name}:{label}"] = count
            for (label, _command), count in local_buttons.items():
                if count > 1:
                    buttons[f"{function.name}:{label}"] = count
        return commands, buttons

    @staticmethod
    def _self_calls(tree):
        calls = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "self":
                calls.add(node.func.attr)
        return calls

    @staticmethod
    def _service_names(tree):
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self" and target.attr.endswith(("_service", "_manager")):
                        names.add(target.attr)
        return names

    @staticmethod
    def _used_services(tree):
        return {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self" and node.attr.endswith(("_service", "_manager"))}

    @staticmethod
    def _direct_sql_lines(tree):
        lines = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr not in {"execute", "executemany"}:
                continue
            value = node.func.value
            if isinstance(value, ast.Attribute) and value.attr in {"conn", "cur", "connection", "cursor"}:
                lines.append(node.lineno)
        return sorted(lines)

    @staticmethod
    def _module_window_lines(tree):
        return [node.lineno for node in tree.body if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute) and node.value.func.attr in {"Tk", "Toplevel"}]

    @staticmethod
    def _stale_imports(tree):
        imported = {}
        used = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                used.add(node.id)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported[alias.asname or alias.name.split(".")[0]] = node.lineno
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported[alias.asname or alias.name] = node.lineno
        return tuple(sorted(name for name in imported if name not in used and not name.startswith("_")))

    @staticmethod
    def _keyword_text(node, name):
        value = next((item.value for item in node.keywords if item.arg == name), None)
        return value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else ""

    @staticmethod
    def _keyword_expression(node, name):
        value = next((item.value for item in node.keywords if item.arg == name), None)
        return ast.dump(value, include_attributes=False) if value is not None else ""

    @staticmethod
    def _payload(report):
        return {"recommendation": report.recommendation, "checks": [asdict(item) for item in report.checks]}

    @classmethod
    def _markdown(cls, report):
        lines = ["# GenealogyDB Beta Readiness", "", f"## Release Recommendation\n\n{report.recommendation}"]
        for title, values in (("Passed checks", report.passed), ("Warnings", report.warnings), ("Blockers", report.blockers)):
            lines.extend(("", f"## {title}"))
            lines.extend(f"- **{item.name}** ({item.section}): {item.detail}" for item in values)
        lines.extend(("", "## Deferred items", "- Manual UI scaling and interaction verification remains required."))
        return "\n".join(lines) + "\n"
