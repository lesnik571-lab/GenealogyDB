from pathlib import Path

import pytest

from database import initialize_database
from rc_validation_service import PASS
from rc22_validation_service import RC22ValidationService


def test_rc22_validation_preserves_database_and_accepts_current_version(tmp_path):
    configured = tmp_path / "configured.db"
    initialize_database(configured)
    before = RC22ValidationService._checksum(configured)
    service = RC22ValidationService(configured, project_root=Path.cwd())
    service.report_dir = tmp_path / "reports"

    report = service.validate()
    packaging_version = next(
        check for check in report.checks if check.check_id == "packaging.version"
    )

    assert report.checksum_before == before == report.checksum_after
    assert packaging_version.status == PASS
    assert not report.blockers
    assert report.recommendation in {"READY FOR RC1", "READY FOR RC1 WITH WARNINGS"}


def test_rc22_validation_rejects_non_22_version(tmp_path, monkeypatch):
    monkeypatch.setattr("rc22_validation_service.APP_VERSION", "2.1.0-rc1")
    (tmp_path / "build_info.py").write_text(
        'APP_VERSION = "2.1.0-rc1"\n',
        encoding="utf-8",
    )
    service = RC22ValidationService(
        tmp_path / "configured.db",
        project_root=tmp_path,
    )

    with pytest.raises(AssertionError, match="GenealogyDB 2.2"):
        service._version_ready()
