import re
import sqlite3
import unicodedata

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
        self.conn = sqlite3.connect(db_name)
        self.cur = self.conn.cursor()

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
                data.get("gedcom_id") or "",
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
        self.conn.commit()
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
        self.conn.commit()
        return self.cur.rowcount > 0

    def delete_person(self, person_id):
        if not person_id:
            return False
        person = self.get_person(person_id)
        person_gedcom_id = person[0] if person else None
        if person_gedcom_id:
            self.cur.execute("DELETE FROM family_children WHERE child_id = ?", (person_gedcom_id,))
        self.cur.execute("DELETE FROM people WHERE id = ?", (person_id,))
        self.conn.commit()
        return self.cur.rowcount > 0

    def create_family(self, data):
        self._validate_family_data(data)
        self.cur.execute(
            """
            INSERT INTO families (gedcom_id, husband_id, wife_id) VALUES (?, ?, ?)
            """,
            (data.get("gedcom_id") or "", data.get("husband") or "", data.get("wife") or ""),
        )
        family_id = self.cur.lastrowid
        family_gedcom_id = data.get("gedcom_id") or ""
        for child in data.get("children", []):
            self.cur.execute(
                "INSERT INTO family_children (family_id, child_id) VALUES (?, ?)",
                (family_gedcom_id, child),
            )
        self.conn.commit()
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
            "UPDATE families SET gedcom_id = ?, husband_id = ?, wife_id = ? WHERE id = ?",
            (family_gedcom_id, data.get("husband") or "", data.get("wife") or "", family_id),
        ).rowcount
        self.cur.execute("DELETE FROM family_children WHERE family_id = ?", (family_gedcom_id,))
        for child in data.get("children", []):
            self.cur.execute(
                "INSERT INTO family_children (family_id, child_id) VALUES (?, ?)",
                (family_gedcom_id, child),
            )
        self.conn.commit()
        return update_count > 0

    def delete_family(self, family_id):
        if not family_id:
            return False
        self.cur.execute("DELETE FROM family_children WHERE family_id = ?", (self._family_gedcom_id(family_id),))
        self.cur.execute("DELETE FROM families WHERE id = ?", (family_id,))
        self.conn.commit()
        return self.cur.rowcount > 0

    def get_family(self, family_id):
        if not family_id:
            return None
        self.cur.execute(
            "SELECT id, gedcom_id, husband_id, wife_id FROM families WHERE id = ?",
            (family_id,),
        )
        row = self.cur.fetchone()
        if not row:
            return None
        family_id, gedcom_id, husband_id, wife_id = row
        self.cur.execute("SELECT child_id FROM family_children WHERE family_id = ?", (gedcom_id,))
        children = [child_id for (child_id,) in self.cur.fetchall()]
        return {"id": family_id, "gedcom_id": gedcom_id, "husband": husband_id, "wife": wife_id, "children": children}

    def _family_gedcom_id(self, family_id):
        row = self.cur.execute("SELECT gedcom_id FROM families WHERE id = ?", (family_id,)).fetchone()
        return row[0] if row else None

    def _validate_person_data(self, data):
        if not data.get("first_name") or not data.get("last_name"):
            raise ValueError("Имя и фамилия обязательны")

    def _validate_family_data(self, data):
        if not data.get("gedcom_id"):
            raise ValueError("GEDCOM ID семьи обязателен")

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

    def get_parents(self, person_gedcom_id):
        if not person_gedcom_id:
            return []

        self.cur.execute(
            """
            SELECT DISTINCT p.last_name, p.first_name, p.gedcom_id
            FROM family_children fc
            JOIN families f ON f.gedcom_id = fc.family_id
            JOIN people p
              ON p.gedcom_id = f.husband_id
              OR p.gedcom_id = f.wife_id
            WHERE fc.child_id = ?
            ORDER BY p.last_name, p.first_name
            """,
            (person_gedcom_id,),
        )
        return self.cur.fetchall()

    def get_spouses(self, person_gedcom_id):
        if not person_gedcom_id:
            return []

        self.cur.execute(
            """
            SELECT DISTINCT p.last_name, p.first_name, p.gedcom_id
            FROM families f
            JOIN people p
              ON (f.husband_id = ? AND p.gedcom_id = f.wife_id)
              OR (f.wife_id = ? AND p.gedcom_id = f.husband_id)
            WHERE f.husband_id = ? OR f.wife_id = ?
            ORDER BY p.last_name, p.first_name
            """,
            (
                person_gedcom_id,
                person_gedcom_id,
                person_gedcom_id,
                person_gedcom_id,
            ),
        )
        return self.cur.fetchall()

    def get_children(self, person_gedcom_id):
        if not person_gedcom_id:
            return []

        self.cur.execute(
            """
            SELECT DISTINCT c.last_name, c.first_name, c.gedcom_id
            FROM families f
            JOIN family_children fc ON fc.family_id = f.gedcom_id
            JOIN people c ON c.gedcom_id = fc.child_id
            WHERE f.husband_id = ? OR f.wife_id = ?
            ORDER BY c.last_name, c.first_name
            """,
            (person_gedcom_id, person_gedcom_id),
        )
        return self.cur.fetchall()

    def get_siblings(self, person_gedcom_id):
        if not person_gedcom_id:
            return []

        self.cur.execute(
            """
            SELECT DISTINCT p.last_name, p.first_name, p.gedcom_id
            FROM people p
            JOIN family_children fc ON p.gedcom_id = fc.child_id
            JOIN families f ON f.gedcom_id = fc.family_id
            JOIN family_children parent_fc ON f.gedcom_id = parent_fc.family_id
            WHERE parent_fc.child_id = ?
              AND p.gedcom_id != ?
              AND p.gedcom_id != ''
            ORDER BY p.last_name, p.first_name
            """,
            (person_gedcom_id, person_gedcom_id),
        )
        return self.cur.fetchall()
