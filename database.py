import sqlite3
from pathlib import Path

from config import DB_NAME


SCHEMA_PATH = Path(__file__).with_name("schema.sql")
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
    "families": {"id", "gedcom_id", "husband_id", "wife_id"},
    "family_children": {"family_id", "child_id"},
}


def load_schema():
    return SCHEMA_PATH.read_text(encoding="utf-8")


def _table_columns(connection, table_name):
    cursor = connection.execute(f'PRAGMA table_info("{table_name}")')
    return {row[1] for row in cursor.fetchall()}


def database_is_initialized(connection):
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


def initialize_database(database_name=DB_NAME):
    database_path = Path(database_name)
    database_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(database_name) as connection:
        if database_is_initialized(connection):
            return False

        connection.executescript(load_schema())
        return True


if __name__ == "__main__":
    created = initialize_database()
    if created:
        print("База данных создана.")
    else:
        print("База данных уже готова.")
