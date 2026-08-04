from pathlib import Path

from beta21_validation_service import BLOCKED, Beta21ValidationService
from database import initialize_database


def validator(tmp_path):
    configured = tmp_path / "configured.db"; initialize_database(configured)
    return configured, Beta21ValidationService(configured, project_root=Path(__file__).resolve().parents[1])


def test_complete_temporary_scenario_ordering_and_configured_database_isolation(tmp_path):
    configured, service = validator(tmp_path); before = configured.read_bytes()
    report = service.validate()
    identifiers = [check.check_id for check in report.checks]
    assert configured.read_bytes() == before
    assert identifiers.index("collaboration.identities") < identifiers.index("exchange.security") < identifiers.index("merge.preview") < identifiers.index("conflict.preview") < identifiers.index("history.preview") < identifiers.index("workflow.safety")
    assert report.configured_checksum_before == report.configured_checksum_after


def test_security_preview_cleanup_and_confirmation_checks_pass_in_temporary_space(tmp_path):
    _, service = validator(tmp_path); report = service.validate(); checks = {check.check_id: check for check in report.checks}
    assert checks["exchange.security"].status == "PASS"
    assert checks["merge.preview"].status == "PASS"
    assert checks["history.preview"].status == "PASS"
    assert checks["workflow.safety"].status == "PASS"
    assert checks["undo.backup-boundaries"].status == "PASS"


def test_reports_are_deterministic_and_correlation_remediation_recalculates_recommendation(tmp_path):
    _, service = validator(tmp_path); report = service.validate(); first = [path.read_bytes() for path in service.export_all(report)]
    assert report.recommendation == "READY FOR 2.1.0-BETA1"
    assert next(check for check in report.checks if check.check_id == "audit.correlation").status == "PASS"
    assert first == [path.read_bytes() for path in service.export_all(report)]