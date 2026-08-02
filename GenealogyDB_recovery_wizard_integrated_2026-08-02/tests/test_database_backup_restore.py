from pathlib import Path
import sqlite3

import pytest

from database import backup_database, restore_database, validate_database_file


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
