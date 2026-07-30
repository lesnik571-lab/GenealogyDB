import sqlite3

from config import DB_NAME
from parser import parse_gedcom


def import_gedcom(filename):

    data = parse_gedcom(filename)

    people = data["people"]
    families = data["families"]

    conn = sqlite3.connect(DB_NAME)
    cur = conn.cursor()

    cur.execute("DELETE FROM family_children")
    cur.execute("DELETE FROM families")
    cur.execute("DELETE FROM people")
    conn.commit()

    imported_people = 0

    for p in people:

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
                p["gedcom_id"],
                p["first_name"],
                p["last_name"],
                p["sex"],
                p["birth_date"],
                p["birth_place"],
                p["death_date"],
                p["death_place"],
                p["occupation"],
                p["note"],
            ),
        )

        imported_people += 1

    for fam in families:

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
                fam["gedcom_id"],
                fam["husband"],
                fam["wife"],
            ),
        )

        for child in fam["children"]:

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
                    fam["gedcom_id"],
                    child,
                ),
            )

    conn.commit()
    conn.close()

    print(f"Импортировано людей: {imported_people}")
    print(f"Импортировано семей: {len(families)}")