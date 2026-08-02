from pathlib import Path
import sqlite3

import pytest

from repository.person_repository import PersonRepository
from repository.person_event_service import PersonEventService


@pytest.fixture
def person_event_repo(tmp_path):
    db_path = tmp_path / "person_events.db"
    conn = sqlite3.connect(db_path)
    schema_sql = Path("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.close()

    repo = PersonRepository(str(db_path))
    try:
        yield repo
    finally:
        repo.close()


def test_person_event_service_creates_updates_deletes_and_lists_events(person_event_repo):
    service = PersonEventService(person_event_repo)
    person_id = person_event_repo.create_person({
        "gedcom_id": "I1",
        "first_name": "Anna",
        "last_name": "Smith",
    })

    event_id = service.create_event(
        person_id,
        event_type="birth",
        date="1901",
        place="London",
        description="Born in London",
    )

    events = service.list_events(person_id)
    assert len(events) == 1
    assert events[0]["event_type"] == "birth"
    assert events[0]["date"] == "1901"

    updated = service.update_event(
        event_id,
        event_type="custom",
        date="1902",
        place="Paris",
        description="Moved to Paris",
    )
    assert updated is True

    stored = person_event_repo.get_person_event(event_id)
    assert stored["event_type"] == "custom"
    assert stored["place"] == "Paris"

    deleted = service.delete_event(event_id)
    assert deleted is True
    assert person_event_repo.list_person_events(person_id) == []
