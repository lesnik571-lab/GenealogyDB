"""Persistent recently viewed people for quick navigation."""

from __future__ import annotations

import json
from pathlib import Path


class RecentPeopleService:
    """Keep a bounded most-recently-used person list per database."""

    FORMAT_VERSION = 1

    def __init__(self, path, *, database_scope, limit=20):
        self.path = Path(path)
        self.database_scope = str(database_scope).strip()
        self.limit = max(1, int(limit))
        if not self.database_scope:
            raise ValueError("Database scope is required.")

    def list_ids(self) -> tuple[int, ...]:
        payload = self._read_payload()
        if not isinstance(payload, dict) or not isinstance(payload.get("databases"), dict):
            return ()
        return self._normalize_ids(payload["databases"].get(self.database_scope, ()))

    def record(self, person_id) -> tuple[int, ...]:
        identifier = self._person_id(person_id)
        recent = [value for value in self.list_ids() if value != identifier]
        recent.insert(0, identifier)
        recent = recent[: self.limit]
        self._save(recent)
        return tuple(recent)

    def remove(self, person_id) -> bool:
        identifier = self._person_id(person_id)
        recent = list(self.list_ids())
        if identifier not in recent:
            return False
        recent.remove(identifier)
        self._save(recent)
        return True

    def prune(self, valid_person_ids) -> tuple[int, ...]:
        valid = {
            self._person_id(person_id)
            for person_id in valid_person_ids
            if not isinstance(person_id, bool) and str(person_id).strip()
        }
        current = self.list_ids()
        filtered = tuple(person_id for person_id in current if person_id in valid)
        if filtered != current:
            self._save(filtered)
        return filtered

    def clear(self) -> None:
        if self.list_ids():
            self._save(())

    def _read_payload(self):
        if not self.path.is_file():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None

    def _save(self, person_ids) -> None:
        current_payload = self._read_payload()
        databases = {}
        if isinstance(current_payload, dict) and isinstance(current_payload.get("databases"), dict):
            databases = {
                str(key): list(self._normalize_ids(values))
                for key, values in current_payload["databases"].items()
            }
        databases[self.database_scope] = list(person_ids)
        payload = {
            "version": self.FORMAT_VERSION,
            "databases": databases,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        try:
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            temporary.replace(self.path)
        finally:
            if temporary.exists():
                temporary.unlink()

    def _normalize_ids(self, values) -> tuple[int, ...]:
        if not isinstance(values, list):
            return ()
        recent = []
        for value in values:
            if isinstance(value, bool):
                continue
            try:
                identifier = self._person_id(value)
            except (TypeError, ValueError):
                continue
            if identifier not in recent:
                recent.append(identifier)
            if len(recent) >= self.limit:
                break
        return tuple(recent)

    @staticmethod
    def _person_id(value) -> int:
        if isinstance(value, bool):
            raise TypeError("Boolean values are not person identifiers.")
        identifier = int(value)
        if identifier <= 0:
            raise ValueError("Person identifier must be positive.")
        return identifier
