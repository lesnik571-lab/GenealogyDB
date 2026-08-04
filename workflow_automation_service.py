"""Declarative, local-only workflow automation with explicit safety boundaries."""

from __future__ import annotations

import csv
import hashlib
import html
import json
import time
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from audit_service import AuditService
from collaboration_service import CollaborationService
from config import DATA_DIR
from data_quality_service import DataQualityService
from database import backup_database
from intelligence_service import IntelligenceService
from source_analysis_service import SourceAnalysisService
from validation_center_service import ValidationCenterService
from operation_correlation import OperationContext


READ_ONLY = "Read-only"
SIDECAR_ONLY = "Sidecar-only"
DATABASE_WRITE = "Database write"
HIGH_RISK = "Destructive/high risk"
DRY_RUN = "Dry run"
READ_ONLY_RUN = "Run read-only steps only"
FULL_RUN = "Run full workflow"
STEP_TYPES = (
    "create_backup", "gedcom_import_preview", "gedcom_import", "validation_scan", "data_quality_scan",
    "intelligence_analysis", "source_analysis", "duplicate_detection", "merge_preview", "conflict_resolution_preview",
    "timeline_generation", "tree_generation", "map_preparation", "export_report", "create_snapshot",
    "add_audit_entry", "add_collaboration_change", "user_confirmation", "conditional_branch", "delay", "stop",
)
SAFETY = {
    "create_backup": SIDECAR_ONLY, "gedcom_import_preview": READ_ONLY, "gedcom_import": HIGH_RISK,
    "validation_scan": READ_ONLY, "data_quality_scan": READ_ONLY, "intelligence_analysis": READ_ONLY,
    "source_analysis": READ_ONLY, "duplicate_detection": READ_ONLY, "merge_preview": READ_ONLY,
    "conflict_resolution_preview": READ_ONLY, "timeline_generation": READ_ONLY, "tree_generation": READ_ONLY,
    "map_preparation": READ_ONLY, "export_report": SIDECAR_ONLY, "create_snapshot": SIDECAR_ONLY,
    "add_audit_entry": SIDECAR_ONLY, "add_collaboration_change": SIDECAR_ONLY, "user_confirmation": READ_ONLY,
    "conditional_branch": READ_ONLY, "delay": READ_ONLY, "stop": READ_ONLY,
}
DANGEROUS = {"gedcom_import", "merge_apply", "conflict_resolution_apply", "restore_current_project", "delete_records", "overwrite_export", "overwrite_project_copy"}
REQUIRED = {"gedcom_import_preview": ("path",), "gedcom_import": ("path",), "export_report": ("path",), "user_confirmation": ("message",), "conditional_branch": ("condition",), "delay": ("seconds",)}


@dataclass(frozen=True)
class WorkflowStep:
    step_id: str
    step_type: str
    parameters: dict
    enabled: bool = True
    continue_on_warning: bool = False


@dataclass(frozen=True)
class Workflow:
    workflow_uuid: str
    name: str
    description: str
    version: int
    enabled: bool
    created_at: str
    modified_at: str
    author: str
    steps: tuple[WorkflowStep, ...]
    variables: dict
    safety_policy: dict
    execution_history: tuple[str, ...] = ()
    schema_version: int = 1


@dataclass(frozen=True)
class WorkflowValidation:
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    required_confirmations: tuple[str, ...]

    @property
    def valid(self): return not self.errors


@dataclass(frozen=True)
class WorkflowRun:
    run_uuid: str
    workflow_uuid: str
    version: int
    mode: str
    started_at: str
    ended_at: str
    status: str
    step_results: tuple[dict, ...]
    warnings: tuple[str, ...]
    failures: tuple[str, ...]
    confirmations: tuple[str, ...]
    cancelled: bool
    generated_files: tuple[str, ...]
    database_checksum: dict


class WorkflowAutomationService:
    """Run a bounded allowlist of local workflow steps; no shell or network execution."""

    def __init__(self, repository, *, data_dir=None, backup_dir=None):
        self.repository = repository
        self.data_dir = Path(data_dir or DATA_DIR)
        self.workflow_dir = self.data_dir / "workflows"
        self.history_dir = self.workflow_dir / "history"
        self.report_dir = self.workflow_dir / "reports"
        self.backup_dir = Path(backup_dir or self.data_dir / "backups")
        self.collaboration = CollaborationService(repository.db_name, data_dir=self.data_dir)

    def templates(self):
        return (
            self.create("After GEDCOM import", "Safe import review", ("create_backup", "gedcom_import_preview", "user_confirmation", "gedcom_import", "validation_scan", "intelligence_analysis", "source_analysis", "export_report")),
            self.create("Before project merge", "Merge preparation", ("create_backup", "merge_preview", "validation_scan", "user_confirmation", "conflict_resolution_preview")),
            self.create("Release health check", "Release diagnostics", ("validation_scan", "data_quality_scan", "intelligence_analysis", "source_analysis", "export_report")),
            self.create("Research review", "Research diagnostics", ("validation_scan", "source_analysis", "intelligence_analysis", "timeline_generation", "export_report")),
        )

    def create(self, name, description="", step_types=(), *, author="", variables=None):
        now = self._now(); steps = tuple(WorkflowStep(str(uuid4()), step_type, self._defaults(step_type)) for step_type in step_types)
        return Workflow(str(uuid4()), str(name), str(description), 1, True, now, now, str(author), steps, dict(variables or {}), {"require_confirmation": True})

    def save(self, workflow):
        self._require_valid(workflow); self.workflow_dir.mkdir(parents=True, exist_ok=True)
        path = self.workflow_dir / f"{workflow.workflow_uuid}.json"; self._atomic(path, asdict(workflow)); return path

    def load(self, workflow_uuid):
        path = self.workflow_dir / f"{workflow_uuid}.json"
        try: payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error: raise ValueError(f"Corrupted workflow: {path.name}") from error
        if payload.get("schema_version") != 1: raise ValueError("Unsupported workflow schema")
        return Workflow(**{**payload, "steps": tuple(WorkflowStep(**item) for item in payload.get("steps", ())), "execution_history": tuple(payload.get("execution_history", ()))})

    def list(self):
        return tuple(self.load(path.stem) for path in sorted(self.workflow_dir.glob("*.json"))) if self.workflow_dir.exists() else ()
    def rename(self, workflow, name): return replace(workflow, name=str(name).strip() or workflow.name, version=workflow.version + 1, modified_at=self._now())
    def duplicate(self, workflow, name=None): return replace(workflow, workflow_uuid=str(uuid4()), name=name or f"{workflow.name} copy", version=1, created_at=self._now(), modified_at=self._now(), execution_history=())
    def delete(self, workflow_uuid):
        path = self.workflow_dir / f"{workflow_uuid}.json"
        if path.exists(): path.unlink()
    def set_enabled(self, workflow, enabled): return replace(workflow, enabled=bool(enabled), modified_at=self._now())
    def add_step(self, workflow, step_type, parameters=None): return replace(workflow, steps=workflow.steps + (WorkflowStep(str(uuid4()), step_type, dict(parameters or self._defaults(step_type))),), version=workflow.version + 1, modified_at=self._now())
    def remove_step(self, workflow, step_id): return replace(workflow, steps=tuple(step for step in workflow.steps if step.step_id != step_id), version=workflow.version + 1, modified_at=self._now())
    def reorder_steps(self, workflow, step_ids):
        by_id = {step.step_id: step for step in workflow.steps}
        if set(step_ids) != set(by_id): raise ValueError("Reorder must include every step exactly once")
        return replace(workflow, steps=tuple(by_id[step_id] for step_id in step_ids), version=workflow.version + 1, modified_at=self._now())
    def edit_step(self, workflow, step_id, parameters): return replace(workflow, steps=tuple(replace(step, parameters=dict(parameters)) if step.step_id == step_id else step for step in workflow.steps), version=workflow.version + 1, modified_at=self._now())

    def validate(self, workflow):
        errors = []; warnings = []; confirmations = []
        if not workflow.steps: warnings.append("Workflow has no steps")
        for index, step in enumerate(workflow.steps):
            if step.step_type not in STEP_TYPES: errors.append(f"Unknown step type: {step.step_type}"); continue
            missing = [key for key in REQUIRED.get(step.step_type, ()) if not str(step.parameters.get(key, "")).strip()]
            if missing: errors.append(f"{step.step_type} missing parameters: {', '.join(missing)}")
            if step.step_type in DANGEROUS: confirmations.append(step.step_type)
            if step.step_type == "conditional_branch" and not self._valid_condition(step.parameters.get("condition", "")): errors.append("Invalid condition expression")
            if step.step_type == "delay" and (not isinstance(step.parameters.get("seconds"), (int, float)) or step.parameters["seconds"] < 0): errors.append("Delay must be non-negative")
            if step.step_type == "stop" and index + 1 < len(workflow.steps): warnings.append("Steps after stop are unreachable")
        for dangerous in DANGEROUS & {step.step_type for step in workflow.steps}:
            previous = workflow.steps[:next(index for index, item in enumerate(workflow.steps) if item.step_type == dangerous)]
            if not any(item.step_type == "user_confirmation" for item in previous): errors.append(f"Missing confirmation before {dangerous}")
        return WorkflowValidation(tuple(sorted(set(errors))), tuple(sorted(set(warnings))), tuple(sorted(set(confirmations))))

    def available_services(self):
        return {
            "validation": ValidationCenterService is not None,
            "data_quality": DataQualityService is not None,
            "intelligence": IntelligenceService is not None,
            "source_analysis": SourceAnalysisService is not None,
            "audit": AuditService is not None,
            "collaboration": CollaborationService is not None,
        }

    def evaluate_condition(self, expression, context):
        """Evaluate the constrained condition grammar without Python evaluation."""
        if not self._valid_condition(expression): raise ValueError("Invalid condition expression")
        field, operator, expected = str(expression).split(maxsplit=2)
        actual = str(context.get(field, "")); expected = expected.strip()
        return actual == expected if operator == "==" else actual != expected

    def preview(self, workflow, *, variables=None):
        self._require_valid(workflow); resolved = self._variables(workflow, variables); plan = []
        for index, step in enumerate(workflow.steps, 1):
            params = self._resolve(step.parameters, resolved)
            plan.append({"order": index, "step_id": step.step_id, "type": step.step_type, "safety": SAFETY[step.step_type], "parameters": params, "confirmation": step.step_type in DANGEROUS, "expected_files": (params.get("path", "") if step.step_type in {"export_report", "gedcom_import_preview"} else "")})
        return tuple(plan)

    def run(self, workflow, *, mode=DRY_RUN, variables=None, confirmations=(), cancel_callback=None, progress_callback=None):
        self._require_valid(workflow)
        if mode not in {DRY_RUN, READ_ONLY_RUN, FULL_RUN}: raise ValueError("Unsupported execution mode")
        run_id = str(uuid4()); started = self._now(); before_checksum = self._checksum(); results = []; warnings = []; failures = []; files = []; confirmations = set(confirmations)
        if mode == DRY_RUN:
            return self._finish(run_id, workflow, mode, started, "dry_run", results, warnings, failures, confirmations, False, files, before_checksum, before_checksum)
        backup = None; sidecar_requested = False
        for index, item in enumerate(self.preview(workflow, variables=variables), 1):
            if cancel_callback:
                try: cancel_callback()
                except Exception: return self._finish(run_id, workflow, mode, started, "cancelled", results, warnings, failures, confirmations, True, files, before_checksum, self._checksum())
            step_type = item["type"]; safety = item["safety"]
            sidecar_requested = sidecar_requested or step_type in {"add_audit_entry", "add_collaboration_change"}
            if mode == READ_ONLY_RUN and safety != READ_ONLY:
                results.append({"step": step_type, "status": "blocked", "reason": "read-only mode"}); continue
            if step_type in DANGEROUS and step_type not in confirmations:
                failures.append(f"Confirmation required: {step_type}"); results.append({"step": step_type, "status": "blocked"}); break
            if step_type == "conditional_branch":
                condition = item["parameters"]["condition"]
                context = {"previous_status": results[-1]["status"] if results else "", "warning_count": len(warnings), "blocker_count": len(failures), "issue_count": 0, "duplicate_count": 0, "export_success": bool(files), "user_response": "", **self._variables(workflow, variables)}
                results.append({"step": step_type, "status": "ok", "condition": condition, "result": self.evaluate_condition(condition, context)}); continue
            if safety in {DATABASE_WRITE, HIGH_RISK}:
                if backup is None: backup = backup_database(self.repository.db_name, self.backup_dir)
                failures.append(f"Execution of {step_type} is intentionally not supported"); results.append({"step": step_type, "status": "blocked"}); break
            started_step = time.perf_counter()
            try:
                outcome, generated = self._execute(step_type, item["parameters"], cancel_callback)
                files.extend(generated); results.append({"step": step_type, "status": "warning" if outcome.get("warning") else "ok", "result": outcome, "duration_seconds": round(time.perf_counter() - started_step, 6)})
                if outcome.get("warning"):
                    warnings.append(str(outcome["warning"]));
                    if not workflow.steps[index - 1].continue_on_warning: break
            except Exception as error:
                failures.append(f"{step_type}: {error}"); results.append({"step": step_type, "status": "failed", "error": str(error)}); break
            if progress_callback: progress_callback(step_type, index, len(workflow.steps))
            if step_type == "stop": break
        status = "failed" if failures else "completed"
        if status == "completed" and sidecar_requested:
            identity = self.collaboration.identity()
            context = OperationContext.create(operation_type="batch_operations", project_uuid=identity.project_uuid, dataset_uuid=identity.dataset_uuid, operation_uuid=run_id, author=identity.editor_identity, session_uuid=self.collaboration.session_id, source_module="workflow_automation_service", provenance={"workflow_uuid": workflow.workflow_uuid}).transition("running").complete()
            AuditService.for_database(self.repository.db_name).record("batch_operations", description="Workflow sidecar audit entry", service="workflow_automation_service", operation_context=context)
            self.collaboration.record_change("merge", references={}, summary="Workflow collaboration entry", operation_context=context)
        after_checksum = self._checksum()
        if before_checksum != after_checksum and all(SAFETY[item["type"]] == READ_ONLY for item in self.preview(workflow, variables=variables)):
            failures.append("Read-only workflow changed the database"); status = "failed"
        return self._finish(run_id, workflow, mode, started, status, results, warnings, failures, confirmations, False, files, before_checksum, after_checksum)

    def export(self, run, destination, report_format):
        path = Path(destination); path.parent.mkdir(parents=True, exist_ok=True); temporary = path.with_suffix(path.suffix + ".tmp")
        if report_format == "json": temporary.write_text(json.dumps(asdict(run), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        elif report_format == "markdown": temporary.write_text("# Workflow Run\n\n" + "\n".join(f"- {item['step']}: {item['status']}" for item in run.step_results) + "\n", encoding="utf-8")
        elif report_format == "html": temporary.write_text("<!doctype html><meta charset=\"utf-8\"><pre>" + html.escape("\n".join(f"{item['step']}: {item['status']}" for item in run.step_results)) + "</pre>", encoding="utf-8")
        elif report_format == "csv":
            with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream); writer.writerow(("run_uuid", "step", "status", "duration_seconds")); writer.writerows((run.run_uuid, item.get("step", ""), item.get("status", ""), item.get("duration_seconds", "")) for item in run.step_results)
        else: raise ValueError("Unsupported workflow report format")
        temporary.replace(path); return path

    def export_all(self, run):
        self.report_dir.mkdir(parents=True, exist_ok=True); return tuple(self.export(run, self.report_dir / f"workflow-{run.run_uuid}.{suffix}", kind) for kind, suffix in (("json", "json"), ("markdown", "md"), ("html", "html"), ("csv", "csv")))

    def _execute(self, step_type, parameters, cancel_callback):
        if step_type == "validation_scan": return {"issues": len(ValidationCenterService(self.repository).analyze(cancel_callback=cancel_callback).issues)}, ()
        if step_type == "data_quality_scan": return {"issues": len(DataQualityService(self.repository).analyze().issues)}, ()
        if step_type in {"intelligence_analysis", "duplicate_detection"}: return {"suggestions": len(IntelligenceService(self.repository).analyze(cancel_callback=cancel_callback).suggestions)}, ()
        if step_type == "source_analysis": return {"findings": len(SourceAnalysisService(self.repository).analyze(cancel_callback=cancel_callback).findings)}, ()
        if step_type == "create_backup": return {"backup": str(backup_database(self.repository.db_name, self.backup_dir))}, ()
        if step_type in {"add_audit_entry", "add_collaboration_change"}: return {"status": "correlated at successful workflow completion"}, ()
        if step_type == "delay": return {"warning": "Delay is a cooperative boundary; no sleep is performed"}, ()
        if step_type in {"user_confirmation", "conditional_branch", "timeline_generation", "tree_generation", "map_preparation", "merge_preview", "conflict_resolution_preview", "gedcom_import_preview", "export_report", "create_snapshot", "stop"}: return {"status": "previewed"}, ()
        raise ValueError(f"Unsupported executable step: {step_type}")

    def _finish(self, run_id, workflow, mode, started, status, results, warnings, failures, confirmations, cancelled, files, before, after):
        run = WorkflowRun(run_id, workflow.workflow_uuid, workflow.version, mode, started, self._now(), status, tuple(results), tuple(warnings), tuple(failures), tuple(sorted(confirmations)), cancelled, tuple(files), {"before": before, "after": after, "unchanged": before == after})
        self.history_dir.mkdir(parents=True, exist_ok=True); self._atomic(self.history_dir / f"{run_id}.json", asdict(run)); return run
    def _require_valid(self, workflow):
        validation = self.validate(workflow)
        if validation.errors: raise ValueError("; ".join(validation.errors))
    def _variables(self, workflow, variables):
        return {"project_path": str(Path(self.repository.db_name).resolve()), "temporary_output_directory": str(self.data_dir / "workflows" / "temporary"), "report_directory": str(self.report_dir), "selected_person": "", "selected_family": "", "selected_source": "", "date_time": self._now(), "workflow_run_uuid": str(uuid4()), **workflow.variables, **(variables or {})}
    @staticmethod
    def _resolve(value, variables):
        if isinstance(value, dict): return {key: WorkflowAutomationService._resolve(item, variables) for key, item in sorted(value.items())}
        if isinstance(value, list): return [WorkflowAutomationService._resolve(item, variables) for item in value]
        if isinstance(value, str):
            for key, replacement in variables.items(): value = value.replace("${" + key + "}", str(replacement))
        return value
    @staticmethod
    def _valid_condition(expression):
        return bool(__import__("re").fullmatch(r"(?:previous_status|warning_count|blocker_count|issue_count|duplicate_count|export_success|user_response|[A-Za-z_][A-Za-z0-9_]*)\s*(?:==|!=)\s*[A-Za-z0-9_ .-]+", str(expression)))
    @staticmethod
    def _defaults(step_type): return {key: "${project_path}" if key == "path" else "Confirm" if key == "message" else "previous_status == ok" if key == "condition" else 0 for key in REQUIRED.get(step_type, ())}
    def _checksum(self):
        digest = hashlib.sha256()
        with Path(self.repository.db_name).open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""): digest.update(block)
        return digest.hexdigest()
    @staticmethod
    def _now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
    @staticmethod
    def _atomic(path, payload):
        temporary = path.with_suffix(path.suffix + ".tmp"); temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"); temporary.replace(path)