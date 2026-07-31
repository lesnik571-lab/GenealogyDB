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
        imported_families = 0
        imported_children = 0

        for family in families:
            self._insert_family(family)
            imported_families += 1
            imported_children += self._insert_children(family)

        return imported_families, imported_children

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
        imported_children = 0

        for child in dict.fromkeys(family["children"]):
            self.cursor.execute(
                """
                INSERT OR IGNORE INTO family_children
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
            imported_children += self.cursor.rowcount

        return imported_children


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
            imported_families, imported_children = FamilyImporter(
                cursor
            ).import_families(families)

        result = {
            "people": imported_people,
            "families": imported_families,
            "family_children": imported_children,
        }

        print(f"Импортировано людей: {result['people']}")
        print(f"Импортировано семей: {result['families']}")
        print(f"Импортировано связей с детьми: {result['family_children']}")
        return result


def import_gedcom(filename):
    return GedcomImporter().import_file(filename)
