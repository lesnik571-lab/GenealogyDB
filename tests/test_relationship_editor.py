from pathlib import Path
import sqlite3

import pytest

from repository.person_repository import PersonRepository
from repository.relationship_service import RelationshipService


@pytest.fixture
def relationship_repo(tmp_path):
    db_path = tmp_path / "relationships.db"
    conn = sqlite3.connect(db_path)
    schema_sql = Path("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.close()

    repo = PersonRepository(str(db_path))
    try:
        yield repo
    finally:
        repo.close()


def test_relationship_service_creates_and_validates_family(relationship_repo):
    service = RelationshipService(relationship_repo)

    person_a = relationship_repo.create_person({"gedcom_id": "I1", "first_name": "Anna", "last_name": "Smith"})
    person_b = relationship_repo.create_person({"gedcom_id": "I2", "first_name": "Bob", "last_name": "Smith"})
    person_c = relationship_repo.create_person({"gedcom_id": "I3", "first_name": "Clara", "last_name": "Smith"})

    family_id = service.create_family(husband_gedcom_id="I1", wife_gedcom_id="I2", child_gedcom_ids=["I3"])

    family = relationship_repo.get_family(family_id)
    assert family["husband"] == "I1"
    assert family["wife"] == "I2"
    assert family["children"] == ["I3"]

    with pytest.raises(ValueError):
        service.create_family(husband_gedcom_id="I1", wife_gedcom_id="I1", child_gedcom_ids=["I3"])

    with pytest.raises(ValueError):
        service.create_family(husband_gedcom_id="I1", wife_gedcom_id="I2", child_gedcom_ids=["I1"])


def test_relationship_service_updates_and_deletes_family(relationship_repo):
    service = RelationshipService(relationship_repo)

    person_a = relationship_repo.create_person({"gedcom_id": "I1", "first_name": "Anna", "last_name": "Smith"})
    person_b = relationship_repo.create_person({"gedcom_id": "I2", "first_name": "Bob", "last_name": "Smith"})
    person_c = relationship_repo.create_person({"gedcom_id": "I3", "first_name": "Clara", "last_name": "Smith"})
    person_d = relationship_repo.create_person({"gedcom_id": "I4", "first_name": "Drew", "last_name": "Smith"})

    family_id = service.create_family(husband_gedcom_id="I1", wife_gedcom_id="I2", child_gedcom_ids=["I3"])

    updated = service.update_family(family_id, husband_gedcom_id="I1", wife_gedcom_id="I2", child_gedcom_ids=["I4"])
    assert updated is True

    family = relationship_repo.get_family(family_id)
    assert family["children"] == ["I4"]

    deleted = service.delete_family(family_id)
    assert deleted is True
    assert relationship_repo.get_family(family_id) is None


def test_relationship_service_rejects_circular_links(relationship_repo):
    service = RelationshipService(relationship_repo)

    relationship_repo.create_person({"gedcom_id": "I1", "first_name": "Anna", "last_name": "Smith"})
    relationship_repo.create_person({"gedcom_id": "I2", "first_name": "Bob", "last_name": "Smith"})

    service.create_family(husband_gedcom_id="", wife_gedcom_id="", child_gedcom_ids=["I1"])

    with pytest.raises(ValueError):
        service.create_family(husband_gedcom_id="I2", wife_gedcom_id="", child_gedcom_ids=["I1"])
