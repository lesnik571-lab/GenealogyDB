import sqlite3

from config import DB_NAME


class PersonRepository:
    def __init__(self, db_name=DB_NAME):
        self.conn = sqlite3.connect(db_name)
        self.cur = self.conn.cursor()

    def close(self):
        self.conn.close()

    def list_people(self, surname=None):
        if surname:
            self.cur.execute(
                """
                SELECT id, last_name, first_name, birth_date, death_date
                FROM people
                WHERE last_name LIKE ? COLLATE NOCASE
                ORDER BY last_name, first_name
                """,
                (surname + "%",),
            )
        else:
            self.cur.execute(
                """
                SELECT id, last_name, first_name, birth_date, death_date
                FROM people
                ORDER BY last_name, first_name
                LIMIT 500
                """
            )

        return self.cur.fetchall()

    def get_person(self, person_id):
        self.cur.execute(
            """
            SELECT
                gedcom_id,
                last_name,
                first_name,
                sex,
                birth_date,
                birth_place,
                death_date,
                death_place,
                occupation,
                note
            FROM people
            WHERE id = ?
            """,
            (person_id,),
        )
        return self.cur.fetchone()

    def get_person_by_gedcom_id(self, gedcom_id):
        self.cur.execute(
            """
            SELECT id
            FROM people
            WHERE gedcom_id = ?
            """,
            (gedcom_id,),
        )
        return self.cur.fetchone()

    def get_parents(self, person_gedcom_id):
        if not person_gedcom_id:
            return []

        self.cur.execute(
            """
            SELECT DISTINCT p.last_name, p.first_name, p.gedcom_id
            FROM family_children fc
            JOIN families f ON f.gedcom_id = fc.family_id
            JOIN people p
              ON p.gedcom_id = f.husband_id
              OR p.gedcom_id = f.wife_id
            WHERE fc.child_id = ?
            ORDER BY p.last_name, p.first_name
            """,
            (person_gedcom_id,),
        )
        return self.cur.fetchall()

    def get_spouses(self, person_gedcom_id):
        if not person_gedcom_id:
            return []

        self.cur.execute(
            """
            SELECT DISTINCT p.last_name, p.first_name, p.gedcom_id
            FROM families f
            JOIN people p
              ON (f.husband_id = ? AND p.gedcom_id = f.wife_id)
              OR (f.wife_id = ? AND p.gedcom_id = f.husband_id)
            WHERE f.husband_id = ? OR f.wife_id = ?
            ORDER BY p.last_name, p.first_name
            """,
            (
                person_gedcom_id,
                person_gedcom_id,
                person_gedcom_id,
                person_gedcom_id,
            ),
        )
        return self.cur.fetchall()

    def get_children(self, person_gedcom_id):
        if not person_gedcom_id:
            return []

        self.cur.execute(
            """
            SELECT DISTINCT c.last_name, c.first_name, c.gedcom_id
            FROM families f
            JOIN family_children fc ON fc.family_id = f.gedcom_id
            JOIN people c ON c.gedcom_id = fc.child_id
            WHERE f.husband_id = ? OR f.wife_id = ?
            ORDER BY c.last_name, c.first_name
            """,
            (person_gedcom_id, person_gedcom_id),
        )
        return self.cur.fetchall()

    def get_siblings(self, person_gedcom_id):
        if not person_gedcom_id:
            return []

        self.cur.execute(
            """
            SELECT DISTINCT p.last_name, p.first_name, p.gedcom_id
            FROM people p
            JOIN family_children fc ON p.gedcom_id = fc.child_id
            JOIN families f ON f.gedcom_id = fc.family_id
            JOIN family_children parent_fc ON f.gedcom_id = parent_fc.family_id
            WHERE parent_fc.child_id = ?
              AND p.gedcom_id != ?
              AND p.gedcom_id != ''
            ORDER BY p.last_name, p.first_name
            """,
            (person_gedcom_id, person_gedcom_id),
        )
        return self.cur.fetchall()
