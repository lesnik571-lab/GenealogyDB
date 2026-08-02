import sqlite3
from pathlib import Path

import pytest

from relationship_placeholder_repair_service import RelationshipPlaceholderRepairService
from repository.person_repository import PersonRepository


def _build_repo(tmp_path, db_name="placeholder_repair.db"):
    db_path = tmp_path / db_name
    conn = sqlite3.connect(db_path)
    conn.executescript(Path("schema.sql").read_text(encoding="utf-8"))
    conn.commit()
    conn.close()
    return PersonRepository(str(db_path))


def _insert_placeholder(repo, gedcom_id, sex="", birth_date="", death_date=""):
    repo.cur.execute(
        """
        INSERT INTO people (gedcom_id, first_name, last_name, sex, birth_date, death_date)
        VALUES (?, '', '', ?, ?, ?)
        """,
        (gedcom_id, sex, birth_date, death_date),
    )
    repo.conn.commit()


def test_replaces_placeholder_spouse_when_high_confidence(tmp_path):
    repo = _build_repo(tmp_path, "spouse_replace.db")
    service = RelationshipPlaceholderRepairService(repo)

    repo.create_person({"gedcom_id": "IY", "first_name": "Яков", "last_name": "Лесник", "sex": "M"})
    repo.create_person({"gedcom_id": "IC", "first_name": "Михаил", "last_name": "Лесник", "sex": "M"})
    repo.create_person({"gedcom_id": "IR", "first_name": "Рахиль", "last_name": "Лесник", "sex": "F"})
    _insert_placeholder(repo, "IP", sex="F")

    repo.create_family({"gedcom_id": "F1", "husband": "IY", "wife": "IP", "children": ["IC"]})
    repo.create_family({"gedcom_id": "F2", "husband": "IY", "wife": "IR", "children": ["IC"]})

    plan = service.build_repair_plan()
    assert plan["changes"]
    assert any(change["table"] == "families" and change["column"] == "wife_id" for change in plan["changes"])

    before_people_count = len(repo.list_people_full())
    result = service.apply_repair_plan(plan)
    assert result["applied_changes"] >= 1

    family = repo.find_family("IY", "IR", include_children=True)
    assert family is not None
    assert "IC" in family["children"]
    assert len(repo.list_people_full()) == before_people_count
    repo.close()


def test_replaces_placeholder_child_when_high_confidence(tmp_path):
    repo = _build_repo(tmp_path, "child_replace.db")
    service = RelationshipPlaceholderRepairService(repo)

    repo.create_person({"gedcom_id": "IY", "first_name": "Яков", "last_name": "Лесник", "sex": "M"})
    repo.create_person({"gedcom_id": "IM", "first_name": "Мария", "last_name": "Лесник", "sex": "F"})
    repo.create_person({"gedcom_id": "IMH", "first_name": "Михаил", "last_name": "Лесник", "sex": "M", "birth_date": "1930"})
    _insert_placeholder(repo, "IPCH", sex="M", birth_date="1930")

    repo.create_family({"gedcom_id": "F1", "husband": "IY", "wife": "IM", "children": ["IPCH"]})
    repo.create_family({"gedcom_id": "F2", "husband": "IY", "wife": "IM", "children": ["IMH"]})

    plan = service.build_repair_plan()
    child_updates = [change for change in plan["changes"] if change["table"] == "family_children"]
    assert child_updates
    assert any(change["old_value"] == "IPCH" and change["new_value"] == "IMH" for change in child_updates)

    service.apply_repair_plan(plan)
    family = repo.find_family("IY", "IM", include_children=True)
    assert family is not None
    assert "IMH" in family["children"]
    assert "IPCH" not in family["children"]
    repo.close()


def test_does_not_match_by_surname_only(tmp_path):
    repo = _build_repo(tmp_path, "surname_only.db")
    service = RelationshipPlaceholderRepairService(repo)

    repo.create_person({"gedcom_id": "IY", "first_name": "Яков", "last_name": "Лесник", "sex": "M"})
    repo.create_person({"gedcom_id": "INAMED", "first_name": "Иван", "last_name": "Лесник", "sex": "M"})
    _insert_placeholder(repo, "IP", sex="M")
    repo.create_family({"gedcom_id": "F1", "husband": "IP", "wife": "", "children": ["IY"]})

    plan = service.build_repair_plan()
    assert not plan["changes"]
    assert any(item["placeholder_reference"] == "IP" for item in plan["unresolved"])
    repo.close()


def test_uncertain_case_remains_unchanged(tmp_path):
    repo = _build_repo(tmp_path, "uncertain.db")
    service = RelationshipPlaceholderRepairService(repo)

    repo.create_person({"gedcom_id": "IY", "first_name": "Яков", "last_name": "Лесник", "sex": "M"})
    repo.create_person({"gedcom_id": "IC", "first_name": "Михаил", "last_name": "Лесник", "sex": "M"})
    _insert_placeholder(repo, "IP", sex="F")
    repo.create_person({"gedcom_id": "IA", "first_name": "Анна", "last_name": "Иванова", "sex": "F"})
    repo.create_person({"gedcom_id": "IB", "first_name": "Алла", "last_name": "Петрова", "sex": "F"})

    repo.create_family({"gedcom_id": "F1", "husband": "IY", "wife": "IP", "children": ["IC"]})
    repo.create_family({"gedcom_id": "F2", "husband": "IY", "wife": "IA", "children": ["IC"]})
    repo.create_family({"gedcom_id": "F3", "husband": "IY", "wife": "IB", "children": ["IC"]})

    plan = service.build_repair_plan()
    assert not plan["changes"]
    unresolved = [item for item in plan["unresolved"] if item["placeholder_reference"] == "IP"]
    assert unresolved
    assert "ambiguous" in unresolved[0]["reason"] or "high-confidence" in unresolved[0]["reason"]
    repo.close()


def test_transaction_rolls_back_on_failure(tmp_path):
    repo = _build_repo(tmp_path, "rollback.db")
    service = RelationshipPlaceholderRepairService(repo)

    repo.create_person({"gedcom_id": "IY", "first_name": "Яков", "last_name": "Лесник", "sex": "M"})
    repo.create_person({"gedcom_id": "IC", "first_name": "Михаил", "last_name": "Лесник", "sex": "M"})
    repo.create_person({"gedcom_id": "IR", "first_name": "Рахиль", "last_name": "Лесник", "sex": "F"})
    _insert_placeholder(repo, "IP", sex="F")
    repo.create_family({"gedcom_id": "F1", "husband": "IY", "wife": "IP", "children": ["IC"]})
    repo.create_family({"gedcom_id": "F2", "husband": "IY", "wife": "IR", "children": ["IC"]})

    plan = service.build_repair_plan()
    assert plan["changes"]

    with pytest.raises(RuntimeError):
        service.apply_repair_plan(plan, fail_after_changes=1)

    # Verify rollback: placeholder reference is still present.
    family = repo.find_family("IY", "IP", include_children=True)
    assert family is not None
    repo.close()


def test_no_duplicate_people_created_during_repair(tmp_path):
    repo = _build_repo(tmp_path, "no_duplicate_people.db")
    service = RelationshipPlaceholderRepairService(repo)

    repo.create_person({"gedcom_id": "IY", "first_name": "Яков", "last_name": "Лесник", "sex": "M"})
    repo.create_person({"gedcom_id": "IC", "first_name": "Михаил", "last_name": "Лесник", "sex": "M"})
    repo.create_person({"gedcom_id": "IR", "first_name": "Рахиль", "last_name": "Лесник", "sex": "F"})
    _insert_placeholder(repo, "IP", sex="F")
    repo.create_family({"gedcom_id": "F1", "husband": "IY", "wife": "IP", "children": ["IC"]})
    repo.create_family({"gedcom_id": "F2", "husband": "IY", "wife": "IR", "children": ["IC"]})

    before_count = len(repo.list_people_full())
    plan = service.build_repair_plan()
    service.apply_repair_plan(plan)
    after_count = len(repo.list_people_full())

    assert before_count == after_count
    repo.close()