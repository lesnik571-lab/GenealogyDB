"""Cross-module, temporary-only stabilization checks for GenealogyDB 2.1."""

from __future__ import annotations

import ast
import gc
import hashlib
import html
import json
import tempfile
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
from workflow_automation_service import WorkflowAutomationService
from operation_correlation import OperationContext

PASS = "PASS"
WARNING = "WARNING"
BLOCKED = "BLOCKED"
SERVICES = ("CollaborationService", "ProjectMergeService", "ConflictResolutionService", "HistoryBrowserService", "WorkflowAutomationService", "ChangeExchangeService", "TaskManager", "AuditService", "UndoManager", "backup_database", "PerformanceService", "ReleaseCenterService")
LIFECYCLES = {
    "exchange": ("Draft", "Validated", "Exported", "Inspected", "Accepted for merge", "Rejected"),
    "merge": ("Analysis", "Preview", "Resolution required", "Ready", "Applied", "Cancelled"),
    "resolution": ("Review", "Plan", "Preview", "Applied", "Cancelled"),
    "workflow": ("Draft", "Validated", "Dry run", "Running", "Completed", "Failed", "Cancelled"),
    "history": ("Indexed", "Previewed", "Restored copy", "Current restore"),
}


@dataclass(frozen=True)
class IntegrationCheck:
    check_id: str
    section: str
    status: str
    evidence: str
    reason: str = ""


@dataclass(frozen=True)
class IntegrationReport:
    checks: tuple[IntegrationCheck, ...]
    recommendation: str
    configured_checksum_before: str
    configured_checksum_after: str

    @property
    def blockers(self): return tuple(check for check in self.checks if check.status == BLOCKED)


class IntegrationStabilizationService:
    """Inspect 2.1 seams without starting Tk or writing the configured database."""

    def __init__(self, configured_database, *, project_root=None):
        self.configured_database = Path(configured_database).resolve()
        self.project_root = Path(project_root or Path(__file__).resolve().parent)
        self.viewer_path = self.project_root / "viewer.py"
        self.report_dir = self.project_root / "release" / "2.1-integration"

    def validate(self):
        before = self._checksum(self.configured_database); tree = ast.parse(self.viewer_path.read_text(encoding="utf-8"))
        checks = [self._availability_check(), self._viewer_check(tree), self._identity_check(), self._lifecycle_check(), self._temporary_scenario_check()]
        after = self._checksum(self.configured_database)
        checks.append(self._check("data.configured-checksum", "Data safety", before == after, f"before={before}; after={after}", "Configured database changed"))
        report = IntegrationReport(tuple(checks), self.recommendation(checks), before, after); self.export_all(report); return report

    @staticmethod
    def recommendation(checks):
        if any(check.status == BLOCKED for check in checks): return "NOT READY"
        return "READY WITH WARNINGS" if any(check.status == WARNING for check in checks) else "READY FOR 2.1 BETA VALIDATION"

    def export_all(self, report):
        self.report_dir.mkdir(parents=True, exist_ok=True); markdown = self._markdown(report)
        outputs = (self.report_dir / "integration.json", self.report_dir / "integration.md", self.report_dir / "integration.html")
        for path, content in zip(outputs, (json.dumps(self._payload(report), ensure_ascii=False, indent=2, sort_keys=True), markdown, "<!doctype html><meta charset=\"utf-8\"><pre>" + html.escape(markdown) + "</pre>")):
            temporary = path.with_suffix(path.suffix + ".tmp"); temporary.write_text(content, encoding="utf-8"); temporary.replace(path)
        return outputs

    def _availability_check(self):
        missing = [name for name in SERVICES if name not in self.viewer_path.read_text(encoding="utf-8") and name not in {"backup_database"}]
        return self._check("services.availability", "Service availability", not missing, f"{len(SERVICES) - len(missing)}/{len(SERVICES)} services anchored", ", ".join(missing))

    def _viewer_check(self, tree):
        methods = {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}; labels = []; targets = []; direct_sql = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "add_command":
                label = next((keyword.value.value for keyword in node.keywords if keyword.arg == "label" and isinstance(keyword.value, ast.Constant)), "")
                if label: labels.append(label)
                command = next((keyword.value for keyword in node.keywords if keyword.arg == "command"), None)
                if isinstance(command, ast.Attribute) and isinstance(command.value, ast.Name) and command.value.id == "self": targets.append(command.attr)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"execute", "executemany"}: direct_sql.append(node.lineno)
        missing = sorted(set(targets) - methods)
        required = ("Collaboration", "Project Merge", "Conflict Resolution", "History Browser", "Workflow Automation", "Offline Change Exchange", "2.1 Integration Check")
        absent = [label for label in required if labels.count(label) != 1]
        if absent or missing:
            return self._check("viewer.integration", "Viewer integration", False, f"commands={len(targets)}", "; ".join(absent + missing))
        if direct_sql:
            return IntegrationCheck("viewer.integration", "Viewer integration", WARNING, f"commands={len(targets)}", f"Legacy direct SQL at lines: {', '.join(map(str, direct_sql))}")
        return self._check("viewer.integration", "Viewer integration", True, f"commands={len(targets)}", "")

    def _identity_check(self):
        temporary = Path(tempfile.mkdtemp(prefix="genealogy-integration-")); database = temporary / "identity.db"; initialize_database(database); repository = PersonRepository(database)
        try:
            service = CollaborationService(database, data_dir=temporary / "data"); identities = (service.identity().project_uuid, service.identity().dataset_uuid)
            valid = all(self._valid_uuid(value) for value in identities)
            change = service.record_change("create_person", references={}, summary="integration")
            valid = valid and self._valid_uuid(change.operation_id) and len({*identities, change.operation_id}) == 3
            return self._check("identity.uuid", "Identity consistency", valid, "project, dataset, operation UUIDs validated", "Malformed or reused identity")
        finally:
            repository.close(); gc.collect(); __import__("shutil").rmtree(temporary)

    def _lifecycle_check(self):
        valid = all(len(states) == len(set(states)) and states[0] in {"Draft", "Analysis", "Review", "Indexed"} for states in LIFECYCLES.values())
        invalid = self.transition_allowed("exchange", "Rejected", "Accepted for merge")
        return self._check("lifecycle.transitions", "Lifecycle consistency", valid and not invalid, f"{len(LIFECYCLES)} lifecycle models", "Invalid transition accepted")

    def _temporary_scenario_check(self):
        temporary = Path(tempfile.mkdtemp(prefix="genealogy-integration-")); first = temporary / "a.db"; second = temporary / "b.db"; initialize_database(first); initialize_database(second); left = PersonRepository(first); right = PersonRepository(second)
        try:
            before = left.capture_command_state(); person = left.create_person({"gedcom_id": "I1", "first_name": "Ada", "last_name": "Lovelace"}); data = temporary / "data"; ledger = CollaborationService(first, data_dir=data); identity = ledger.identity(); context = OperationContext.create(operation_type="person_create", project_uuid=identity.project_uuid, dataset_uuid=identity.dataset_uuid, session_uuid=ledger.session_id, source_module="integration_stabilization_service").transition("running").complete(); delta = __import__("undo_manager").RepositoryDeltaCommand._build_delta(before, left.capture_command_state()); AuditService.for_database(first).record_delta("person_create", delta, description="Integration correlation", service="integration_stabilization_service", operation_context=context); ledger.record_change("edit_person", references={"person": (str(person),)}, summary="exchange", operation_context=context)
            exchange = ChangeExchangeService(left, data_dir=data); package = exchange.export(temporary / "exchange.zip"); inspected = ChangeExchangeService(right, data_dir=data).inspect(package)
            history = HistoryBrowserService(left, data_dir=data); snapshot_id, snapshot = history.create_snapshot(); preview = history.historical_preview(type("Entry", (), {"entry_id": "snapshot", "after_snapshot_reference": f"snapshot:{snapshot}"})()); history.close_preview(preview)
            workflow = WorkflowAutomationService(left, data_dir=data).create("dry", step_types=("validation_scan",)); run = WorkflowAutomationService(left, data_dir=data).run(workflow)
            valid = inspected.status in {"Inspected", "Rejected"} and not Path(preview.temporary_path).exists() and run.status == "dry_run" and HistoryBrowserService(left, data_dir=data).correlation_diagnostics()["complete"]
            return self._check("temporary.end-to-end", "Temporary scenarios", valid, "exchange, history preview, workflow dry run", "Temporary scenario failed")
        except Exception as error:
            return IntegrationCheck("temporary.end-to-end", "Temporary scenarios", BLOCKED, "", str(error))
        finally:
            left.close(); right.close(); gc.collect(); __import__("shutil").rmtree(temporary)

    @staticmethod
    def transition_allowed(lifecycle, current, next_state):
        states = LIFECYCLES[lifecycle]
        return current in states and next_state in states and states.index(next_state) == states.index(current) + 1
    @staticmethod
    def _valid_uuid(value):
        try: UUID(str(value)); return True
        except ValueError: return False
    @staticmethod
    def _checksum(path):
        if not path.is_file(): return "missing"
        return hashlib.sha256(path.read_bytes()).hexdigest()
    @staticmethod
    def _check(check_id, section, passed, evidence, reason): return IntegrationCheck(check_id, section, PASS if passed else BLOCKED, evidence, "" if passed else reason)
    @staticmethod
    def _payload(report): return {"recommendation": report.recommendation, "configured_checksum_before": report.configured_checksum_before, "configured_checksum_after": report.configured_checksum_after, "checks": [asdict(check) for check in report.checks]}
    @staticmethod
    def _markdown(report):
        lines = ["# GenealogyDB 2.1 Integration", "", f"Recommendation: **{report.recommendation}**", "", "| Section | Status | Evidence | Reason |", "| --- | --- | --- | --- |"]
        lines.extend(f"| {check.section} | {check.status} | {check.evidence} | {check.reason} |" for check in report.checks); return "\n".join(lines) + "\n"