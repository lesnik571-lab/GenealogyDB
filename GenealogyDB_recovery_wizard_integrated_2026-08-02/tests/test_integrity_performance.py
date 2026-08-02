import sqlite3
import time
from pathlib import Path

from integrity_service import IntegrityCheckService
from repository import PersonRepository


def _build_performance_db(tmp_path, people_count=10000):
    db_path = tmp_path / "integrity_perf.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(Path("schema.sql").read_text(encoding="utf-8"))

    people_rows = []
    for index in range(people_count):
        gedcom_id = f"I{index + 1}"
        # Creates predictable groups while avoiding global all-to-all duplicate comparisons.
        first_name = f"Alex{index % 80}"
        last_name = f"Family{index % 250}"
        birth_year = 1850 + (index % 140)
        birth_date = f"1 JAN {birth_year}"
        people_rows.append((gedcom_id, first_name, last_name, "M" if index % 2 else "F", birth_date, "", "", "", "", ""))

    conn.executemany(
        """
        INSERT INTO people (
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
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        people_rows,
    )

    family_rows = []
    family_children_rows = []
    for fam_index in range(1, 2001):
        father_id = f"I{fam_index}"
        mother_id = f"I{fam_index + 2000}"
        family_id = f"F{fam_index}"
        family_rows.append((family_id, father_id, mother_id))
        child_1 = f"I{5000 + (fam_index % 4000)}"
        child_2 = f"I{6000 + (fam_index % 3000)}"
        family_children_rows.append((family_id, child_1))
        family_children_rows.append((family_id, child_2))

    conn.executemany("INSERT INTO families (gedcom_id, husband_id, wife_id) VALUES (?, ?, ?)", family_rows)
    conn.executemany("INSERT INTO family_children (family_id, child_id) VALUES (?, ?)", family_children_rows)

    conn.commit()
    conn.close()
    return db_path


def test_integrity_scan_performance_10000_people(tmp_path):
    db_path = _build_performance_db(tmp_path, people_count=10000)
    repo = PersonRepository(str(db_path))
    service = IntegrityCheckService(repo, data_dir=tmp_path / "data")

    started = time.perf_counter()
    report = service.run_checks()
    elapsed = time.perf_counter() - started

    assert isinstance(report, dict)
    assert set(report.keys()) == {"duplicates", "date_problems", "broken_relationships", "empty_people"}
    assert elapsed < 25.0, f"Integrity scan is too slow for 10k people: {elapsed:.2f}s"

    repo.close()
