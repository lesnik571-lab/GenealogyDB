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


def test_viewer_registers_favorite_navigation_actions():
    source = Path("viewer.py").read_text(encoding="utf-8")

    assert "from person_favorites_service import PersonFavoritesService" in source
    assert 'label="Избранные люди"' in source
    assert 'label="Добавить/убрать выбранного"' in source
    assert "def open_favorites(self):" in source
    assert "def toggle_selected_favorite(self):" in source
    assert '"Добавить в избранное"' in source
    assert '"Убрать из избранного"' in source


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
