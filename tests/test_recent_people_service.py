import json
from pathlib import Path

from home_person_service import HomePersonService
from person_favorites_service import PersonFavoritesService
from recent_people_service import RecentPeopleService
from viewer import GenealogyViewer


def test_recent_people_are_most_recent_first_and_bounded(tmp_path):
    service = RecentPeopleService(
        tmp_path / "recent_people.json",
        database_scope="database-a",
        limit=3,
    )

    assert service.record(1) == (1,)
    assert service.record(2) == (2, 1)
    assert service.record(3) == (3, 2, 1)
    assert service.record(2) == (2, 3, 1)
    assert service.record(4) == (4, 2, 3)
    assert service.list_ids() == (4, 2, 3)


def test_recent_people_are_isolated_by_database(tmp_path):
    path = tmp_path / "recent_people.json"
    first = RecentPeopleService(path, database_scope="database-a")
    second = RecentPeopleService(path, database_scope="database-b")

    first.record(7)
    second.record(9)

    assert first.list_ids() == (7,)
    assert second.list_ids() == (9,)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["databases"] == {"database-a": [7], "database-b": [9]}


def test_recent_people_remove_one_entry_without_clearing_others(tmp_path):
    service = RecentPeopleService(
        tmp_path / "recent_people.json",
        database_scope="database-a",
    )
    for person_id in (1, 2, 3):
        service.record(person_id)

    assert service.list_ids() == (3, 2, 1)
    assert service.remove(2)
    assert service.list_ids() == (3, 1)
    assert not service.remove(2)
    assert service.list_ids() == (3, 1)


def test_recent_people_prune_clear_and_ignore_invalid_saved_ids(tmp_path):
    path = tmp_path / "recent_people.json"
    path.write_text(
        json.dumps({
            "version": 1,
            "databases": {"database-a": [4, "5", 4, -1, True, "bad"]},
        }),
        encoding="utf-8",
    )
    service = RecentPeopleService(path, database_scope="database-a")

    assert service.list_ids() == (4, 5)
    assert service.prune((5, 8)) == (5,)
    service.clear()
    assert service.list_ids() == ()


def test_viewer_registers_recent_people_navigation():
    source = Path("viewer.py").read_text(encoding="utf-8")

    assert "from recent_people_service import RecentPeopleService" in source
    assert 'label="Недавние люди"' in source
    assert "def open_recent_people(self):" in source
    assert "def _add_selected_recent_to_favorites(self):" in source
    assert "def _set_selected_recent_as_home(self):" in source
    assert "def _remove_selected_recent_person(self):" in source
    assert 'text="Удалить из недавних"' in source
    assert "command=self._set_selected_recent_as_home" in source
    assert 'listbox.bind("<Return>", lambda _event: self._open_selected_recent_person())' in source


class _UnselectedRecentListbox:
    def curselection(self):
        return ()


def test_selected_recent_person_falls_back_to_only_recent_person():
    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer._recent_people_listbox = _UnselectedRecentListbox()
    viewer._recent_person_ids = [42]

    assert viewer._selected_recent_person_id() == 42


class _FavoriteRecorder:
    def __init__(self):
        self.ids = []

    def add(self, person_id):
        if person_id in self.ids:
            return False
        self.ids.append(person_id)
        return True


class _StatusLabel:
    def __init__(self):
        self.text = ""

    def config(self, *, text):
        self.text = text


def test_recent_person_can_be_added_to_favorites_without_toggling():
    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer._recent_people_listbox = _UnselectedRecentListbox()
    viewer._recent_person_ids = [42]
    viewer.person_favorites_service = _FavoriteRecorder()
    viewer.status_label = _StatusLabel()
    viewer._favorites_listbox = None
    viewer._refresh_recent_people_window = lambda: None

    assert viewer._add_selected_recent_to_favorites()
    assert viewer.person_favorites_service.ids == [42]
    assert viewer.status_label.text == "Человек добавлен в избранное."

    assert not viewer._add_selected_recent_to_favorites()
    assert viewer.person_favorites_service.ids == [42]
    assert viewer.status_label.text == "Человек уже в избранном."


def test_recent_person_can_be_set_as_home_person():
    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer._recent_people_listbox = _UnselectedRecentListbox()
    viewer._recent_person_ids = [42]
    calls = []
    viewer._set_home_person = lambda person_id, **kwargs: calls.append(
        (person_id, kwargs)
    ) or person_id

    assert viewer._set_selected_recent_as_home() == 42
    assert calls == [(42, {"refresh_card": True})]


class _NavigationRepository:
    def get_person(self, person_id):
        people = {
            2: ("@I2@", "Коэн", "Анна", "F", "1950", "", "", ""),
            6: ("@I6@", "Леви", "Давид", "M", "1948", "", "2020", ""),
        }
        return people.get(person_id)


class _NavigationListbox:
    def __init__(self):
        self.rows = []

    def delete(self, _start, _end):
        self.rows.clear()

    def insert(self, _index, value):
        self.rows.append(value)


def test_recent_people_show_home_and_favorite_markers(tmp_path):
    recent = RecentPeopleService(
        tmp_path / "recent.json", database_scope="database-a"
    )
    recent.record(2)
    recent.record(6)
    favorites = PersonFavoritesService(
        tmp_path / "favorites.json", database_scope="database-a"
    )
    favorites.add(6)
    home = HomePersonService(tmp_path / "home.json", database_scope="database-a")
    home.set_id(2)

    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer.repository = _NavigationRepository()
    viewer.recent_people_service = recent
    viewer.person_favorites_service = favorites
    viewer.home_person_service = home
    viewer._recent_people_listbox = _NavigationListbox()
    viewer._recent_people_status_label = None

    viewer._refresh_recent_people_window()

    assert viewer._recent_person_ids == [6, 2]
    assert viewer._recent_people_listbox.rows == [
        "★ Леви Давид, 1948 — 2020 (ID 6)",
        "⌂ Коэн Анна, 1950 (ID 2)",
    ]
