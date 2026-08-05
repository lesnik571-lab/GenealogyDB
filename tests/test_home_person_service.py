import json
from pathlib import Path

from home_person_service import HomePersonService


def test_home_person_set_reload_and_clear(tmp_path):
    path = tmp_path / "home_person.json"
    service = HomePersonService(path, database_scope="database-a")

    assert service.get_id() is None
    assert service.set_id(42) == 42
    assert HomePersonService(path, database_scope="database-a").get_id() == 42
    assert service.clear()
    assert service.get_id() is None
    assert not service.clear()


def test_home_person_is_isolated_by_database(tmp_path):
    path = tmp_path / "home_person.json"
    first = HomePersonService(path, database_scope="database-a")
    second = HomePersonService(path, database_scope="database-b")

    first.set_id(7)
    second.set_id(9)

    assert first.get_id() == 7
    assert second.get_id() == 9
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["databases"] == {"database-a": 7, "database-b": 9}


def test_home_person_ignores_invalid_saved_value(tmp_path):
    path = tmp_path / "home_person.json"
    path.write_text(
        json.dumps({"version": 1, "databases": {"database-a": True}}),
        encoding="utf-8",
    )

    assert HomePersonService(path, database_scope="database-a").get_id() is None


def test_viewer_registers_home_person_navigation():
    source = Path("viewer.py").read_text(encoding="utf-8")

    assert "from home_person_service import HomePersonService" in source
    assert 'label="Открыть главного человека"' in source
    assert 'label="Сделать выбранного главным"' in source
    assert "def open_home_person(self):" in source
    assert "def set_selected_home_person(self):" in source
