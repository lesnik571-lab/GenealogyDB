import sqlite3
from pathlib import Path

import pytest

from config import DB_NAME
from repository import DatabaseRepository, PersonRepository


@pytest.fixture
def temp_db(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(db_path)
    try:
        schema_sql = Path("schema.sql").read_text(encoding="utf-8")
        conn.executescript(schema_sql)
        conn.commit()
        yield db_path
    finally:
        conn.close()


def test_person_repository_lists_and_fetches_people(temp_db):
    repo = PersonRepository(str(temp_db))

    conn = sqlite3.connect(temp_db)
    conn.execute(
        "INSERT INTO people (gedcom_id, first_name, last_name, birth_date, death_date) VALUES (?, ?, ?, ?, ?)",
        ("I1", "John", "Doe", "1 JAN 2000", ""),
    )
    conn.execute(
        "INSERT INTO people (gedcom_id, first_name, last_name, birth_date, death_date) VALUES (?, ?, ?, ?, ?)",
        ("I2", "Jane", "Smith", "", "2 JAN 2020"),
    )
    conn.commit()
    conn.close()

    rows = repo.list_people("D")
    assert len(rows) == 1
    assert rows[0][1] == "Doe"

    person = repo.get_person(1)
    assert person[1] == "Doe"
    assert person[2] == "John"

    repo.close()


def test_person_repository_relationship_methods(temp_db):
    repo = PersonRepository(str(temp_db))

    conn = sqlite3.connect(temp_db)
    conn.execute("INSERT INTO people (gedcom_id, first_name, last_name) VALUES (?, ?, ?)", ("I1", "John", "Doe"))
    conn.execute("INSERT INTO people (gedcom_id, first_name, last_name) VALUES (?, ?, ?)", ("I2", "Jane", "Doe"))
    conn.execute("INSERT INTO people (gedcom_id, first_name, last_name) VALUES (?, ?, ?)", ("I3", "Alice", "Doe"))
    conn.execute("INSERT INTO families (gedcom_id, husband_id, wife_id) VALUES (?, ?, ?)", ("F1", "I1", "I2"))
    conn.execute("INSERT INTO family_children (family_id, child_id) VALUES (?, ?)", ("F1", "I3"))
    conn.commit()
    conn.close()

    assert sorted(repo.get_parents("I3")) == sorted([("Doe", "John", "I1"), ("Doe", "Jane", "I2")])
    assert repo.get_spouses("I1") == [("Doe", "Jane", "I2")]
    assert repo.get_children("I1") == [("Doe", "Alice", "I3")]
    assert repo.get_siblings("I3") == []
    assert repo.get_person_by_gedcom_id("I1") == (1,)

    repo.close()


def test_person_repository_filters_by_name_year_and_sex(temp_db):
    repo = PersonRepository(str(temp_db))

    conn = sqlite3.connect(temp_db)
    conn.execute(
        "INSERT INTO people (gedcom_id, first_name, last_name, sex, birth_date, death_date) VALUES (?, ?, ?, ?, ?, ?)",
        ("I1", "John", "Doe", "M", "1 JAN 1900", "1 JAN 1950"),
    )
    conn.execute(
        "INSERT INTO people (gedcom_id, first_name, last_name, sex, birth_date, death_date) VALUES (?, ?, ?, ?, ?, ?)",
        ("I2", "Jane", "Smith", "F", "2 JAN 1910", "2 JAN 1960"),
    )
    conn.execute(
        "INSERT INTO people (gedcom_id, first_name, last_name, sex, birth_date, death_date) VALUES (?, ?, ?, ?, ?, ?)",
        ("I3", "John", "Smith", "M", "3 JAN 1920", "3 JAN 1980"),
    )
    conn.commit()
    conn.close()

    name_matches = repo.list_people(first_name="John")
    assert len(name_matches) == 2
    assert {row[2] for row in name_matches} == {"John"}

    combined_matches = repo.list_people(last_name="Smith", birth_year=1920, death_year=1980, sex="M")
    assert len(combined_matches) == 1
    assert combined_matches[0][1:3] == ("Smith", "John")

    repo.close()


def test_duplicate_detection_reports_exact_and_near_matches(temp_db):
    repo = PersonRepository(str(temp_db))

    conn = sqlite3.connect(temp_db)
    conn.execute(
        "INSERT INTO people (gedcom_id, first_name, last_name, birth_date, death_date) VALUES (?, ?, ?, ?, ?)",
        ("I1", "John", "Doe", "1 JAN 1900", "2 JAN 1950"),
    )
    conn.execute(
        "INSERT INTO people (gedcom_id, first_name, last_name, birth_date, death_date) VALUES (?, ?, ?, ?, ?)",
        ("I2", "Jöhn", "Döe", "01 Jan 1900", "02 JAN 1950"),
    )
    conn.execute(
        "INSERT INTO people (gedcom_id, first_name, last_name, birth_date, death_date) VALUES (?, ?, ?, ?, ?)",
        ("I3", "Jane", "Smith", "3 JAN 1910", "4 JAN 1960"),
    )
    conn.commit()
    conn.close()

    candidates = repo.find_duplicate_candidates()
    assert any(candidate["confidence"] >= 0.95 for candidate in candidates)
    assert any(candidate["confidence"] >= 0.8 for candidate in candidates)

    repo.close()


def test_database_repository_initializes_schema_and_imports_data(temp_db):
    repo = DatabaseRepository(str(temp_db))
    conn = repo.connect()
    repo.initialize_schema(conn)
    repo.clear_tables(conn)

    people = [{"gedcom_id": "I1", "first_name": "John", "last_name": "Doe", "sex": "M", "birth_date": "", "birth_place": "", "death_date": "", "death_place": "", "occupation": "", "note": ""}]
    families = [{"gedcom_id": "F1", "husband": "I1", "wife": "", "children": []}]

    imported_people = repo.import_people(conn, people)
    repo.import_families(conn, families)

    assert imported_people == 1
    assert conn.execute("SELECT COUNT(*) FROM people").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM families").fetchone()[0] == 1

    conn.close()
