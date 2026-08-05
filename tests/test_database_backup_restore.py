from pathlib import Path
import sqlite3

import pytest

from database import backup_database, restore_database, validate_database_file
from undo_manager import UndoManager
import viewer as viewer_module
from viewer import GenealogyViewer


@pytest.fixture
def populated_db(tmp_path):
    db_path = tmp_path / "genealogy.db"
    conn = sqlite3.connect(db_path)
    schema_sql = Path("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.execute(
        "INSERT INTO people (gedcom_id, first_name, last_name) VALUES (?, ?, ?)",
        ("I1", "John", "Doe"),
    )
    conn.commit()
    conn.close()
    return db_path


def test_backup_database_creates_timestamped_copy(populated_db, tmp_path):
    backup_path = backup_database(populated_db, tmp_path / "backups")

    assert backup_path.exists()
    assert backup_path.parent == tmp_path / "backups"
    assert backup_path.name.startswith("genealogy-")
    validate_database_file(backup_path)


def test_validate_database_file_rejects_invalid_sqlite_file(tmp_path):
    invalid_path = tmp_path / "not_a_database.db"
    invalid_path.write_text("not sqlite", encoding="utf-8")

    with pytest.raises(ValueError):
        validate_database_file(invalid_path)


def test_restore_database_replaces_target_and_creates_safety_backup(populated_db, tmp_path):
    restored_db_path = tmp_path / "restored.db"
    restored_db_path.write_bytes(populated_db.read_bytes())

    backup_path = tmp_path / "restored-backup.db"
    backup_path.write_bytes(populated_db.read_bytes())

    source_backup = tmp_path / "source.db"
    conn = sqlite3.connect(source_backup)
    schema_sql = Path("schema.sql").read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.execute(
        "INSERT INTO people (gedcom_id, first_name, last_name) VALUES (?, ?, ?)",
        ("I2", "Jane", "Smith"),
    )
    conn.commit()
    conn.close()

    safety_backup = restore_database(source_backup, restored_db_path)

    assert safety_backup is not None
    assert restored_db_path.exists()
    validate_database_file(restored_db_path)
    conn = sqlite3.connect(restored_db_path)
    people = conn.execute("SELECT gedcom_id, first_name, last_name FROM people ORDER BY gedcom_id").fetchall()
    conn.close()
    assert people == [("I2", "Jane", "Smith")]


def test_viewer_restore_discards_stale_undo_and_person_context(monkeypatch):
    calls = []
    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer.undo_manager = UndoManager()
    viewer.undo_manager._undo_stack.append(object())
    viewer.undo_manager._redo_stack.append(object())
    viewer.current_person_id = 7
    viewer.current_person_gedcom_id = "I7"
    viewer._person_history = [3, 7]
    viewer._person_history_index = 1
    viewer._person_card_body = None
    viewer._person_dialog = None
    viewer._refresh_person_navigation_views = lambda: calls.append("navigation")
    viewer.refresh_views = lambda: calls.append("refresh")

    monkeypatch.setattr(viewer_module.filedialog, "askopenfilename", lambda **_kwargs: "source.db")
    monkeypatch.setattr(viewer_module.messagebox, "askyesno", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(viewer_module.messagebox, "showinfo", lambda *_args, **_kwargs: calls.append("info"))
    monkeypatch.setattr(
        viewer_module,
        "restore_database",
        lambda source, target: calls.append(("restore", source, target)),
    )
    monkeypatch.setattr(viewer_module, "DB_NAME", "target.db")

    viewer.restore_database()

    assert not viewer.undo_manager.can_undo
    assert not viewer.undo_manager.can_redo
    assert viewer.current_person_id is None
    assert viewer.current_person_gedcom_id is None
    assert viewer._person_history == []
    assert viewer._person_history_index == -1
    assert calls == [
        ("restore", "source.db", "target.db"),
        "navigation",
        "refresh",
        "info",
    ]
