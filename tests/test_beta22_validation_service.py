from pathlib import Path

from beta21_validation_service import BetaValidationCheck
from beta22_validation_service import (
    BLOCKED,
    PASS,
    Beta22ValidationService,
)
from database import initialize_database


def test_beta22_validation_covers_navigation_and_preserves_configured_database(tmp_path):
    configured = tmp_path / "configured.db"
    initialize_database(configured)
    before = configured.read_bytes()
    service = Beta22ValidationService(configured, project_root=Path.cwd())
    service.report_dir = tmp_path / "reports"

    report = service.validate()
    checks = {check.check_id: check for check in report.checks}

    assert configured.read_bytes() == before
    assert checks["navigation.persistence"].status == PASS
    assert checks["viewer.navigation-2.2"].status == PASS
    assert checks["packaging.metadata-2.2"].status == PASS
    assert report.recommendation == "READY FOR 2.2.0-BETA2"
    assert all(path.is_file() for path in service.export_all(report))


def test_beta22_recommendation_blocks_on_failed_check():
    checks = (BetaValidationCheck("blocked", "Navigation", BLOCKED, "", "failed"),)

    assert Beta22ValidationService.recommendation(checks) == "NOT READY FOR 2.2.0-BETA2"
