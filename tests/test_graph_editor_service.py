import inspect
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from audit_service import AuditService
from graph_editor_service import (
    GraphEditorSafetyError,
    GraphEditorService,
    GraphModification,
)
from repository.person_repository import PersonRepository
from undo_manager import AppliedDeltaCommand, UndoManager
from viewer import GenealogyViewer


def build_repository(tmp_path, name="graph.db"):
    database = tmp_path / name
    connection = sqlite3.connect(database)
    connection.executescript(Path("schema.sql").read_text(encoding="utf-8"))
    connection.close()
    return PersonRepository(str(database))


def add_person(repository, number, sex="", first_name=None):
    return repository.create_person({
        "gedcom_id": f"I{number}",
        "first_name": first_name or f"Person{number}",
        "last_name": "Graph",
        "sex": sex,
    })


def add_family(repository, number, husband="", wife="", children=(), relationship_type="unknown"):
    return repository.create_family({
        "gedcom_id": f"F{number}",
        "husband": husband,
        "wife": wife,
        "children": list(children),
        "relationship_type": relationship_type,
    })


def test_build_graph_contains_people_families_and_automatically_routable_edges(tmp_path):
    repository = build_repository(tmp_path)
    try:
        add_person(repository, 1, "M")
        add_person(repository, 2, "F")
        add_person(repository, 3)
        add_family(repository, 1, "I1", "I2", ("I3",), "marriage")

        graph = GraphEditorService(repository).build_graph()

        assert {node.person_id for node in graph.nodes} == {1, 2, 3}
        assert len(graph.families) == 1
        assert {(edge.kind, edge.source_id, edge.target_id) for edge in graph.edges} == {
            ("spouse", 1, 2), ("parent", 1, 3), ("parent", 2, 3),
        }
        assert graph.issues == ()
    finally:
        repository.close()


def test_validation_highlights_cycles_duplicates_invalid_and_orphan_nodes(tmp_path):
    repository = build_repository(tmp_path, "issues.db")
    try:
        add_person(repository, 1)
        add_person(repository, 2)
        add_person(repository, 3)
        add_person(repository, 4)
        add_family(repository, 1, "I1", "", ("I2", "I2"))
        add_family(repository, 2, "I2", "", ("I1",))
        repository.conn.execute(
            "INSERT INTO families (gedcom_id, husband_id, wife_id, relationship_type) VALUES (?, ?, ?, ?)",
            ("F3", "I3", "I3", "marriage"),
        )
        repository.conn.execute(
            "INSERT INTO families (gedcom_id, husband_id, wife_id, relationship_type) VALUES (?, ?, ?, ?)",
            ("F4", "MISSING", "", "unknown"),
        )
        repository.conn.commit()

        graph = GraphEditorService(repository).build_graph()
        issue_kinds = {issue.kind for issue in graph.issues}

        assert {"cycle", "duplicate", "invalid", "orphan"} <= issue_kinds
        assert any(4 in issue.node_ids for issue in graph.issues if issue.kind == "orphan")
        assert any(3 in issue.node_ids for issue in graph.issues if issue.kind == "invalid")
        assert any("MISSING" in issue.description for issue in graph.issues if issue.kind == "invalid")
    finally:
        repository.close()


def test_add_and_remove_spouse_are_previewed_then_executed(tmp_path):
    repository = build_repository(tmp_path, "spouse.db")
    try:
        first = add_person(repository, 1, "M")
        second = add_person(repository, 2, "F")
        service = GraphEditorService(repository)
        before = repository.capture_command_state()

        preview = service.preview((GraphModification(
            "add_spouse", first, second, relationship_type="civil_partner"
        ),))

        assert preview.can_execute
        assert repository.capture_command_state() == before
        assert any(edge.kind == "spouse" for edge in preview.after.edges)
        result = service.execute(preview)
        family_id = repository.list_families_raw()[0]["id"]
        assert result.delta
        remove_preview = service.preview((GraphModification(
            "remove_spouse", first, family_id=family_id
        ),))
        service.execute(remove_preview)
        assert repository.list_families_raw() == []
    finally:
        repository.close()


def test_link_remove_and_change_parent_operations(tmp_path):
    repository = build_repository(tmp_path, "parents.db")
    try:
        child = add_person(repository, 1)
        old_parent = add_person(repository, 2, "M")
        new_parent = add_person(repository, 3, "M")
        service = GraphEditorService(repository)

        service.execute(service.preview((GraphModification(
            "link_parent", child, old_parent, role="father"
        ),)))
        family_id = repository.list_families_raw()[0]["id"]
        assert {row[2] for row in repository.get_parents(child)} == {"I2"}

        service.execute(service.preview((GraphModification(
            "change_parent", child, new_parent, family_id=family_id,
            role="father", old_parent_id=old_parent,
        ),)))
        assert {row[2] for row in repository.get_parents(child)} == {"I3"}

        service.execute(service.preview((GraphModification(
            "remove_parent", child, new_parent, family_id=family_id, role="father"
        ),)))
        assert repository.get_parents(child) == []
    finally:
        repository.close()


def test_reattach_child_moves_child_between_families(tmp_path):
    repository = build_repository(tmp_path, "reattach.db")
    try:
        child = add_person(repository, 1)
        old_parent = add_person(repository, 2, "M")
        new_parent = add_person(repository, 3, "M")
        old_family = add_family(repository, 1, "I2", "", ("I1",))
        service = GraphEditorService(repository)

        preview = service.preview((GraphModification(
            "reattach_child", child, new_parent, family_id=old_family,
            role="father", relationship_type="unknown",
        ),))
        assert preview.can_execute
        service.execute(preview)

        assert {row[2] for row in repository.get_parents(child)} == {"I3"}
        assert old_parent == 2
    finally:
        repository.close()


def test_preview_blocks_self_links_duplicate_links_and_pedigree_cycles(tmp_path):
    repository = build_repository(tmp_path, "blockers.db")
    try:
        first = add_person(repository, 1, "M")
        second = add_person(repository, 2, "F")
        add_family(repository, 1, "I1", "", ("I2",))
        service = GraphEditorService(repository)

        self_preview = service.preview((GraphModification("add_spouse", first, first),))
        duplicate_preview = service.preview((GraphModification(
            "link_parent", second, first, role="father"
        ),))
        cycle_preview = service.preview((GraphModification(
            "link_parent", first, second, role="father"
        ),))

        assert any("разными" in item for item in self_preview.blockers)
        assert any("существует" in item for item in duplicate_preview.blockers)
        assert any("цикл" in item.lower() for item in cycle_preview.blockers)
        with pytest.raises(GraphEditorSafetyError):
            service.execute(cycle_preview)
    finally:
        repository.close()


def test_multiple_modifications_execute_in_one_transaction_and_rollback_together(tmp_path, monkeypatch):
    repository = build_repository(tmp_path, "transaction.db")
    try:
        first = add_person(repository, 1, "M")
        second = add_person(repository, 2, "F")
        child = add_person(repository, 3)
        service = GraphEditorService(repository)
        preview = service.preview((
            GraphModification("add_spouse", first, second, relationship_type="marriage"),
            GraphModification("link_parent", child, first, role="father"),
        ))
        before = repository.capture_command_state()
        original = service._execute_modification
        calls = {"count": 0}

        def fail_second(modification):
            calls["count"] += 1
            if calls["count"] == 2:
                raise RuntimeError("forced failure")
            return original(modification)

        monkeypatch.setattr(service, "_execute_modification", fail_second)
        with pytest.raises(RuntimeError, match="forced failure"):
            service.execute(preview)

        assert repository.capture_command_state() == before
    finally:
        repository.close()


def test_multiple_previewed_modifications_execute_as_one_batch_delta(tmp_path):
    repository = build_repository(tmp_path, "batch.db")
    try:
        first = add_person(repository, 1, "M")
        second = add_person(repository, 2, "F")
        child = add_person(repository, 3)
        service = GraphEditorService(repository)
        preview = service.preview((
            GraphModification("add_spouse", first, second, relationship_type="marriage"),
            GraphModification("link_parent", child, first, role="father"),
        ))

        result = service.execute(preview)

        assert result.delta
        assert len(result.modifications) == 2
        assert repository.get_spouses(first)
        assert {row[2] for row in repository.get_parents(child)} == {"I1"}
    finally:
        repository.close()


def test_graph_delta_supports_undo_redo_and_audit_logging(tmp_path):
    ui_repository = build_repository(tmp_path, "undo.db")
    first = add_person(ui_repository, 1, "M")
    second = add_person(ui_repository, 2, "F")
    worker_repository = PersonRepository(ui_repository.db_name)
    try:
        service = GraphEditorService(worker_repository)
        result = service.execute(service.preview((GraphModification(
            "add_spouse", first, second, relationship_type="marriage"
        ),)))
        worker_repository.close()
        manager = UndoManager()
        manager.record_applied(AppliedDeltaCommand("Редактор дерева", ui_repository, result.delta, result))

        assert ui_repository.get_spouses(first)
        assert manager.undo()
        assert ui_repository.get_spouses(first) == []
        assert manager.redo()
        assert ui_repository.get_spouses(first)
        records = AuditService.for_database(ui_repository.db_name).list_records(
            service="graph_editor_service"
        )
        assert len(records) == 1
        assert records[0].operation_type == "relationship_change"
        assert "families" in records[0].affected_tables
    finally:
        try:
            worker_repository.close()
        except sqlite3.ProgrammingError:
            pass
        ui_repository.close()


def test_viewer_graph_button_interactions_preview_and_context_menus_are_wired():
    widget_source = inspect.getsource(GenealogyViewer._create_widgets)
    open_source = inspect.getsource(GenealogyViewer.open_graph_editor)
    draw_source = inspect.getsource(GenealogyViewer._draw_graph_editor)
    drag_source = inspect.getsource(GenealogyViewer._finish_graph_card_drag)
    menu_source = inspect.getsource(GenealogyViewer._open_graph_context_menu)
    preview_source = inspect.getsource(GenealogyViewer._preview_graph_editor_modifications)
    execute_source = inspect.getsource(GenealogyViewer._execute_graph_editor_preview)

    assert 'text="Редактор дерева"' in widget_source
    assert "command=self.open_graph_editor" in widget_source
    assert 'values=("Перемещение", "Родитель → ребёнок", "Супруги")' in open_source
    assert 'bind("<MouseWheel>"' in open_source
    assert 'bind("<ButtonPress-2>"' in open_source
    assert "create_line" in draw_source
    assert "graph-person" in draw_source
    assert "issue_color" in draw_source
    assert "GraphModification" in drag_source
    assert "_preview_graph_editor_modifications" in drag_source
    for label in ("Person", "Family", "Relationship"):
        assert f'label="{label}"' in menu_source
    assert "remove_selected_relationship" in menu_source
    assert "change_parent" in menu_source
    assert "reattach_child" in menu_source
    assert "_submit_repository_task" in preview_source
    assert "GraphEditorService(repository).preview" in preview_source
    assert "_submit_repository_task" in execute_source
    assert "GraphEditorService(repository).execute" in execute_source
    assert ".conn" not in open_source + draw_source + preview_source + execute_source


def test_headless_layout_issue_colors_zoom_and_completion(monkeypatch):
    nodes = (
        SimpleNamespace(person_id=1),
        SimpleNamespace(person_id=2),
        SimpleNamespace(person_id=3),
    )
    edges = (
        SimpleNamespace(kind="parent", source_id=1, target_id=3),
        SimpleNamespace(kind="parent", source_id=2, target_id=3),
    )
    model = SimpleNamespace(nodes=nodes, edges=edges)
    positions = GenealogyViewer._graph_editor_auto_layout(model)
    assert positions[1][1] == positions[2][1]
    assert positions[3][1] > positions[1][1]
    assert GenealogyViewer._graph_editor_issue_color({"cycle"}, "black") == "#c62828"
    assert GenealogyViewer._graph_editor_issue_color({"duplicate"}, "black") == "#ef6c00"
    assert GenealogyViewer._graph_editor_issue_color({"orphan"}, "black") == "#7b8790"

    calls = []

    class FakeUndoManager:
        def record_applied(self, command):
            calls.append(("undo", command.name))

    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer.repository = object()
    viewer._get_undo_manager = lambda: FakeUndoManager()
    viewer._close_graph_editor_preview = lambda: calls.append(("close",))
    viewer.refresh_views = lambda: calls.append(("refresh",))
    viewer._load_graph_editor = lambda: calls.append(("load",))
    result = SimpleNamespace(delta={"families": object()})

    viewer._complete_graph_editor_modification(result)

    assert calls == [("undo", "Редактор дерева"), ("close",), ("refresh",), ("load",)]
