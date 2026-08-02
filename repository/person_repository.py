import re
import sqlite3
import unicodedata
from contextlib import contextmanager

from config import DB_NAME


def _normalize_text(value):
    if not value:
        return ""
    value = value.strip().lower()
    # normalize common Cyrillic variants
    value = value.replace("ё", "е")
    value = value.replace("ъ", "")
    # remove diacritics (e.g. Jöhn -> John)
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^\w\s]", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _normalize_date(value):
    if not value:
        return ""
    value = value.strip().lower()
    value = value.replace("ё", "е")
    # normalize date-like strings to comparable form
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^\w\s]", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    # normalize numeric components like day/month numbers (remove leading zeros)
    value = re.sub(r"\b0+([0-9]+)\b", r"\1", value)
    return value


class PersonRepository:
    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name
        self.conn = sqlite3.connect(db_name)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.cur = self.conn.cursor()
        self._transaction_depth = 0
        self._ensure_family_relationship_schema()

    @contextmanager
    def transaction(self):
        outermost = self._transaction_depth == 0
        if outermost:
            self.conn.execute("BEGIN")
        self._transaction_depth += 1
        try:
            yield
        except Exception:
            self._transaction_depth -= 1
            if outermost:
                self.conn.rollback()
            raise
        else:
            self._transaction_depth -= 1
            if outermost:
                self.conn.commit()

    def _commit_if_needed(self):
        if self._transaction_depth == 0:
            self.conn.commit()

    def _ensure_family_relationship_schema(self):
        columns = {row[1] for row in self.conn.execute('PRAGMA table_info("families")').fetchall()}
        if "relationship_type" not in columns:
            self.conn.execute("ALTER TABLE families ADD COLUMN relationship_type TEXT NOT NULL DEFAULT 'unknown'")
            self.conn.commit()

    def close(self):
        self.conn.close()

    def list_people(self, surname=None, first_name=None, last_name=None, birth_year=None, death_year=None, sex=None, limit=500):
        self.cur.execute(
            """
            SELECT id, last_name, first_name, birth_date, death_date, sex
            FROM people
            ORDER BY last_name, first_name
            """
        )
        rows = self.cur.fetchall()

        filtered_rows = []
        for row in rows:
            person_id, person_last_name, person_first_name, birth_date, death_date, person_sex = row

            search_text = next((value for value in (surname, first_name, last_name) if value), None)
            if search_text and not self._matches_name_query(person_first_name, person_last_name, search_text):
                continue
            if birth_year is not None and birth_year != "" and not self._matches_year(birth_date, birth_year):
                continue
            if death_year is not None and death_year != "" and not self._matches_year(death_date, death_year):
                continue
            if sex and person_sex and person_sex.upper() != sex.upper():
                continue

            filtered_rows.append((person_id, person_last_name, person_first_name, birth_date, death_date))
            if len(filtered_rows) >= limit:
                break

        return filtered_rows

    def list_people_full(self):
        self.cur.execute(
            """
            SELECT id, gedcom_id, first_name, last_name, sex, birth_date, birth_place, death_date, death_place, occupation, note
            FROM people
            ORDER BY id
            """
        )
        rows = self.cur.fetchall()
        return [
            {
                "id": row[0],
                "gedcom_id": row[1] or "",
                "first_name": row[2] or "",
                "last_name": row[3] or "",
                "sex": row[4] or "",
                "birth_date": row[5] or "",
                "birth_place": row[6] or "",
                "death_date": row[7] or "",
                "death_place": row[8] or "",
                "occupation": row[9] or "",
                "note": row[10] or "",
            }
            for row in rows
        ]

    def search_people_for_picker(self, query="", exclude_reference=None, limit=100):
        search_text = _normalize_text(query)
        search_date = _normalize_date(query)
        excluded = self._expand_person_references(exclude_reference)
        matches = []
        for person in self.list_people_full():
            reference = person["gedcom_id"] or str(person["id"])
            if reference in excluded or str(person["id"]) in excluded:
                continue
            haystacks = [
                _normalize_text(person["first_name"]),
                _normalize_text(person["last_name"]),
                _normalize_text(f"{person['last_name']} {person['first_name']}"),
                _normalize_text(f"{person['first_name']} {person['last_name']}"),
                _normalize_text(person["gedcom_id"]),
                _normalize_text(str(person["id"])),
                _normalize_date(person["birth_date"]),
            ]
            if search_text and not any(search_text in value for value in haystacks if value):
                continue
            if search_date and search_date not in haystacks[-1] and search_text and search_text not in haystacks[-1]:
                pass
            matches.append({
                **person,
                "reference": reference,
                "display_name": self.format_person_label(person),
            })
            if len(matches) >= limit:
                break
        return matches

    def list_people_for_integrity(self):
        self.cur.execute(
            """
            SELECT id, gedcom_id, first_name, last_name, birth_date, birth_place, death_date
            FROM people
            ORDER BY id
            """
        )
        rows = self.cur.fetchall()
        return [
            {
                "id": row[0],
                "gedcom_id": row[1] or "",
                "first_name": row[2] or "",
                "last_name": row[3] or "",
                "birth_date": row[4] or "",
                "birth_place": row[5] or "",
                "death_date": row[6] or "",
            }
            for row in rows
        ]

    def list_families_raw(self):
        self.cur.execute(
            """
            SELECT id, gedcom_id, husband_id, wife_id, relationship_type
            FROM families
            ORDER BY id
            """
        )
        rows = self.cur.fetchall()
        return [
            {
                "id": row[0],
                "gedcom_id": row[1] or "",
                "husband_id": row[2] or "",
                "wife_id": row[3] or "",
                "relationship_type": row[4] or "unknown",
            }
            for row in rows
        ]

    def list_family_children_raw(self):
        self.cur.execute(
            """
            SELECT family_id, child_id
            FROM family_children
            ORDER BY family_id, child_id
            """
        )
        rows = self.cur.fetchall()
        return [
            {
                "family_id": row[0] or "",
                "child_id": row[1] or "",
            }
            for row in rows
        ]

    def list_all_person_events(self):
        self.cur.execute(
            """
            SELECT id, person_id, event_type, event_date, event_place, description
            FROM person_events
            ORDER BY id
            """
        )
        rows = self.cur.fetchall()
        return [
            {
                "id": row[0],
                "person_id": row[1],
                "event_type": row[2],
                "date": row[3] or "",
                "place": row[4] or "",
                "description": row[5] or "",
            }
            for row in rows
        ]

    def list_person_events_for_integrity(self):
        self.cur.execute(
            """
            SELECT person_id, event_type, event_date
            FROM person_events
            ORDER BY id
            """
        )
        rows = self.cur.fetchall()
        return [
            {
                "person_id": row[0],
                "event_type": row[1],
                "date": row[2] or "",
            }
            for row in rows
        ]

    @staticmethod
    def _matches_prefix(value, text):
        return (value or "").lower().startswith((text or "").lower())

    @staticmethod
    def _matches_search(value, text):
        return (text or "") in (value or "")

    @staticmethod
    def _matches_name_query(first_name, last_name, text):
        if not text:
            return True

        normalized_text = _normalize_text(text)
        if not normalized_text:
            return True

        parts = normalized_text.split()
        if len(parts) == 1:
            term = parts[0]
            return term in _normalize_text(first_name) or term in _normalize_text(last_name)

        if len(parts) == 2:
            first_term, second_term = parts
            first_name_normalized = _normalize_text(first_name)
            last_name_normalized = _normalize_text(last_name)
            return (
                first_term in first_name_normalized and second_term in last_name_normalized
            ) or (
                second_term in first_name_normalized and first_term in last_name_normalized
            )

        return False

    @staticmethod
    def _matches_year(value, year):
        match = re.search(r"(\d{4})", value or "")
        if not match:
            return False
        return int(match.group(1)) == int(year)

    def get_person(self, person_id):
        self.cur.execute(
            """
            SELECT
                gedcom_id,
                last_name,
                first_name,
                sex,
                birth_date,
                birth_place,
                death_date,
                death_place,
                occupation,
                note
            FROM people
            WHERE id = ?
            """,
            (person_id,),
        )
        return self.cur.fetchone()

    def get_person_record(self, person_id):
        self.cur.execute(
            """
            SELECT id, gedcom_id, last_name, first_name, sex, birth_date, birth_place, death_date, death_place, occupation, note
            FROM people
            WHERE id = ?
            """,
            (person_id,),
        )
        row = self.cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "gedcom_id": row[1] or "",
            "last_name": row[2] or "",
            "first_name": row[3] or "",
            "sex": row[4] or "",
            "birth_date": row[5] or "",
            "birth_place": row[6] or "",
            "death_date": row[7] or "",
            "death_place": row[8] or "",
            "occupation": row[9] or "",
            "note": row[10] or "",
            "reference": (row[1] or str(row[0])),
        }

    def get_person_record_by_reference(self, person_reference):
        person_id = self.resolve_person_reference(person_reference)
        if person_id is None or not str(person_id).strip():
            return None
        if isinstance(person_id, int) or str(person_id).isdigit():
            return self.get_person_record(int(person_id))
        lookup = self.get_person_by_gedcom_id(str(person_id))
        if not lookup:
            return None
        return self.get_person_record(int(lookup[0]))

    @staticmethod
    def format_person_label(person):
        name = f"{person.get('last_name', '')} {person.get('first_name', '')}".strip() or "Без имени"
        birth = person.get("birth_date") or "?"
        gedcom = person.get("gedcom_id") or "-"
        return f"ID {person.get('id')} | {name} | р. {birth} | GEDCOM {gedcom}"

    def get_person_by_gedcom_id(self, gedcom_id):
        self.cur.execute(
            """
            SELECT id
            FROM people
            WHERE gedcom_id = ?
            """,
            (gedcom_id,),
        )
        return self.cur.fetchone()

    def create_person(self, data):
        self._validate_person_data(data)
        self.cur.execute(
            """
            INSERT INTO people (
                gedcom_id,
                first_name,
                last_name,
                sex,
                birth_date,
                birth_place,
                death_date,
                death_place,
                occupation,
                note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data.get("gedcom_id") or None,
                data.get("first_name") or "",
                data.get("last_name") or "",
                data.get("sex") or "",
                data.get("birth_date") or "",
                data.get("birth_place") or "",
                data.get("death_date") or "",
                data.get("death_place") or "",
                data.get("occupation") or "",
                data.get("note") or "",
            ),
        )
        self._commit_if_needed()
        return self.cur.lastrowid

    def update_person(self, person_id, data):
        if not person_id:
            return False
        self._validate_person_data(data)
        self.cur.execute(
            """
            UPDATE people
            SET first_name = ?, last_name = ?, sex = ?, birth_date = ?, birth_place = ?, death_date = ?, death_place = ?, occupation = ?, note = ?
            WHERE id = ?
            """,
            (
                data.get("first_name") or "",
                data.get("last_name") or "",
                data.get("sex") or "",
                data.get("birth_date") or "",
                data.get("birth_place") or "",
                data.get("death_date") or "",
                data.get("death_place") or "",
                data.get("occupation") or "",
                data.get("note") or "",
                person_id,
            ),
        )
        self._commit_if_needed()
        return self.cur.rowcount > 0

    def update_person_fields(self, person_id, changes):
        if not person_id or not changes:
            return False
        allowed_fields = {
            "first_name", "last_name", "sex", "birth_date", "birth_place",
            "death_date", "death_place", "occupation", "note",
        }
        invalid_fields = set(changes) - allowed_fields
        if invalid_fields:
            raise ValueError(f"Unsupported person fields: {', '.join(sorted(invalid_fields))}")
        assignments = ", ".join(f"{field} = ?" for field in changes)
        values = [changes[field] for field in changes]
        self.cur.execute(
            f"UPDATE people SET {assignments} WHERE id = ?",
            (*values, person_id),
        )
        self._commit_if_needed()
        return self.cur.rowcount > 0

    def delete_person(self, person_id):
        if not person_id:
            return False
        person = self.get_person(person_id)
        person_gedcom_id = person[0] if person else None
        if person_gedcom_id:
            self.cur.execute("DELETE FROM family_children WHERE child_id = ?", (person_gedcom_id,))
        self.cur.execute("DELETE FROM people WHERE id = ?", (person_id,))
        self._commit_if_needed()
        return self.cur.rowcount > 0

    def create_family(self, data):
        self._validate_family_data(data)
        self.cur.execute(
            """
            INSERT INTO families (gedcom_id, husband_id, wife_id, relationship_type) VALUES (?, ?, ?, ?)
            """,
            (
                data.get("gedcom_id") or "",
                data.get("husband") or "",
                data.get("wife") or "",
                data.get("relationship_type") or "unknown",
            ),
        )
        family_id = self.cur.lastrowid
        family_gedcom_id = data.get("gedcom_id") or ""
        for child in data.get("children", []):
            self.cur.execute(
                "INSERT INTO family_children (family_id, child_id) VALUES (?, ?)",
                (family_gedcom_id, child),
            )
        self._commit_if_needed()
        return family_id

    def create_person_event(self, data):
        self.cur.execute(
            """
            INSERT INTO person_events (person_id, event_type, event_date, event_place, description)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                data.get("person_id") or "",
                data.get("event_type") or "custom",
                data.get("date") or "",
                data.get("place") or "",
                data.get("description") or "",
            ),
        )
        self.conn.commit()
        return self.cur.lastrowid

    def create_person_media(self, data):
        self.cur.execute(
            """
            INSERT INTO person_media (person_id, media_type, title, file_path, description)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                data.get("person_id"),
                data.get("media_type") or "document",
                data.get("title") or "",
                data.get("file_path") or "",
                data.get("description") or "",
            ),
        )
        self.conn.commit()
        return self.cur.lastrowid

    def list_person_media(self, person_id):
        if not person_id:
            return []
        self.cur.execute(
            """
            SELECT id, person_id, media_type, title, file_path, description, created_at
            FROM person_media
            WHERE person_id = ?
            ORDER BY id
            """,
            (person_id,),
        )
        rows = self.cur.fetchall()
        return [
            {
                "id": row[0],
                "person_id": row[1],
                "media_type": row[2],
                "title": row[3],
                "file_path": row[4],
                "description": row[5],
                "created_at": row[6],
            }
            for row in rows
        ]

    def get_person_media(self, media_id):
        if not media_id:
            return None
        self.cur.execute(
            """
            SELECT id, person_id, media_type, title, file_path, description, created_at
            FROM person_media
            WHERE id = ?
            """,
            (media_id,),
        )
        row = self.cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "person_id": row[1],
            "media_type": row[2],
            "title": row[3],
            "file_path": row[4],
            "description": row[5],
            "created_at": row[6],
        }

    def delete_person_media(self, media_id):
        if not media_id:
            return False
        self.cur.execute("DELETE FROM person_media WHERE id = ?", (media_id,))
        self.conn.commit()
        return self.cur.rowcount > 0

    def update_person_media(self, media_id, data):
        if not media_id:
            return False
        self.cur.execute(
            """
            UPDATE person_media
            SET title = ?, description = ?
            WHERE id = ?
            """,
            (
                data.get("title") or "",
                data.get("description") or "",
                media_id,
            ),
        )
        self.conn.commit()
        return self.cur.rowcount > 0

    def create_person_source(self, data):
        self.cur.execute(
            """
            INSERT INTO person_sources (person_id, title, source_url, archive_reference, note)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                data.get("person_id"),
                data.get("title") or "",
                data.get("source_url") or "",
                data.get("archive_reference") or "",
                data.get("note") or "",
            ),
        )
        self.conn.commit()
        return self.cur.lastrowid

    def update_person_source(self, source_id, data):
        if not source_id:
            return False
        self.cur.execute(
            """
            UPDATE person_sources
            SET title = ?, source_url = ?, archive_reference = ?, note = ?
            WHERE id = ?
            """,
            (
                data.get("title") or "",
                data.get("source_url") or "",
                data.get("archive_reference") or "",
                data.get("note") or "",
                source_id,
            ),
        )
        self.conn.commit()
        return self.cur.rowcount > 0

    def list_person_sources(self, person_id):
        if not person_id:
            return []
        self.cur.execute(
            """
            SELECT id, person_id, title, source_url, archive_reference, note, created_at
            FROM person_sources
            WHERE person_id = ?
            ORDER BY id
            """,
            (person_id,),
        )
        rows = self.cur.fetchall()
        return [
            {
                "id": row[0],
                "person_id": row[1],
                "title": row[2],
                "source_url": row[3],
                "archive_reference": row[4],
                "note": row[5],
                "created_at": row[6],
            }
            for row in rows
        ]

    def get_geocoding_cache(self, normalized_place):
        if not normalized_place:
            return None
        self.cur.execute(
            """
            SELECT id, normalized_place, original_place, latitude, longitude, status, provider, error_message, updated_at
            FROM geocoding_cache
            WHERE normalized_place = ?
            """,
            (normalized_place,),
        )
        row = self.cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "normalized_place": row[1],
            "original_place": row[2] or "",
            "latitude": row[3],
            "longitude": row[4],
            "status": row[5] or "missing",
            "provider": row[6] or "",
            "error_message": row[7] or "",
            "updated_at": row[8] or "",
        }

    def get_geocoding_cache_batch(self, normalized_places):
        places = [place for place in sorted(set(normalized_places or [])) if place]
        if not places:
            return {}
        placeholders = ",".join("?" for _ in places)
        self.cur.execute(
            f"""
            SELECT id, normalized_place, original_place, latitude, longitude, status, provider, error_message, updated_at
            FROM geocoding_cache
            WHERE normalized_place IN ({placeholders})
            """,
            tuple(places),
        )
        rows = self.cur.fetchall()
        return {
            row[1]: {
                "id": row[0],
                "normalized_place": row[1],
                "original_place": row[2] or "",
                "latitude": row[3],
                "longitude": row[4],
                "status": row[5] or "missing",
                "provider": row[6] or "",
                "error_message": row[7] or "",
                "updated_at": row[8] or "",
            }
            for row in rows
        }

    def upsert_geocoding_cache(self, normalized_place, original_place="", latitude=None, longitude=None, status="missing", provider="", error_message=""):
        if not normalized_place:
            return None

        self.cur.execute(
            """
            INSERT INTO geocoding_cache (normalized_place, original_place, latitude, longitude, status, provider, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(normalized_place) DO UPDATE SET
                original_place = excluded.original_place,
                latitude = excluded.latitude,
                longitude = excluded.longitude,
                status = excluded.status,
                provider = excluded.provider,
                error_message = excluded.error_message,
                updated_at = CURRENT_TIMESTAMP
            """,
            (normalized_place, original_place or "", latitude, longitude, status or "missing", provider or "", error_message or ""),
        )
        self.conn.commit()
        return self.get_geocoding_cache(normalized_place)

    def get_person_source(self, source_id):
        if not source_id:
            return None
        self.cur.execute(
            """
            SELECT id, person_id, title, source_url, archive_reference, note, created_at
            FROM person_sources
            WHERE id = ?
            """,
            (source_id,),
        )
        row = self.cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "person_id": row[1],
            "title": row[2],
            "source_url": row[3],
            "archive_reference": row[4],
            "note": row[5],
            "created_at": row[6],
        }

    def delete_person_source(self, source_id):
        if not source_id:
            return False
        self.cur.execute("DELETE FROM person_sources WHERE id = ?", (source_id,))
        self.conn.commit()
        return self.cur.rowcount > 0

    def update_person_event(self, event_id, data):
        if not event_id:
            return False
        self.cur.execute(
            """
            UPDATE person_events
            SET event_type = ?, event_date = ?, event_place = ?, description = ?
            WHERE id = ?
            """,
            (
                data.get("event_type") or "custom",
                data.get("date") or "",
                data.get("place") or "",
                data.get("description") or "",
                event_id,
            ),
        )
        self.conn.commit()
        return self.cur.rowcount > 0

    def delete_person_event(self, event_id):
        if not event_id:
            return False
        self.cur.execute("DELETE FROM person_events WHERE id = ?", (event_id,))
        self.conn.commit()
        return self.cur.rowcount > 0

    def list_person_events(self, person_id):
        if not person_id:
            return []
        self.cur.execute(
            """
            SELECT id, person_id, event_type, event_date, event_place, description
            FROM person_events
            WHERE person_id = ?
            ORDER BY id
            """,
            (person_id,),
        )
        rows = self.cur.fetchall()
        return [
            {
                "id": row[0],
                "person_id": row[1],
                "event_type": row[2],
                "date": row[3],
                "place": row[4],
                "description": row[5],
            }
            for row in rows
        ]

    def get_person_event(self, event_id):
        if not event_id:
            return None
        self.cur.execute(
            """
            SELECT id, person_id, event_type, event_date, event_place, description
            FROM person_events
            WHERE id = ?
            """,
            (event_id,),
        )
        row = self.cur.fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "person_id": row[1],
            "event_type": row[2],
            "date": row[3],
            "place": row[4],
            "description": row[5],
        }

    def update_family(self, family_id, data):
        if not family_id:
            return False
        # allow updates that don't include gedcom_id; keep existing gedcom_id
        family_gedcom_id = data.get("gedcom_id") or self._family_gedcom_id(family_id)
        update_count = self.cur.execute(
            "UPDATE families SET gedcom_id = ?, husband_id = ?, wife_id = ?, relationship_type = ? WHERE id = ?",
            (
                family_gedcom_id,
                data.get("husband") or "",
                data.get("wife") or "",
                data.get("relationship_type") or self._family_relationship_type(family_id) or "unknown",
                family_id,
            ),
        ).rowcount
        family_refs = self._family_child_family_ids(family_id)
        if family_gedcom_id and family_gedcom_id not in family_refs:
            family_refs.append(family_gedcom_id)
        if family_refs:
            placeholders = ",".join("?" for _ in family_refs)
            self.cur.execute(f"DELETE FROM family_children WHERE family_id IN ({placeholders})", tuple(family_refs))
        for child in data.get("children", []):
            self.cur.execute(
                "INSERT INTO family_children (family_id, child_id) VALUES (?, ?)",
                (family_gedcom_id, child),
            )
        self._commit_if_needed()
        return update_count > 0

    def delete_family(self, family_id):
        if not family_id:
            return False
        family_refs = self._family_child_family_ids(family_id)
        if family_refs:
            placeholders = ",".join("?" for _ in family_refs)
            self.cur.execute(f"DELETE FROM family_children WHERE family_id IN ({placeholders})", tuple(family_refs))
        self.cur.execute("DELETE FROM families WHERE id = ?", (family_id,))
        self._commit_if_needed()
        return self.cur.rowcount > 0

    def get_family(self, family_id):
        if not family_id:
            return None
        self.cur.execute(
            "SELECT id, gedcom_id, husband_id, wife_id, relationship_type FROM families WHERE id = ?",
            (family_id,),
        )
        row = self.cur.fetchone()
        if not row:
            return None
        family_id, gedcom_id, husband_id, wife_id, relationship_type = row
        family_refs = self._family_child_family_ids(family_id)
        if not family_refs:
            family_refs = [gedcom_id]
        placeholders = ",".join("?" for _ in family_refs)
        self.cur.execute(f"SELECT child_id FROM family_children WHERE family_id IN ({placeholders})", tuple(family_refs))
        children = [child_id for (child_id,) in self.cur.fetchall()]
        return {"id": family_id, "gedcom_id": gedcom_id, "husband": husband_id, "wife": wife_id, "children": children, "relationship_type": relationship_type or "unknown"}

    def list_person_families(self, person_reference):
        references = sorted(self._expand_person_references(person_reference))
        if not references:
            return []
        placeholders, params = self._build_in_clause(references)
        self.cur.execute(
            f"""
            SELECT DISTINCT f.id
            FROM families f
            LEFT JOIN family_children fc ON {self._family_child_join_clause('f')}
            WHERE f.husband_id IN ({placeholders})
               OR f.wife_id IN ({placeholders})
               OR fc.child_id IN ({placeholders})
            ORDER BY f.id
            """,
            params * 3,
        )
        family_ids = [row[0] for row in self.cur.fetchall()]
        families = []
        for family_id in family_ids:
            family = self.get_family(family_id)
            if family:
                families.append(family)
        return families

    def find_family(self, husband_reference="", wife_reference="", relationship_type=None, include_children=False):
        husband_reference = str(husband_reference or "").strip()
        wife_reference = str(wife_reference or "").strip()
        families = self.list_families_raw()
        for family in families:
            if family["husband_id"] != husband_reference or family["wife_id"] != wife_reference:
                continue
            if relationship_type and family["relationship_type"] not in (relationship_type, "", "unknown"):
                continue
            if not include_children:
                family_children = self.get_family(family["id"]).get("children", [])
                if family_children:
                    continue
            return self.get_family(family["id"])
        return None

    def _family_gedcom_id(self, family_id):
        row = self.cur.execute("SELECT gedcom_id FROM families WHERE id = ?", (family_id,)).fetchone()
        return row[0] if row else None

    def _family_relationship_type(self, family_id):
        row = self.cur.execute("SELECT relationship_type FROM families WHERE id = ?", (family_id,)).fetchone()
        return row[0] if row else None

    def _validate_person_data(self, data):
        if not data.get("first_name") or not data.get("last_name"):
            raise ValueError("Имя и фамилия обязательны")

    def _validate_family_data(self, data):
        if not data.get("gedcom_id"):
            raise ValueError("GEDCOM ID семьи обязателен")

    @staticmethod
    def _deduplicate_relatives(rows, exclude_references=None):
        seen = set()
        normalized = []
        exclude_references = set(exclude_references or [])
        for last_name, first_name, person_reference in rows or []:
            if not person_reference or person_reference in exclude_references:
                continue
            if person_reference in seen:
                continue
            seen.add(person_reference)
            normalized.append((last_name, first_name, person_reference))
        return normalized

    def resolve_person_reference(self, person_reference):
        if person_reference is None:
            return None
        ref = str(person_reference).strip()
        if not ref:
            return None
        if ref.isdigit():
            person = self.get_person(int(ref))
            return int(ref) if person else None
        person = self.get_person_by_gedcom_id(ref)
        if person:
            raw_id = person[0]
            if isinstance(raw_id, int):
                return raw_id
            as_text = str(raw_id).strip()
            return int(as_text) if as_text.isdigit() else as_text
        return None

    def _expand_person_references(self, person_reference):
        if person_reference is None:
            return set()
        ref = str(person_reference).strip()
        if not ref:
            return set()

        references = {ref}
        if ref.isdigit():
            person = self.get_person(int(ref))
            if person and person[0]:
                references.add(str(int(ref)))
                if person[0]:
                    references.add(str(person[0]).strip())
        else:
            person = self.get_person_by_gedcom_id(ref)
            if person:
                references.add(str(person[0]))
        return {item for item in references if item}

    def _person_ref_sql(self, alias):
        return f"COALESCE(NULLIF({alias}.gedcom_id, ''), CAST({alias}.id AS TEXT))"

    def _build_in_clause(self, values):
        placeholders = ",".join("?" for _ in values)
        return placeholders, tuple(values)

    def _family_child_family_ids(self, family_id):
        if not family_id:
            return []
        family_refs = []
        gedcom_id = self._family_gedcom_id(family_id)
        for value in (gedcom_id, str(family_id).strip()):
            if value and value not in family_refs:
                family_refs.append(value)
        return family_refs

    @staticmethod
    def _family_child_join_clause(family_alias="f", child_alias="fc"):
        return f"({family_alias}.gedcom_id = {child_alias}.family_id OR CAST({family_alias}.id AS TEXT) = {child_alias}.family_id)"

    def _get_parent_ids(self, person_reference):
        references = sorted(self._expand_person_references(person_reference))
        if not references:
            return set()
        placeholders, params = self._build_in_clause(references)
        self.cur.execute(
            f"""
            SELECT DISTINCT f.husband_id, f.wife_id
            FROM families f
            JOIN family_children fc ON {self._family_child_join_clause('f')}
            WHERE fc.child_id IN ({placeholders})
            """,
            params,
        )
        parent_ids = set()
        for father_id, mother_id in self.cur.fetchall():
            if father_id:
                parent_ids.add(father_id)
            if mother_id:
                parent_ids.add(mother_id)
        return parent_ids

    def _get_child_families(self, person_reference):
        references = sorted(self._expand_person_references(person_reference))
        if not references:
            return []
        placeholders, params = self._build_in_clause(references)
        self.cur.execute(
            f"""
            SELECT f.id, f.gedcom_id, f.husband_id, f.wife_id
            FROM families f
            JOIN family_children fc ON {self._family_child_join_clause('f')}
            WHERE fc.child_id IN ({placeholders})
            ORDER BY f.id
            """,
            params,
        )
        return self.cur.fetchall()

    def _get_parent_sets(self, person_gedcom_id):
        father_ids = set()
        mother_ids = set()
        for _family_id, _family_gedcom_id, husband_id, wife_id in self._get_child_families(person_gedcom_id):
            if husband_id:
                father_ids.add(husband_id)
            if wife_id:
                mother_ids.add(wife_id)
        return father_ids, mother_ids

    def _get_biological_parent_pair(self, person_gedcom_id):
        child_families = self._get_child_families(person_gedcom_id)
        if not child_families:
            return "", ""
        return child_families[0][2] or "", child_families[0][3] or ""

    def _people_rows_by_references(self, person_references, exclude_reference=None):
        references = sorted({str(ref).strip() for ref in (person_references or []) if str(ref).strip()})
        if not references:
            return []
        placeholders, params = self._build_in_clause(references)
        person_ref_sql = self._person_ref_sql("people")
        self.cur.execute(
            f"""
            SELECT DISTINCT last_name, first_name, {person_ref_sql} AS person_reference
            FROM people
            WHERE gedcom_id IN ({placeholders})
               OR CAST(id AS TEXT) IN ({placeholders})
            ORDER BY last_name, first_name
            """,
            params + params,
        )
        rows = self.cur.fetchall()
        exclude_refs = self._expand_person_references(exclude_reference)
        return self._deduplicate_relatives(rows, exclude_references=exclude_refs)

    def get_biological_fathers(self, person_reference):
        child_families = self._get_child_families(person_reference)
        if not child_families:
            return []
        husband_id = child_families[0][2]
        return self._people_rows_by_references({husband_id}, exclude_reference=person_reference)

    def get_biological_mothers(self, person_reference):
        child_families = self._get_child_families(person_reference)
        if not child_families:
            return []
        wife_id = child_families[0][3]
        return self._people_rows_by_references({wife_id}, exclude_reference=person_reference)

    def get_adoptive_parents(self, person_reference):
        child_families = self._get_child_families(person_reference)
        if len(child_families) <= 1:
            return []
        biological_ids = set()
        primary_husband_id = child_families[0][2]
        primary_wife_id = child_families[0][3]
        if primary_husband_id:
            biological_ids.add(primary_husband_id)
        if primary_wife_id:
            biological_ids.add(primary_wife_id)

        adoptive_parent_ids = set()
        for _family_id, _family_gedcom_id, husband_id, wife_id in child_families[1:]:
            if husband_id and husband_id not in biological_ids:
                adoptive_parent_ids.add(husband_id)
            if wife_id and wife_id not in biological_ids:
                adoptive_parent_ids.add(wife_id)
        return self._people_rows_by_references(adoptive_parent_ids, exclude_reference=person_reference)

    def get_fathers(self, person_reference):
        references = sorted(self._expand_person_references(person_reference))
        if not references:
            return []
        placeholders, params = self._build_in_clause(references)
        person_ref_sql = self._person_ref_sql("p")
        self.cur.execute(
            f"""
            SELECT DISTINCT p.last_name, p.first_name, {person_ref_sql}
            FROM family_children fc
            JOIN families f ON {self._family_child_join_clause('f')}
            JOIN people p ON p.gedcom_id = f.husband_id OR CAST(p.id AS TEXT) = f.husband_id
            WHERE fc.child_id IN ({placeholders})
              AND f.husband_id != ''
            ORDER BY p.last_name, p.first_name
            """,
            params,
        )
        return self._deduplicate_relatives(self.cur.fetchall(), exclude_references=self._expand_person_references(person_reference))

    def get_mothers(self, person_reference):
        references = sorted(self._expand_person_references(person_reference))
        if not references:
            return []
        placeholders, params = self._build_in_clause(references)
        person_ref_sql = self._person_ref_sql("p")
        self.cur.execute(
            f"""
            SELECT DISTINCT p.last_name, p.first_name, {person_ref_sql}
            FROM family_children fc
                        JOIN families f ON {self._family_child_join_clause('f')}
            JOIN people p ON p.gedcom_id = f.wife_id OR CAST(p.id AS TEXT) = f.wife_id
            WHERE fc.child_id IN ({placeholders})
              AND f.wife_id != ''
            ORDER BY p.last_name, p.first_name
            """,
            params,
        )
        return self._deduplicate_relatives(self.cur.fetchall(), exclude_references=self._expand_person_references(person_reference))

    def get_grandparents(self, person_reference):
        parent_ids = sorted(self._get_parent_ids(person_reference))
        if not parent_ids:
            return []

        placeholders, params = self._build_in_clause(parent_ids)
        person_ref_sql = self._person_ref_sql("gp")
        self.cur.execute(
            f"""
            SELECT DISTINCT gp.last_name, gp.first_name, {person_ref_sql}
            FROM families pf
            JOIN family_children pfc ON {self._family_child_join_clause('pf', 'pfc')}
            JOIN people gp ON gp.gedcom_id = pf.husband_id OR gp.gedcom_id = pf.wife_id OR CAST(gp.id AS TEXT) = pf.husband_id OR CAST(gp.id AS TEXT) = pf.wife_id
            WHERE pfc.child_id IN ({placeholders})
            ORDER BY gp.last_name, gp.first_name
            """,
            params,
        )
        return self._deduplicate_relatives(self.cur.fetchall(), exclude_references=self._expand_person_references(person_reference))

    def get_grandchildren(self, person_reference):
        children = self.get_children(person_reference)
        child_ids = sorted({child[2] for child in children if child and child[2]})
        if not child_ids:
            return []

        placeholders, params = self._build_in_clause(child_ids)
        person_ref_sql = self._person_ref_sql("gc")
        self.cur.execute(
            f"""
            SELECT DISTINCT gc.last_name, gc.first_name, {person_ref_sql}
            FROM families f
            JOIN family_children fc ON {self._family_child_join_clause('f')}
            JOIN people gc ON gc.gedcom_id = fc.child_id OR CAST(gc.id AS TEXT) = fc.child_id
            WHERE f.husband_id IN ({placeholders})
               OR f.wife_id IN ({placeholders})
            ORDER BY gc.last_name, gc.first_name
            """,
            params + params,
        )
        return self._deduplicate_relatives(self.cur.fetchall(), exclude_references=self._expand_person_references(person_reference))

    def get_full_siblings(self, person_reference):
        father_id, mother_id = self._get_biological_parent_pair(person_reference)
        if not father_id or not mother_id:
            return []

        exclude_refs = sorted(self._expand_person_references(person_reference))
        if not exclude_refs:
            return []
        exclude_placeholders, exclude_params = self._build_in_clause(exclude_refs)

        self.cur.execute(
            f"""
            SELECT DISTINCT COALESCE(NULLIF(c.gedcom_id, ''), CAST(c.id AS TEXT))
            FROM families f
                        JOIN family_children fc ON {self._family_child_join_clause('f')}
            JOIN people c ON c.gedcom_id = fc.child_id OR CAST(c.id AS TEXT) = fc.child_id
            WHERE f.husband_id = ?
              AND f.wife_id = ?
              AND COALESCE(NULLIF(c.gedcom_id, ''), CAST(c.id AS TEXT)) NOT IN ({exclude_placeholders})
            """,
            (father_id, mother_id, *exclude_params),
        )
        full_sibling_ids = {row[0] for row in self.cur.fetchall() if row[0]}
        return self._people_rows_by_references(full_sibling_ids, exclude_reference=person_reference)

    def get_half_siblings_paternal(self, person_reference):
        father_id, mother_id = self._get_biological_parent_pair(person_reference)
        if not father_id:
            return []
        siblings = self.get_siblings(person_reference)
        half_paternal_ids = set()
        for _last_name, _first_name, sibling_id in siblings:
            sibling_father, sibling_mother = self._get_biological_parent_pair(sibling_id)
            shared_father = bool(sibling_father and sibling_father == father_id)
            shared_mother = bool(mother_id and sibling_mother and sibling_mother == mother_id)
            if shared_father and not shared_mother:
                half_paternal_ids.add(sibling_id)
        return self._people_rows_by_references(half_paternal_ids, exclude_reference=person_reference)

    def get_half_siblings_maternal(self, person_reference):
        father_id, mother_id = self._get_biological_parent_pair(person_reference)
        if not mother_id:
            return []
        siblings = self.get_siblings(person_reference)
        half_maternal_ids = set()
        for _last_name, _first_name, sibling_id in siblings:
            sibling_father, sibling_mother = self._get_biological_parent_pair(sibling_id)
            shared_father = bool(father_id and sibling_father and sibling_father == father_id)
            shared_mother = bool(sibling_mother and sibling_mother == mother_id)
            if shared_mother and not shared_father:
                half_maternal_ids.add(sibling_id)
        return self._people_rows_by_references(half_maternal_ids, exclude_reference=person_reference)

    def get_uncles_aunts(self, person_reference):
        parent_ids = self._get_parent_ids(person_reference)
        if not parent_ids:
            return []
        relative_ids = set()
        for parent_id in sorted(parent_ids):
            for _last_name, _first_name, sibling_id in self.get_siblings(parent_id):
                if sibling_id and sibling_id != person_reference and sibling_id not in parent_ids:
                    relative_ids.add(sibling_id)
        return self._people_rows_by_references(relative_ids, exclude_reference=person_reference)

    def get_nephews_nieces(self, person_reference):
        sibling_ids = {row[2] for row in self.get_siblings(person_reference)}
        if not sibling_ids:
            return []
        relative_ids = set()
        for sibling_id in sorted(sibling_ids):
            for _last_name, _first_name, child_id in self.get_children(sibling_id):
                if child_id and child_id != person_reference:
                    relative_ids.add(child_id)
        return self._people_rows_by_references(relative_ids, exclude_reference=person_reference)

    def get_first_cousins(self, person_reference):
        uncle_aunt_ids = {row[2] for row in self.get_uncles_aunts(person_reference)}
        if not uncle_aunt_ids:
            return []
        relative_ids = set()
        for relative_id in sorted(uncle_aunt_ids):
            for _last_name, _first_name, child_id in self.get_children(relative_id):
                if child_id and child_id != person_reference:
                    relative_ids.add(child_id)
        return self._people_rows_by_references(relative_ids, exclude_reference=person_reference)

    def find_duplicate_candidates(self):
        self.cur.execute(
            """
            SELECT id, gedcom_id, first_name, last_name, birth_date, death_date
            FROM people
            ORDER BY last_name, first_name
            """
        )
        people = self.cur.fetchall()

        candidates = []
        for index, left in enumerate(people):
            for right in people[index + 1:]:
                score = self._score_duplicate_pair(left, right)
                if score >= 0.75:
                    candidates.append({
                        "left_id": left[0],
                        "right_id": right[0],
                        "left_gedcom_id": left[1],
                        "right_gedcom_id": right[1],
                        "left_name": self._format_name(left[2], left[3]),
                        "right_name": self._format_name(right[2], right[3]),
                        "left_birth": left[4],
                        "right_birth": right[4],
                        "left_death": left[5],
                        "right_death": right[5],
                        "confidence": round(score, 2),
                    })
                    # also include the reverse direction to surface both sides
                    candidates.append({
                        "left_id": right[0],
                        "right_id": left[0],
                        "left_gedcom_id": right[1],
                        "right_gedcom_id": left[1],
                        "left_name": self._format_name(right[2], right[3]),
                        "right_name": self._format_name(left[2], left[3]),
                        "left_birth": right[4],
                        "right_birth": left[4],
                        "left_death": right[5],
                        "right_death": left[5],
                        "confidence": round(score, 2),
                    })

        candidates.sort(key=lambda item: item["confidence"], reverse=True)
        return candidates

    def _score_duplicate_pair(self, left, right):
        first_score = self._compare_field(left[2], right[2])
        last_score = self._compare_field(left[3], right[3])
        birth_score = self._compare_field(left[4], right[4])
        death_score = self._compare_field(left[5], right[5])

        weighted = (
            0.35 * first_score +
            0.35 * last_score +
            0.15 * birth_score +
            0.15 * death_score
        )
        return min(1.0, max(0.0, weighted))

    @staticmethod
    def _compare_field(left, right):
        # use date normalizer for fields containing digits (likely dates)
        if left and re.search(r"\d", str(left)) or right and re.search(r"\d", str(right)):
            normalized_left = _normalize_date(left)
            normalized_right = _normalize_date(right)
        else:
            normalized_left = _normalize_text(left)
            normalized_right = _normalize_text(right)
        if not normalized_left and not normalized_right:
            return 0.0
        if not normalized_left or not normalized_right:
            return 0.0
        if normalized_left == normalized_right:
            return 1.0
        if normalized_left.startswith(normalized_right) or normalized_right.startswith(normalized_left):
            return 0.9
        if normalized_left[:3] == normalized_right[:3] and len(normalized_left) > 2 and len(normalized_right) > 2:
            return 0.8
        return 0.0

    @staticmethod
    def _format_name(first_name, last_name):
        return f"{last_name or ''} {first_name or ''}".strip()

    def get_parents(self, person_reference):
        references = sorted(self._expand_person_references(person_reference))
        if not references:
            return []
        placeholders, params = self._build_in_clause(references)
        person_ref_sql = self._person_ref_sql("p")

        self.cur.execute(
            f"""
            SELECT DISTINCT p.last_name, p.first_name, {person_ref_sql}
            FROM family_children fc
                        JOIN families f ON {self._family_child_join_clause('f')}
            JOIN people p
              ON p.gedcom_id = f.husband_id
              OR p.gedcom_id = f.wife_id
              OR CAST(p.id AS TEXT) = f.husband_id
              OR CAST(p.id AS TEXT) = f.wife_id
            WHERE fc.child_id IN ({placeholders})
            ORDER BY p.last_name, p.first_name
            """,
            params,
        )
        return self._deduplicate_relatives(self.cur.fetchall(), exclude_references=self._expand_person_references(person_reference))

    def get_spouses(self, person_reference):
        references = sorted(self._expand_person_references(person_reference))
        if not references:
            return []
        placeholders, params = self._build_in_clause(references)
        person_ref_sql = self._person_ref_sql("p")

        self.cur.execute(
            f"""
            SELECT DISTINCT p.last_name, p.first_name, {person_ref_sql}
            FROM families f
            JOIN people p
              ON (
                    f.husband_id IN ({placeholders})
                AND (p.gedcom_id = f.wife_id OR CAST(p.id AS TEXT) = f.wife_id)
              )
              OR (
                    f.wife_id IN ({placeholders})
                AND (p.gedcom_id = f.husband_id OR CAST(p.id AS TEXT) = f.husband_id)
              )
            WHERE f.husband_id IN ({placeholders}) OR f.wife_id IN ({placeholders})
            ORDER BY p.last_name, p.first_name
            """,
            params + params + params + params,
        )
        return self._deduplicate_relatives(self.cur.fetchall(), exclude_references=self._expand_person_references(person_reference))

    def get_children(self, person_reference):
        references = sorted(self._expand_person_references(person_reference))
        if not references:
            return []
        placeholders, params = self._build_in_clause(references)
        person_ref_sql = self._person_ref_sql("c")

        self.cur.execute(
            f"""
            SELECT DISTINCT c.last_name, c.first_name, {person_ref_sql}
            FROM families f
            JOIN family_children fc ON {self._family_child_join_clause('f')}
            JOIN people c ON c.gedcom_id = fc.child_id OR CAST(c.id AS TEXT) = fc.child_id
            WHERE f.husband_id IN ({placeholders}) OR f.wife_id IN ({placeholders})
            ORDER BY c.last_name, c.first_name
            """,
            params + params,
        )
        return self._deduplicate_relatives(self.cur.fetchall(), exclude_references=self._expand_person_references(person_reference))

    def get_siblings(self, person_reference):
        references = sorted(self._expand_person_references(person_reference))
        if not references:
            return []

        parent_ids = sorted(self._get_parent_ids(person_reference))
        if not parent_ids:
            return []

        parent_placeholders, parent_params = self._build_in_clause(parent_ids)
        self_placeholders, self_params = self._build_in_clause(references)
        person_ref_sql = self._person_ref_sql("p")
        self.cur.execute(
            f"""
            SELECT DISTINCT p.last_name, p.first_name, {person_ref_sql}
            FROM people p
            JOIN family_children fc ON p.gedcom_id = fc.child_id OR CAST(p.id AS TEXT) = fc.child_id
                        JOIN families f ON {self._family_child_join_clause('f')}
            WHERE {person_ref_sql} NOT IN ({self_placeholders})
              AND (
                    f.husband_id IN ({parent_placeholders})
                 OR f.wife_id IN ({parent_placeholders})
              )
            ORDER BY p.last_name, p.first_name
            """,
            self_params + parent_params + parent_params,
        )
        return self._deduplicate_relatives(self.cur.fetchall(), exclude_references=self._expand_person_references(person_reference))
