"""Persistent user-side favorites for quick person navigation."""

from __future__ import annotations

import json
from pathlib import Path


class PersonFavoritesService:
    """Store favorite database person identifiers outside the genealogy database."""

    FORMAT_VERSION = 1
    SCOPED_FORMAT_VERSION = 2

    def __init__(self, path, *, database_scope=None):
        self.path = Path(path)
        self.database_scope = str(database_scope).strip() if database_scope else None

    def list_ids(self) -> tuple[int, ...]:
        return self._load()

    def contains(self, person_id) -> bool:
        return self._person_id(person_id) in self._load()

    def add(self, person_id) -> bool:
        identifier = self._person_id(person_id)
        favorites = list(self._load())
        if identifier in favorites:
            return False
        favorites.append(identifier)
        self._save(favorites)
        return True

    def remove(self, person_id) -> bool:
        identifier = self._person_id(person_id)
        favorites = list(self._load())
        if identifier not in favorites:
            return False
        favorites.remove(identifier)
        self._save(favorites)
        return True

    def restore(self, person_id, index) -> bool:
        """Restore a removed favorite near its previous ordered position."""
        identifier = self._person_id(person_id)
        favorites = list(self._load())
        if identifier in favorites:
            return False
        position = max(0, min(int(index), len(favorites)))
        favorites.insert(position, identifier)
        self._save(favorites)
        return True

    def toggle(self, person_id) -> bool:
        identifier = self._person_id(person_id)
        if self.contains(identifier):
            self.remove(identifier)
            return False
        self.add(identifier)
        return True

    def prune(self, valid_person_ids) -> tuple[int, ...]:
        valid = {
            self._person_id(person_id)
            for person_id in valid_person_ids
            if not isinstance(person_id, bool) and str(person_id).strip()
        }
        current = self._load()
        filtered = tuple(person_id for person_id in current if person_id in valid)
        if filtered != current:
            self._save(filtered)
        return filtered

    def _load(self) -> tuple[int, ...]:
        payload = self._read_payload()
        if payload is None:
            return ()
        if self.database_scope and isinstance(payload, dict) and isinstance(payload.get("databases"), dict):
            values = payload["databases"].get(self.database_scope, ())
            return self._normalize_ids(values)

        values = payload.get("person_ids", ()) if isinstance(payload, dict) else payload
        favorites = self._normalize_ids(values)
        if self.database_scope and isinstance(values, list):
            self._save(favorites)
        return favorites

    def _read_payload(self):
        if not self.path.is_file():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None

    def _normalize_ids(self, values) -> tuple[int, ...]:
        if not isinstance(values, list):
            return ()
        favorites = []
        for value in values:
            if isinstance(value, bool):
                continue
            try:
                identifier = self._person_id(value)
            except (TypeError, ValueError):
                continue
            if identifier not in favorites:
                favorites.append(identifier)
        return tuple(favorites)

    def _save(self, person_ids) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        if self.database_scope:
            current_payload = self._read_payload()
            databases = {}
            if isinstance(current_payload, dict) and isinstance(current_payload.get("databases"), dict):
                databases = {
                    str(key): list(self._normalize_ids(values))
                    for key, values in current_payload["databases"].items()
                }
            elif isinstance(current_payload, dict) and isinstance(current_payload.get("person_ids"), list):
                databases[self.database_scope] = list(
                    self._normalize_ids(current_payload["person_ids"])
                )
            elif isinstance(current_payload, list):
                databases[self.database_scope] = list(self._normalize_ids(current_payload))
            databases[self.database_scope] = list(person_ids)
            payload = {
                "version": self.SCOPED_FORMAT_VERSION,
                "databases": databases,
            }
        else:
            payload = {
                "version": self.FORMAT_VERSION,
                "person_ids": list(person_ids),
            }
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
