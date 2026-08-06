"""Queued background tasks with Tk-safe progress reporting."""

from __future__ import annotations

import itertools
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import tkinter as tk
from tkinter import ttk

from logging_service import get_logger


@dataclass
class TaskContext:
    """Worker-facing progress and cancellation interface."""

    cancel_event: threading.Event
    _reporter: Callable[[str, int, int | None], None]

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def report(self, message: str, completed: int = 0, total: int | None = None) -> None:
        self._reporter(message, completed, total)

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise TaskCancelledError()


class TaskCancelledError(Exception):
    """Signal cooperative cancellation without treating it as a failure."""


@dataclass
class BackgroundTask:
    task_id: int
    name: str
    operation: Callable[[TaskContext], Any]
    on_success: Callable[[Any], None] | None = None
    on_error: Callable[[Exception], None] | None = None
    on_cancelled: Callable[[], None] | None = None
    cancellable: bool = False
    cancel_event: threading.Event = field(default_factory=threading.Event)
    submitted_at: float = field(default_factory=time.monotonic)
    started_at: float | None = None
    result: Any = None
    error: Exception | None = None
    finished: threading.Event = field(default_factory=threading.Event)
    progress_message: str = "Выполняется..."
    progress_completed: int = 0
    progress_total: int | None = None


class TkTaskProgressDialog:
    """One unobtrusive progress window for the currently running task."""

    def __init__(self, root, task: BackgroundTask, cancel_callback: Callable[[], None]):
        self.task = task
        self.window = tk.Toplevel(root)
        self.window.title("Фоновая задача")
        self.window.geometry("440x190")
        resizable = getattr(self.window, "resizable", None)
        if callable(resizable):
            resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", lambda: None)
        frame = tk.Frame(self.window)
        frame.pack(fill="both", expand=True, padx=14, pady=14)
        self.name_label = tk.Label(frame, text=task.name, anchor="w")
        self.name_label.pack(fill="x")
        self.message_label = tk.Label(frame, text="Ожидание...", anchor="w")
        self.message_label.pack(fill="x", pady=(8, 4))
        self.progress = ttk.Progressbar(frame, orient="horizontal", mode="indeterminate", maximum=100)
        self.progress.pack(fill="x")
        start = getattr(self.progress, "start", None)
        if callable(start):
            start(12)
        self.elapsed_label = tk.Label(frame, text="Время: 00:00", anchor="w")
        self.elapsed_label.pack(fill="x", pady=(8, 0))
        self.cancel_button = None
        if task.cancellable:
            self.cancel_button = tk.Button(frame, text="Отмена", command=cancel_callback)
            self.cancel_button.pack(anchor="e", pady=(6, 0))

    def update(self, message: str, completed: int, total: int | None, elapsed: float) -> None:
        self.message_label.config(text=message or "Выполняется...")
        minutes, seconds = divmod(max(0, int(elapsed)), 60)
        self.elapsed_label.config(text=f"Время: {minutes:02d}:{seconds:02d}")
        if total and total > 0:
            stop = getattr(self.progress, "stop", None)
            if callable(stop):
                stop()
            self.progress.config(mode="determinate")
            self.progress["value"] = max(0, min(100, completed * 100 / total))

    def mark_cancelling(self) -> None:
        if self.cancel_button is not None:
            self.cancel_button.config(state="disabled", text="Остановка...")

    def close(self) -> None:
        try:
            stop = getattr(self.progress, "stop", None)
            if callable(stop):
                stop()
            self.window.destroy()
        except Exception:
            pass


class TaskManager:
    """Run queued operations off the UI thread and marshal results through Tk."""

    def __init__(self, root, dialog_factory=TkTaskProgressDialog, poll_interval_ms: int = 100):
        self.root = root
        self.dialog_factory = dialog_factory
        self.poll_interval_ms = poll_interval_ms
        self.logger = get_logger("tasks")
        self._pending: queue.Queue[BackgroundTask | None] = queue.Queue()
        self._events: queue.Queue[tuple] = queue.Queue()
        self._ids = itertools.count(1)
        self._tasks: dict[int, BackgroundTask] = {}
        self._dialogs: dict[int, Any] = {}
        self._closed = False
        self._dispatcher = threading.Thread(target=self._dispatch, name="task-manager", daemon=True)
        self._dispatcher.start()
        self._poll_after_id = self.root.after(self.poll_interval_ms, self._poll_events)

    def submit(
        self,
        name: str,
        operation: Callable[[TaskContext], Any],
        *,
        on_success: Callable[[Any], None] | None = None,
        on_error: Callable[[Exception], None] | None = None,
        on_cancelled: Callable[[], None] | None = None,
        cancellable: bool = False,
    ) -> BackgroundTask:
        task = BackgroundTask(
            task_id=next(self._ids),
            name=name,
            operation=operation,
            on_success=on_success,
            on_error=on_error,
            on_cancelled=on_cancelled,
            cancellable=cancellable,
        )
        self._tasks[task.task_id] = task
        self._pending.put(task)
        self.logger.info("Task queued: id=%s name=%s", task.task_id, task.name)
        return task

    def cancel(self, task_id: int) -> bool:
        task = self._tasks.get(task_id)
        if task is None or not task.cancellable:
            return False
        task.cancel_event.set()
        dialog = self._dialogs.get(task_id)
        if dialog is not None:
            dialog.mark_cancelling()
        self.logger.info("Task cancellation requested: id=%s name=%s", task.task_id, task.name)
        return True

    def shutdown(self) -> None:
        self._closed = True
        poll_after_id = self._poll_after_id
        self._poll_after_id = None
        cancel_after = getattr(self.root, "after_cancel", None)
        if poll_after_id is not None and callable(cancel_after):
            try:
                cancel_after(poll_after_id)
            except tk.TclError:
                pass
        self._pending.put(None)

    @staticmethod
    def wait(task: BackgroundTask, timeout: float | None = None) -> Any:
        if not task.finished.wait(timeout):
            raise TimeoutError(f"Task did not finish: {task.name}")
        if task.error is not None:
            raise task.error
        if task.cancel_event.is_set():
            raise TaskCancelledError()
        return task.result

    def _dispatch(self) -> None:
        while True:
            task = self._pending.get()
            if task is None:
                return
            if task.cancel_event.is_set():
                task.finished.set()
                self._events.put(("cancelled", task))
                continue
            task.started_at = time.monotonic()
            self.logger.info("Task started: id=%s name=%s", task.task_id, task.name)
            self._events.put(("started", task))
            context = TaskContext(
                task.cancel_event,
                lambda message, completed=0, total=None, task=task: self._events.put(
                    ("progress", task, message, completed, total)
                ),
            )
            try:
                result = task.operation(context)
                if task.cancel_event.is_set():
                    raise TaskCancelledError()
            except TaskCancelledError:
                self.logger.info("Task cancelled: id=%s name=%s", task.task_id, task.name)
                self._events.put(("cancelled", task))
            except Exception as error:
                task.error = error
                self.logger.exception("Task failed: id=%s name=%s", task.task_id, task.name)
                self._events.put(("error", task, error))
            else:
                task.result = result
                elapsed = time.monotonic() - task.started_at
                self.logger.info("Task completed: id=%s name=%s elapsed=%.3fs", task.task_id, task.name, elapsed)
                self._events.put(("success", task, result))
            finally:
                task.finished.set()

    def _poll_events(self) -> None:
        self._poll_after_id = None
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                break
            kind, task, *payload = event
            if kind == "started":
                if self.dialog_factory is not None:
                    self._dialogs[task.task_id] = self.dialog_factory(
                        self.root, task, lambda task_id=task.task_id: self.cancel(task_id)
                    )
            elif kind == "progress":
                task.progress_message = payload[0]
                task.progress_completed = payload[1]
                task.progress_total = payload[2]
                dialog = self._dialogs.get(task.task_id)
                if dialog is not None:
                    elapsed = time.monotonic() - (task.started_at or time.monotonic())
                    dialog.update(payload[0], payload[1], payload[2], elapsed)
            else:
                dialog = self._dialogs.pop(task.task_id, None)
                if dialog is not None:
                    dialog.close()
                self._tasks.pop(task.task_id, None)
                if kind == "success" and task.on_success:
                    task.on_success(payload[0])
                elif kind == "error" and task.on_error:
                    task.on_error(payload[0])
                elif kind == "cancelled" and task.on_cancelled:
                    task.on_cancelled()
        for task_id, dialog in tuple(self._dialogs.items()):
            task = self._tasks.get(task_id)
            if task is not None:
                elapsed = time.monotonic() - (task.started_at or time.monotonic())
                dialog.update(
                    task.progress_message,
                    task.progress_completed,
                    task.progress_total,
                    elapsed,
                )
        if not self._closed:
            self._poll_after_id = self.root.after(self.poll_interval_ms, self._poll_events)