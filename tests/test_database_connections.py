import sqlite3

import database


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
