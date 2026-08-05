from pathlib import Path
import sqlite3

from viewer import GenealogyViewer


def test_insert_people_creates_clickable_relationship_tags(tmp_path):
    db_path = tmp_path / "viewer.db"
    conn = sqlite3.connect(db_path)
    schema_sql = Path("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.execute("INSERT INTO people (gedcom_id, first_name, last_name) VALUES (?, ?, ?)", ("I1", "John", "Doe"))
    conn.commit()
    conn.close()

    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer.repository = type("Repo", (), {"get_person_by_gedcom_id": lambda self, gedcom_id: (1,)})()
    viewer.show_person = lambda person_id: person_id
    viewer.format_name = staticmethod(lambda last_name, first_name: f"{last_name or ''} {first_name or ''}".strip())

    class FakeText:
        def __init__(self):
            self._text = ""

        def insert(self, _index, value):
            self._text += value

        def get(self, *_args):
            return self._text

        def index(self, _index):
            return "1.0"

        def tag_add(self, *_args, **_kwargs):
            return None

        def tag_configure(self, *_args, **_kwargs):
            return None

        def tag_bind(self, *_args, **_kwargs):
            return None

    text = FakeText()
    viewer.insert_people(text, [("Doe", "John", "I1")], "нет")

    assert "Doe John [I1]" in text.get("1.0", "end")


def test_query_people_falls_back_to_simple_repository_list_method():
    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer.repository = type("Repo", (), {
        "list_people": lambda self: [(1, "Doe", "John", "", "")],
    })()

    rows = viewer._query_people("")

    assert rows == [(1, "Doe", "John", "", "")]


def test_query_people_passes_surname_search_to_repository():
    viewer = GenealogyViewer.__new__(GenealogyViewer)
    captured = {}

    def fake_list_people(self, surname=None, first_name=None, last_name=None, birth_year=None, death_year=None, sex=None, limit=500):
        captured["args"] = (surname, first_name, last_name)
        return [(2, "Smith", "Jane", "1980", "")]

    viewer.repository = type("Repo", (), {"list_people": fake_list_people})()

    rows = viewer._query_people("Smith")

    assert rows == [(2, "Smith", "Jane", "1980", "")]
    assert captured["args"] == ("Smith", "Smith", "Smith")


def test_query_people_passes_first_name_search_to_repository():
    viewer = GenealogyViewer.__new__(GenealogyViewer)
    captured = {}

    def fake_list_people(self, surname=None, first_name=None, last_name=None, birth_year=None, death_year=None, sex=None, limit=500):
        captured["args"] = (surname, first_name, last_name)
        return [(3, "Doe", "John", "1900", "1950")]

    viewer.repository = type("Repo", (), {"list_people": fake_list_people})()

    rows = viewer._query_people("John")

    assert rows == [(3, "Doe", "John", "1900", "1950")]
    assert captured["args"] == ("John", "John", "John")


def test_search_people_populates_all_visible_columns():
    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer.repository = type("Repo", (), {
        "list_people": lambda self, surname=None, first_name=None, last_name=None, birth_year=None, death_year=None, sex=None, limit=500: [(7, "Smith", "Jane", "1980", "2020")],
    })()

    class FakeRoot:
        def update_idletasks(self):
            return None

    class FakeEntry:
        def get(self):
            return ""

    class FakeLabel:
        def config(self, *args, **kwargs):
            return None

    class FakeTree:
        def __init__(self):
            self.rows = {}
            self.order = []

        def insert(self, _parent, _index, values=None, **_kwargs):
            item_id = str(len(self.order) + 1)
            self.rows[item_id] = {"values": list(values or [])}
            self.order.append(item_id)
            return item_id

        def get_children(self):
            return list(self.order)

        def item(self, item_id):
            return self.rows[item_id]

        def delete(self, item_id):
            self.rows.pop(item_id, None)
            self.order = [item for item in self.order if item != item_id]

    viewer.root = FakeRoot()
    viewer.search_entry = FakeEntry()
    viewer.status_label = FakeLabel()
    viewer.tree = FakeTree()
    viewer._clear_tree = lambda: [viewer.tree.delete(item) for item in viewer.tree.get_children()]

    viewer.search_people()

    row = viewer.tree.item(viewer.tree.get_children()[0])["values"]
    assert row == [7, "Jane Smith", "1980", "2020"]


def test_search_people_marks_home_and_favorite_people():
    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer.repository = type("Repo", (), {
        "list_people": lambda self, surname=None, first_name=None, last_name=None, birth_year=None, death_year=None, sex=None, limit=500: [(7, "Smith", "Jane", "1980", "2020")],
    })()
    viewer.person_favorites_service = type("Favorites", (), {
        "list_ids": lambda self: (7,),
    })()
    viewer.home_person_service = type("Home", (), {
        "get_id": lambda self: 7,
    })()

    class FakeRoot:
        def update_idletasks(self):
            return None

    class FakeEntry:
        def get(self):
            return ""

    class FakeLabel:
        def config(self, *args, **kwargs):
            return None

    class FakeTree:
        def __init__(self):
            self.rows = {}
            self.order = []

        def insert(self, _parent, _index, values=None, **_kwargs):
            item_id = str(len(self.order) + 1)
            self.rows[item_id] = {"values": list(values or [])}
            self.order.append(item_id)
            return item_id

        def get_children(self):
            return list(self.order)

        def item(self, item_id):
            return self.rows[item_id]

        def delete(self, item_id):
            self.rows.pop(item_id, None)
            self.order = [item for item in self.order if item != item_id]

    viewer.root = FakeRoot()
    viewer.search_entry = FakeEntry()
    viewer.status_label = FakeLabel()
    viewer.tree = FakeTree()
    viewer._clear_tree = lambda: [viewer.tree.delete(item) for item in viewer.tree.get_children()]

    viewer.search_people()

    row = viewer.tree.item(viewer.tree.get_children()[0])["values"]
    assert row == [7, "⌂ ★ Jane Smith", "1980", "2020"]
