import sqlite3
from pathlib import Path

from integrity_service import IntegrityCheckService
from repository import PersonRepository


def _build_repo(tmp_path, db_name="integrity.db"):
    db_path = tmp_path / db_name
    conn = sqlite3.connect(db_path)
    conn.executescript(Path("schema.sql").read_text(encoding="utf-8"))
    conn.commit()
    conn.close()
    return PersonRepository(str(db_path))


def test_integrity_duplicate_detection(tmp_path):
    repo = _build_repo(tmp_path, "dup.db")
    repo.create_person({"gedcom_id": "I1", "first_name": "Boris", "last_name": "Lesnik", "sex": "M", "birth_date": "1 JAN 1900", "birth_place": "Moscow", "death_date": "", "death_place": "", "occupation": "", "note": ""})
    repo.create_person({"gedcom_id": "I2", "first_name": "Борис", "last_name": "Лесник", "sex": "M", "birth_date": "1900", "birth_place": "Moscow", "death_date": "", "death_place": "", "occupation": "", "note": ""})

    service = IntegrityCheckService(repo, data_dir=tmp_path / "data")
    report = service.run_checks()

    assert report["duplicates"]
    assert any(item["left_person_id"] and item["right_person_id"] for item in report["duplicates"])
    duplicate = report["duplicates"][0]
    assert duplicate["match_score"] == 95
    assert duplicate["match_reasons"] == [
        "same name",
        "same birth year: 1900",
        "same birth place",
    ]
    repo.close()


def test_integrity_contradictory_dates(tmp_path):
    repo = _build_repo(tmp_path, "dates.db")

    repo.create_person({"gedcom_id": "I1", "first_name": "Parent", "last_name": "A", "sex": "M", "birth_date": "1 JAN 2000", "birth_place": "", "death_date": "", "death_place": "", "occupation": "", "note": ""})
    child_id = repo.create_person({"gedcom_id": "I2", "first_name": "Child", "last_name": "A", "sex": "F", "birth_date": "1 JAN 1990", "birth_place": "", "death_date": "1 JAN 1989", "death_place": "", "occupation": "", "note": ""})
    spouse_id = repo.create_person({"gedcom_id": "I3", "first_name": "Spouse", "last_name": "A", "sex": "F", "birth_date": "1 JAN 2001", "birth_place": "", "death_date": "1 JAN 2010", "death_place": "", "occupation": "", "note": ""})

    repo.create_family({"gedcom_id": "F1", "husband": "I1", "wife": "I3", "children": ["I2"]})
    repo.create_person_event({"person_id": spouse_id, "event_type": "marriage", "date": "1 JAN 1990", "place": "", "description": ""})
    repo.create_person_event({"person_id": spouse_id, "event_type": "custom", "date": "1 JAN 2020", "place": "", "description": ""})
    repo.create_person_event({"person_id": child_id, "event_type": "custom", "date": "32 JAN 2000", "place": "", "description": ""})

    service = IntegrityCheckService(repo, data_dir=tmp_path / "data")
    report = service.run_checks()
    messages = [item["message"] for item in report["date_problems"]]

    assert any("раньше даты рождения" in message for message in messages)
    assert any("Ребенок родился раньше родителя" in message for message in messages)
    assert any("меньше 12 лет" in message for message in messages)
    assert any("Дата брака раньше даты рождения" in message for message in messages)
    assert any("после смерти" in message for message in messages)
    assert any("невозможная календарная дата" in message for message in messages)
    repo.close()


def test_integrity_broken_links_self_relations_and_orphans(tmp_path):
    repo = _build_repo(tmp_path, "broken.db")

    repo.create_person({"gedcom_id": "I1", "first_name": "Self", "last_name": "X", "sex": "M", "birth_date": "", "birth_place": "", "death_date": "", "death_place": "", "occupation": "", "note": ""})

    conn = repo.conn
    conn.execute("INSERT INTO families (gedcom_id, husband_id, wife_id) VALUES (?, ?, ?)", ("F1", "I1", "I1"))
    conn.execute("INSERT INTO family_children (family_id, child_id) VALUES (?, ?)", ("F1", "I1"))
    conn.execute("INSERT INTO family_children (family_id, child_id) VALUES (?, ?)", ("F1", "I1"))
    conn.execute("INSERT INTO families (gedcom_id, husband_id, wife_id) VALUES (?, ?, ?)", ("F2", "MISSING", ""))
    conn.execute("INSERT INTO family_children (family_id, child_id) VALUES (?, ?)", ("BROKEN", "I1"))
    conn.execute("INSERT INTO family_children (family_id, child_id) VALUES (?, ?)", ("F2", "MISSING_CHILD"))
    conn.commit()

    service = IntegrityCheckService(repo, data_dir=tmp_path / "data")
    report = service.run_checks()
    messages = [item["message"] for item in report["broken_relationships"]]

    assert any("несуществующ" in message for message in messages)
    assert any("дублирующ" in message for message in messages)
    assert any("сам с собой как супруг" in message for message in messages)
    assert any("сам с собой как родитель/ребенок" in message for message in messages)
    repo.close()


def test_integrity_circular_ancestry(tmp_path):
    repo = _build_repo(tmp_path, "cycle.db")
    for gedcom_id in ["I1", "I2", "I3"]:
        repo.create_person({"gedcom_id": gedcom_id, "first_name": gedcom_id, "last_name": "Cycle", "sex": "", "birth_date": "", "birth_place": "", "death_date": "", "death_place": "", "occupation": "", "note": ""})

    repo.create_family({"gedcom_id": "F1", "husband": "I2", "wife": "", "children": ["I1"]})
    repo.create_family({"gedcom_id": "F2", "husband": "I3", "wife": "", "children": ["I2"]})
    repo.create_family({"gedcom_id": "F3", "husband": "I1", "wife": "", "children": ["I3"]})

    service = IntegrityCheckService(repo, data_dir=tmp_path / "data")
    report = service.run_checks()

    assert any("циклический предок" in item["message"] for item in report["broken_relationships"])
    repo.close()


def test_integrity_empty_people_detection(tmp_path):
    repo = _build_repo(tmp_path, "empty.db")

    conn = repo.conn
    conn.execute(
        "INSERT INTO people (gedcom_id, first_name, last_name, birth_date, death_date) VALUES (?, ?, ?, ?, ?)",
        ("I1", "", "", "", ""),
    )
    conn.execute(
        "INSERT INTO people (gedcom_id, first_name, last_name, birth_date, death_date) VALUES (?, ?, ?, ?, ?)",
        ("I2", "", "", "1900", ""),
    )
    conn.commit()

    empty_person_id = conn.execute("SELECT id FROM people WHERE gedcom_id = ?", ("I1",)).fetchone()[0]

    service = IntegrityCheckService(repo, data_dir=tmp_path / "data")
    report = service.run_checks()

    empty_ids = {person_id for item in report["empty_people"] for person_id in item["person_ids"]}
    assert empty_person_id in empty_ids
    repo.close()


def test_integrity_no_false_error_for_partial_gedcom_dates(tmp_path):
    repo = _build_repo(tmp_path, "partial.db")
    repo.create_person({"gedcom_id": "I1", "first_name": "Approx", "last_name": "Date", "sex": "", "birth_date": "ABT 1900", "birth_place": "", "death_date": "AFT 1900", "death_place": "", "occupation": "", "note": ""})

    service = IntegrityCheckService(repo, data_dir=tmp_path / "data")
    report = service.run_checks()

    assert not any("невозможная" in item["message"] for item in report["date_problems"])
    assert not any("не удалось распознать" in item["message"] for item in report["date_problems"])
    repo.close()


def test_integrity_csv_export(tmp_path):
    repo = _build_repo(tmp_path, "csv.db")
    repo.create_person({"gedcom_id": "I1", "first_name": "Boris", "last_name": "Lesnik", "sex": "", "birth_date": "1900", "birth_place": "", "death_date": "", "death_place": "", "occupation": "", "note": ""})
    repo.create_person({"gedcom_id": "I2", "first_name": "Борис", "last_name": "Лесник", "sex": "", "birth_date": "1900", "birth_place": "", "death_date": "", "death_place": "", "occupation": "", "note": ""})

    service = IntegrityCheckService(repo, data_dir=tmp_path / "data")
    report = service.run_checks()
    output = service.export_report_csv(report, tmp_path / "report.csv")

    content = output.read_text(encoding="utf-8")
    assert "section,severity,message" in content
    assert "duplicates" in content
    repo.close()
