import sqlite3
from datetime import datetime
from pathlib import Path

from config import DB_NAME, RESOURCE_DIR
from logging_service import log_operation

SCHEMA_PATH = RESOURCE_DIR / "schema.sql"
REQUIRED_COLUMNS = {
    "people": {
        "id",
        "gedcom_id",
        "first_name",
        "last_name",
        "sex",
        "birth_date",
        "birth_place",
        "death_date",
        "death_place",
        "occupation",
        "note",
    },
    "families": {"id", "gedcom_id", "husband_id", "wife_id", "relationship_type"},
    "family_children": {"family_id", "child_id"},
    "person_events": {"id", "person_id", "event_type", "event_date", "event_place", "description"},
    "person_media": {"id", "person_id", "media_type", "title", "file_path", "description", "created_at"},
    "person_sources": {"id", "person_id", "title", "source_url", "archive_reference", "note", "created_at"},
    "geocoding_cache": {"id", "normalized_place", "original_place", "latitude", "longitude", "status", "provider", "error_message", "updated_at"},
}

CORE_REQUIRED_COLUMNS = {
    "people": REQUIRED_COLUMNS["people"],
    "families": REQUIRED_COLUMNS["families"],
    "family_children": REQUIRED_COLUMNS["family_children"],
    "person_events": REQUIRED_COLUMNS["person_events"],
}


def supported_schema_requirements():
    """Return the application's mandatory and optional table requirements.

    The core relationship and event tables are required for every supported
    GenealogyDB database. Feature tables can be absent in legacy databases.
    """
    mandatory = {name: frozenset(columns) for name, columns in CORE_REQUIRED_COLUMNS.items()}
    optional = {
        name: frozenset(columns)
        for name, columns in REQUIRED_COLUMNS.items()
        if name not in CORE_REQUIRED_COLUMNS
    }
    return mandatory, optional


def load_schema(schema_path=SCHEMA_PATH):
    """Load SQL schema text from disk."""
    path = Path(schema_path)
    try:
        schema = path.read_text(encoding="utf-8")
    except OSError as error:
        raise RuntimeError(f"Не удалось прочитать схему базы: {path}") from error

    if not schema.strip():
        raise RuntimeError(f"Файл схемы базы пуст: {path}")

    return schema


def _table_columns(connection, table_name):
    cursor = connection.execute(f'PRAGMA table_info("{table_name}")')
    return {row[1] for row in cursor.fetchall()}


def database_is_initialized(connection):
    """Return whether a connection contains the required application tables."""
    cursor = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        """
    )
    existing_tables = {row[0] for row in cursor.fetchall()}

    for table_name, required_columns in REQUIRED_COLUMNS.items():
        if table_name not in existing_tables:
            return False
        if not required_columns.issubset(_table_columns(connection, table_name)):
            return False

    return True


def _core_database_is_initialized(connection):
    cursor = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        """
    )
    existing_tables = {row[0] for row in cursor.fetchall()}

    for table_name, required_columns in CORE_REQUIRED_COLUMNS.items():
        if table_name not in existing_tables:
            return False
        if not required_columns.issubset(_table_columns(connection, table_name)):
            return False

    return True


def initialize_database(database_name=DB_NAME, schema_path=SCHEMA_PATH):
    """Create or upgrade an application database and return its path."""
    database_path = Path(database_name)
    database_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        if _core_database_is_initialized(connection):
            _ensure_family_relationship_type_column(connection)
            _ensure_person_events_table(connection)
            _ensure_person_media_and_sources_tables(connection)
            _ensure_source_management_tables(connection)
            _ensure_geocoding_cache_table(connection)
            connection.commit()
            return False

        connection.executescript(load_schema(schema_path))
        _ensure_family_relationship_type_column(connection)
        _ensure_person_events_table(connection)
        _ensure_person_media_and_sources_tables(connection)
        _ensure_source_management_tables(connection)
        _ensure_geocoding_cache_table(connection)
        connection.commit()
        return True
    finally:
        connection.close()


def _ensure_family_relationship_type_column(connection):
    existing_tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "families" not in existing_tables:
        return
    columns = {row[1] for row in connection.execute('PRAGMA table_info("families")')}
    if "relationship_type" not in columns:
        connection.execute("ALTER TABLE families ADD COLUMN relationship_type TEXT NOT NULL DEFAULT 'unknown'")
        connection.commit()


def _ensure_person_events_table(connection):
    existing_tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "person_events" not in existing_tables:
        connection.execute(
            """
            CREATE TABLE person_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                event_date TEXT,
                event_place TEXT,
                description TEXT,
                FOREIGN KEY(person_id) REFERENCES people(id) ON DELETE CASCADE
            )
            """
        )
        connection.commit()
        return

    required_columns = {"id", "person_id", "event_type", "event_date", "event_place", "description"}
    columns = {row[1] for row in connection.execute('PRAGMA table_info("person_events")')}
    missing_columns = sorted(required_columns - columns)
    if missing_columns:
        for column_name in missing_columns:
            if column_name == "event_date":
                connection.execute("ALTER TABLE person_events ADD COLUMN event_date TEXT")
            elif column_name == "event_place":
                connection.execute("ALTER TABLE person_events ADD COLUMN event_place TEXT")
            elif column_name == "description":
                connection.execute("ALTER TABLE person_events ADD COLUMN description TEXT")
            elif column_name == "person_id":
                connection.execute("ALTER TABLE person_events ADD COLUMN person_id INTEGER")
            elif column_name == "event_type":
                connection.execute("ALTER TABLE person_events ADD COLUMN event_type TEXT")
            elif column_name == "id":
                connection.execute("ALTER TABLE person_events ADD COLUMN id INTEGER")
        connection.commit()


def _ensure_person_media_and_sources_tables(connection):
    connection.execute("PRAGMA foreign_keys = ON")
    existing_tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    if "person_media" not in existing_tables:
        connection.execute(
            """
            CREATE TABLE person_media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id INTEGER NOT NULL,
                media_type TEXT NOT NULL CHECK(media_type IN ('photo', 'document')),
                title TEXT,
                file_path TEXT NOT NULL,
                description TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(person_id) REFERENCES people(id) ON DELETE CASCADE
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_person_media_person_id ON person_media(person_id)")

    if "person_sources" not in existing_tables:
        connection.execute(
            """
            CREATE TABLE person_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id INTEGER NOT NULL,
                title TEXT,
                source_url TEXT,
                archive_reference TEXT,
                note TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(person_id) REFERENCES people(id) ON DELETE CASCADE
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_person_sources_person_id ON person_sources(person_id)")

    connection.commit()


def _ensure_source_management_tables(connection):
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT,
            publication TEXT,
            repository_name TEXT,
            call_number TEXT,
            source_url TEXT,
            notes TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS citations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            target_type TEXT NOT NULL CHECK(target_type IN ('person', 'family', 'event', 'relationship')),
            target_id TEXT NOT NULL,
            page TEXT,
            quality TEXT,
            transcription TEXT,
            comment TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE CASCADE
        )
        """
    )
    connection.execute("CREATE INDEX IF NOT EXISTS idx_citations_source_id ON citations(source_id)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_citations_target ON citations(target_type, target_id)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_sources_repository ON sources(repository_name)")
    connection.commit()


def _ensure_geocoding_cache_table(connection):
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS geocoding_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            normalized_place TEXT NOT NULL UNIQUE,
            original_place TEXT,
            latitude REAL,
            longitude REAL,
            status TEXT NOT NULL DEFAULT 'missing',
            provider TEXT,
            error_message TEXT,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_geocoding_cache_normalized_place ON geocoding_cache(normalized_place)"
    )
    connection.commit()


def validate_database_file(database_path):
    """Validate that a file is a readable initialized application database."""
    path = Path(database_path).expanduser()
    if not path.exists():
        raise ValueError(f"Файл базы не найден: {path}")
    if not path.is_file():
        raise ValueError(f"Путь к базе не является файлом: {path}")

    connection = None
    try:
        connection = sqlite3.connect(
            f"file:{path.resolve().as_posix()}?mode=ro",
            uri=True,
        )
        try:
            integrity_check = connection.execute("PRAGMA integrity_check").fetchone()
            if not integrity_check or str(integrity_check[0]).upper() != "OK":
                raise ValueError(f"Файл базы повреждён: {path}")

            existing_tables = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            missing_tables = [name for name in REQUIRED_COLUMNS if name not in existing_tables]
            if missing_tables:
                raise ValueError(
                    f"Файл базы не содержит ожидаемых таблиц: {', '.join(sorted(missing_tables))}"
                )

            for table_name, required_columns in REQUIRED_COLUMNS.items():
                columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table_name}")')}
                missing_columns = sorted(required_columns - columns)
                if missing_columns:
                    raise ValueError(
                        f"Таблица {table_name} не содержит ожидаемых столбцов: {', '.join(missing_columns)}"
                    )
        finally:
            connection.close()
    except sqlite3.DatabaseError as error:
        raise ValueError(f"Не удалось прочитать файл SQLite: {path}") from error

    return True


def _build_backup_path(source_path, destination_path=None):
    source = Path(source_path).expanduser()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if destination_path is None:
        return source.with_name(f"{source.stem}-{timestamp}{source.suffix}")

    destination = Path(destination_path).expanduser()
    if destination.exists() and destination.is_dir():
        return destination / f"{source.stem}-{timestamp}{source.suffix}"
    if destination.suffix:
        return destination.with_name(f"{destination.stem}-{timestamp}{destination.suffix}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    return destination / f"{source.stem}-{timestamp}{source.suffix}"


@log_operation("Database backup")
def backup_database(source_path, destination_path=None):
    """Create a consistent SQLite backup and return its destination path."""
    source = Path(source_path).expanduser()
    if not source.exists():
        raise FileNotFoundError(f"Файл базы не найден: {source}")

    validate_database_file(source)
    destination = _build_backup_path(source, destination_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(
        f"file:{source.resolve().as_posix()}?mode=ro",
        uri=True,
    )
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()
    validate_database_file(destination)
    return destination


@log_operation("Database restore")
def restore_database(source_path, target_path, create_safety_backup=True):
    """Restore a validated database through SQLite's online backup mechanism."""
    source = Path(source_path).expanduser()
    target = Path(target_path).expanduser()

    if not source.exists():
        raise FileNotFoundError(f"Файл резервной копии не найден: {source}")

    validate_database_file(source)

    backup_path = None
    if create_safety_backup and target.exists():
        backup_path = backup_database(target, target.parent / f"{target.stem}-before-restore{target.suffix}")

    target.parent.mkdir(parents=True, exist_ok=True)
    source_connection = sqlite3.connect(
        f"file:{source.resolve().as_posix()}?mode=ro",
        uri=True,
    )
    target_connection = sqlite3.connect(target)
    try:
        source_connection.backup(target_connection)
    finally:
        target_connection.close()
        source_connection.close()
    validate_database_file(target)
    return backup_path


if __name__ == "__main__":
    initialize_database()
