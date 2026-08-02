import threading
import time
import sys
import inspect
from types import SimpleNamespace

from app import GenealogyApplication
from task_manager import TaskManager, TkTaskProgressDialog
from viewer import GenealogyViewer


class FakeRoot:
    def __init__(self):
        self.callbacks = []
        self.ui_thread = threading.get_ident()

    def after(self, _delay, callback):
        self.callbacks.append(callback)

    def pump(self):
        callbacks = list(self.callbacks)
        self.callbacks.clear()
        for callback in callbacks:
            callback()


class FakeDialog:
    instances = []

    def __init__(self, _root, task, cancel_callback):
        self.task = task
        self.cancel_callback = cancel_callback
        self.updates = []
        self.closed = False
        self.cancelling = False
        self.instances.append(self)

    def update(self, message, completed, total, elapsed):
        self.updates.append((message, completed, total, elapsed, threading.get_ident()))

    def mark_cancelling(self):
        self.cancelling = True

    def close(self):
        self.closed = True


def pump_until(root, condition, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        root.pump()
        if condition():
            return
        time.sleep(0.005)
    raise AssertionError("task did not finish")


def test_tasks_run_in_background_in_submission_order_and_callbacks_use_ui_thread():
    FakeDialog.instances.clear()
    root = FakeRoot()
    manager = TaskManager(root, dialog_factory=FakeDialog, poll_interval_ms=1)
    execution = []
    callbacks = []

    def operation(number):
        def run(context):
            execution.append((number, threading.get_ident()))
            context.report(f"task {number}", number, 2)
            return number
        return run

    manager.submit("first", operation(1), on_success=lambda result: callbacks.append((result, threading.get_ident())))
    manager.submit("second", operation(2), on_success=lambda result: callbacks.append((result, threading.get_ident())))

    pump_until(root, lambda: len(callbacks) == 2)

    assert [number for number, _thread in execution] == [1, 2]
    assert all(thread != root.ui_thread for _number, thread in execution)
    assert callbacks == [(1, root.ui_thread), (2, root.ui_thread)]
    assert all(update[4] == root.ui_thread for dialog in FakeDialog.instances for update in dialog.updates)
    assert all(dialog.closed for dialog in FakeDialog.instances)
    manager.shutdown()


def test_safe_task_can_be_cancelled_cooperatively():
    FakeDialog.instances.clear()
    root = FakeRoot()
    manager = TaskManager(root, dialog_factory=FakeDialog, poll_interval_ms=1)
    outcomes = []

    def operation(context):
        while not context.cancelled:
            time.sleep(0.002)
        context.raise_if_cancelled()

    task = manager.submit("cancellable", operation, on_cancelled=lambda: outcomes.append("cancelled"), cancellable=True)
    pump_until(root, lambda: bool(FakeDialog.instances))
    assert manager.cancel(task.task_id) is True
    pump_until(root, lambda: outcomes == ["cancelled"])

    assert FakeDialog.instances[0].cancelling is True
    assert FakeDialog.instances[0].closed is True
    manager.shutdown()


def test_unsafe_task_rejects_cancellation_and_errors_are_delivered_on_ui_thread():
    root = FakeRoot()
    manager = TaskManager(root, dialog_factory=FakeDialog, poll_interval_ms=1)
    errors = []

    def operation(_context):
        raise RuntimeError("failure")

    task = manager.submit("unsafe", operation, on_error=lambda error: errors.append((str(error), threading.get_ident())))
    assert manager.cancel(task.task_id) is False
    pump_until(root, lambda: bool(errors))

    assert errors == [("failure", root.ui_thread)]
    manager.shutdown()


def test_wait_returns_background_result_without_tk_event_processing():
    root = FakeRoot()
    manager = TaskManager(root, dialog_factory=None)
    task = manager.submit("headless", lambda _context: (threading.get_ident(), 42))

    worker_thread, result = manager.wait(task, timeout=2)

    assert worker_thread != root.ui_thread
    assert result == 42
    manager.shutdown()


def test_gedcom_launcher_runs_import_on_background_dispatcher(tmp_path, monkeypatch):
    gedcom_path = tmp_path / "family.ged"
    gedcom_path.write_text("0 TRLR\n", encoding="utf-8")
    called_from = []

    def import_gedcom(_filename):
        called_from.append(threading.get_ident())
        return {"people": 1, "families": 0, "family_children": 0}

    monkeypatch.setitem(sys.modules, "importer", SimpleNamespace(import_gedcom=import_gedcom))
    application = GenealogyApplication()
    application.read_input = lambda _prompt: str(gedcom_path)
    ui_thread = threading.get_ident()

    application.import_gedcom()

    assert called_from and called_from[0] != ui_thread


def test_required_long_running_viewer_operations_submit_repository_tasks():
    methods = (
        GenealogyViewer.open_recovery_wizard,
        GenealogyViewer._find_recovery_matches,
        GenealogyViewer._refresh_data_quality_report,
        GenealogyViewer._refresh_timeline,
        GenealogyViewer._load_family_timeline,
        GenealogyViewer.open_kinship_analyzer,
        GenealogyViewer.search_people,
        GenealogyViewer._start_life_map_geocoding,
    )

    for method in methods:
        assert "_submit_repository_task" in inspect.getsource(method)
    assert "task_manager.submit" in inspect.getsource(GenealogyApplication.import_gedcom)


def test_progress_dialog_contains_required_task_progress_elapsed_and_safe_cancel_controls():
    source = inspect.getsource(TkTaskProgressDialog)

    assert "task.name" in source
    assert "ttk.Progressbar" in source
    assert 'text="Время: 00:00"' in source
    assert "if task.cancellable" in source
    assert 'text="Отмена"' in source


def test_viewer_close_stops_task_manager_before_repository():
    calls = []
    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer.task_manager = SimpleNamespace(shutdown=lambda: calls.append("tasks"))
    viewer.repository = SimpleNamespace(close=lambda: calls.append("repository"))

    viewer.close()

    assert calls == ["tasks", "repository"]