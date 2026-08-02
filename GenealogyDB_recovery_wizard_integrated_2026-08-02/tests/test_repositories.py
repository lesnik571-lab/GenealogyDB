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


def test_relationship_queries_cover_all_explicit_relatives_without_duplicates_or_self(temp_db):
    repo = PersonRepository(str(temp_db))

    conn = sqlite3.connect(temp_db)
    people = [
        ("I1", "John", "Root"),
        ("I2", "Jane", "SpouseA"),
        ("I3", "Lena", "SpouseB"),
        ("I4", "Child1", "Root"),
        ("I5", "Child2", "Root"),
        ("I6", "Child3", "Root"),
        ("I7", "Partner", "Child1"),
        ("I8", "Grandchild", "Root"),
        ("I9", "Father", "Root"),
        ("I10", "Mother", "Root"),
        ("I11", "Grandpa1", "Root"),
        ("I12", "Grandma1", "Root"),
        ("I13", "Grandpa2", "Root"),
        ("I14", "Grandma2", "Root"),
        ("I15", "Full", "Sibling"),
        ("I16", "HalfF", "Sibling"),
        ("I17", "HalfM", "Sibling"),
        ("I18", "OtherMother", "Root"),
        ("I19", "OtherFather", "Root"),
        ("I20", "AdoptiveFather", "Root"),
        ("I21", "AdoptiveMother", "Root"),
        ("I22", "UncleP", "Root"),
        ("I23", "AuntM", "Root"),
        ("I24", "Cousin1", "Root"),
        ("I25", "Cousin2", "Root"),
        ("I26", "Nephew", "Root"),
    ]
    conn.executemany(
        "INSERT INTO people (gedcom_id, first_name, last_name) VALUES (?, ?, ?)",
        people,
    )

    families = [
        ("F1", "I9", "I10"),
        ("F1_DUP", "I9", "I10"),
        ("F2", "I11", "I12"),
        ("F3", "I13", "I14"),
        ("F4", "I9", "I18"),
        ("F5", "I19", "I10"),
        ("F6", "I1", "I2"),
        ("F6_DUP", "I1", "I2"),
        ("F7", "I1", "I3"),
        ("F8", "I4", "I7"),
        ("F9", "I20", "I21"),
        ("F10", "I11", "I12"),
        ("F11", "I13", "I14"),
        ("F12", "I15", ""),
        ("F13", "I22", ""),
        ("F14", "I23", ""),
    ]
    conn.executemany("INSERT INTO families (gedcom_id, husband_id, wife_id) VALUES (?, ?, ?)", families)

    family_children = [
        ("F1", "I1"),
        ("F1", "I15"),
        ("F1_DUP", "I1"),
        ("F2", "I9"),
        ("F3", "I10"),
        ("F4", "I16"),
        ("F5", "I17"),
        ("F6", "I4"),
        ("F6", "I5"),
        ("F6_DUP", "I5"),
        ("F7", "I6"),
        ("F8", "I8"),
        ("F9", "I1"),
        ("F9", "I16"),
        ("F10", "I9"),
        ("F10", "I22"),
        ("F11", "I10"),
        ("F11", "I23"),
        ("F12", "I26"),
        ("F13", "I24"),
        ("F14", "I25"),
        ("BROKEN_FAMILY", "I1"),
    ]
    conn.executemany("INSERT INTO family_children (family_id, child_id) VALUES (?, ?)", family_children)
    conn.commit()
    conn.close()

    biological_fathers = repo.get_biological_fathers("I1")
    biological_mothers = repo.get_biological_mothers("I1")
    adoptive_parents = repo.get_adoptive_parents("I1")
    spouses = repo.get_spouses("I1")
    children = repo.get_children("I1")
    full_siblings = repo.get_full_siblings("I1")
    half_siblings_paternal = repo.get_half_siblings_paternal("I1")
    half_siblings_maternal = repo.get_half_siblings_maternal("I1")
    siblings = repo.get_siblings("I1")
    grandparents = repo.get_grandparents("I1")
    grandchildren = repo.get_grandchildren("I1")
    uncles_aunts = repo.get_uncles_aunts("I1")
    nephews_nieces = repo.get_nephews_nieces("I1")
    first_cousins = repo.get_first_cousins("I1")

    assert {row[2] for row in biological_fathers} == {"I9"}
    assert {row[2] for row in biological_mothers} == {"I10"}
    assert {row[2] for row in adoptive_parents} == {"I20", "I21"}
    assert {row[2] for row in spouses} == {"I2", "I3"}
    assert {row[2] for row in children} == {"I4", "I5", "I6"}
    assert {row[2] for row in full_siblings} == {"I15"}
    assert {row[2] for row in half_siblings_paternal} == {"I16"}
    assert {row[2] for row in half_siblings_maternal} == {"I17"}
    assert {row[2] for row in siblings} == {"I15", "I16", "I17"}
    assert {row[2] for row in grandparents} == {"I11", "I12", "I13", "I14"}
    assert {row[2] for row in grandchildren} == {"I8"}
    assert {row[2] for row in uncles_aunts} == {"I22", "I23"}
    assert {row[2] for row in nephews_nieces} == {"I26"}
    assert {row[2] for row in first_cousins} == {"I24", "I25"}

    # Duplicate family/child relations should not duplicate relatives.
    assert len(adoptive_parents) == 2
    assert len(spouses) == 2
    assert len(children) == 3
    assert len(full_siblings) == 1
    assert len(half_siblings_paternal) == 1
    assert len(half_siblings_maternal) == 1

    # Person must never appear in own relative lists.
    relative_sets = [
        biological_fathers,
        biological_mothers,
        adoptive_parents,
        spouses,
        children,
        siblings,
        full_siblings,
        half_siblings_paternal,
        half_siblings_maternal,
        grandparents,
        grandchildren,
        uncles_aunts,
        nephews_nieces,
        first_cousins,
    ]
    assert all("I1" not in {row[2] for row in rows} for rows in relative_sets)

    repo.close()


def test_relationship_queries_support_manual_parent_child_links_and_deduplicate_children(temp_db):
    repo = PersonRepository(str(temp_db))

    conn = sqlite3.connect(temp_db)
    father_id = conn.execute(
        "INSERT INTO people (gedcom_id, first_name, last_name) VALUES (?, ?, ?)",
        ("", "Manual", "Father"),
    ).lastrowid
    mother_id = conn.execute(
        "INSERT INTO people (gedcom_id, first_name, last_name) VALUES (?, ?, ?)",
        ("P_MOTHER", "Manual", "Mother"),
    ).lastrowid
    child_a_id = conn.execute(
        "INSERT INTO people (gedcom_id, first_name, last_name) VALUES (?, ?, ?)",
        ("P_CHILD_A", "Child", "A"),
    ).lastrowid
    child_b_id = conn.execute(
        "INSERT INTO people (gedcom_id, first_name, last_name) VALUES (?, ?, ?)",
        ("P_CHILD_B", "Child", "B"),
    ).lastrowid

    conn.execute(
        "INSERT INTO families (gedcom_id, husband_id, wife_id) VALUES (?, ?, ?)",
        ("F_MANUAL", str(father_id), str(mother_id)),
    )
    conn.executemany(
        "INSERT INTO family_children (family_id, child_id) VALUES (?, ?)",
        [
            ("F_MANUAL", str(child_a_id)),
            ("F_MANUAL", str(child_a_id)),
            ("F_MANUAL", str(child_b_id)),
        ],
    )
    conn.commit()
    conn.close()

    father_children = repo.get_children(str(father_id))
    mother_children = repo.get_children(str(mother_id))
    child_a_parents = repo.get_parents(str(child_a_id))

    assert {row[2] for row in father_children} == {"P_CHILD_A", "P_CHILD_B"}
    assert {row[2] for row in mother_children} == {"P_CHILD_A", "P_CHILD_B"}
    assert {row[2] for row in child_a_parents} == {str(father_id), "P_MOTHER"}
    assert len(father_children) == 2
    assert len(mother_children) == 2

    repo.close()


def test_relationship_queries_support_mixed_manual_and_gedcom_references(temp_db):
    repo = PersonRepository(str(temp_db))

    conn = sqlite3.connect(temp_db)
    manual_father_id = conn.execute(
        "INSERT INTO people (gedcom_id, first_name, last_name) VALUES (?, ?, ?)",
        ("", "Manual", "Father"),
    ).lastrowid
    mother_id = conn.execute(
        "INSERT INTO people (gedcom_id, first_name, last_name) VALUES (?, ?, ?)",
        ("I200", "Gedcom", "Mother"),
    ).lastrowid
    child_id = conn.execute(
        "INSERT INTO people (gedcom_id, first_name, last_name) VALUES (?, ?, ?)",
        ("I100", "Gedcom", "Child"),
    ).lastrowid

    conn.execute(
        "INSERT INTO families (gedcom_id, husband_id, wife_id) VALUES (?, ?, ?)",
        ("F_MIXED", str(manual_father_id), "I200"),
    )
    conn.execute(
        "INSERT INTO family_children (family_id, child_id) VALUES (?, ?)",
        ("F_MIXED", "I100"),
    )
    conn.commit()
    conn.close()

    parents = repo.get_parents(str(child_id))
    father_children = repo.get_children(str(manual_father_id))

    assert {row[2] for row in parents} == {str(manual_father_id), "I200"}
    assert {row[2] for row in father_children} == {"I100"}

    repo.close()
