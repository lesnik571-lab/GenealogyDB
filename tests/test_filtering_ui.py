from pathlib import Path
import sqlite3

from viewer import GenealogyViewer


def test_viewer_search_uses_filters(tmp_path):
    db_path = tmp_path / "viewer_filters.db"
    conn = sqlite3.connect(db_path)
    schema_sql = Path("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.execute("INSERT INTO people (gedcom_id, first_name, last_name, sex, birth_date, death_date) VALUES (?, ?, ?, ?, ?, ?)", ("I1", "John", "Doe", "M", "1 JAN 1900", "1 JAN 1950"))
    conn.execute("INSERT INTO people (gedcom_id, first_name, last_name, sex, birth_date, death_date) VALUES (?, ?, ?, ?, ?, ?)", ("I2", "Jane", "Smith", "F", "2 JAN 1910", "2 JAN 1960"))
    conn.commit()
    conn.close()

    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer.repository = type("Repo", (), {
        "list_people": lambda self, first_name=None, last_name=None, birth_year=None, death_year=None, sex=None: [
            (1, "Doe", "John", "1 JAN 1900", "1 JAN 1950")
        ],
        "get_person_by_gedcom_id": lambda self, gedcom_id: (1,),
        "get_parents": lambda self, gedcom_id: [],
        "get_spouses": lambda self, gedcom_id: [],
        "get_children": lambda self, gedcom_id: [],
        "get_siblings": lambda self, gedcom_id: [],
        "get_person": lambda self, person_id: ("I1", "Doe", "John", "M", "", "", "", "", "", ""),
        "close": lambda self: None,
    })()
    viewer._clear_tree = lambda: None
    viewer._insert_person_row = lambda person_id, last_name, first_name, birth_date, death_date: None
    viewer._refresh_family_tree = lambda: None
    viewer.view_mode = type("ViewMode", (), {"get": lambda self, *args, **kwargs: "list"})()
    viewer.tree = type("Tree", (), {"get_children": lambda self: []})()
    viewer.first_name_entry = type("Entry", (), {"get": lambda self: "John"})()
    viewer.last_name_entry = type("Entry", (), {"get": lambda self: ""})()
    viewer.birth_year_entry = type("Entry", (), {"get": lambda self: ""})()
    viewer.death_year_entry = type("Entry", (), {"get": lambda self: ""})()
    viewer.sex_var = type("Var", (), {"get": lambda self: ""})()

    rows = viewer.repository.list_people(first_name="John")
    assert len(rows) == 1
