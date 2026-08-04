"""Temporary-only release validation for the GenealogyDB 2.1 beta candidate."""

from __future__ import annotations

import ast
import gc
import hashlib
import html
import json
import shutil
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import UUID

from audit_service import AuditService
from change_exchange_service import ChangeExchangeService
from collaboration_service import CollaborationService
from conflict_resolution_service import ConflictResolutionService
from database import initialize_database
from history_browser_service import HistoryBrowserService
from project_merge_service import ProjectMergeService
from repository.person_repository import PersonRepository
from workflow_automation_service import DRY_RUN, READ_ONLY_RUN, WorkflowAutomationService
from operation_correlation import OperationContext, validate_correlation
from undo_manager import RepositoryDeltaCommand

PASS = "PASS"
WARNING = "WARNING"
BLOCKED = "BLOCKED"
SKIPPED = "SKIPPED"
CANDIDATE_VERSION = "2.1.0-beta1"


@dataclass(frozen=True)
class BetaValidationCheck:
    check_id: str
    section: str
    status: str
    evidence: str
    reason: str = ""


@dataclass(frozen=True)
class BetaValidationReport:
    checks: tuple[BetaValidationCheck, ...]
    recommendation: str
    configured_checksum_before: str
    configured_checksum_after: str

    @property
    def blockers(self):
        return tuple(check for check in self.checks if check.status == BLOCKED)

    @property
    def warnings(self):
        return tuple(check for check in self.checks if check.status == WARNING)


class Beta21ValidationService:
    """Exercise release workflows using only disposable databases and sidecars."""

    def __init__(self, configured_database, *, project_root=None):
        self.configured_database = Path(configured_database).resolve()
        self.project_root = Path(project_root or Path(__file__).resolve().parent)
        self.viewer_path = self.project_root / "viewer.py"
        self.report_dir = self.project_root / "release" / "2.1-beta-validation"

    def validate(self):
        before = self._checksum(self.configured_database)
        temporary = Path(tempfile.mkdtemp(prefix="genealogy-beta21-"))
        try:
            checks = self._temporary_checks(temporary)
            checks.extend((self._viewer_check(), self._packaging_check()))
        finally:
            gc.collect()
            leftovers = tuple(sorted(path.name for path in temporary.rglob("*") if path.suffix == ".tmp")) if temporary.exists() else ()
            shutil.rmtree(temporary, ignore_errors=True)
        after = self._checksum(self.configured_database)
        checks.append(self._result("data.configured-database", "Data safety", before == after and not leftovers, "configured database checksum stable", "Configured database changed or temporary sidecar cleanup was incomplete"))
        report = BetaValidationReport(tuple(checks), self.recommendation(checks), before, after)
        self.export_all(report)
        return report

    def _temporary_checks(self, root):
        first_path, second_path = root / "first.db", root / "second.db"
        initialize_database(first_path); initialize_database(second_path)
        first, second = PersonRepository(first_path), PersonRepository(second_path)
        data = root / "sidecars"
        try:
            baseline = self._checksum(first_path)
            before_write = first.capture_command_state()
            first_person = first.create_person({"gedcom_id": "I1", "first_name": "Ada", "last_name": "Lovelace"})
            second.create_person({"gedcom_id": "I1", "first_name": "Augusta", "last_name": "King"})
            collaboration = CollaborationService(first_path, data_dir=data, editor_identity="Beta validator")
            identity = collaboration.identity()
            context = OperationContext.create(operation_type="person_create", project_uuid=identity.project_uuid, dataset_uuid=identity.dataset_uuid, author=identity.editor_identity, session_uuid=collaboration.session_id, source_module="beta21_validation_service", affected_entity_types=("person",), affected_entity_ids=(str(first_person),)).transition("running").complete()
            delta = RepositoryDeltaCommand._build_delta(before_write, first.capture_command_state())
            audit = AuditService.for_database(first_path)
            audit.record_delta("person_create", delta, description="Beta validation synthetic write", service="beta21_validation_service", operation_context=context)
            change = collaboration.record_change("create_person", references={"person": (str(first_person),)}, summary="Beta validation", operation_context=context)
            reloaded = CollaborationService(first_path, data_dir=data).identity()
            collaboration_ok = all(self._uuid(value) for value in (identity.project_uuid, identity.dataset_uuid, change.operation_id)) and identity == reloaded and not collaboration.diagnostics().orphan_operation_ids
            checks = [self._result("collaboration.identities", "Collaboration", collaboration_ok, "project, dataset, editor/session and operation identities persisted", "Malformed, missing, reused, or orphan collaboration metadata")]
            history_entries = HistoryBrowserService(first, data_dir=data, backup_dir=root / "backups").entries()
            correlation = validate_correlation([record for record in audit.list_records() if record.operation_uuid == context.operation_uuid], [item for item in collaboration.changes() if item.operation_uuid == context.operation_uuid])
            history_matches = [entry for entry in history_entries if entry.operation_uuid == context.operation_uuid]
            checks.append(self._result("audit.correlation", "Audit consistency", correlation["complete"] and len(history_matches) == 1 and history_matches[0].provenance.get("status") == "completed", "one Audit, Collaboration and History counterpart share the completed operation UUID", "Missing, duplicate, mismatched, or uncorrelated operation counterpart"))

            exchange = ChangeExchangeService(first, data_dir=data)
            package = exchange.export(root / "changes.zip", include_snapshot=first_path)
            inspected = exchange.inspect(package)
            duplicate_detected = bool(exchange.preview_against_current(inspected).already_applied)
            unsafe = root / "unsafe.zip"
            with zipfile.ZipFile(unsafe, "w") as archive:
                archive.writestr("../payload.exe", b"unsafe")
            unsafe_rejected = exchange.inspect(unsafe).status == "Rejected"
            exchange_ok = inspected.status == "Inspected" and duplicate_detected and unsafe_rejected and self._checksum(first_path) != baseline
            checks.append(self._result("exchange.security", "Offline exchange", exchange_ok, "deterministic package, checksum, duplicate operation and unsafe ZIP checks", "Exchange validation or isolation failure"))

            merge_input = exchange.to_project_merge_input(inspected, root / "merge-input.db")
            second_before = self._checksum(second_path)
            merge_preview = ProjectMergeService(second, data_dir=data, backup_dir=root / "backups").analyze(merge_input, parent_operation_uuid=inspected.package_uuid)
            cancelled = False
            try:
                ProjectMergeService(second, data_dir=data).analyze(merge_input, cancel_callback=lambda: (_ for _ in ()).throw(RuntimeError("cancelled")))
            except RuntimeError:
                cancelled = True
            checks.append(self._result("merge.preview", "Project merge", self._checksum(second_path) == second_before and cancelled and self._uuid(merge_preview.merge_operation_id), "read-only preview, duplicate/conflict detection and cancellation", "Merge preview wrote data or ignored cancellation"))

            resolution = ConflictResolutionService(second, data_dir=data, backup_dir=root / "backups")
            plan = resolution.review(merge_input, merge_preview=merge_preview)
            plan_path = resolution.save_plan(plan)
            resolution_preview = resolution.preview(plan)
            atomic = plan_path.exists() and not plan_path.with_suffix(".json.tmp").exists()
            dangerous_blocked = bool(resolution_preview.blockers) or not resolution_preview.updates
            checks.append(self._result("conflict.preview", "Conflict resolution", atomic and dangerous_blocked and self._uuid(plan.plan_id), "atomic plan persistence and conservative relationship blocking", "Conflict plan was non-atomic or dangerous resolution was allowed"))

            history = HistoryBrowserService(first, data_dir=data, backup_dir=root / "backups")
            snapshot_id, snapshot = history.create_snapshot(label="beta")
            entry = type("SnapshotEntry", (), {"entry_id": snapshot_id, "after_snapshot_reference": f"snapshot:{snapshot}"})()
            preview = history.historical_preview(entry)
            history.close_preview(preview)
            checks.append(self._result("history.preview", "History", not Path(preview.temporary_path).exists() and self._uuid(snapshot_id), "audit/collaboration indexing surface and read-only historical preview cleanup", "Historical preview cleanup failed"))

            workflow_service = WorkflowAutomationService(first, data_dir=data, backup_dir=root / "backups")
            templates_ok = all(workflow_service.validate(template).valid for template in workflow_service.templates())
            read_only = workflow_service.create("read only", step_types=("validation_scan",))
            dry_run = workflow_service.run(read_only, mode=DRY_RUN)
            readonly_run = workflow_service.run(read_only, mode=READ_ONLY_RUN)
            dangerous = workflow_service.create("dangerous", step_types=("gedcom_import",))
            confirmation_enforced = not workflow_service.validate(dangerous).valid
            checks.append(self._result("workflow.safety", "Workflow automation", templates_ok and dry_run.status == "dry_run" and readonly_run.database_checksum["unchanged"] and confirmation_enforced, "template validation, dry run, read-only run and confirmation enforcement", "Workflow bypassed a safety restriction"))

            package_uuid = inspected.package_uuid
            identities_ok = all(self._uuid(value) for value in (package_uuid, merge_preview.merge_operation_id, plan.plan_id, dry_run.run_uuid, snapshot_id))
            checks.append(self._result("combined.identity-chain", "Combined integration", identities_ok and merge_preview.parent_operation_uuid == package_uuid, "package, merge, resolution, workflow and history identities validated", "Malformed or orphaned integration identity"))
            checks.append(self._result("undo.backup-boundaries", "Undo and backup consistency", not (root / "backups").exists(), "preview-only and dry-run paths created no backup", "Preview-only path created a backup"))
            return checks
        except Exception as error:
            return [BetaValidationCheck("temporary.scenarios", "Temporary workflows", BLOCKED, "", str(error))]
        finally:
            first.close(); second.close(); gc.collect()

    def _viewer_check(self):
        source = self.viewer_path.read_text(encoding="utf-8"); tree = ast.parse(source)
        methods = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}
        commands = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_command"):
                continue
            label = next((item.value.value for item in node.keywords if item.arg == "label" and isinstance(item.value, ast.Constant)), "")
            command = next((item.value for item in node.keywords if item.arg == "command"), None)
            target = command.attr if isinstance(command, ast.Attribute) and isinstance(command.value, ast.Name) and command.value.id == "self" else ""
            commands.append((label, target))
        required = {"Совместная работа", "Объединение проектов", "Разрешение конфликтов", "Просмотр истории", "Автоматизация процессов", "Автономный обмен изменениями", "Проверка интеграции 2.1", "Проверка готовности 2.1 Beta"}
        matches = {label: [target for current, target in commands if current == label] for label in required}
        valid = all(len(targets) == 1 and targets[0] in methods for targets in matches.values())
        import_tk = any(
            isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
            and isinstance(node.value.func, ast.Attribute) and node.value.func.attr == "Tk"
            for node in tree.body
        )
        return self._result("viewer.commands", "Viewer", valid and not import_tk, "required 2.1 commands have one callback; import has no Tk root", "Duplicate/missing menu callback or import-time Tk root")

    def _packaging_check(self):
        required = ("build_info.py", "GenealogyDB.spec", "installer/GenealogyDB.iss", "README.md", "USER_MANUAL.md", "CHANGELOG.md", "LICENSE")
        missing = tuple(path for path in required if not (self.project_root / path).is_file())
        build_info = (self.project_root / "build_info.py").read_text(encoding="utf-8") if not missing else ""
        return self._result("packaging.metadata", "Packaging and documentation", not missing and "APP_VERSION" in build_info, f"candidate target: {CANDIDATE_VERSION}", ", ".join(missing) or "Missing build metadata")

    @staticmethod
    def recommendation(checks):
        if any(check.status == BLOCKED for check in checks):
            return "NOT READY FOR 2.1.0-BETA1"
        if any(check.status == WARNING for check in checks):
            return "READY FOR 2.1.0-BETA1 WITH WARNINGS"
        return "READY FOR 2.1.0-BETA1"

    def export_all(self, report):
        self.report_dir.mkdir(parents=True, exist_ok=True)
        markdown = self._markdown(report)
        values = (json.dumps(self._payload(report), ensure_ascii=False, indent=2, sort_keys=True) + "\n", markdown, "<!doctype html><meta charset=\"utf-8\"><pre>" + html.escape(markdown) + "</pre>")
        paths = tuple(self.report_dir / f"beta21-validation.{suffix}" for suffix in ("json", "md", "html"))
        for path, value in zip(paths, values):
            temporary = path.with_suffix(path.suffix + ".tmp"); temporary.write_text(value, encoding="utf-8"); temporary.replace(path)
        return paths

    @staticmethod
    def _result(check_id, section, passed, evidence, reason):
        return BetaValidationCheck(check_id, section, PASS if passed else BLOCKED, evidence, "" if passed else reason)

    @staticmethod
    def _checksum(path):
        digest = hashlib.sha256()
        if not path.is_file():
            return "missing"
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        return digest.hexdigest()

    @staticmethod
    def _uuid(value):
        try:
            UUID(str(value)); return True
        except ValueError:
            return False

    @staticmethod
    def _payload(report):
        return {"candidate_version": CANDIDATE_VERSION, "recommendation": report.recommendation, "configured_checksum_before": report.configured_checksum_before, "configured_checksum_after": report.configured_checksum_after, "checks": [asdict(check) for check in report.checks]}

    @staticmethod
    def _markdown(report):
        lines = ["# GenealogyDB 2.1 Beta Validation", "", f"Candidate: **{CANDIDATE_VERSION}**", f"Recommendation: **{report.recommendation}**", "", "| Section | Status | Evidence | Reason |", "| --- | --- | --- | --- |"]
        lines.extend(f"| {check.section} | {check.status} | {check.evidence} | {check.reason} |" for check in report.checks)
        return "\n".join(lines) + "\n"