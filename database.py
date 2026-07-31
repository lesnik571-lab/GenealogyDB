import sqlite3
from pathlib import Path

from config import DB_NAME


SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def load_schema():
    return SCHEMA_PATH.read_text(encoding="utf-8")


def initialize_database():
    schema = load_schema()

    with sqlite3.connect(DB_NAME) as conn:
        conn.executescript(schema)


if __name__ == "__main__":
    initialize_database()
    print("База данных успешно создана.")