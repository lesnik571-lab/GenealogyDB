from pathlib import Path

from database import initialize_database
from integration_stabilization_service import IntegrationStabilizationService


def test_integration_report_is_deterministic_and_preserves_configured_database(tmp_path):
    configured = tmp_path / "configured.db"; initialize_database(configured)
    service = IntegrationStabilizationService(configured, project_root=Path(__file__).resolve().parents[1])
    before = configured.read_bytes(); report = service.validate(); first = [path.read_bytes() for path in service.export_all(report)]
    assert configured.read_bytes() == before and report.configured_checksum_before == report.configured_checksum_after
    assert not report.blockers and report.recommendation == "READY WITH WARNINGS"
    assert first == [path.read_bytes() for path in service.export_all(report)]


def test_lifecycle_rejects_invalid_transitions_and_uuid_validation():
    assert not IntegrationStabilizationService.transition_allowed("exchange", "Rejected", "Accepted for merge")
    assert IntegrationStabilizationService.transition_allowed("workflow", "Draft", "Validated")
    assert not IntegrationStabilizationService._valid_uuid("not-a-uuid")