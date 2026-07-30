import sqlite3
from config import DB_NAME


def initialize_database():
    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    with open("schema.sql", "r", encoding="utf-8") as f:
        cur.executescript(f.read())

    conn.commit()
    conn.close()


if __name__ == "__main__":
    initialize_database()
    print("База данных успешно создана.")