import sqlite3

from config import DB_NAME
from parser import parse_gedcom


class DatabaseCleaner:
    def __init__(self, cursor):
        self.cursor = cursor

    def clear(self):
        self.cursor.execute("DELETE FROM family_children")
        self.cursor.execute("DELETE FROM families")
        self.cursor.execute("DELETE FROM people")


class PeopleImporter:
    def __init__(self, cursor):
        self.cursor = cursor

    def import_people(self, people):
        imported_people = 0

        for person in people:
            self.cursor.execute(
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

        return imported_people


class FamilyImporter:
    def __init__(self, cursor):
        self.cursor = cursor

    def import_families(self, families):
        for family in families:
            self._insert_family(family)
            self._insert_children(family)

    def _insert_family(self, family):
        self.cursor.execute(
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

    def _insert_children(self, family):
        for child in family["children"]:
            self.cursor.execute(
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


class GedcomImporter:
    def __init__(self, database_name=DB_NAME):
        self.database_name = database_name

    def import_file(self, filename):
        data = parse_gedcom(filename)
        people = data["people"]
        families = data["families"]

        with sqlite3.connect(self.database_name) as connection:
            cursor = connection.cursor()
            DatabaseCleaner(cursor).clear()
            imported_people = PeopleImporter(cursor).import_people(people)
            FamilyImporter(cursor).import_families(families)

        print(f"Импортировано людей: {imported_people}")
        print(f"Импортировано семей: {len(families)}")


def import_gedcom(filename):
    GedcomImporter().import_file(filename)
