"""Persistent home-person selection for each genealogy database."""

from __future__ import annotations

import json
from pathlib import Path


class HomePersonService:
    """Store one quick-return person identifier per database."""

    FORMAT_VERSION = 1

    def __init__(self, path, *, database_scope):
        self.path = Path(path)
        self.database_scope = str(database_scope).strip()
        if not self.database_scope:
            raise ValueError("Database scope is required.")

    def get_id(self) -> int | None:
        payload = self._read_payload()
        if not isinstance(payload, dict) or not isinstance(payload.get("databases"), dict):
            return None
        value = payload["databases"].get(self.database_scope)
        try:
            return self._person_id(value)
        except (TypeError, ValueError):
            return None

    def set_id(self, person_id) -> int:
        identifier = self._person_id(person_id)
        self._save(identifier)
        return identifier

    def clear(self) -> bool:
        if self.get_id() is None:
            return False
        self._save(None)
        return True

    def _read_payload(self):
        if not self.path.is_file():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None

    def _save(self, person_id) -> None:
        current_payload = self._read_payload()
        databases = {}
        if isinstance(current_payload, dict) and isinstance(current_payload.get("databases"), dict):
            for key, value in current_payload["databases"].items():
                try:
                    databases[str(key)] = self._person_id(value)
                except (TypeError, ValueError):
                    continue
        if person_id is None:
            databases.pop(self.database_scope, None)
        else:
            databases[self.database_scope] = person_id
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

    @staticmethod
    def _person_id(value) -> int:
        if isinstance(value, bool):
            raise TypeError("Boolean values are not person identifiers.")
        identifier = int(value)
        if identifier <= 0:
            raise ValueError("Person identifier must be positive.")
        return identifier
