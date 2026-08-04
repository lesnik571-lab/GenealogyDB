from pathlib import Path

import pytest

from database import initialize_database
from repository.person_repository import PersonRepository
from workflow_automation_service import DRY_RUN, FULL_RUN, READ_ONLY_RUN, WorkflowAutomationService
from audit_service import AuditService
from collaboration_service import CollaborationService
from operation_correlation import validate_correlation


def repository(tmp_path):
    path = tmp_path / "workflow.db"; initialize_database(path); return PersonRepository(path)


def test_crud_templates_determinism_and_corrupted_recovery(tmp_path):
    repo = repository(tmp_path)
    try:
        service = WorkflowAutomationService(repo, data_dir=tmp_path / "data")
        workflow = service.create("Test", step_types=("validation_scan", "source_analysis")); first = service.save(workflow).read_bytes(); service.save(workflow)
        assert first == (service.workflow_dir / f"{workflow.workflow_uuid}.json").read_bytes() and service.load(workflow.workflow_uuid) == workflow
        duplicate = service.duplicate(workflow); service.save(duplicate); assert len(service.list()) == 2 and service.rename(workflow, "Renamed").name == "Renamed"
        assert {item.name for item in service.templates()} == {"After GEDCOM import", "Before project merge", "Release health check", "Research review"}
        (service.workflow_dir / "broken.json").write_text("{", encoding="utf-8")
        with pytest.raises(ValueError): service.load("broken")
    finally: repo.close()


def test_validation_modes_variables_conditions_and_confirmation(tmp_path):
    repo = repository(tmp_path)
    try:
        service = WorkflowAutomationService(repo, data_dir=tmp_path / "data")
        workflow = service.create("Bad", step_types=("unknown",)); assert service.validate(workflow).errors
        workflow = service.create("Import", step_types=("gedcom_import",)); assert "Missing confirmation" in "; ".join(service.validate(workflow).errors)
        workflow = service.create("Read", step_types=("validation_scan", "delay", "stop", "source_analysis"), variables={"x": "value"})
        assert service.validate(workflow).warnings and service.preview(workflow)[1]["parameters"]["seconds"] == 0
        before = repo.capture_command_state(); dry = service.run(workflow, mode=DRY_RUN); readonly = service.run(workflow, mode=READ_ONLY_RUN)
        assert dry.status == "dry_run" and readonly.status == "completed" and repo.capture_command_state() == before
        conditional = service.create("Conditional", step_types=("conditional_branch",))
        invalid_condition = service.edit_step(conditional, conditional.steps[0].step_id, {"condition": "__import__('os')"}); assert service.validate(invalid_condition).errors
    finally: repo.close()


def test_execution_history_reports_cancel_backup_and_safe_failure(tmp_path):
    repo = repository(tmp_path)
    try:
        service = WorkflowAutomationService(repo, data_dir=tmp_path / "data", backup_dir=tmp_path / "backups")
        workflow = service.create("Run", step_types=("create_backup", "validation_scan", "add_audit_entry", "add_collaboration_change"))
        run = service.run(workflow, mode=FULL_RUN); assert run.status == "completed" and Path(service.history_dir / f"{run.run_uuid}.json").exists() and all(path.exists() for path in service.export_all(run))
        audit = [record for record in AuditService.for_database(repo.db_name).list_records() if record.operation_uuid == run.run_uuid]
        changes = [change for change in CollaborationService(repo.db_name, data_dir=tmp_path / "data").changes() if change.operation_uuid == run.run_uuid]
        assert len(audit) == len(changes) == 1 and validate_correlation(audit, changes)["complete"]
        cancelled = service.run(workflow, mode=FULL_RUN, cancel_callback=lambda: (_ for _ in ()).throw(RuntimeError("cancel"))); assert cancelled.cancelled
        dangerous = service.create("Danger", step_types=("user_confirmation", "gedcom_import")); result = service.run(dangerous, mode=FULL_RUN, confirmations=("gedcom_import",)); assert result.status == "failed" and list((tmp_path / "backups").glob("*.db"))
    finally: repo.close()


def test_order_conditions_warnings_service_availability_and_database_isolation(tmp_path):
    repo = repository(tmp_path)
    try:
        service = WorkflowAutomationService(repo, data_dir=tmp_path / "data", backup_dir=tmp_path / "backups")
        workflow = service.create("Order", step_types=("validation_scan", "conditional_branch", "delay", "source_analysis"))
        assert service.evaluate_condition("previous_status == ok", {"previous_status": "ok"})
        assert not service.evaluate_condition("warning_count != 0", {"warning_count": 0})
        assert all(service.available_services().values())
        before = repo.capture_command_state(); run = service.run(workflow, mode=FULL_RUN)
        assert [item["step"] for item in run.step_results] == ["validation_scan", "conditional_branch", "delay"]
        assert run.warnings and repo.capture_command_state() == before
        dangerous = service.create("Write boundary", step_types=("user_confirmation", "gedcom_import"))
        result = service.run(dangerous, mode=FULL_RUN, confirmations=("gedcom_import",))
        assert result.status == "failed" and list((tmp_path / "backups").glob("*.db")) and repo.capture_command_state() == before
    finally: repo.close()