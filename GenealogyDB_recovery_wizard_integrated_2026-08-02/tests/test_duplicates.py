import sqlite3
from pathlib import Path

from repository import PersonRepository


def test_duplicate_detection_identifies_exact_and_near_matches(tmp_path):
    db_path = tmp_path / "duplicates.db"
    conn = sqlite3.connect(db_path)
    schema_sql = Path("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema_sql)
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

    repo = PersonRepository(str(db_path))
    candidates = repo.find_duplicate_candidates()
    repo.close()

    assert len(candidates) >= 2
    assert any(candidate["confidence"] >= 0.95 for candidate in candidates)
    assert any(candidate["confidence"] >= 0.8 for candidate in candidates)
