import sqlite3
from pathlib import Path

from repository.person_repository import PersonRepository
from repository.person_timeline_service import PersonTimelineService


def _build_repo(tmp_path, db_name="timeline.db"):
    db_path = tmp_path / db_name
    conn = sqlite3.connect(db_path)
    conn.executescript(Path("schema.sql").read_text(encoding="utf-8"))
    conn.commit()
    conn.close()
    return PersonRepository(str(db_path))


def _event_types(entries):
    return [entry["event_type"] for entry in entries]


def test_timeline_ordering(tmp_path):
    repo = _build_repo(tmp_path, "ordering.db")
    person_id = repo.create_person(
        {
            "gedcom_id": "I1",
            "first_name": "Anna",
            "last_name": "Smith",
            "birth_date": "1 JAN 1900",
            "death_date": "1 JAN 1980",
        }
    )

    repo.create_person_event({"person_id": person_id, "event_type": "residence", "date": "1930", "place": "Berlin", "description": "Moved"})
    repo.create_person_event({"person_id": person_id, "event_type": "marriage", "date": "1920", "place": "Paris", "description": "Married"})

    service = PersonTimelineService(repo)
    timeline = service.build_timeline(person_id)

    types = _event_types(timeline)
    assert types.index("birth") < types.index("marriage") < types.index("residence") < types.index("death")
    repo.close()


def test_timeline_approximate_gedcom_dates_sorting(tmp_path):
    repo = _build_repo(tmp_path, "approx.db")
    person_id = repo.create_person(
        {
            "gedcom_id": "I1",
            "first_name": "John",
            "last_name": "Doe",
            "birth_date": "ABT 1899",
        }
    )

    repo.create_person_event({"person_id": person_id, "event_type": "custom", "date": "BEF 1910", "place": "", "description": "Before event"})
    repo.create_person_event({"person_id": person_id, "event_type": "custom", "date": "BET 1910 AND 1912", "place": "", "description": "Between event"})
    repo.create_person_event({"person_id": person_id, "event_type": "custom", "date": "FROM 1913 TO 1915", "place": "", "description": "Range event"})
    repo.create_person_event({"person_id": person_id, "event_type": "custom", "date": "AFT 1915", "place": "", "description": "After event"})

    service = PersonTimelineService(repo)
    timeline = service.build_timeline(person_id)

    labels = [entry["description"] for entry in timeline if entry["event_type"] == "custom"]
    assert labels == ["Before event", "Between event", "Range event", "After event"]
    repo.close()


def test_timeline_unknown_dates_are_last(tmp_path):
    repo = _build_repo(tmp_path, "unknown.db")
    person_id = repo.create_person(
        {
            "gedcom_id": "I1",
            "first_name": "Unknown",
            "last_name": "Date",
            "birth_date": "1900",
        }
    )

    repo.create_person_event({"person_id": person_id, "event_type": "custom", "date": "1901", "place": "", "description": "Known"})
    repo.create_person_event({"person_id": person_id, "event_type": "custom", "date": "SOMETIME", "place": "", "description": "Unknown"})

    service = PersonTimelineService(repo)
    timeline = service.build_timeline(person_id)

    custom_descriptions = [entry["description"] for entry in timeline if entry["event_type"] == "custom"]
    assert custom_descriptions[-1] == "Unknown"
    repo.close()


def test_timeline_custom_events_and_source_detection(tmp_path):
    repo = _build_repo(tmp_path, "custom_source.db")
    person_id = repo.create_person({"gedcom_id": "I1", "first_name": "Custom", "last_name": "Events"})
    source_id = repo.create_person_source(
        {
            "person_id": person_id,
            "title": "Archive 42",
            "source_url": "",
            "archive_reference": "A-42",
            "note": "",
        }
    )

    event_id = repo.create_person_event(
        {
            "person_id": person_id,
            "event_type": "custom",
            "date": "1930",
            "place": "Riga",
            "description": f"Documented move SRC:{source_id}",
        }
    )

    service = PersonTimelineService(repo)
    timeline = service.build_timeline(person_id)

    custom_entries = [entry for entry in timeline if entry.get("event_id") == event_id]
    assert custom_entries
    assert custom_entries[0]["source_id"] == source_id
    assert custom_entries[0]["source_title"] == "Archive 42"
    repo.close()


def test_timeline_export_csv_and_pdf(tmp_path):
    repo = _build_repo(tmp_path, "export.db")
    person_id = repo.create_person(
        {
            "gedcom_id": "I1",
            "first_name": "Export",
            "last_name": "Test",
            "birth_date": "1900",
            "death_date": "1980",
        }
    )
    repo.create_person_event({"person_id": person_id, "event_type": "marriage", "date": "1920", "place": "Kyiv", "description": "Married"})

    service = PersonTimelineService(repo)
    timeline = service.build_timeline(person_id)

    csv_path = service.export_timeline_csv(timeline, tmp_path / "timeline.csv")
    pdf_path = service.export_timeline_pdf(timeline, tmp_path / "timeline.pdf")

    csv_content = csv_path.read_text(encoding="utf-8")
    pdf_bytes = pdf_path.read_bytes()

    assert "date,place,event_type,description,source,contradiction" in csv_content
    assert "Брак" in csv_content
    assert pdf_bytes.startswith(b"%PDF")
    repo.close()
