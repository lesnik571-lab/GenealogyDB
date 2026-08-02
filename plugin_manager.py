from __future__ import annotations

import ast
import importlib.util
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType, ModuleType
from typing import Any, Callable, Mapping


FORBIDDEN_IMPORTS = {"database", "repository", "sqlite3"}
FORBIDDEN_CALLS = {"cursor", "execute", "executemany", "executescript"}


@dataclass(frozen=True)
class PluginInfo:
    """Metadata and load state for an installed plugin."""
    name: str
    version: str
    path: Path


class ReadOnlyPluginData:
    """Repository-backed, copy-only data exposed to plugins."""

    __slots__ = ("__repository",)

    def __init__(self, repository: Any) -> None:
        self.__repository = repository

    def people(self) -> tuple[Mapping[str, Any], ...]:
        return self._freeze(self.__repository.list_people_full())

    def families(self) -> tuple[Mapping[str, Any], ...]:
        return self._freeze(self.__repository.list_families_raw())

    def family_children(self) -> tuple[Mapping[str, Any], ...]:
        return self._freeze(self.__repository.list_family_children_raw())

    def events(self) -> tuple[Mapping[str, Any], ...]:
        return self._freeze(self.__repository.list_person_events_for_integrity())

    def attachments(self) -> tuple[Mapping[str, Any], ...]:
        records = []
        for person in self.__repository.list_people_full():
            records.extend(self.__repository.list_person_media(person["id"]))
        return self._freeze(records)

    def statistics(self) -> Mapping[str, int]:
        return MappingProxyType({
            "People": len(self.people()),
            "Families": len(self.families()),
            "Events": len(self.events()),
            "Attachments": len(self.attachments()),
        })

    @staticmethod
    def _freeze(records: Any) -> tuple[Mapping[str, Any], ...]:
        return tuple(MappingProxyType(dict(record)) for record in records)


class PluginApp:
    """Capability-limited application facade passed to plugin register functions."""

    __slots__ = (
        "data", "__button_registrar", "__menu_registrar",
        "__report_registrar", "__export_registrar",
    )

    def __init__(
        self,
        data: ReadOnlyPluginData,
        button_registrar: Callable[[str, Callable[[], None]], Any],
        menu_registrar: Callable[[str, str, Callable[[], None]], Any],
        report_registrar: Callable[[str, Callable[[], Any]], Callable[[], None]],
        export_registrar: Callable[[str, Callable[[Path], Any]], Callable[[], None]],
    ) -> None:
        self.data = data
        self.__button_registrar = button_registrar
        self.__menu_registrar = menu_registrar
        self.__report_registrar = report_registrar
        self.__export_registrar = export_registrar

    def add_viewer_button(self, label: str, command: Callable[[], None]) -> Any:
        self._validate_registration(label, command)
        return self.__button_registrar(label, command)

    def add_menu_item(
        self, menu_name: str, label: str, command: Callable[[], None]
    ) -> Any:
        self._validate_registration(menu_name, command)
        self._validate_label(label)
        return self.__menu_registrar(menu_name, label, command)

    def add_read_only_report(
        self, name: str, provider: Callable[[], Any]
    ) -> Callable[[], None]:
        self._validate_registration(name, provider)
        return self.__report_registrar(name, provider)

    def add_export(
        self, name: str, exporter: Callable[[Path], Any]
    ) -> Callable[[], None]:
        self._validate_registration(name, exporter)
        return self.__export_registrar(name, exporter)

    @classmethod
    def _validate_registration(cls, label: str, callback: Callable[..., Any]) -> None:
        cls._validate_label(label)
        if not callable(callback):
            raise TypeError("Plugin callback must be callable.")

    @staticmethod
    def _validate_label(label: str) -> None:
        if not isinstance(label, str) or not label.strip():
            raise ValueError("Plugin labels must be non-empty strings.")


class PluginManager:
    """Discover, validate, and isolate plugins loaded from one directory."""

    def __init__(self, plugin_dir: str | Path, log_path: str | Path) -> None:
        self.plugin_dir = Path(plugin_dir)
        self.log_path = Path(log_path)

    def load_plugins(self, app: PluginApp) -> tuple[PluginInfo, ...]:
        self.plugin_dir.mkdir(parents=True, exist_ok=True)
        loaded = []
        for path in sorted(self.plugin_dir.glob("*.py"), key=lambda item: item.name.casefold()):
            if path.name.startswith("_"):
                continue
            try:
                self._validate_source(path)
                module = self._load_module(path)
                info = self._plugin_info(module, path)
                module.register(app)
                loaded.append(info)
                self._log("INFO", f"Loaded {info.name} {info.version} from {path.name}")
            except Exception as error:
                self._log("ERROR", f"Failed {path.name}: {type(error).__name__}: {error}")
        return tuple(loaded)

    @staticmethod
    def _validate_source(path: Path) -> None:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = {alias.name.split(".", 1)[0] for alias in node.names}
                if imported & FORBIDDEN_IMPORTS:
                    raise ValueError("Plugin imports a forbidden database module.")
            elif isinstance(node, ast.ImportFrom):
                root_name = str(node.module or "").split(".", 1)[0]
                if root_name in FORBIDDEN_IMPORTS:
                    raise ValueError("Plugin imports a forbidden database module.")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in FORBIDDEN_CALLS:
                    raise ValueError("Plugin contains a forbidden direct database call.")

    @staticmethod
    def _load_module(path: Path) -> ModuleType:
        module_name = f"genealogydb_plugin_{path.stem}_{abs(hash(path.resolve()))}"
        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot create import spec for {path.name}.")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _plugin_info(module: ModuleType, path: Path) -> PluginInfo:
        name = getattr(module, "plugin_name", None)
        version = getattr(module, "plugin_version", None)
        register = getattr(module, "register", None)
        if not isinstance(name, str) or not name.strip():
            raise ValueError("plugin_name must be a non-empty string.")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("plugin_version must be a non-empty string.")
        if not callable(register):
            raise ValueError("register(app) must be callable.")
        return PluginInfo(name.strip(), version.strip(), path)

    def _log(self, level: str, message: str) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.log_path.open("a", encoding="utf-8") as log_file:
            log_file.write(f"{timestamp} [{level}] {message}\n")

    def log_runtime_error(self, context: str, error: Exception) -> None:
        self._log("ERROR", f"Runtime failure in {context}: {type(error).__name__}: {error}")