import sqlite3

from config import DB_NAME


class DatabaseRepository:
    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name

    def connect(self):
        return sqlite3.connect(self.db_name)

    def load_schema_sql(self):
        with open("schema.sql", "r", encoding="utf-8") as f:
            return f.read()

    def initialize_schema(self, conn):
        cur = conn.cursor()
        cur.executescript(self.load_schema_sql())
        conn.commit()

    def clear_tables(self, conn):
        cur = conn.cursor()
        cur.execute("DELETE FROM family_children")
        cur.execute("DELETE FROM families")
        cur.execute("DELETE FROM people")
        conn.commit()

    def import_people(self, conn, people):
        cur = conn.cursor()
        imported_people = 0

        for person in people:
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
                (
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
                ),
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
                    wife_id
                )
                VALUES (?, ?, ?)
                """,
                (
                    family["gedcom_id"],
                    family["husband"],
                    family["wife"],
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
