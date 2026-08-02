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

    relationship_repo.create_person({"gedcom_id": "I1", "first_name": "Anna", "last_name": "Smith"})
    relationship_repo.create_person({"gedcom_id": "I2", "first_name": "Bob", "last_name": "Smith"})
    relationship_repo.create_person({"gedcom_id": "I3", "first_name": "Clara", "last_name": "Smith"})

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

    relationship_repo.create_person({"gedcom_id": "I1", "first_name": "Anna", "last_name": "Smith"})
    relationship_repo.create_person({"gedcom_id": "I2", "first_name": "Bob", "last_name": "Smith"})
    relationship_repo.create_person({"gedcom_id": "I3", "first_name": "Clara", "last_name": "Smith"})
    relationship_repo.create_person({"gedcom_id": "I4", "first_name": "Drew", "last_name": "Smith"})

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


def test_relationship_service_accepts_numeric_references_for_manual_people(relationship_repo):
    service = RelationshipService(relationship_repo)

    father_id = relationship_repo.create_person({"gedcom_id": "", "first_name": "Manual", "last_name": "Father"})
    mother_id = relationship_repo.create_person({"gedcom_id": "I2", "first_name": "Manual", "last_name": "Mother"})
    child_id = relationship_repo.create_person({"gedcom_id": "I3", "first_name": "Manual", "last_name": "Child"})

    family_id = service.create_family(
        husband_gedcom_id=str(father_id),
        wife_gedcom_id=str(mother_id),
        child_gedcom_ids=[str(child_id)],
    )

    family = relationship_repo.get_family(family_id)
    assert family["husband"] == str(father_id)
    assert family["wife"] == "I2"
    assert family["children"] == ["I3"]

    assert {row[2] for row in relationship_repo.get_children(str(father_id))} == {"I3"}
    assert {row[2] for row in relationship_repo.get_parents(str(child_id))} == {str(father_id), "I2"}


def test_relationship_service_links_existing_father(relationship_repo):
    service = RelationshipService(relationship_repo)

    relationship_repo.create_person({"gedcom_id": "I1", "first_name": "Ivan", "last_name": "Father"})
    child_id = relationship_repo.create_person({"gedcom_id": "I2", "first_name": "Petr", "last_name": "Child"})

    service.link_parent(str(child_id), "I1", "father")

    assert {row[2] for row in relationship_repo.get_biological_fathers(str(child_id))} == {"I1"}


def test_relationship_service_links_existing_mother(relationship_repo):
    service = RelationshipService(relationship_repo)

    relationship_repo.create_person({"gedcom_id": "I1", "first_name": "Maria", "last_name": "Mother"})
    child_id = relationship_repo.create_person({"gedcom_id": "I2", "first_name": "Petr", "last_name": "Child"})

    service.link_parent(str(child_id), "I1", "mother")

    assert {row[2] for row in relationship_repo.get_biological_mothers(str(child_id))} == {"I1"}


def test_relationship_service_links_existing_child_to_one_parent(relationship_repo):
    service = RelationshipService(relationship_repo)

    relationship_repo.create_person({"gedcom_id": "I1", "first_name": "Olga", "last_name": "Parent"})
    relationship_repo.create_person({"gedcom_id": "I2", "first_name": "Nina", "last_name": "Child"})

    family = service.link_child("I1", "I2")

    assert family["children"] == ["I2"]
    assert {row[2] for row in relationship_repo.get_children("I1")} == {"I2"}


def test_relationship_service_links_child_to_two_parents(relationship_repo):
    service = RelationshipService(relationship_repo)

    relationship_repo.create_person({"gedcom_id": "I1", "first_name": "Father", "last_name": "Parent"})
    relationship_repo.create_person({"gedcom_id": "I2", "first_name": "Mother", "last_name": "Parent"})
    relationship_repo.create_person({"gedcom_id": "I3", "first_name": "Child", "last_name": "Person"})

    service.link_child("I1", "I3", other_parent_reference="I2")

    assert {row[2] for row in relationship_repo.get_parents("I3")} == {"I1", "I2"}
    assert {row[2] for row in relationship_repo.get_children("I1")} == {"I3"}
    assert {row[2] for row in relationship_repo.get_children("I2")} == {"I3"}


def test_relationship_service_creates_and_links_new_child(relationship_repo):
    service = RelationshipService(relationship_repo)

    relationship_repo.create_person({"gedcom_id": "I1", "first_name": "Parent", "last_name": "One"})

    family = service.create_child_and_link("I1", {"first_name": "New", "last_name": "Child", "sex": "F"})

    assert len(family["children"]) == 1
    child_reference = family["children"][0]
    child_record = relationship_repo.get_person_record_by_reference(child_reference)
    assert child_record["first_name"] == "New"
    assert {row[2] for row in relationship_repo.get_children("I1")} == {child_reference}


def test_relationship_service_creates_former_spouse_and_civil_partner_links(relationship_repo):
    service = RelationshipService(relationship_repo)

    relationship_repo.create_person({"gedcom_id": "I1", "first_name": "Alex", "last_name": "Root"})
    relationship_repo.create_person({"gedcom_id": "I2", "first_name": "Bella", "last_name": "Former"})

    former_family = service.link_partner("I1", "I2", relationship_type="former_spouse")
    civil_family = service.create_partner_and_link("I1", {"first_name": "Cara", "last_name": "Civil"}, relationship_type="civil_partner")

    assert former_family["relationship_type"] == "former_spouse"
    assert civil_family["relationship_type"] == "civil_partner"
    state = service.get_relationship_editor_state("I1")
    assert {row["relationship_type"] for row in state["partners"]} == {"former_spouse", "civil_partner"}


def test_relationship_service_reuses_compatible_family_when_linking_child(relationship_repo):
    service = RelationshipService(relationship_repo)

    relationship_repo.create_person({"gedcom_id": "I1", "first_name": "Father", "last_name": "One"})
    relationship_repo.create_person({"gedcom_id": "I2", "first_name": "Mother", "last_name": "Two"})
    relationship_repo.create_person({"gedcom_id": "I3", "first_name": "Child", "last_name": "Three"})

    family_id = service.create_family("I1", "I2", relationship_type="marriage")
    family = service.link_child("I1", "I3", other_parent_reference="I2")

    assert family["id"] == family_id
    assert family["children"] == ["I3"]
    assert len(relationship_repo.list_families_raw()) == 1


def test_relationship_service_repairs_legacy_numeric_child_links_without_duplicates(relationship_repo):
    service = RelationshipService(relationship_repo)

    relationship_repo.create_person({"gedcom_id": "I1", "first_name": "Яков", "last_name": "Лесник"})
    relationship_repo.create_person({"gedcom_id": "I2", "first_name": "Михаил", "last_name": "Лесник"})
    relationship_repo.create_person({"gedcom_id": "I3", "first_name": "Маргарита", "last_name": "Трахтенберг"})
    relationship_repo.create_person({"gedcom_id": "I4", "first_name": "Антон", "last_name": "Ермаков"})
    relationship_repo.create_person({"gedcom_id": "I5", "first_name": "Никита", "last_name": "Ермаков"})

    relationship_repo.create_family({"gedcom_id": "F1", "husband": "I1", "wife": "", "children": [], "relationship_type": "unknown"})
    family_row_id = relationship_repo.conn.execute("SELECT id FROM families WHERE gedcom_id = ?", ("F1",)).fetchone()[0]
    relationship_repo.conn.execute("INSERT INTO family_children (family_id, child_id) VALUES (?, ?)", (str(family_row_id), "I2"))
    relationship_repo.conn.commit()

    family_before = relationship_repo.get_family(family_row_id)
    assert family_before["children"] == ["I2"]

    updated_family = service.link_parent("I2", "I3", "mother")
    assert updated_family["id"] == family_row_id
    assert updated_family["husband"] == "I1"
    assert updated_family["wife"] == "I3"
    assert updated_family["children"] == ["I2"]

    anton_family = service.link_child("I2", "I4")
    service.link_child("I2", "I5")

    assert len(relationship_repo.list_people_full()) == 5
    assert len(relationship_repo.list_families_raw()) == 2
    assert {row[2] for row in relationship_repo.get_children("I1")} == {"I2"}
    assert {row[2] for row in relationship_repo.get_parents("I2")} == {"I1", "I3"}
    assert {row[2] for row in relationship_repo.get_children("I2")} == {"I4", "I5"}
    assert {row[2] for row in relationship_repo.get_parents("I4")} == {"I2"}
    assert anton_family["children"] == ["I4"]


def test_relationship_service_rejects_duplicate_links(relationship_repo):
    service = RelationshipService(relationship_repo)

    relationship_repo.create_person({"gedcom_id": "I1", "first_name": "Father", "last_name": "One"})
    relationship_repo.create_person({"gedcom_id": "I2", "first_name": "Child", "last_name": "Two"})

    service.link_child("I1", "I2")

    with pytest.raises(ValueError):
        service.link_child("I1", "I2")


def test_relationship_service_rejects_self_links(relationship_repo):
    service = RelationshipService(relationship_repo)

    relationship_repo.create_person({"gedcom_id": "I1", "first_name": "Solo", "last_name": "Person"})

    with pytest.raises(ValueError):
        service.link_parent("I1", "I1", "father")
    with pytest.raises(ValueError):
        service.link_partner("I1", "I1")
    with pytest.raises(ValueError):
        service.link_child("I1", "I1")


def test_relationship_service_rejects_ancestry_cycles(relationship_repo):
    service = RelationshipService(relationship_repo)

    relationship_repo.create_person({"gedcom_id": "I1", "first_name": "Parent", "last_name": "One"})
    relationship_repo.create_person({"gedcom_id": "I2", "first_name": "Child", "last_name": "Two"})

    service.link_child("I1", "I2")

    with pytest.raises(ValueError):
        service.link_child("I2", "I1")


def test_relationship_service_removes_link_without_deleting_people(relationship_repo):
    service = RelationshipService(relationship_repo)

    relationship_repo.create_person({"gedcom_id": "I1", "first_name": "Parent", "last_name": "One"})
    relationship_repo.create_person({"gedcom_id": "I2", "first_name": "Child", "last_name": "Two"})

    family = service.link_child("I1", "I2")
    service.remove_child_link(family["id"], "I2")

    assert relationship_repo.get_person_by_gedcom_id("I1") is not None
    assert relationship_repo.get_person_by_gedcom_id("I2") is not None
    assert relationship_repo.get_children("I1") == []
