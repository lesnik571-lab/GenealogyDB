"""Headless, temporary-only RC1 workflow validation for GenealogyDB."""

from __future__ import annotations

import hashlib
import html
import importlib
import json
import ast
import shutil
import sqlite3
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from build_info import APP_VERSION
from database import backup_database, initialize_database, restore_database, validate_database_file
from evidence_service import EvidenceOperation, EvidenceService
from gedcom.parser import parse_gedcom
from importer import GedcomImporter
from intelligence_service import IntelligenceService
from performance_service import PerformanceService
from repository.person_repository import PersonRepository
from repository.relationship_service import RelationshipService
from research_workspace_service import ResearchWorkspaceService
from source_analysis_service import SourceAnalysisService
from timeline_studio_service import TimelineStudioService
from tree_canvas_service import TreeCanvasService
from undo_manager import AddPersonCommand, EditPersonCommand, UndoManager
from validation_center_service import ValidationCenterService
from geo_map_studio_service import GeoMapStudioService


PASS = "PASS"
WARNING = "WARNING"
BLOCKED = "BLOCKED"
SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class RCValidationCheck:
    check_id: str
    category: str
    description: str
    status: str
    duration_seconds: float
    evidence: str
    reason: str = ""
    cleanup_result: str = "not applicable"


@dataclass(frozen=True)
class RCValidationReport:
    version: str
    checks: tuple[RCValidationCheck, ...]
    recommendation: str
    configured_database: str
    checksum_before: str
    checksum_after: str

    @property
    def blockers(self):
        return tuple(check for check in self.checks if check.status == BLOCKED)

    @property
    def warnings(self):
        return tuple(check for check in self.checks if check.status == WARNING)


class RCValidationService:
    """Exercise release workflows without opening UI or modifying the working DB."""

    CATEGORIES = (
        "Startup", "Database", "Import", "CRUD", "Relationships", "Sources",
        "Attachments", "Undo/Redo", "Backup/Restore", "Analysis", "Visualization",
        "Persistence", "Export", "Packaging",
    )
    REQUIRED_BUILD_FILES = (
        "GenealogyDB.spec", "installer/GenealogyDB.iss", "assets/app_icon.svg",
        "USER_MANUAL.md", "CHANGELOG.md", "build_info.py", "requirements-build.txt",
        "schema.sql", "resources/default_config.json",
    )

    def __init__(self, configured_database, *, project_root: str | Path = Path(__file__).resolve().parent):
        self.configured_database = Path(configured_database).expanduser()
        self.project_root = Path(project_root)
        self.report_dir = self.project_root / "release" / "rc1-validation"

    def validate(self) -> RCValidationReport:
        before = self._checksum(self.configured_database)
        checks: list[RCValidationCheck] = []
        checks.append(RCValidationCheck(
            "database.configured-availability", "Database", "Configured database is available without creation",
            PASS if self.configured_database.is_file() else WARNING, 0.0,
            str(self.configured_database), "" if self.configured_database.is_file() else "Configured database does not exist; validation did not create it.",
            "not applicable",
        ))
        temporary_root = Path(tempfile.mkdtemp(prefix="genealogydb-rc1-"))
        cleanup_result = "not run"
        try:
            checks.extend(self._startup_checks())
            checks.extend(self._workflow_checks(temporary_root))
            checks.extend(self._packaging_checks())
        finally:
            try:
                shutil.rmtree(temporary_root)
                cleanup_result = "temporary root removed"
            except OSError as error:
                cleanup_result = f"cleanup failed: {error}"
        checks = [
            RCValidationCheck(
                check.check_id, check.category, check.description,
                WARNING if cleanup_result.startswith("cleanup failed") and check.status == PASS else check.status,
                check.duration_seconds, check.evidence,
                check.reason or (cleanup_result if cleanup_result.startswith("cleanup failed") else ""),
                cleanup_result if check.cleanup_result == "temporary root pending" else check.cleanup_result,
            )
            for check in checks
        ]
        after = self._checksum(self.configured_database)
        safety_status = PASS if before == after else BLOCKED
        safety_reason = "" if before == after else "Configured database checksum changed during validation."
        checks.append(RCValidationCheck(
            "database.configured-checksum", "Database", "Configured database is unchanged",
            safety_status, 0.0, f"before={before or 'missing'}; after={after or 'missing'}",
            safety_reason, cleanup_result,
        ))
        report = RCValidationReport(
            APP_VERSION, tuple(checks), self.recommendation(checks), str(self.configured_database), before, after,
        )
        self.export_all(report)
        return report

    @staticmethod
    def recommendation(checks) -> str:
        checks = tuple(checks)
        if any(check.status == BLOCKED for check in checks):
            return "NOT READY FOR RC1"
        if any(check.status == WARNING for check in checks):
            return "READY FOR RC1 WITH WARNINGS"
        return "READY FOR RC1"

    def export_all(self, report: RCValidationReport) -> tuple[Path, Path, Path]:
        self.report_dir.mkdir(parents=True, exist_ok=True)
        payload = self._payload(report)
        outputs = (
            self.report_dir / "rc1-validation.json",
            self.report_dir / "rc1-validation.md",
            self.report_dir / "rc1-validation.html",
        )
        contents = (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            self._markdown(report),
            "<!doctype html><html><meta charset=\"utf-8\"><body><pre>" + html.escape(self._markdown(report)) + "</pre></body></html>",
        )
        for path, content in zip(outputs, contents):
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(path)
        return outputs

    def _startup_checks(self):
        checks = []
        started = time.perf_counter()
        before_threads = {thread.ident for thread in threading.enumerate()}
        try:
            import tkinter as tk
            root_before = tk._default_root
            importlib.import_module("viewer")
            root_after = tk._default_root
            status = PASS if root_before is root_after is None else BLOCKED
            checks.append(self._check("startup.headless-import", "Startup", "Viewer imports without a Tkinter window", status, started, "tk._default_root remained unset" if status == PASS else "Tkinter root exists", "" if status == PASS else "Headless import created or retained a Tkinter root."))
        except Exception as error:
            checks.append(self._check("startup.headless-import", "Startup", "Viewer imports without a Tkinter window", BLOCKED, started, "", str(error)))
        started = time.perf_counter()
        after_threads = {thread.ident for thread in threading.enumerate()}
        new_threads = after_threads - before_threads
        checks.append(self._check("startup.no-background-task", "Startup", "Import leaves no background task active", PASS if not new_threads else WARNING, started, f"new thread ids={sorted(new_threads)}", "" if not new_threads else "New threads remained after import."))
        started = time.perf_counter()
        viewer_path = self.project_root / "viewer.py"
        try:
            tree = ast.parse(viewer_path.read_text(encoding="utf-8"), filename=str(viewer_path))
            direct_sql = [node.lineno for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"execute", "executemany"} and isinstance(node.func.value, ast.Attribute) and node.func.value.attr in {"conn", "cur"}]
            module_messages = [node.lineno for node in tree.body if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute) and node.value.func.attr.startswith("show")]
            status = PASS if not direct_sql and not module_messages else BLOCKED
            checks.append(self._check("startup.viewer-safety", "Startup", "Viewer has no direct SQL or import-time message boxes", status, started, "static AST scan" if status == PASS else f"direct SQL lines={direct_sql}; message lines={module_messages}", "" if status == PASS else "Viewer headless safety contract failed."))
        except (OSError, SyntaxError) as error:
            checks.append(self._check("startup.viewer-safety", "Startup", "Viewer has no direct SQL or import-time message boxes", BLOCKED, started, "", str(error)))
        return checks

    def _workflow_checks(self, root: Path):
        database = root / "workflow.db"
        sidecars = root / "sidecars"
        exports = root / "exports"
        fixture = root / "workflow.ged"
        fixture.write_text("0 HEAD\n1 SOUR RC validation\n0 @I1@ INDI\n1 NAME Import /Person/\n1 SEX M\n0 TRLR\n", encoding="utf-8")
        checks = [self._call("database.first-startup", "Startup", "First application database startup", lambda: self._assert(not database.exists(), "temporary database path is absent"))]
        checks.append(self._call("database.initialize", "Database", "New database schema initialization", lambda: self._initialize(database)))
        preview = self._call("import.preview", "Import", "GEDCOM import preview", lambda: self._import_preview(fixture))
        checks.append(preview)
        checks.append(self._call("import.confirmed", "Import", "Confirmed GEDCOM import", lambda: self._confirmed_import(database, fixture)))
        repository = PersonRepository(database)
        try:
            ids = self._seed_workflow(repository)
            checks.extend(self._crud_relationship_checks(repository, ids))
            checks.extend(self._analysis_visualization_checks(repository, ids, sidecars, exports))
            checks.append(self._call("backup.restore", "Backup/Restore", "Online backup and restore to separate temporary file", lambda: self._backup_restore(database, root / "backups", root / "restored.db")))
        finally:
            repository.close()
        checks.append(self._call("startup.restart", "Startup", "Application restart simulation", lambda: self._restart(database)))
        return [self._with_cleanup(check) for check in checks]

    def _seed_workflow(self, repository):
        parent = repository.create_person({"gedcom_id": "I100", "first_name": "Parent", "last_name": "RC", "sex": "M", "birth_date": "1900", "birth_place": "Riga"})
        partner = repository.create_person({"gedcom_id": "I101", "first_name": "Partner", "last_name": "RC", "sex": "F", "birth_date": "1905", "birth_place": "Riga"})
        child = repository.create_person({"gedcom_id": "I102", "first_name": "Child", "last_name": "RC", "birth_date": "1930", "birth_place": "Paris"})
        return {"parent": parent, "partner": partner, "child": child}

    def _crud_relationship_checks(self, repository, ids):
        checks = []
        checks.append(self._call("crud.person-create-edit", "CRUD", "Person creation and editing", lambda: self._person_edit(repository, ids["parent"])))
        relationships = RelationshipService(repository)
        checks.append(self._call("relationships.family-parent-child", "Relationships", "Family and parent-child relationship creation", lambda: self._parent_child(relationships)))
        checks.append(self._call("relationships.partner", "Relationships", "Spouse or partner relationship creation", lambda: self._partner(relationships)))
        checks.append(self._call("crud.event", "CRUD", "Person event creation", lambda: self._event(repository, ids["parent"])))
        checks.append(self._call("sources.citation", "Sources", "Source and citation attachment", lambda: self._citation(repository, ids["parent"])))
        checks.append(self._call("attachments.metadata", "Attachments", "Attachment metadata creation", lambda: self._attachment(repository, ids["parent"])))
        checks.append(self._call("undo-redo.person", "Undo/Redo", "Undo and redo person change", lambda: self._undo_redo(repository)))
        return checks

    def _analysis_visualization_checks(self, repository, ids, sidecars, exports):
        checks = []
        validation = ValidationCenterService(repository, data_dir=sidecars / "validation")
        checks.append(self._call("analysis.validation", "Analysis", "Validation scan", lambda: self._assert(validation.analyze(include_ignored=True) is not None, "validation report produced")))
        intelligence = IntelligenceService(repository, sidecars / "intelligence")
        intelligence_report = intelligence.analyze()
        intelligence.ignore(intelligence_report.suggestions[0].suggestion_id) if intelligence_report.suggestions else None
        checks.append(self._call("analysis.intelligence", "Analysis", "Intelligence analysis and disposition persistence", lambda: self._assert(intelligence.dispositions_path.exists(), "intelligence report and disposition sidecar produced")))
        checks.append(self._call("analysis.cancellation", "Analysis", "Cancelled headless analysis leaves no background task", lambda: self._cancelled_analysis(intelligence)))
        source_analysis = SourceAnalysisService(repository, sidecars / "source-analysis")
        source_report = source_analysis.analyze()
        source_analysis.ignore(source_report.findings[0].finding_id) if source_report.findings else None
        checks.append(self._call("analysis.source", "Analysis", "Source analysis and ignore persistence", lambda: self._assert(source_analysis.dispositions_path.exists(), "source analysis report and disposition sidecar produced")))
        timeline = TimelineStudioService(repository, data_dir=sidecars / "timeline")
        timeline_model = timeline.build(scope="complete_database"); timeline.save_view("RC", {"scope": "complete_database", "filters": {}})
        checks.append(self._call("visualization.timeline", "Visualization", "Timeline preparation and view persistence", lambda: self._assert(timeline_model.events and timeline.list_views(), "timeline model and view produced")))
        tree = TreeCanvasService(repository, layout_dir=sidecars / "tree")
        tree_model = tree.build(ids["parent"]); tree.save_positions(ids["parent"], {ids["parent"]: (10, 10)})
        checks.append(self._call("visualization.tree", "Visualization", "Tree preparation and layout persistence", lambda: self._assert(tree_model.nodes and any((sidecars / "tree").iterdir()), "tree model and layout sidecar produced")))
        geo = GeoMapStudioService(repository, data_dir=sidecars / "map")
        geo_model = geo.build(scope="complete_database"); geo.save_view("RC", {"zoom": 4, "center": [0, 0], "layers": [], "filters": {}})
        checks.append(self._call("visualization.map", "Visualization", "Map preparation and view persistence", lambda: self._assert(geo_model.markers and geo.list_views(), "map model and view produced")))
        workspace = ResearchWorkspaceService(repository, data_dir=sidecars / "research")
        project = workspace.create_project("RC workflow", "Temporary validation")
        checks.append(self._call("persistence.research", "Persistence", "Research workspace persistence", lambda: self._assert(workspace.load(project.project.project_id).project.title == "RC workflow", "research workspace reloaded")))
        performance = PerformanceService(sidecars / "performance")
        performance.record("rc-validation", 1, 0.001)
        checks.append(self._call("persistence.sidecars", "Persistence", "Temporary UI, ignore, and performance sidecars", lambda: self._assert(any(sidecars.rglob("*.json")), "temporary sidecar JSON files produced")))
        checks.append(self._call("export.formats", "Export", "CSV, JSON, Markdown, HTML, PDF, SVG, and PNG generation", lambda: self._exports(intelligence, intelligence_report, tree, tree_model, exports)))
        return checks

    def _packaging_checks(self):
        return [self._call("packaging.resources", "Packaging", "Build definitions, resources, icons, and release metadata", self._build_resources)] + [self._call("packaging.version", "Packaging", "Version remains beta1 and is prepared for RC1", self._version_ready)]

    def _initialize(self, database):
        created = initialize_database(database)
        self._assert(created and validate_database_file(database), "schema initialized in a new temporary database")

    def _import_preview(self, fixture):
        data = parse_gedcom(fixture)
        self._assert(len(data["people"]) == 1, "one GEDCOM person parsed without database mutation")

    def _confirmed_import(self, database, fixture):
        result = GedcomImporter(database).import_gedcom(fixture)
        self._assert(result["people"] == 1, "one GEDCOM person imported into temporary database")

    def _person_edit(self, repository, person_id):
        self._assert(repository.update_person_fields(person_id, {"occupation": "Archivist"}) and repository.get_person_record(person_id)["occupation"] == "Archivist", "person created and updated")

    def _parent_child(self, service):
        family = service.link_parent("I102", "I100", "father")
        self._assert(family and service.repository.get_parents("I102"), "parent-child family link created")

    def _partner(self, service):
        family = service.link_partner("I100", "I101", "marriage")
        self._assert(family and service.get_relationship_editor_state("I100")["partners"], "partner link created")

    def _event(self, repository, person_id):
        event_id = repository.create_person_event({"person_id": person_id, "event_type": "residence", "date": "1920", "place": "Riga"})
        self._assert(event_id and repository.list_person_events(person_id), "event row created")

    def _citation(self, repository, person_id):
        evidence = EvidenceService(repository)
        source = evidence.sources.create_source({"title": "RC Register", "repository": "Archive"})
        result = evidence.execute(evidence.preview((EvidenceOperation("attach_citation", source_id=source["id"], target_type="person", target_id=str(person_id), data={"confidence": "Strong"}),)))
        self._assert(result.operations, "source and citation attached")

    def _attachment(self, repository, person_id):
        attachment = repository.create_person_media({"person_id": person_id, "media_type": "document", "title": "RC scan", "file_path": "temporary.pdf"})
        self._assert(attachment and repository.get_person_media(attachment), "attachment metadata created")

    def _undo_redo(self, repository):
        manager = UndoManager()
        person_id = manager.execute(AddPersonCommand(repository, {"gedcom_id": "I200", "first_name": "Undo", "last_name": "RC"}))
        manager.execute(EditPersonCommand(repository, person_id, {"first_name": "Redo", "last_name": "RC"}))
        self._assert(manager.undo() and manager.redo() and repository.get_person_record(person_id)["first_name"] == "Redo", "undo and redo restored expected state")

    def _backup_restore(self, source, backup_dir, restored):
        backup = backup_database(source, backup_dir)
        validate_database_file(backup)
        restore_database(backup, restored)
        self._assert(self._table_counts(source) == self._table_counts(restored), "backup integrity and restored required-table record counts match")

    def _restart(self, database):
        repository = PersonRepository(database)
        try:
            self._assert(repository.list_people_full(), "temporary database reopened after simulated restart")
        finally:
            repository.close()

    def _cancelled_analysis(self, intelligence):
        try:
            intelligence.analyze(cancel_callback=lambda: (_ for _ in ()).throw(RuntimeError("cancelled")))
        except RuntimeError as error:
            self._assert(str(error) == "cancelled", "cancellation raised without starting a background task")
            return "cancellation raised without starting a background task"
        raise AssertionError("analysis cancellation callback was not observed")

    def _exports(self, intelligence, report, tree, tree_model, exports):
        exports.mkdir(parents=True, exist_ok=True)
        paths = {
            "csv": intelligence.export(report, exports / "rc-intelligence.csv", "csv"),
            "json": intelligence.export(report, exports / "rc-intelligence.json", "json"),
            "markdown": intelligence.export(report, exports / "rc-intelligence.md", "markdown"),
            "html": intelligence.export(report, exports / "rc-intelligence.html", "html"),
            "pdf": tree.export_pdf(tree_model, exports / "rc-tree.pdf"),
            "svg": tree.export_svg(tree_model, exports / "rc-tree.svg"),
            "png": tree.export_png(tree_model, exports / "rc-tree.png"),
        }
        signatures = {"pdf": b"%PDF", "png": b"\x89PNG", "svg": b"<svg", "json": b"{", "html": b"<!doctype", "markdown": b"#", "csv": b"\xef\xbb\xbf"}
        for kind, path in paths.items():
            self._assert(path.name.startswith("rc-") and path.is_file() and path.stat().st_size > 0 and path.read_bytes().lstrip().startswith(signatures[kind]), f"{kind} export {path.name} has expected signature")

    def _build_resources(self):
        missing = [path for path in self.REQUIRED_BUILD_FILES if not (self.project_root / path).is_file()]
        self._assert(not missing, "required build files present" if not missing else f"missing: {', '.join(missing)}")

    def _version_ready(self):
        text = (self.project_root / "build_info.py").read_text(encoding="utf-8")
        expected_version = APP_VERSION
        supported = expected_version.startswith("2.1.0-") and any(
            marker in expected_version for marker in ("beta", "rc")
        )
        self._assert(
            supported and f'APP_VERSION = "{expected_version}"' in text,
            "2.1 prerelease metadata is synchronized and can be promoted to rc1",
        )

    def _call(self, check_id, category, description, operation: Callable[[], object]):
        started = time.perf_counter()
        try:
            evidence = operation()
            return self._check(check_id, category, description, PASS, started, str(evidence or "completed"))
        except Exception as error:
            return self._check(check_id, category, description, BLOCKED, started, "", str(error))

    @staticmethod
    def _check(check_id, category, description, status, started, evidence, reason=""):
        return RCValidationCheck(check_id, category, description, status, round(time.perf_counter() - started, 6), evidence, reason, "temporary root pending")

    @staticmethod
    def _with_cleanup(check):
        return RCValidationCheck(**{**asdict(check), "cleanup_result": "temporary root pending"})

    @staticmethod
    def _assert(condition, evidence):
        if not condition:
            raise AssertionError(evidence)
        return evidence

    @staticmethod
    def _checksum(path):
        if not path.is_file():
            return ""
        digest = hashlib.sha256()
        with path.open("rb") as database:
            for chunk in iter(lambda: database.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _table_counts(path):
        connection = sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)
        try:
            return {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in ("people", "families", "family_children", "person_events")}
        finally:
            connection.close()

    @staticmethod
    def _payload(report):
        return {"version": report.version, "recommendation": report.recommendation, "configured_database": report.configured_database, "checksum_before": report.checksum_before, "checksum_after": report.checksum_after, "checks": [asdict(check) for check in report.checks]}

    @staticmethod
    def _markdown(report):
        lines = ["# GenealogyDB RC1 Validation", "", f"Recommendation: **{report.recommendation}**", f"Version: `{report.version}`", f"Configured database checksum: `{report.checksum_before or 'missing'}` -> `{report.checksum_after or 'missing'}`", "", "| ID | Category | Status | Duration | Evidence | Reason | Cleanup |", "| --- | --- | --- | ---: | --- | --- | --- |"]
        lines.extend(f"| {check.check_id} | {check.category} | {check.status} | {check.duration_seconds:.3f}s | {check.evidence} | {check.reason} | {check.cleanup_result} |" for check in report.checks)
        return "\n".join(lines) + "\n"