"""Static final-release audit for the GenealogyDB desktop application."""

from __future__ import annotations

import ast
import html
import io
import json
import tokenize
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path

from build_info import APP_VERSION


PASS = "PASS"
BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class FinalAuditCheck:
    check_id: str
    area: str
    status: str
    evidence: str
    reason: str = ""


@dataclass(frozen=True)
class FinalAuditReport:
    version: str
    checks: tuple[FinalAuditCheck, ...]
    recommendation: str

    @property
    def blockers(self):
        return tuple(check for check in self.checks if check.status == BLOCKED)


class FinalAuditService:
    """Inspect release inputs without creating a Tk window or modifying data."""

    REQUIRED_PACKAGING_FILES = (
        "GenealogyDB.spec", "installer/GenealogyDB.iss", "assets/app.ico",
        "assets/app_icon.svg", "README.md", "USER_MANUAL.md", "CHANGELOG.md",
        "LICENSE", "build_info.py", "requirements-build.txt", "schema.sql",
    )
    PRODUCTION_EXCLUSIONS = {".venv", "tests", "GenealogyDB_recovery_wizard_integrated_2026-08-02", "data", "release"}

    def __init__(self, *, project_root: str | Path = Path(__file__).resolve().parent):
        self.project_root = Path(project_root)
        self.viewer_path = self.project_root / "viewer.py"
        self.report_dir = self.project_root / "release" / "final-audit"

    def audit(self) -> FinalAuditReport:
        tree = ast.parse(self.viewer_path.read_text(encoding="utf-8"), filename=str(self.viewer_path))
        checks = [
            self._interactive_check(tree),
            self._help_check(tree),
            self._export_check(tree),
            self._dialog_check(tree),
            self._russian_labels_check(tree),
            self._viewer_safety_check(tree),
            self._service_check(tree),
            self._imports_check(tree),
            self._hygiene_check(),
            self._packaging_check(),
            self._version_check(),
        ]
        report = FinalAuditReport(APP_VERSION, tuple(checks), self.recommendation(checks))
        self.export_all(report)
        return report

    @staticmethod
    def recommendation(checks) -> str:
        return "NOT READY" if any(check.status == BLOCKED for check in checks) else "READY FOR 2.1.0"

    def export_all(self, report: FinalAuditReport) -> tuple[Path, Path, Path]:
        self.report_dir.mkdir(parents=True, exist_ok=True)
        outputs = (
            self.report_dir / "final-audit.json",
            self.report_dir / "final-audit.md",
            self.report_dir / "final-audit.html",
        )
        markdown = self._markdown(report)
        contents = (
            json.dumps(self._payload(report), ensure_ascii=False, indent=2, sort_keys=True),
            markdown,
            "<!doctype html><html><meta charset=\"utf-8\"><body><pre>" + html.escape(markdown) + "</pre></body></html>",
        )
        for path, content in zip(outputs, contents):
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(path)
        return outputs

    def _interactive_check(self, tree):
        methods = self._methods(tree)
        targets = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr == "add_command" or node.func.attr == "Button":
                target = self._keyword(node, "command")
                if target is not None:
                    targets.extend(self._self_references(target))
            elif node.func.attr == "bind" and len(node.args) >= 2:
                targets.extend(self._self_references(node.args[1]))
        missing = sorted(set(targets) - methods)
        return self._result("viewer.interactions", "Menus, toolbar buttons, and keyboard shortcuts", not missing, f"{len(targets)} command targets checked", ", ".join(missing))

    def _help_check(self, tree):
        entries = []
        targets = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr != "add_command":
                continue
            if isinstance(node.func.value, ast.Name) and node.func.value.id == "help_menu":
                label = self._constant(self._keyword(node, "label"))
                if label:
                    entries.append(label)
                    targets.extend(self._self_references(self._keyword(node, "command")))
        missing = sorted(set(targets) - self._methods(tree))
        return self._result("viewer.help", "Help entries have commands", bool(entries) and not missing, f"entries: {', '.join(entries)}", ", ".join(missing) or "no Help entries")

    def _export_check(self, tree):
        export_methods = sorted(node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name.startswith("_export_"))
        dialogs = sum(1 for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "asksaveasfilename")
        return self._result("viewer.exports", "Export entries have handlers", bool(export_methods) and dialogs > 0, f"{len(export_methods)} export handlers; {dialogs} save dialogs", "no export handlers or save dialogs")

    def _dialog_check(self, tree):
        empty = []
        titles = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "title":
                titles += 1
                if node.args and isinstance(node.args[0], ast.Constant) and not str(node.args[0].value).strip():
                    empty.append(node.lineno)
        return self._result("viewer.dialog-titles", "Dialog titles are non-empty", not empty, f"{titles} title calls", f"empty title lines: {empty}")

    def _russian_labels_check(self, tree):
        labels = [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str) and any("\u0400" <= char <= "\u04ff" for char in node.value)]
        malformed = [value for value in labels if unicodedata.normalize("NFC", value) != value or "�" in value]
        return self._result("viewer.russian-labels", "Russian labels are valid Unicode", not malformed, f"{len(labels)} Russian labels checked", "; ".join(malformed[:3]))

    def _viewer_safety_check(self, tree):
        direct_sql = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr not in {"execute", "executemany"}:
                continue
            if isinstance(node.func.value, ast.Attribute) and node.func.value.attr in {"conn", "cur", "connection", "cursor"}:
                direct_sql.append(node.lineno)
        return self._result("viewer.no-direct-sql", "Viewer contains no direct SQL", not direct_sql, "AST scan", f"direct SQL lines: {direct_sql}")

    def _service_check(self, tree):
        init = next((node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == "__init__"), None)
        counts = {}
        if init:
            for node in ast.walk(init):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Name) and target.value.id == "self" and target.attr.endswith(("_service", "_manager")):
                            counts[target.attr] = counts.get(target.attr, 0) + 1
        duplicates = sorted(name for name, count in counts.items() if count > 1)
        referenced = {
            attribute.attr
            for function in ast.walk(tree)
            if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)) and function.name != "__init__"
            for attribute in ast.walk(function)
            if isinstance(attribute, ast.Attribute) and isinstance(attribute.value, ast.Name) and attribute.value.id == "self"
        }
        dead = sorted(name for name in counts if name not in referenced)
        return self._result("viewer.services", "Services instantiate once and remain registered", not duplicates and not dead, f"{len(counts)} startup services", ", ".join(duplicates + dead))

    def _imports_check(self, tree):
        imported = {}
        used = {
            node.id for node in ast.walk(tree)
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported[alias.asname or alias.name.split(".")[0]] = node.lineno
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    imported[alias.asname or alias.name] = node.lineno
        unused = sorted(name for name in imported if name not in used and not name.startswith("_"))
        return self._result("viewer.imports", "Viewer has no statically unused imports", not unused, "AST import/use scan", ", ".join(unused))

    def _hygiene_check(self):
        prohibited = []
        for path in self._production_python_files():
            text = path.read_text(encoding="utf-8")
            comments = "\n".join(
                token.string for token in tokenize.generate_tokens(io.StringIO(text).readline)
                if token.type == tokenize.COMMENT
            )
            for marker in ("TODO", "FIXME", "HACK"):
                if marker in comments:
                    prohibited.append(f"{path.relative_to(self.project_root)}:{marker}")
            if path.name not in {"app.py"}:
                tree = ast.parse(text, filename=str(path))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    if isinstance(node.func, ast.Name) and node.func.id in {"print", "breakpoint"}:
                        prohibited.append(f"{path.relative_to(self.project_root)}:{node.func.id}")
                    if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "pdb":
                        prohibited.append(f"{path.relative_to(self.project_root)}:pdb")
        return self._result("repository.hygiene", "No development markers or debug output remain", not prohibited, "production Python scan", ", ".join(prohibited))

    def _packaging_check(self):
        missing = [path for path in self.REQUIRED_PACKAGING_FILES if not (self.project_root / path).is_file()]
        return self._result("packaging.resources", "Packaging files, resources, and release documentation", not missing, "all required files present" if not missing else "", ", ".join(missing))

    def _version_check(self):
        build_info = (self.project_root / "build_info.py").read_text(encoding="utf-8")
        installer = (self.project_root / "installer" / "GenealogyDB.iss").read_text(encoding="utf-8")
        valid = (
            APP_VERSION == "2.1.0"
            and 'APP_VERSION = "2.1.0"' in build_info
            and 'MyAppVersion "2.1.0"' in installer
        )
        return self._result(
            "packaging.version",
            "Version metadata is synchronized for the 2.1.0 final release",
            valid,
            f"current version: {APP_VERSION}",
            "version anchors are not 2.1.0",
        )

    def _production_python_files(self):
        return tuple(path for path in self.project_root.rglob("*.py") if not any(part in self.PRODUCTION_EXCLUSIONS for part in path.relative_to(self.project_root).parts))

    @staticmethod
    def _methods(tree):
        return {node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}

    @staticmethod
    def _keyword(node, name):
        return next((keyword.value for keyword in node.keywords if keyword.arg == name), None)

    @staticmethod
    def _constant(node):
        return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else ""

    @staticmethod
    def _self_references(node):
        if node is None:
            return []
        references = [
            item.func.attr
            for item in ast.walk(node)
            if isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute)
            and isinstance(item.func.value, ast.Name) and item.func.value.id == "self"
        ]
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
            references.append(node.attr)
        return references

    @staticmethod
    def _result(check_id, area, passed, evidence, reason):
        return FinalAuditCheck(check_id, area, PASS if passed else BLOCKED, evidence, "" if passed else reason)

    @staticmethod
    def _payload(report):
        return {"version": report.version, "recommendation": report.recommendation, "checks": [asdict(check) for check in report.checks]}

    @staticmethod
    def _markdown(report):
        lines = ["# GenealogyDB Final Release Audit", "", f"Recommendation: **{report.recommendation}**", f"Version staged: `{report.version}`", "", "| ID | Area | Status | Evidence | Reason |", "| --- | --- | --- | --- | --- |"]
        lines.extend(f"| {check.check_id} | {check.area} | {check.status} | {check.evidence} | {check.reason} |" for check in report.checks)
        return "\n".join(lines) + "\n"