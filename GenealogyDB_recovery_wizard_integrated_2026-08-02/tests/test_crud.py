import sqlite3
from pathlib import Path

from repository import PersonRepository


def test_person_repository_crud_operations(tmp_path):
    db_path = tmp_path / "crud.db"
    conn = sqlite3.connect(db_path)
    schema_sql = Path("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.commit()
    conn.close()

    repo = PersonRepository(str(db_path))
    person_id = repo.create_person({
        "gedcom_id": "I1",
        "first_name": "John",
        "last_name": "Doe",
        "sex": "M",
        "birth_date": "1 JAN 1900",
        "birth_place": "",
        "death_date": "",
        "death_place": "",
        "occupation": "",
        "note": "",
    })

    updated = repo.update_person(person_id, {"first_name": "Johnny", "last_name": "Doe"})
    assert updated is True
    stored = repo.get_person(person_id)
    assert stored[2] == "Johnny"

    deleted = repo.delete_person(person_id)
    assert deleted is True
    assert repo.get_person(person_id) is None
    repo.close()


def test_family_repository_crud_operations(tmp_path):
    db_path = tmp_path / "family_crud.db"
    conn = sqlite3.connect(db_path)
    schema_sql = Path("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.commit()
    conn.close()

    repo = PersonRepository(str(db_path))
    family_id = repo.create_family({"gedcom_id": "F1", "husband": "I1", "wife": "I2", "children": []})
    family = repo.get_family(family_id)
    assert family["gedcom_id"] == "F1"

    updated = repo.update_family(family_id, {"husband": "I3", "wife": "I4"})
    assert updated is True
    updated_family = repo.get_family(family_id)
    assert updated_family["husband"] == "I3"

    deleted = repo.delete_family(family_id)
    assert deleted is True
    assert repo.get_family(family_id) is None
    repo.close()
