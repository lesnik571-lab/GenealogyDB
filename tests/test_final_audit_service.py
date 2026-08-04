from pathlib import Path

from build_info import APP_VERSION
from final_audit_service import BLOCKED, PASS, FinalAuditCheck, FinalAuditService


def test_final_audit_reports_static_viewer_contracts_and_deterministic_outputs(tmp_path):
    service = FinalAuditService(project_root=Path.cwd())
    service.report_dir = tmp_path / "reports"
    report = service.audit()
    checks = {check.check_id: check for check in report.checks}

    assert checks["viewer.interactions"].status == PASS
    assert checks["viewer.help"].status == PASS
    assert checks["viewer.no-direct-sql"].status == PASS
    assert checks["viewer.imports"].status == PASS
    assert checks["repository.hygiene"].status == PASS
    if APP_VERSION == "2.1.0":
        assert checks["packaging.version"].status == PASS
        assert report.recommendation == "READY FOR 2.1.0"
    else:
        assert checks["packaging.version"].status == BLOCKED
        assert report.recommendation == "NOT READY"
    first = service.export_all(report)
    assert first == service.export_all(report)
    assert all(path.exists() and path.stat().st_size > 0 for path in first)


def test_final_audit_recommendation_requires_no_blockers():
    checks = (FinalAuditCheck("ready", "Viewer", PASS, "ok"),)
    assert FinalAuditService.recommendation(checks) == "READY FOR 2.1.0"
    assert FinalAuditService.recommendation((*checks, FinalAuditCheck("blocked", "Packaging", BLOCKED, "", "missing"))) == "NOT READY"