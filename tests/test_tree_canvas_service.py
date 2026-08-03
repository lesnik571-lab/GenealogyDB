import sqlite3
import inspect
from pathlib import Path
from types import SimpleNamespace

import pytest

from database import initialize_database
from repository.person_repository import PersonRepository
from audit_service import AuditService
from tree_canvas_service import (
    CARD_HEIGHT, CARD_WIDTH, MAX_ZOOM, MIN_ZOOM, TreeCanvasChange,
    TreeCanvasNavigation, TreeCanvasSafetyError, TreeCanvasService,
)
from viewer import GenealogyViewer


def repository(tmp_path):
    path = tmp_path / "tree.db"
    initialize_database(path)
    return PersonRepository(path)


def add_person(repo, index, first=None):
    return repo.create_person({
        "gedcom_id": f"I{index}", "first_name": first or f"Person{index}", "last_name": "Tree",
        "birth_date": f"{1900 + index}-01-01",
    })


def family(repo, index, husband, wife, children, relationship_type="marriage"):
    return repo.create_family({
        "gedcom_id": f"F{index}", "husband": f"I{husband}", "wife": f"I{wife}",
        "children": [f"I{child}" for child in children], "relationship_type": relationship_type,
    })


def test_deterministic_layout_no_overlap_multiple_spouses_and_half_siblings(tmp_path):
    repo = repository(tmp_path)
    try:
        for index in range(1, 8):
            add_person(repo, index)
        family(repo, 1, 1, 2, [3, 4])
        family(repo, 2, 1, 5, [6])
        family(repo, 3, 7, 2, [])
        service = TreeCanvasService(repo, layout_dir=tmp_path / "layouts")
        first = service.build(3, ancestor_depth=3, descendant_depth=3)
        second = service.build(3, ancestor_depth=3, descendant_depth=3)

        assert first.positions == second.positions
        assert {1, 2, 4, 6, 5}.issubset({node.person_id for node in first.nodes})
        centers = [(x + CARD_WIDTH / 2, y + CARD_HEIGHT / 2) for x, y in first.positions.values()]
        assert len(centers) == len(set(centers))
        bounds = [(x, y, x + CARD_WIDTH, y + CARD_HEIGHT) for x, y in first.positions.values()]
        assert not any(a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1] for index, a in enumerate(bounds) for b in bounds[index + 1:])
    finally:
        repo.close()


def test_adopted_relationship_collapse_positions_navigation_and_zoom_bounds(tmp_path):
    repo = repository(tmp_path)
    try:
        for index in range(1, 8):
            add_person(repo, index)
        family(repo, 1, 1, 2, [3])
        family(repo, 2, 4, 5, [3])
        family(repo, 3, 3, 6, [7], "civil_partner")
        service = TreeCanvasService(repo, layout_dir=tmp_path / "layouts")
        model = service.build(3, ancestor_depth=3, descendant_depth=3)
        assert any(connector.special for connector in model.connectors)
        collapsed = service.build(3, ancestor_depth=3, descendant_depth=3, collapsed_ids=(3,))
        assert 7 not in {node.person_id for node in collapsed.nodes}
        service.save_positions(3, {3: (777, 555)})
        restored = service.build(3)
        assert restored.positions[3] == (777.0, 555.0)
        navigation = TreeCanvasNavigation(3)
        navigation.visit(7)
        assert navigation.back() == 3
        assert navigation.forward() == 7
        assert MIN_ZOOM == 0.35 and MAX_ZOOM == 2.5
    finally:
        repo.close()


def test_exports_and_large_read_only_canvas_do_not_change_database(tmp_path):
    repo = repository(tmp_path)
    try:
        for index in range(1, 501):
            add_person(repo, index, first="" if index == 500 else None)
        family(repo, 1, 1, 251, list(range(2, 251)))
        for index in range(2, 251):
            family(repo, index, index, index + 250, [])
        before = repo.capture_command_state()
        service = TreeCanvasService(repo, layout_dir=tmp_path / "layouts")
        model = service.build(1, ancestor_depth=8, descendant_depth=8)
        svg = service.export_svg(model, tmp_path / "tree.svg", scale=1.0)
        png = service.export_png(model, tmp_path / "tree.png", scale=1.0)
        pdf = service.export_pdf(model, tmp_path / "tree.pdf", scale=1.0)

        assert len(model.nodes) >= 500
        assert svg.read_text(encoding="utf-8").startswith("<svg")
        assert png.read_bytes().startswith(b"\x89PNG")
        assert pdf.read_bytes().startswith(b"%PDF")
        assert repo.capture_command_state() == before
    finally:
        repo.close()


def test_viewer_tree_canvas_is_a_read_only_graph_editor_extension():
    graph_editor = inspect.getsource(GenealogyViewer.open_graph_editor)
    opening = inspect.getsource(GenealogyViewer.open_tree_canvas)
    loading = inspect.getsource(GenealogyViewer._load_tree_canvas)
    drawing = inspect.getsource(GenealogyViewer._draw_tree_canvas)
    exporting = inspect.getsource(GenealogyViewer._export_tree_canvas)

    assert 'text="Интерактивное полотно"' in graph_editor
    assert "tk.Canvas" in opening
    for label in ("Назад", "Вперёд", "Предки", "Потомки", "Вписать", "Центр", "Сохранить позиции", "SVG", "PNG", "PDF"):
        assert f'"{label}"' in opening
    assert "cancellable=True" in loading
    assert "TreeCanvasService(repository).build" in loading
    assert "create_rectangle" in drawing
    assert "create_line" in drawing
    assert '"<Double-1>"' in drawing
    assert '"<Button-3>"' in drawing
    assert "save_positions" in inspect.getsource(GenealogyViewer._save_tree_canvas_positions)
    assert "export_" in exporting
    assert "repository.conn" not in opening + loading + drawing + exporting
    assert "GraphModification" not in opening + loading + drawing


def test_headless_zoom_bounds_and_navigation_callbacks():
    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer._tree_canvas_zoom = 1.0
    calls = []
    viewer._draw_tree_canvas = lambda: calls.append(viewer._tree_canvas_zoom)

    viewer._zoom_tree_canvas(-99)
    assert viewer._tree_canvas_zoom == MIN_ZOOM
    viewer._zoom_tree_canvas(99)
    assert viewer._tree_canvas_zoom == MAX_ZOOM

    viewer._tree_canvas_navigation = TreeCanvasNavigation(1)
    viewer._tree_canvas_navigation.visit(2)
    viewer._tree_canvas_collapsed_ids = {2}
    viewer._load_tree_canvas = lambda: calls.append("load")
    viewer._tree_canvas_back()
    assert viewer._tree_canvas_navigation.current == 1
    assert viewer._tree_canvas_collapsed_ids == set()
    assert calls[-1] == "load"


def test_canvas_edit_preview_blocks_self_relationship_and_dry_run_never_writes(tmp_path):
    repo = repository(tmp_path)
    try:
        add_person(repo, 1)
        service = TreeCanvasService(repo, layout_dir=tmp_path / "layouts")
        before = repo.capture_command_state()
        preview = service.preview_changes([TreeCanvasChange("add_spouse", 1, 1)])

        assert not preview.can_execute
        assert "самим собой" in preview.blockers[0]
        assert service.dry_run(preview)["can_execute"] is False
        assert repo.capture_command_state() == before
        with pytest.raises(TreeCanvasSafetyError):
            service.execute_changes(preview, backup_dir=tmp_path / "backups")
        assert repo.capture_command_state() == before
    finally:
        repo.close()


def test_canvas_edit_changes_relationship_type_with_backup_and_audit(tmp_path):
    repo = repository(tmp_path)
    try:
        add_person(repo, 1)
        add_person(repo, 2)
        family_id = family(repo, 1, 1, 2, [])
        service = TreeCanvasService(repo, layout_dir=tmp_path / "layouts")
        preview = service.preview_changes([
            TreeCanvasChange("change_relationship_type", 1, 2, family_id, relationship_type="civil_partner")
        ])

        result = service.execute_changes(preview, backup_dir=tmp_path / "backups")

        assert result.backup_path.exists()
        assert repo.get_family(family_id)["relationship_type"] == "civil_partner"
        records = AuditService.for_database(repo.db_name).list_records(service="tree_canvas_service")
        assert any(record.service == "tree_canvas_service" for record in records)
    finally:
        repo.close()


def test_canvas_edit_blocks_parent_removal_that_would_leave_children_in_partial_family(tmp_path):
    repo = repository(tmp_path)
    try:
        for index in range(1, 4):
            add_person(repo, index)
        family_id = family(repo, 1, 1, 2, [3])
        preview = TreeCanvasService(repo).preview_changes([
            TreeCanvasChange("remove_relationship", 1, 2, family_id)
        ])

        assert not preview.can_execute
        assert "неполную семью" in preview.blockers[0]
    finally:
        repo.close()


def test_viewer_tree_canvas_editing_controls_are_preview_first_and_headless_safe():
    opening = inspect.getsource(GenealogyViewer.open_tree_canvas)
    drawing = inspect.getsource(GenealogyViewer._draw_tree_canvas)
    closing = inspect.getsource(GenealogyViewer._close_tree_canvas)
    menu = inspect.getsource(GenealogyViewer._tree_canvas_context_menu)

    assert 'value="Просмотр"' in opening
    assert '"Просмотр", "Редактирование"' in opening
    for label in ("Добавить родителя", "Добавить ребёнка", "Добавить супруга", "Добавить партнёра", "Удалить связь", "Открыть карточку"):
        assert f'label="{label}"' in menu
    assert "tree-canvas-connector" in drawing
    assert "Отменить неподтверждённые изменения?" in closing
    assert "repository.conn" not in opening + drawing + menu
