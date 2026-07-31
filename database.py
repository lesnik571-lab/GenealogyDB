import sqlite3
from pathlib import Path

from config import DB_NAME


SCHEMA_PATH = Path(__file__).with_name("schema.sql")
REQUIRED_TABLES = {"people", "families", "family_children"}


def load_schema():
    return SCHEMA_PATH.read_text(encoding="utf-8")


def database_is_initialized(connection):
    cursor = connection.execute(
        """
        SELECT name
        FROM sqlite_master
        WHERE type = 'table'
        """
    )
    existing_tables = {row[0] for row in cursor.fetchall()}
    return REQUIRED_TABLES.issubset(existing_tables)


def initialize_database():
    database_path = Path(DB_NAME)
    database_path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(DB_NAME) as connection:
        if database_is_initialized(connection):
            return

        connection.executescript(load_schema())


if __name__ == "__main__":
    initialize_database()
    print("База данных готова.")
