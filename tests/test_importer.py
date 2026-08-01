import sqlite3
from pathlib import Path

import pytest

from importer import GedcomImporter


@pytest.fixture
def temp_db(tmp_path):
    db_path = tmp_path / "importer.db"
    conn = sqlite3.connect(db_path)
    try:
        schema_sql = Path("schema.sql").read_text(encoding="utf-8")
        conn.executescript(schema_sql)
        conn.commit()
        yield db_path
    finally:
        conn.close()


def test_importer_imports_gedcom_into_database(tmp_path, temp_db):
    importer = GedcomImporter(str(temp_db))
    fixture_path = Path(__file__).parent / "fixtures" / "sample.ged"

    importer.import_gedcom(str(fixture_path))

    conn = sqlite3.connect(temp_db)
    people_count = conn.execute("SELECT COUNT(*) FROM people").fetchone()[0]
    families_count = conn.execute("SELECT COUNT(*) FROM families").fetchone()[0]
    child_count = conn.execute("SELECT COUNT(*) FROM family_children").fetchone()[0]
    conn.close()

    assert people_count == 3
    assert families_count == 2
    assert child_count == 1


def test_importer_stores_names_in_sqlite_and_preserves_family_links(tmp_path, temp_db):
    importer = GedcomImporter(str(temp_db))
    gedcom_path = tmp_path / "whitespace_names.ged"
    gedcom_path.write_text(
        """0 HEAD
1 SOUR Test
0 @I1@ INDI
 1 NAME John /Doe/
 1 SEX M
0 @I2@ INDI
 1 NAME Jane /Smith/
 1 SEX F
0 @I3@ INDI
 1 NAME Alice /Doe/
 1 SEX F
0 @F1@ FAM
 1 HUSB @I1@
 1 WIFE @I2@
 1 CHIL @I3@
""",
        encoding="utf-8",
    )

    importer.import_gedcom(str(gedcom_path))

    conn = sqlite3.connect(temp_db)
    try:
        people_rows = conn.execute(
            "SELECT gedcom_id, first_name, last_name FROM people ORDER BY gedcom_id"
        ).fetchall()
        family_rows = conn.execute(
            "SELECT gedcom_id, husband_id, wife_id FROM families ORDER BY gedcom_id"
        ).fetchall()
        child_rows = conn.execute(
            "SELECT family_id, child_id FROM family_children ORDER BY family_id, child_id"
        ).fetchall()
    finally:
        conn.close()

    assert people_rows == [
        ("I1", "John", "Doe"),
        ("I2", "Jane", "Smith"),
        ("I3", "Alice", "Doe"),
    ]
    assert family_rows == [("F1", "I1", "I2")]
    assert child_rows == [("F1", "I3")]
