"""Append-only audit history stored separately from genealogy data."""

from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from config import DATA_DIR, DB_NAME


AUDIT_OPERATION_TYPES = (
    "person_create", "person_edit", "person_delete", "merge", "split",
    "relationship_change", "batch_operations", "import", "recovery_wizard",
    "placeholder_repair", "undo", "redo",
)


@dataclass(frozen=True)
class AuditRecord:
    id: int
    timestamp: str
    operation_type: str
    database_id: str
    gedcom_id: str
    affected_tables: tuple[str, ...]
    before_snapshot: dict[str, list[list[Any]]]
    after_snapshot: dict[str, list[list[Any]]]
    description: str
    service: str
    batch_id: str


class AuditService:
    """Record and query immutable application audit entries."""

    def __init__(self, audit_path=None) -> None:
        self.audit_path = Path(audit_path or DATA_DIR / "audit_history.sqlite3")
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @classmethod
    def for_database(cls, database_path=DB_NAME):
        database = Path(database_path).resolve()
        default_database = Path(DB_NAME).resolve()
        if database == default_database:
            return cls(DATA_DIR / "audit_history.sqlite3")
        return cls(database.with_name(f"{database.name}.audit.sqlite3"))

    def _connect(self):
        connection = sqlite3.connect(self.audit_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self):
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS audit_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    operation_type TEXT NOT NULL,
                    database_id TEXT NOT NULL DEFAULT '',
                    gedcom_id TEXT NOT NULL DEFAULT '',
                    affected_tables TEXT NOT NULL,
                    before_snapshot TEXT NOT NULL,
                    after_snapshot TEXT NOT NULL,
                    description TEXT NOT NULL,
                    service TEXT NOT NULL,
                    batch_id TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS audit_records_timestamp
                    ON audit_records(timestamp, id);
                CREATE INDEX IF NOT EXISTS audit_records_operation
                    ON audit_records(operation_type);
                CREATE INDEX IF NOT EXISTS audit_records_person
                    ON audit_records(database_id, gedcom_id);
                CREATE INDEX IF NOT EXISTS audit_records_service
                    ON audit_records(service);
                CREATE INDEX IF NOT EXISTS audit_records_batch
                    ON audit_records(batch_id);
                """
            )

    def record(
        self,
        operation_type: str,
        *,
        database_id: Any = "",
        gedcom_id: Any = "",
        affected_tables=(),
        before_snapshot: Mapping[str, Any] | None = None,
        after_snapshot: Mapping[str, Any] | None = None,
        description: str,
        service: str,
        batch_id: Any = "",
        timestamp: str | None = None,
    ) -> AuditRecord:
        recorded_at = timestamp or datetime.now(timezone.utc).isoformat(timespec="microseconds")
        before = self._normalise_snapshot(before_snapshot or {})
        after = self._normalise_snapshot(after_snapshot or {})
        tables = tuple(sorted(set(affected_tables) | set(before) | set(after)))
        values = (
            recorded_at,
            str(operation_type),
            self._identity_text(database_id),
            self._identity_text(gedcom_id),
            json.dumps(tables, ensure_ascii=False),
            json.dumps(before, ensure_ascii=False, default=str),
            json.dumps(after, ensure_ascii=False, default=str),
            str(description),
            str(service),
            self._identity_text(batch_id),
        )
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO audit_records (
                    timestamp, operation_type, database_id, gedcom_id,
                    affected_tables, before_snapshot, after_snapshot,
                    description, service, batch_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )
            record_id = cursor.lastrowid
        return self.get(record_id)

    def record_delta(
        self,
        operation_type: str,
        delta: Mapping[str, Any],
        *,
        database_id: Any = "",
        gedcom_id: Any = "",
        description: str,
        service: str,
        batch_id: Any = "",
        reverse: bool = False,
    ) -> AuditRecord:
        before = {}
        after = {}
        for table, change in delta.items():
            if hasattr(change, "before_rows"):
                before[table] = change.before_rows
                after[table] = change.after_rows
            else:
                before[table] = change.get("before_rows", ())
                after[table] = change.get("after_rows", ())
        if reverse:
            before, after = after, before
        inferred_database_id, inferred_gedcom_id = self._infer_person_identity(delta)
        return self.record(
            operation_type,
            database_id=database_id or inferred_database_id,
            gedcom_id=gedcom_id or inferred_gedcom_id,
            affected_tables=delta.keys(),
            before_snapshot=before,
            after_snapshot=after,
            description=description,
            service=service,
            batch_id=batch_id,
        )

    def record_state_change(
        self,
        operation_type: str,
        before_state: Mapping[str, Any],
        after_state: Mapping[str, Any],
        *,
        database_id: Any = "",
        gedcom_id: Any = "",
        description: str,
        service: str,
        batch_id: Any = "",
    ) -> AuditRecord:
        changed_tables = tuple(
            sorted(
                table for table in set(before_state) | set(after_state)
                if tuple(before_state.get(table, ())) != tuple(after_state.get(table, ()))
            )
        )
        before = {table: before_state.get(table, ()) for table in changed_tables}
        after = {table: after_state.get(table, ()) for table in changed_tables}
        if not database_id and not gedcom_id:
            database_id, gedcom_id = self._snapshot_people_identity(before, after)
        return self.record(
            operation_type,
            database_id=database_id,
            gedcom_id=gedcom_id,
            affected_tables=changed_tables,
            before_snapshot=before,
            after_snapshot=after,
            description=description,
            service=service,
            batch_id=batch_id,
        )

    @staticmethod
    def capture_database_state(database_path) -> dict[str, tuple[tuple[Any, ...], ...]]:
        state = {}
        with sqlite3.connect(database_path) as connection:
            tables = {
                row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            }
            for table in sorted(tables):
                quoted_table = table.replace('"', '""')
                rows = connection.execute(
                    f'SELECT * FROM "{quoted_table}" ORDER BY rowid'
                ).fetchall()
                state[table] = tuple(tuple(row) for row in rows)
        return state

    def get(self, record_id: int) -> AuditRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM audit_records WHERE id = ?", (int(record_id),)
            ).fetchone()
        return self._row_to_record(row) if row else None

    def list_records(
        self,
        *,
        person: str = "",
        operation: str = "",
        date_from: str = "",
        date_to: str = "",
        service: str = "",
        batch_id: str = "",
        sort_order: str = "desc",
    ) -> list[AuditRecord]:
        clauses = []
        parameters = []
        if person:
            clauses.append(
                "((',' || database_id || ',') LIKE ? OR (',' || gedcom_id || ',') LIKE ?)"
            )
            person_pattern = f"%,{person},%"
            parameters.extend((person_pattern, person_pattern))
        if operation:
            clauses.append("operation_type = ?")
            parameters.append(str(operation))
        if date_from:
            clauses.append("timestamp >= ?")
            parameters.append(str(date_from))
        if date_to:
            upper = str(date_to)
            if len(upper) == 10:
                upper += "T23:59:59.999999+00:00"
            clauses.append("timestamp <= ?")
            parameters.append(upper)
        if service:
            clauses.append("service = ?")
            parameters.append(str(service))
        if batch_id:
            clauses.append("batch_id = ?")
            parameters.append(str(batch_id))
        direction = "ASC" if str(sort_order).lower() == "asc" else "DESC"
        query = "SELECT * FROM audit_records"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += f" ORDER BY timestamp {direction}, id {direction}"
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._row_to_record(row) for row in rows]

    def filter_options(self) -> dict[str, tuple[str, ...]]:
        with self._connect() as connection:
            operations = connection.execute(
                "SELECT DISTINCT operation_type FROM audit_records ORDER BY operation_type"
            ).fetchall()
            services = connection.execute(
                "SELECT DISTINCT service FROM audit_records ORDER BY service"
            ).fetchall()
            batches = connection.execute(
                "SELECT DISTINCT batch_id FROM audit_records WHERE batch_id <> '' ORDER BY batch_id"
            ).fetchall()
        return {
            "operations": tuple(row[0] for row in operations),
            "services": tuple(row[0] for row in services),
            "batch_ids": tuple(row[0] for row in batches),
        }

    def export_json(self, records, destination_path) -> Path:
        destination = Path(destination_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps([asdict(record) for record in records], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return destination

    def export_csv(self, records, destination_path) -> Path:
        destination = Path(destination_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow((
                "id", "timestamp", "operation_type", "database_id", "gedcom_id",
                "affected_tables", "before_snapshot", "after_snapshot",
                "description", "service", "batch_id",
            ))
            for record in records:
                writer.writerow((
                    record.id, record.timestamp, record.operation_type,
                    record.database_id, record.gedcom_id,
                    json.dumps(record.affected_tables, ensure_ascii=False),
                    json.dumps(record.before_snapshot, ensure_ascii=False),
                    json.dumps(record.after_snapshot, ensure_ascii=False),
                    record.description, record.service, record.batch_id,
                ))
        return destination

    @staticmethod
    def _normalise_snapshot(snapshot):
        return {
            str(table): [list(row) for row in rows]
            for table, rows in snapshot.items()
        }

    @staticmethod
    def _identity_text(value):
        if value is None:
            return ""
        if isinstance(value, (tuple, list, set)):
            return ",".join(str(item) for item in value)
        return str(value)

    @staticmethod
    def _infer_person_identity(delta):
        change = delta.get("people")
        if change is None:
            return "", ""
        before = getattr(change, "before_rows", ())
        after = getattr(change, "after_rows", ())
        rows = tuple(after) or tuple(before)
        if not rows:
            return "", ""
        return rows[0][0], rows[0][1]

    @staticmethod
    def _snapshot_people_identity(before, after):
        rows = tuple(after.get("people", ())) or tuple(before.get("people", ()))
        database_ids = [row[0] for row in rows if len(row) > 0]
        gedcom_ids = [row[1] for row in rows if len(row) > 1 and row[1]]
        return database_ids, gedcom_ids

    @staticmethod
    def _row_to_record(row):
        return AuditRecord(
            id=row["id"],
            timestamp=row["timestamp"],
            operation_type=row["operation_type"],
            database_id=row["database_id"],
            gedcom_id=row["gedcom_id"],
            affected_tables=tuple(json.loads(row["affected_tables"])),
            before_snapshot=json.loads(row["before_snapshot"]),
            after_snapshot=json.loads(row["after_snapshot"]),
            description=row["description"],
            service=row["service"],
            batch_id=row["batch_id"],
        )