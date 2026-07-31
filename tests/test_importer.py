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
