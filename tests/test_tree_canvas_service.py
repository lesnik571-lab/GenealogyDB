import sqlite3
import inspect
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from database import initialize_database
from repository.person_repository import PersonRepository
from audit_service import AuditService
from tree_canvas_service import (
    CARD_HEIGHT, CARD_WIDTH, MAX_ZOOM, MIN_ZOOM, TreeCanvasChange,
    TreeAutoLayoutEngine, TreeCanvasLayoutCommand, TreeCanvasNavigation,
    TreeCanvasConnector, TreeCanvasNode, TreeCanvasSafetyError, TreeCanvasService,
    TreeCanvasPrintOptions, TreeLayoutOptions,
)
from undo_manager import UndoManager
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
    for label in ("Назад", "Вперёд", "Предки", "Потомки", "Подогнать к окну", "Центр", "Сохранить позиции", "SVG", "PNG", "PDF", "Автораскладка", "Отменить раскладку", "Повторить раскладку", "Сбросить расположение", "Закрепить карточку", "Открепить карточку", "Открепить все"):
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


def test_auto_layout_is_deterministic_groups_spouses_and_preserves_pins(tmp_path):
    repo = repository(tmp_path)
    try:
        for index in range(1, 8):
            add_person(repo, index)
        family(repo, 1, 1, 2, [3, 4])
        family(repo, 2, 1, 5, [6])
        family(repo, 3, 7, 2, [])
        model = TreeCanvasService(repo).build(3, ancestor_depth=3, descendant_depth=3)
        engine = TreeAutoLayoutEngine()
        options = TreeLayoutOptions(layout_type="compact_family_groups", compact=True)
        grouped = engine.layout(model.nodes, model.connectors, options)
        first = engine.layout(model.nodes, model.connectors, options, pinned_positions={1: (777, 333)})
        second = engine.layout(model.nodes, model.connectors, options, pinned_positions={1: (777, 333)})

        assert first == second
        assert first[1] == (777.0, 333.0)
        assert abs(grouped[1][1] - grouped[2][1]) < CARD_HEIGHT + 1
        assert TreeCanvasService._overlap_count(first, CARD_WIDTH, CARD_HEIGHT) == 0
    finally:
        repo.close()


def test_auto_layout_preview_cancel_undo_named_and_json_config_do_not_change_genealogy(tmp_path):
    repo = repository(tmp_path)
    try:
        for index in range(1, 5):
            add_person(repo, index)
        family(repo, 1, 1, 2, [3, 4])
        service = TreeCanvasService(repo, layout_dir=tmp_path / "layouts")
        model = service.build(3, ancestor_depth=3, descendant_depth=3)
        before_database = repo.capture_command_state()
        current = dict(model.positions)
        preview = service.preview_auto_layout(model, positions=current, pinned_nodes=(3,))

        assert current == model.positions
        assert preview.positions[3] == current[3]
        with pytest.raises(RuntimeError):
            service.preview_auto_layout(model, cancel_callback=lambda: (_ for _ in ()).throw(RuntimeError("cancelled")))
        assert current == model.positions

        result = service.apply_auto_layout(model, preview, name="plan-a")
        command = TreeCanvasLayoutCommand(result)
        manager = UndoManager()
        manager.record_applied(command)
        assert result.path.exists()
        manager.undo()
        assert not result.path.exists()
        manager.redo()
        positions, pinned, metadata = service.load_named_layout(model.center_id, "plan-a")
        assert positions == preview.positions and pinned == frozenset({3})
        assert metadata["name"] == "plan-a"
        duplicate = service.duplicate_named_layout(model.center_id, "plan-a", "copy")
        exported = service.export_layout_configuration(model.center_id, "copy", tmp_path / "layout.json")
        imported = service.import_layout_configuration("imported", exported, center_id=model.center_id)
        assert duplicate.exists() and imported.exists()
        service.rename_named_layout(model.center_id, "imported", "renamed")
        assert {record["name"] for record in service.list_named_layouts(model.center_id)} >= {"plan-a", "copy", "renamed"}
        service.delete_named_layout(model.center_id, "copy")
        assert repo.capture_command_state() == before_database
    finally:
        repo.close()


def test_auto_layout_handles_disconnected_components_reused_ancestors_and_all_directions():
    nodes = tuple(
        TreeCanvasNode(index, f"P{index}", "", "", f"I{index}", "", generation, ())
        for index, generation in ((1, -2), (2, -1), (3, -1), (4, 0), (5, 0), (6, 1), (7, 4), (8, 4))
    )
    connectors = (
        TreeCanvasConnector("p1", "parent", 1, 2, 1, "marriage"),
        TreeCanvasConnector("p2", "parent", 1, 3, 2, "marriage"),
        TreeCanvasConnector("p3", "parent", 2, 4, 3, "marriage"),
        TreeCanvasConnector("p4", "parent", 3, 5, 4, "marriage"),
        TreeCanvasConnector("s1", "spouse", 4, 5, 5, "civil_partner", True),
        TreeCanvasConnector("p5", "parent", 4, 6, 5, "marriage"),
    )
    engine = TreeAutoLayoutEngine()

    for mode in ("top_to_bottom", "bottom_to_top", "left_to_right", "right_to_left", "ancestors_only", "descendants_only", "hourglass", "fan", "compact_family_groups"):
        positions = engine.layout(nodes, connectors, TreeLayoutOptions(layout_type=mode, compact=mode == "compact_family_groups"))
        assert set(positions) == {node.person_id for node in nodes}
        assert TreeCanvasService._overlap_count(positions, CARD_WIDTH, CARD_HEIGHT) == 0


def test_auto_layout_benchmark_is_deterministic_for_500_and_1000_nodes():
    engine = TreeAutoLayoutEngine()
    timings = {}
    for count in (500, 1000):
        nodes = tuple(TreeCanvasNode(index, f"P{index}", "", "", f"I{index}", "", 0, ()) for index in range(count))
        started = time.perf_counter()
        first = engine.layout(nodes, (), TreeLayoutOptions(layout_type="top_to_bottom"))
        elapsed = time.perf_counter() - started
        timings[count] = elapsed
        assert first == engine.layout(nodes, (), TreeLayoutOptions(layout_type="top_to_bottom"))
        assert TreeCanvasService._overlap_count(first, CARD_WIDTH, CARD_HEIGHT) == 0
    assert timings[500] < 2.0
    assert timings[1000] < 2.0


def test_print_export_preview_supports_metadata_branch_scope_poster_and_all_formats(tmp_path):
    repo = repository(tmp_path)
    try:
        for index in range(1, 6):
            add_person(repo, index)
        family(repo, 1, 1, 2, [3])
        family(repo, 2, 3, 4, [5], "civil_partner")
        service = TreeCanvasService(repo, layout_dir=tmp_path / "layouts")
        model = service.build(3, ancestor_depth=3, descendant_depth=3)
        before = repo.capture_command_state()
        options = TreeCanvasPrintOptions(
            scope="selected_branch", orientation="portrait", fit_mode="manual",
            scale=8.0, poster=True, overlap=12, title="Branch print", dpi=144,
        )
        preview = service.prepare_print_preview(model, options)

        assert {node.person_id for node in preview.model.nodes} == {3, 4, 5}
        assert preview.page_count > 1
        assert preview.metadata["root_person_id"] == 3
        assert preview.metadata["number_of_people"] == 3
        svg = service.export_canvas(preview, tmp_path / "tree.svg", "svg")
        png = service.export_canvas(preview, tmp_path / "tree.png", "png")
        jpeg = service.export_canvas(preview, tmp_path / "tree.jpg", "jpeg")
        pdf = service.export_canvas(preview, tmp_path / "tree.pdf", "pdf")

        assert '"root_person_id": 3' in svg.read_text(encoding="utf-8")
        assert png.read_bytes().startswith(b"\x89PNG")
        assert jpeg.read_bytes().startswith(b"\xff\xd8")
        assert pdf.read_bytes().startswith(b"%PDF")
        assert repo.capture_command_state() == before
    finally:
        repo.close()


def test_viewer_print_export_center_is_worker_backed_and_sql_free():
    opening = inspect.getsource(GenealogyViewer.open_tree_canvas)
    dialog = inspect.getsource(GenealogyViewer.open_tree_canvas_print_export)
    preview = inspect.getsource(GenealogyViewer._preview_tree_canvas_print_export)
    submission = inspect.getsource(GenealogyViewer._submit_tree_canvas_export)

    assert 'text="Печать / Экспорт"' in opening
    for value in ("current_view", "selected_branch", "complete_tree", "portrait", "landscape", "fit_width", "fit_page", "jpeg"):
        assert f'"{value}"' in dialog
    assert "prepare_print_preview" in preview
    assert "_submit_repository_task" in preview + submission
    assert "repository.conn" not in opening + dialog + preview + submission
