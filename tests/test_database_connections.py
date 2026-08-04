import sqlite3

import app
import database
from audit_service import AuditService


def test_initialize_database_closes_its_connection(tmp_path, monkeypatch):
    original_connect = sqlite3.connect
    closed = []

    class TrackingConnection(sqlite3.Connection):
        def close(self):
            closed.append(True)
            super().close()

    def connect(*args, **kwargs):
        kwargs["factory"] = TrackingConnection
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(database.sqlite3, "connect", connect)

    assert database.initialize_database(tmp_path / "tracked.db")
    assert closed == [True]


def test_audit_service_closes_every_temporary_connection(tmp_path, monkeypatch):
    database_path = tmp_path / "state.db"
    database.initialize_database(database_path)

    original_connect = sqlite3.connect
    opened = []
    closed = []

    class TrackingConnection(sqlite3.Connection):
        def close(self):
            closed.append(id(self))
            super().close()

    def connect(*args, **kwargs):
        kwargs["factory"] = TrackingConnection
        connection = original_connect(*args, **kwargs)
        opened.append(id(connection))
        return connection

    monkeypatch.setattr(database.sqlite3, "connect", connect)

    audit = AuditService(tmp_path / "audit.sqlite3")
    audit.list_records()
    AuditService.capture_database_state(database_path)

    assert opened
    assert sorted(opened) == sorted(closed)


def test_application_statistics_closes_connection(tmp_path, monkeypatch):
    database_path = tmp_path / "statistics.db"
    database.initialize_database(database_path)
    monkeypatch.setattr(app, "DB_NAME", database_path)

    original_connect = sqlite3.connect
    opened = []
    closed = []

    class TrackingConnection(sqlite3.Connection):
        def close(self):
            closed.append(id(self))
            super().close()

    def connect(*args, **kwargs):
        kwargs["factory"] = TrackingConnection
        connection = original_connect(*args, **kwargs)
        opened.append(id(connection))
        return connection

    monkeypatch.setattr(app.sqlite3, "connect", connect)

    assert app.GenealogyApplication().get_statistics() == {
        "people": 0,
        "families": 0,
        "family_children": 0,
    }
    assert opened == closed
