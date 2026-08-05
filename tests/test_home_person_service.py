import json
from pathlib import Path

from home_person_service import HomePersonService
from viewer import GenealogyViewer


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
    assert 'label="Снять главного человека"' in source
    assert "def open_home_person(self):" in source
    assert "def set_selected_home_person(self):" in source
    assert "def clear_home_person(self, *, refresh_card=False):" in source
    assert "is_home_person = self._home_service().get_id() == person_id" in source
    assert "self.clear_home_person(refresh_card=True)" in source


class _HomeRecorder:
    def __init__(self):
        self.person_id = None

    def get_id(self):
        return self.person_id

    def set_id(self, person_id):
        self.person_id = person_id

    def clear(self):
        if self.person_id is None:
            return False
        self.person_id = None
        return True


class _StatusLabel:
    def __init__(self):
        self.text = ""

    def config(self, *, text):
        self.text = text


def test_open_card_person_can_be_set_as_exact_home_person():
    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer.home_person_service = _HomeRecorder()
    viewer.status_label = _StatusLabel()

    assert viewer._set_home_person(42) == 42
    assert viewer.home_person_service.person_id == 42
    assert viewer.status_label.text == "Главный человек сохранён."


def test_home_person_can_be_cleared_without_changing_genealogy_data():
    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer.home_person_service = _HomeRecorder()
    viewer.home_person_service.set_id(42)
    viewer.status_label = _StatusLabel()
    viewer.tree = None

    assert viewer.clear_home_person()
    assert viewer.home_person_service.person_id is None
    assert viewer.status_label.text == "Главный человек снят."

    assert not viewer.clear_home_person()
    assert viewer.status_label.text == "Главный человек не выбран."
