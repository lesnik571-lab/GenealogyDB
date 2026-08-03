import inspect

from database import initialize_database
from repository.person_repository import PersonRepository
from viewer import GenealogyViewer
from workspace_integration_service import WorkspaceIntegrationService


def test_selection_sync_history_and_closed_modules(tmp_path):
    service = WorkspaceIntegrationService(tmp_path, max_history=3)
    received = []
    service.register_module("tree", lambda context, origin: received.append((context.selected_person_id, origin)))

    service.select_person(10, "main")
    service.select_family(20, "tree")
    service.select_event(30, "timeline")

    assert received == [(10, "main"), (10, "timeline")]
    assert service.context.selected_person_id == 10
    assert service.context.selected_family_id == 20
    assert service.context.selected_event_id == 30
    assert service.navigate_back().selected_family_id == 20
    assert service.navigate_forward().selected_event_id == 30


def test_sync_loop_prevention_stale_ids_and_state_recovery(tmp_path):
    service = WorkspaceIntegrationService(tmp_path)
    calls = []

    def tree_callback(context, _origin):
        calls.append(context.selected_person_id)
        service.select_person(context.selected_person_id, "tree")

    service.register_module("tree", tree_callback)
    service.select_person("not-an-id", "main")
    service.select_source("4", "bad", "evidence")
    state_path = service.save_ui_state({"geometry": "1000x700", "filters": {"name": "Anna"}})

    assert calls == [None]
    assert service.context.selected_source_id == 4
    assert service.context.selected_citation_id is None
    assert WorkspaceIntegrationService(tmp_path).load_ui_state()["geometry"] == "1000x700"

    state_path.write_text("not json", encoding="utf-8")
    assert WorkspaceIntegrationService(tmp_path).load_ui_state() == {}


def test_diagnostics_contains_context_and_modules(tmp_path):
    service = WorkspaceIntegrationService(tmp_path)
    service.register_module("audit", lambda *_args: None)
    service.select_person(7, "main")

    diagnostics = service.diagnostics(running_tasks=2, service_availability={"timeline": True})

    assert diagnostics["registered_modules"] == ("audit",)
    assert diagnostics["context"]["selected_person_id"] == 7
    assert diagnostics["running_tasks"] == 2
    assert diagnostics["service_availability"]["timeline"] is True


def test_viewer_workspace_shortcuts_are_registered_without_creating_tk_windows():
    source = inspect.getsource(GenealogyViewer._create_widgets)

    for label in ("Рабочее пространство", "Главная карточка", "Дерево", "Хронология", "Карта", "Источники", "Проверка данных", "Исследование", "История изменений", "Назад", "Вперёд"):
        assert label in source
    for shortcut in ("<Alt-Left>", "<Alt-Right>", "<Control-1>", "<Control-8>"):
        assert shortcut in source


def test_unified_error_is_single_dialog_and_status_is_contextual(monkeypatch, tmp_path):
    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer.root = object()
    viewer.repository = type("Repository", (), {"db_name": str(tmp_path / "family.db")})()
    viewer.workspace_integration_service = WorkspaceIntegrationService(tmp_path)
    viewer._validation_report = None
    viewer.task_manager = type("Tasks", (), {"_tasks": {1: object()}})()
    messages = []
    monkeypatch.setattr("viewer.messagebox.showerror", lambda *args, **kwargs: messages.append(args))

    viewer._error_dialog_active = True
    viewer._show_unified_error("Проверка", RuntimeError("detail"))
    viewer._error_dialog_active = False
    viewer._show_unified_error("Проверка", RuntimeError("detail"))

    class Label:
        def config(self, **kwargs):
            self.text = kwargs["text"]

    viewer.workspace_status_label = Label()
    viewer.workspace_integration_service.select_person(11, "main")
    viewer._update_workspace_status()

    assert len(messages) == 1
    assert "Человек: 11" in viewer.workspace_status_label.text
    assert "Задач: 1" in viewer.workspace_status_label.text


def test_workspace_integration_never_changes_genealogy_database(tmp_path):
    database_path = tmp_path / "genealogy.db"
    initialize_database(database_path)
    repository = PersonRepository(database_path)
    try:
        before = repository.capture_command_state()
        service = WorkspaceIntegrationService(tmp_path)
        service.select_person(1, "main")
        service.select_family(2, "tree")
        service.save_ui_state({"geometry": "1000x700"})
        assert repository.capture_command_state() == before
    finally:
        repository.close()
