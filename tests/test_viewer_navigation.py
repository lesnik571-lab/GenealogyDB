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

    root = object()
    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer.repository = type("Repo", (), {"get_person_by_gedcom_id": lambda self, gedcom_id: (1,)})()
    viewer.show_person = lambda person_id: person_id
    viewer.format_name = staticmethod(lambda last_name, first_name: f"{last_name or ''} {first_name or ''}".strip())

    import tkinter as tk
    text = tk.Text()
    viewer.insert_people(text, [("Doe", "John", "I1")], "нет")

    assert "Doe John [I1]" in text.get("1.0", "end")
