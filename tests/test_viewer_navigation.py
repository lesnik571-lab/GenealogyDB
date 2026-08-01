from pathlib import Path
import sqlite3
import tkinter as tk
from tkinter import ttk

from viewer import GenealogyViewer


def test_insert_people_creates_clickable_relationship_tags(tmp_path):
    db_path = tmp_path / "viewer.db"
    conn = sqlite3.connect(db_path)
    schema_sql = Path("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.execute("INSERT INTO people (gedcom_id, first_name, last_name) VALUES (?, ?, ?)", ("I1", "John", "Doe"))
    conn.commit()
    conn.close()

    root = object()
    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer.repository = type("Repo", (), {"get_person_by_gedcom_id": lambda self, gedcom_id: (1,)})()
    viewer.show_person = lambda person_id: person_id
    viewer.format_name = staticmethod(lambda last_name, first_name: f"{last_name or ''} {first_name or ''}".strip())

    import tkinter as tk
    text = tk.Text()
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
    viewer.root = tk.Tk()
    viewer.root.withdraw()
    viewer._create_widgets = lambda: None
    viewer.search_entry = tk.Entry()
    viewer.search_entry.insert(0, "")
    viewer.status_label = tk.Label()
    viewer.tree = ttk.Treeview(columns=("id", "name", "birth", "death"), show="headings")
    viewer.tree.heading("id", text="ID")
    viewer.tree.heading("name", text="Имя")
    viewer.tree.heading("birth", text="Рождение")
    viewer.tree.heading("death", text="Смерть")
    viewer._clear_tree = lambda: [viewer.tree.delete(item) for item in viewer.tree.get_children()]

    viewer.search_people()

    row = viewer.tree.item(viewer.tree.get_children()[0])["values"]
    assert row == [7, "Jane Smith", 1980, 2020]

    viewer.root.destroy()
