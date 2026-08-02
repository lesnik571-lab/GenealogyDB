import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def _resolve_path(value, default):
    path = Path(value).expanduser() if value else default
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


DATA_DIR = _resolve_path(
    os.getenv("GENEALOGYDB_DATA_DIR"),
    PROJECT_ROOT / "data",
)
DEFAULT_DB_PATH = (DATA_DIR / "genealogy.db").resolve()
DB_NAME = str(_resolve_path(
    os.getenv("GENEALOGYDB_DB_NAME"),
    DEFAULT_DB_PATH,
))

# application version
APP_VERSION = os.getenv("GENEALOGYDB_APP_VERSION", "2.0")

GEOCODING_PROVIDER = os.getenv("GENEALOGYDB_GEOCODING_PROVIDER", "opencage")
GEOCODING_API_KEY = os.getenv("GENEALOGYDB_GEOCODING_API_KEY", "")
