from pathlib import Path

from beta21_validation_service import BetaValidationCheck
from beta22_validation_service import (
    BLOCKED,
    PASS,
    Beta22ValidationService,
)
from database import initialize_database


def test_beta22_validation_covers_navigation_and_preserves_configured_database(tmp_path, monkeypatch):
    configured = tmp_path / "configured.db"
    initialize_database(configured)
    before = configured.read_bytes()
    service = Beta22ValidationService(configured, project_root=Path.cwd())
    service.report_dir = tmp_path / "reports"
    monkeypatch.setattr(
        service,
        "_packaging_check",
        lambda: BetaValidationCheck(
            "packaging.metadata-2.2",
            "Packaging and documentation",
            PASS,
            "covered independently",
            "",
        ),
    )

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

def test_beta22_packaging_accepts_rc_development_version(tmp_path):
    required_files = (
        "GenealogyDB.spec",
        "installer/GenealogyDB.iss",
        "README.md",
        "USER_MANUAL.md",
        "CHANGELOG.md",
        "LICENSE",
    )
    for relative_path in required_files:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")

    (tmp_path / "build_info.py").write_text(
        'APP_VERSION = "2.2.0-rc1-dev"\n',
        encoding="utf-8",
    )
    (tmp_path / "build_release.ps1").write_text(
        '[string]$Version = "2.2.0-rc1-dev"\n',
        encoding="utf-8",
    )

    service = Beta22ValidationService(
        tmp_path / "configured.db",
        project_root=tmp_path,
    )

    assert service._packaging_check().status == PASS

