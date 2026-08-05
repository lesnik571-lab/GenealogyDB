import json
from pathlib import Path

from person_favorites_service import PersonFavoritesService
from viewer import GenealogyViewer


def test_favorites_add_remove_and_persist_in_order(tmp_path):
    path = tmp_path / "person_favorites.json"
    service = PersonFavoritesService(path)

    assert service.list_ids() == ()
    assert service.add(7)
    assert service.add(3)
    assert not service.add(7)
    assert service.list_ids() == (7, 3)
    assert PersonFavoritesService(path).list_ids() == (7, 3)
    assert service.remove(7)
    assert not service.remove(7)
    assert service.list_ids() == (3,)
    assert not path.with_suffix(".json.tmp").exists()


def test_favorites_toggle_returns_new_state(tmp_path):
    service = PersonFavoritesService(tmp_path / "person_favorites.json")

    assert service.toggle(11)
    assert service.contains(11)
    assert not service.toggle(11)
    assert not service.contains(11)


def test_favorites_normalize_legacy_values_and_ignore_invalid_content(tmp_path):
    path = tmp_path / "person_favorites.json"
    path.write_text(
        json.dumps({"person_ids": [3, "4", 3, -1, True, "invalid"]}),
        encoding="utf-8",
    )

    assert PersonFavoritesService(path).list_ids() == (3, 4)

    path.write_text("{broken", encoding="utf-8")
    assert PersonFavoritesService(path).list_ids() == ()


def test_favorites_prune_missing_people(tmp_path):
    service = PersonFavoritesService(tmp_path / "person_favorites.json")
    for person_id in (2, 4, 6):
        service.add(person_id)

    assert service.prune((2, 6, 8)) == (2, 6)
    assert PersonFavoritesService(service.path).list_ids() == (2, 6)


def test_favorites_are_isolated_by_database_and_migrate_legacy_file(tmp_path):
    path = tmp_path / "person_favorites.json"
    path.write_text(
        json.dumps({"version": 1, "person_ids": [7, 9]}),
        encoding="utf-8",
    )

    first_database = PersonFavoritesService(path, database_scope="database-a")
    assert first_database.list_ids() == (7, 9)

    migrated = json.loads(path.read_text(encoding="utf-8"))
    assert migrated == {
        "version": 2,
        "databases": {"database-a": [7, 9]},
    }

    second_database = PersonFavoritesService(path, database_scope="database-b")
    assert second_database.list_ids() == ()
    assert second_database.add(4)
    assert first_database.list_ids() == (7, 9)
    assert second_database.list_ids() == (4,)


def test_viewer_registers_favorite_navigation_actions():
    source = Path("viewer.py").read_text(encoding="utf-8")

    assert "from person_favorites_service import PersonFavoritesService" in source
    assert 'label="Избранные люди"' in source
    assert 'label="Добавить/убрать выбранного"' in source
    assert "def open_favorites(self):" in source
    assert "def toggle_selected_favorite(self):" in source
    assert '"Добавить в избранное"' in source
    assert '"Убрать из избранного"' in source
    assert "database_scope=str(self.repository.db_name)" in source
    assert 'listbox.bind("<Return>", lambda _event: self._open_selected_favorite())' in source


class _SingleResultTree:
    def selection(self):
        return ()

    def focus(self):
        return ""

    def get_children(self, _parent=""):
        return ("person-42",)

    def item(self, item_id):
        assert item_id == "person-42"
        return {"values": (42, "Мошэ", "", "")}


def test_selected_person_falls_back_to_only_visible_result():
    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer.tree = _SingleResultTree()

    assert viewer._selected_person_id() == 42


class _UnselectedFavoritesListbox:
    def curselection(self):
        return ()


def test_selected_favorite_falls_back_to_only_favorite():
    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer._favorites_listbox = _UnselectedFavoritesListbox()
    viewer._favorite_person_ids = [42]

    assert viewer._selected_favorite_person_id() == 42


class _FavoritesRepository:
    def get_person(self, person_id):
        people = {
            2: ("@I2@", "Коэн", "Анна", "F", "1950", "", "", ""),
            6: ("@I6@", "Леви", "Давид", "M", "1948", "", "2020", ""),
        }
        return people.get(person_id)


class _FavoritesListbox:
    def __init__(self):
        self.rows = []

    def delete(self, _start, _end):
        self.rows.clear()

    def insert(self, _index, value):
        self.rows.append(value)


class _FavoritesStatus:
    def __init__(self):
        self.text = ""

    def config(self, *, text):
        self.text = text


def test_refresh_favorites_prunes_people_missing_from_database(tmp_path):
    service = PersonFavoritesService(tmp_path / "person_favorites.json")
    for person_id in (2, 4, 6):
        service.add(person_id)

    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer.repository = _FavoritesRepository()
    viewer.person_favorites_service = service
    viewer._favorites_listbox = _FavoritesListbox()
    viewer._favorites_status_label = _FavoritesStatus()

    viewer._refresh_favorites_window()

    assert service.list_ids() == (2, 6)
    assert viewer._favorite_person_ids == [2, 6]
    assert viewer._favorites_listbox.rows == [
        "Коэн Анна, 1950 (ID 2)",
        "Леви Давид, 1948 — 2020 (ID 6)",
    ]
    assert viewer._favorites_status_label.text == "Избранных людей: 2"
