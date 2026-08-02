import sqlite3

from config import DB_NAME


class DatabaseRepository:
    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name

    def connect(self):
        conn = sqlite3.connect(self.db_name)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def load_schema_sql(self):
        with open("schema.sql", "r", encoding="utf-8") as f:
            return f.read()

    def initialize_schema(self, conn):
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys = ON")
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS people (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gedcom_id TEXT UNIQUE,
                first_name TEXT,
                last_name TEXT,
                sex TEXT,
                birth_date TEXT,
                birth_place TEXT,
                death_date TEXT,
                death_place TEXT,
                occupation TEXT,
                note TEXT
            );

            CREATE TABLE IF NOT EXISTS families (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gedcom_id TEXT UNIQUE,
                husband_id TEXT,
                wife_id TEXT,
                relationship_type TEXT NOT NULL DEFAULT 'unknown'
            );

            CREATE TABLE IF NOT EXISTS family_children (
                family_id TEXT,
                child_id TEXT
            );

            CREATE TABLE IF NOT EXISTS person_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                event_date TEXT,
                event_place TEXT,
                description TEXT,
                FOREIGN KEY(person_id) REFERENCES people(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS person_media (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id INTEGER NOT NULL,
                media_type TEXT NOT NULL CHECK(media_type IN ('photo', 'document')),
                title TEXT,
                file_path TEXT NOT NULL,
                description TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(person_id) REFERENCES people(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS person_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                person_id INTEGER NOT NULL,
                title TEXT,
                source_url TEXT,
                archive_reference TEXT,
                note TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(person_id) REFERENCES people(id) ON DELETE CASCADE
            );
            """
        )
        conn.commit()

    def clear_tables(self, conn):
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys = ON")
        cur.execute("DELETE FROM person_media")
        cur.execute("DELETE FROM person_sources")
        cur.execute("DELETE FROM person_events")
        cur.execute("DELETE FROM family_children")
        cur.execute("DELETE FROM families")
        cur.execute("DELETE FROM people")
        conn.commit()

    def import_people(self, conn, people):
        cur = conn.cursor()
        imported_people = 0

        for person in people:
            params = (
                person["gedcom_id"],
                person["first_name"],
                person["last_name"],
                person["sex"],
                person["birth_date"],
                person["birth_place"],
                person["death_date"],
                person["death_place"],
                person["occupation"],
                person["note"],
            )
            cur.execute(
                """
                INSERT OR REPLACE INTO people
                (
                    gedcom_id,
                    first_name,
                    last_name,
                    sex,
                    birth_date,
                    birth_place,
                    death_date,
                    death_place,
                    occupation,
                    note
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                params,
            )
            imported_people += 1

        conn.commit()
        return imported_people

    def import_families(self, conn, families):
        cur = conn.cursor()

        for family in families:
            cur.execute(
                """
                INSERT OR REPLACE INTO families
                (
                    gedcom_id,
                    husband_id,
                    wife_id,
                    relationship_type
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    family["gedcom_id"],
                    family["husband"],
                    family["wife"],
                    family.get("relationship_type") or "unknown",
                ),
            )

            for child in family["children"]:
                cur.execute(
                    """
                    INSERT INTO family_children
                    (
                        family_id,
                        child_id
                    )
                    VALUES (?, ?)
                    """,
                    (
                        family["gedcom_id"],
                        child,
                    ),
                )

        conn.commit()
