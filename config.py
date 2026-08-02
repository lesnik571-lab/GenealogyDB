import os
import shutil
import sys
import json
from pathlib import Path

from build_info import APP_VERSION, BUILD_DATE


APP_NAME = "GenealogyDB"
PROJECT_ROOT = Path(__file__).resolve().parent
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT)).resolve()


def _default_app_home():
    local_app_data = os.getenv("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


def _resolve_path(value, default):
    path = Path(value).expanduser() if value else default
    if not path.is_absolute():
        path = RESOURCE_DIR / path
    return path.resolve()


APP_HOME = _resolve_path(os.getenv("GENEALOGYDB_HOME"), _default_app_home())
DATA_DIR = _resolve_path(
    os.getenv("GENEALOGYDB_DATA_DIR"),
    APP_HOME / "data",
)
BACKUP_DIR = APP_HOME / "backups"
EXPORT_DIR = APP_HOME / "exports"
LOG_DIR = APP_HOME / "logs"
PLUGIN_DIR = APP_HOME / "plugins"
USER_CONFIG_PATH = APP_HOME / "config.json"
DEFAULT_CONFIG_PATH = RESOURCE_DIR / "resources" / "default_config.json"
USER_MANUAL_PATH = RESOURCE_DIR / "USER_MANUAL.md"
DEFAULT_DB_PATH = (DATA_DIR / "genealogy.db").resolve()
DB_NAME = str(_resolve_path(
    os.getenv("GENEALOGYDB_DB_NAME"),
    DEFAULT_DB_PATH,
))


def _load_configuration():
    configuration = {}
    for path in (DEFAULT_CONFIG_PATH, USER_CONFIG_PATH):
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(loaded, dict):
            configuration.update(loaded)
    return configuration


def prepare_user_environment():
    """Create writable application folders and seed packaged defaults."""
    for directory in (DATA_DIR, BACKUP_DIR, EXPORT_DIR, LOG_DIR, PLUGIN_DIR):
        directory.mkdir(parents=True, exist_ok=True)

    if not USER_CONFIG_PATH.exists() and DEFAULT_CONFIG_PATH.is_file():
        shutil.copy2(DEFAULT_CONFIG_PATH, USER_CONFIG_PATH)

    bundled_plugins = RESOURCE_DIR / "plugins"
    if bundled_plugins.is_dir():
        for source in bundled_plugins.glob("*.py"):
            if source.name.startswith("_"):
                continue
            destination = PLUGIN_DIR / source.name
            if not destination.exists():
                shutil.copy2(source, destination)


_CONFIGURATION = _load_configuration()

GEOCODING_PROVIDER = os.getenv(
    "GENEALOGYDB_GEOCODING_PROVIDER",
    str(_CONFIGURATION.get("geocoding_provider", "opencage")),
)
GEOCODING_API_KEY = os.getenv("GENEALOGYDB_GEOCODING_API_KEY", "")
