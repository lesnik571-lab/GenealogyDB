import csv
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from audit_service import AuditService
from gedcom_repair_service import GedcomRepairCommand, GedcomRepairService
from undo_manager import UndoManager
from viewer import GenealogyViewer


def write_gedcom(tmp_path, name="broken.ged", content=None, encoding="utf-8"):
    content = content or """0 HEAD
1 CHAR UTF-8
0 @I1@ INDI
1 NAME Alice /One/
1 FAMS @F1@
0 @I1@ INDI
1 NAME Duplicate /One/
0 @I2@ INDI
1 NAME Bob /Two/
1 FAMC @F404@
0 @I3@ INDI
0 @I4@ INDI
1 NAME Cycle /Four/
1 FAMS @F2@
0 @I5@ INDI
1 NAME Cycle /Five/
1 FAMS @F3@
0 @F1@ FAM
1 HUSB @I1@
1 HUSB @I1@
1 WIFE @I404@
1 CHIL @I2@
1 CHIL @I2@
1 DATE 31 FEB 1900
1 BADT NOT_A_VALID_EVENT
0 @F2@ FAM
1 HUSB @I4@
1 CHIL @I5@
0 @F3@ FAM
1 HUSB @I5@
1 CHIL @I4@
0 @F4@ FAM
"""
    path = tmp_path / name
    path.write_text(content, encoding=encoding)
    return path


def issue_kinds(preview):
    return {issue.kind for issue in preview.issues}


def test_analyze_detects_all_required_gedcom_issue_categories(tmp_path):
    source = write_gedcom(tmp_path)
    preview = GedcomRepairService().analyze(source)
    kinds = issue_kinds(preview)

    assert {
        "broken_reference", "duplicate_id", "missing_family_link", "invalid_date",
        "invalid_event_tag", "orphan_individual", "orphan_family", "circular_reference",
        "duplicate_spouse", "duplicate_child", "empty_record",
    } <= kinds
    assert all(issue.severity in {"critical", "warning", "info"} for issue in preview.issues)
    assert all(issue.location and issue.recommended_repair for issue in preview.issues)
    assert all(isinstance(issue.automatic_repair, bool) for issue in preview.issues)


def test_invalid_utf8_is_detected_and_safe_repair_writes_only_copy(tmp_path):
    source = tmp_path / "invalid-utf8.ged"
    original = b"0 HEAD\n0 @I1@ INDI\n1 NAME Bad \xff Name\n"
    source.write_bytes(original)
    service = GedcomRepairService()
    analyzed = service.analyze(source)
    encoding_issue = next(issue for issue in analyzed.issues if issue.kind == "encoding")
    repaired = service.preview(source, (encoding_issue.issue_id,))
    destination = tmp_path / "invalid-utf8.repaired.ged"

    result = service.execute(repaired, destination)

    assert source.read_bytes() == original
    assert result.repaired_path == destination
    assert destination.read_text(encoding="utf-8").find("?") >= 0


def test_preview_repairs_selected_safe_lines_and_all_safe_never_overwrites_original(tmp_path):
    source = write_gedcom(tmp_path)
    service = GedcomRepairService()
    analyzed = service.analyze(source)
    safe = analyzed.safe_issue_ids
    selected = service.preview(source, safe[:2])
    all_safe = service.preview(source, safe_only=True)
    destination = tmp_path / "repaired.ged"

    assert len(selected.selected_issue_ids) == 2
    assert len(all_safe.selected_issue_ids) == len(safe)
    with pytest.raises(ValueError, match="перезаписывать"):
        service.execute(all_safe, source)
    result = service.execute(all_safe, destination)

    assert result.repaired_path.exists()
    repaired = destination.read_text(encoding="utf-8")
    assert "31 FEB 1900" not in repaired
    assert "1 BADT NOT_A_VALID_EVENT" not in repaired
    assert "1 EVEN NOT_A_VALID_EVENT" in repaired
    assert "1 HUSB @I1@\n1 HUSB @I1@" not in repaired
    assert "1 CHIL @I2@\n1 CHIL @I2@" not in repaired
    assert source.read_text(encoding="utf-8") != repaired


def test_unsafe_selection_stale_source_and_diagnostics_only_are_blocked(tmp_path):
    source = write_gedcom(tmp_path)
    service = GedcomRepairService()
    analyzed = service.analyze(source)
    unsafe = next(issue for issue in analyzed.issues if not issue.automatic_repair)
    with pytest.raises(ValueError, match="безопасного"):
        service.preview(source, (unsafe.issue_id,))

    preview = service.preview(source, safe_only=True)
    source.write_text(source.read_text(encoding="utf-8") + "0 TRLR\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="изменился"):
        service.execute(preview, tmp_path / "stale.ged")

    read_only = GedcomRepairService(diagnostics_only=True)
    preview = read_only.preview(source, safe_only=True)
    with pytest.raises(PermissionError, match="диагностики"):
        read_only.execute(preview, tmp_path / "blocked.ged")


def test_reports_audit_and_file_undo_redo(tmp_path):
    source = write_gedcom(tmp_path)
    database = tmp_path / "genealogy.db"
    database.touch()
    service = GedcomRepairService(database)
    preview = service.preview(source, safe_only=True)
    csv_path = service.export_report_csv(preview, tmp_path / "report.csv")
    json_path = service.export_report_json(preview, tmp_path / "report.json")
    result = service.execute(preview, tmp_path / "repaired.ged")
    command = GedcomRepairCommand(result)
    manager = UndoManager()
    manager.record_applied(command)

    with csv_path.open(encoding="utf-8-sig", newline="") as report:
        rows = list(csv.DictReader(report))
    report_json = json.loads(json_path.read_text(encoding="utf-8"))
    audit = AuditService.for_database(database).list_records(service="gedcom_repair_service")
    assert rows and {"severity", "location", "recommended_repair", "automatic_repair"} <= set(rows[0])
    assert report_json["issues"]
    assert command.has_effect
    assert manager.undo()
    assert not result.repaired_path.exists()
    assert manager.redo()
    assert result.repaired_path.exists()
    assert len(audit) == 1
    assert audit[0].operation_type == "gedcom_repair"
    assert audit[0].batch_id


def test_repair_batches_selected_safe_issues_into_one_output(tmp_path):
    source = write_gedcom(tmp_path)
    service = GedcomRepairService()
    preview = service.preview(source, safe_only=True)
    result = service.execute(preview, tmp_path / "batch-repaired.ged")

    assert len(result.selected_issue_ids) > 1
    assert result.repaired_path.exists()


def test_viewer_gedcom_repair_button_preview_workers_and_exports_are_wired():
    widgets = inspect.getsource(GenealogyViewer._create_widgets)
    opening = inspect.getsource(GenealogyViewer.open_gedcom_repair_center)
    analysis = inspect.getsource(GenealogyViewer._analyze_gedcom_repair_file)
    repair = inspect.getsource(GenealogyViewer._repair_gedcom_issue_ids)
    exporting = inspect.getsource(GenealogyViewer._export_gedcom_repair_report)
    completion = inspect.getsource(GenealogyViewer._complete_gedcom_repair)

    assert 'text="Исправление GEDCOM"' in widgets
    assert "command=self.open_gedcom_repair_center" in widgets
    for column in ("Важность", "Расположение", "Рекомендация", "Авто"):
        assert f'"{column}"' in opening
    assert "Исправить выбранные" in opening
    assert "Исправить все безопасные" in opening
    assert "Только диагностика" in opening
    assert "_submit_repository_task" in analysis
    assert "GedcomRepairService" in analysis
    assert "service.preview(source_path, issue_ids)" in repair
    assert "service.execute(preview, destination)" in repair
    assert "_submit_repository_task" in exporting
    assert "GedcomRepairCommand(result)" in completion
    assert ".conn" not in opening + analysis + repair + exporting + completion


def test_headless_gedcom_repair_render_selection_and_completion():
    class FakeTree:
        def __init__(self):
            self.rows = {}

        def get_children(self):
            return tuple(self.rows)

        def delete(self, item):
            self.rows.pop(item, None)

        def insert(self, _parent, _index, iid=None, values=()):
            self.rows[iid] = list(values)

        def item(self, item, values=None):
            if values is not None:
                self.rows[item] = list(values)
            return {"values": self.rows[item]}

    class FakeLabel:
        def __init__(self):
            self.text = ""

        def config(self, **kwargs):
            self.text = kwargs.get("text", self.text)

    class FakeButton:
        def __init__(self):
            self.state = ""

        def config(self, **kwargs):
            self.state = kwargs["state"]

    class FakeVar:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer._gedcom_repair_issue_tree = FakeTree()
    viewer._gedcom_repair_status = FakeLabel()
    viewer._gedcom_repair_apply_button = FakeButton()
    viewer._gedcom_repair_diagnostics_var = FakeVar(False)
    preview = SimpleNamespace(
        issues=(
            SimpleNamespace(issue_id="safe", automatic_repair=True, severity="warning", location="L1", description="Bad", recommended_repair="Fix"),
            SimpleNamespace(issue_id="manual", automatic_repair=False, severity="critical", location="L2", description="Manual", recommended_repair="Review"),
        ),
    )

    viewer._render_gedcom_repair_preview(preview)

    assert viewer._selected_gedcom_repair_issue_ids() == ("safe",)
    assert viewer._gedcom_repair_apply_button.state == "normal"
    assert "Проблем: 2" in viewer._gedcom_repair_status.text
    viewer._gedcom_repair_diagnostics_var.value = True
    viewer._update_gedcom_repair_controls()
    assert viewer._gedcom_repair_apply_button.state == "disabled"

    calls = []
    viewer._get_undo_manager = lambda: SimpleNamespace(
        record_applied=lambda command: calls.append(("undo", command.name))
    )
    viewer._analyze_gedcom_repair_file = lambda: calls.append(("analyze",))
    viewer._gedcom_repair_window = None
    result = SimpleNamespace(repaired_path=Path("repaired.ged"))
    viewer._complete_gedcom_repair(result)
    assert calls == [("undo", "Исправление GEDCOM"), ("analyze",)]
