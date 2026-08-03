"""Shared UI workspace context without mutating genealogy records."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from config import DATA_DIR


MODULES = (
    "main",
    "tree",
    "timeline",
    "map",
    "evidence",
    "validation",
    "research",
    "audit",
)
MAX_HISTORY = 100


@dataclass(frozen=True)
class WorkspaceContext:
    selected_person_id: int | None = None
    selected_family_id: int | None = None
    selected_event_id: int | None = None
    selected_source_id: int | None = None
    selected_citation_id: int | None = None
    active_module: str = "main"
    filters: dict[str, Any] = field(default_factory=dict)


class WorkspaceIntegrationService:
    """Publish selection changes between open UI modules and persist UI-only state."""

    def __init__(
        self,
        data_dir: str | Path | None = None,
        *,
        max_history: int = MAX_HISTORY,
        logger: logging.Logger | None = None,
    ) -> None:
        self.data_dir = Path(data_dir or DATA_DIR) / "ui_state"
        self.state_path = self.data_dir / "workspace.json"
        self.max_history = max(1, int(max_history))
        self.logger = logger or logging.getLogger("genealogydb.workspace")
        self.context = WorkspaceContext()
        self.history: list[WorkspaceContext] = []
        self.history_index = -1
        self._listeners: dict[str, Callable[[WorkspaceContext, str], None]] = {}
        self._syncing: set[str] = set()
        self.last_sync_event = ""

    def register_module(self, module: str, callback: Callable[[WorkspaceContext, str], None]) -> None:
        if module not in MODULES:
            raise ValueError(f"Unknown workspace module: {module}")
        self._listeners[module] = callback

    def unregister_module(self, module: str) -> None:
        self._listeners.pop(module, None)
        self._syncing.discard(module)

    @property
    def registered_modules(self) -> tuple[str, ...]:
        return tuple(sorted(self._listeners))

    def update(self, origin: str, *, add_history: bool = True, **changes: Any) -> WorkspaceContext:
        """Update context and notify only currently registered modules.

        A module receiving a notification is held in a guard set until its callback
        finishes, so a selection handler cannot immediately recurse into itself.
        """
        if origin not in MODULES:
            raise ValueError(f"Unknown workspace module: {origin}")
        allowed = set(WorkspaceContext.__dataclass_fields__)
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Unknown workspace context fields: {sorted(unknown)}")
        next_context = WorkspaceContext(**{**asdict(self.context), **changes})
        if next_context == self.context:
            return self.context
        self.context = next_context
        if add_history:
            self._push_history(next_context)
        self.last_sync_event = f"{origin}:{next_context.active_module}"
        self._notify(origin)
        return self.context

    def select_person(self, person_id: int | None, origin: str, *, active_module: str | None = None) -> WorkspaceContext:
        return self.update(origin, selected_person_id=self._safe_id(person_id), active_module=active_module or origin)

    def select_family(self, family_id: int | None, origin: str, *, active_module: str | None = None) -> WorkspaceContext:
        return self.update(origin, selected_family_id=self._safe_id(family_id), active_module=active_module or origin)

    def select_event(self, event_id: int | None, origin: str, *, active_module: str | None = None) -> WorkspaceContext:
        return self.update(origin, selected_event_id=self._safe_id(event_id), active_module=active_module or origin)

    def select_source(self, source_id: int | None, citation_id: int | None, origin: str, *, active_module: str | None = None) -> WorkspaceContext:
        return self.update(origin, selected_source_id=self._safe_id(source_id), selected_citation_id=self._safe_id(citation_id), active_module=active_module or origin)

    def navigate_back(self) -> WorkspaceContext | None:
        if self.history_index <= 0:
            return None
        self.history_index -= 1
        return self._restore_history("navigation")

    def navigate_forward(self) -> WorkspaceContext | None:
        if self.history_index >= len(self.history) - 1:
            return None
        self.history_index += 1
        return self._restore_history("navigation")

    def save_ui_state(self, state: Mapping[str, Any]) -> Path:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "workspace": asdict(self.context), "history": [asdict(item) for item in self.history], "history_index": self.history_index, "ui": dict(state)}
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.state_path)
        return self.state_path

    def load_ui_state(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("UI state is not an object")
            workspace = payload.get("workspace", {})
            self.context = WorkspaceContext(**{key: workspace.get(key, getattr(WorkspaceContext(), key)) for key in WorkspaceContext.__dataclass_fields__})
            self.history = [WorkspaceContext(**item) for item in payload.get("history", []) if isinstance(item, dict)]
            self.history_index = min(max(-1, int(payload.get("history_index", -1))), len(self.history) - 1)
            return dict(payload.get("ui", {}))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            self.logger.warning("Unable to load workspace UI state: %s", error)
            self.context = WorkspaceContext()
            self.history = []
            self.history_index = -1
            return {}

    def diagnostics(
        self,
        *,
        running_tasks: int = 0,
        service_availability: Mapping[str, bool] | None = None,
        open_modules: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        return {
            "registered_modules": self.registered_modules,
            "open_modules": tuple(sorted(open_modules)),
            "context": asdict(self.context),
            "history_size": len(self.history),
            "history_index": self.history_index,
            "running_tasks": int(running_tasks),
            "service_availability": dict(service_availability or {}),
            "last_sync_event": self.last_sync_event,
        }

    @staticmethod
    def _safe_id(value: int | str | None) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _push_history(self, context: WorkspaceContext) -> None:
        if self.history_index >= 0 and self.history[self.history_index] == context:
            return
        del self.history[self.history_index + 1:]
        self.history.append(context)
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]
        self.history_index = len(self.history) - 1

    def _restore_history(self, origin: str) -> WorkspaceContext:
        self.context = self.history[self.history_index]
        self.last_sync_event = f"{origin}:{self.context.active_module}"
        self._notify(origin)
        return self.context

    def _notify(self, origin: str) -> None:
        for module, callback in tuple(self._listeners.items()):
            if module == origin or module in self._syncing:
                continue
            self._syncing.add(module)
            try:
                callback(self.context, origin)
            except Exception:
                self.logger.exception("Workspace synchronization failed for %s", module)
            finally:
                self._syncing.discard(module)
