"""Central logging and privacy-safe diagnostics export for GenealogyDB."""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
import threading
import zipfile
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from build_info import APP_VERSION, BUILD_DATE
from config import DB_NAME, LOG_DIR, USER_CONFIG_PATH


LOGGER_NAME = "genealogydb"
LOG_FILENAME = "genealogydb.log"
MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 9
_exception_hooks_installed = False


def configure_logging(log_dir: str | Path = LOG_DIR) -> logging.Logger:
    """Configure the application logger with ten files of at most 5 MB each."""
    directory = Path(log_dir)
    directory.mkdir(parents=True, exist_ok=True)
    destination = (directory / LOG_FILENAME).resolve()
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    for handler in tuple(logger.handlers):
        if getattr(handler, "_genealogydb_handler", False):
            if Path(handler.baseFilename).resolve() == destination:
                return logger
            logger.removeHandler(handler)
            handler.close()

    handler = RotatingFileHandler(
        destination,
        maxBytes=MAX_LOG_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler._genealogydb_handler = True
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    ))
    logger.addHandler(handler)
    return logger


def get_logger(component: str | None = None) -> logging.Logger:
    """Return a configured application or component logger."""
    application_logger = logging.getLogger(LOGGER_NAME)
    if not any(
        getattr(handler, "_genealogydb_handler", False)
        for handler in application_logger.handlers
    ):
        configure_logging()
    return logging.getLogger(
        LOGGER_NAME if not component else f"{LOGGER_NAME}.{component}"
    )


def log_operation(event: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Log operation start, completion, and unexpected failure without payloads."""
    def decorate(function: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(function)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            logger = get_logger(function.__module__)
            logger.info("%s started", event)
            try:
                result = function(*args, **kwargs)
            except Exception:
                logger.exception("%s failed", event)
                raise
            logger.info("%s completed", event)
            return result
        return wrapped
    return decorate


def install_exception_logging(root: Any = None) -> None:
    """Install process, thread, and optional Tk callback exception logging."""
    global _exception_hooks_installed
    logger = get_logger("exceptions")
    if not _exception_hooks_installed:
        previous_sys_hook = sys.excepthook
        previous_thread_hook = threading.excepthook

        def system_hook(exception_type: type[BaseException], error: BaseException, traceback: Any) -> None:
            logger.critical("Unexpected application exception", exc_info=(exception_type, error, traceback))
            previous_sys_hook(exception_type, error, traceback)

        def thread_hook(arguments: threading.ExceptHookArgs) -> None:
            logger.critical(
                "Unexpected thread exception",
                exc_info=(arguments.exc_type, arguments.exc_value, arguments.exc_traceback),
            )
            previous_thread_hook(arguments)

        sys.excepthook = system_hook
        threading.excepthook = thread_hook
        _exception_hooks_installed = True

    if root is not None:
        def report_callback_exception(exception_type: type[BaseException], error: BaseException, traceback: Any) -> None:
            logger.critical("Unexpected Tk callback exception", exc_info=(exception_type, error, traceback))
        root.report_callback_exception = report_callback_exception


def diagnostics_snapshot(
    plugins: Iterable[Any] = (),
    services: Iterable[str] = (),
    database_path: str | Path = DB_NAME,
    log_dir: str | Path = LOG_DIR,
) -> dict[str, Any]:
    """Return non-sensitive runtime diagnostics suitable for display or export."""
    plugin_names = sorted(
        f"{getattr(plugin, 'name', plugin)} {getattr(plugin, 'version', '')}".strip()
        for plugin in plugins
    )
    return {
        "application_version": APP_VERSION,
        "build_date": BUILD_DATE,
        "database_path": str(Path(database_path).resolve()),
        "log_folder": str(Path(log_dir).resolve()),
        "python_version": sys.version.split()[0],
        "sqlite_version": sqlite3.sqlite_version,
        "plugins": plugin_names,
        "loaded_services": sorted(set(services)),
    }


def export_diagnostics(
    destination: str | Path,
    snapshot: Mapping[str, Any],
    log_dir: str | Path = LOG_DIR,
    configuration_path: str | Path = USER_CONFIG_PATH,
) -> Path:
    """Create a diagnostics ZIP containing only logs and non-database metadata."""
    output = Path(destination)
    output.parent.mkdir(parents=True, exist_ok=True)
    logs = Path(log_dir)
    configuration = Path(configuration_path)

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(logs.glob(f"{LOG_FILENAME}*")):
            if path.is_file():
                archive.write(path, f"logs/{path.name}")
        if configuration.is_file():
            archive.write(configuration, "configuration/config.json")
        archive.writestr(
            "version.json",
            json.dumps({
                "application_version": APP_VERSION,
                "build_date": BUILD_DATE,
                "python_version": snapshot.get("python_version"),
                "sqlite_version": snapshot.get("sqlite_version"),
            }, indent=2),
        )
        archive.writestr(
            "plugins.json",
            json.dumps(snapshot.get("plugins", []), indent=2),
        )
        archive.writestr("diagnostics.json", json.dumps(dict(snapshot), indent=2))

    get_logger("diagnostics").info("Diagnostics exported")
    return output