import sqlite3
from pathlib import Path

from viewer import GenealogyViewer


def test_family_tree_builder_creates_canvas_nodes(tmp_path):
    db_path = tmp_path / "tree.db"
    conn = sqlite3.connect(db_path)
    schema_sql = Path("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.execute("INSERT INTO people (gedcom_id, first_name, last_name) VALUES (?, ?, ?)", ("I1", "John", "Doe"))
    conn.execute("INSERT INTO people (gedcom_id, first_name, last_name) VALUES (?, ?, ?)", ("I2", "Jane", "Doe"))
    conn.execute("INSERT INTO people (gedcom_id, first_name, last_name) VALUES (?, ?, ?)", ("I3", "Alice", "Doe"))
    conn.execute("INSERT INTO families (gedcom_id, husband_id, wife_id) VALUES (?, ?, ?)", ("F1", "I1", "I2"))
    conn.execute("INSERT INTO family_children (family_id, child_id) VALUES (?, ?)", ("F1", "I3"))
    conn.commit()
    conn.close()

    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer.repository = type("Repo", (), {
        "get_person_by_gedcom_id": lambda self, gedcom_id: (1,),
        "get_parents": lambda self, gedcom_id: [("Doe", "John", "I1")],
        "get_spouses": lambda self, gedcom_id: [("Doe", "Jane", "I2")],
        "get_children": lambda self, gedcom_id: [("Doe", "Alice", "I3")],
        "get_siblings": lambda self, gedcom_id: [],
        "get_person": lambda self, person_id: ("I1", "Doe", "John", "M", "", "", "", "", "", ""),
    })()
    viewer.format_name = staticmethod(lambda last_name, first_name: f"{last_name or ''} {first_name or ''}".strip())
    viewer.show_person = lambda person_id: person_id

    nodes = viewer.build_family_tree_nodes("I1")
    assert len(nodes) == 4
    assert any(node["id"] == "I1" for node in nodes)
    assert any(node["id"] == "I2" for node in nodes)
    assert any(node["id"] == "I3" for node in nodes)
