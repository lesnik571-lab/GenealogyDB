"""Read-only duplicate-person detection for GenealogyDB 2.3."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
    "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r",
    "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
}


@dataclass(frozen=True)
class DuplicateCandidate:
    """One possible duplicate pair with explainable matching evidence."""

    person_id: int
    duplicate_id: int
    score: int
    reasons: tuple[str, ...]


class PersonDuplicateService:
    """Find likely duplicate people without modifying repository data."""

    def __init__(self, repository):
        self.repository = repository

    def find_candidates(self, *, min_score=80):
        threshold = max(0, min(100, int(min_score)))
        people = self.repository.list_people_full()
        candidates = []

        for index, person in enumerate(people):
            for duplicate in people[index + 1 :]:
                candidate = self._compare(person, duplicate)
                if candidate is not None and candidate.score >= threshold:
                    candidates.append(candidate)

        return tuple(
            sorted(
                candidates,
                key=lambda item: (-item.score, item.person_id, item.duplicate_id),
            )
        )

    @classmethod
    def _compare(cls, person, duplicate):
        first_name = cls._normalize_text(person.get("first_name"))
        duplicate_first_name = cls._normalize_text(duplicate.get("first_name"))
        last_name = cls._normalize_text(person.get("last_name"))
        duplicate_last_name = cls._normalize_text(duplicate.get("last_name"))

        if (
            not first_name
            or not last_name
            or first_name != duplicate_first_name
            or last_name != duplicate_last_name
        ):
            return None

        score = 60
        reasons = ["same name"]

        birth_year = cls._year(person.get("birth_date"))
        duplicate_birth_year = cls._year(duplicate.get("birth_date"))
        if birth_year and birth_year == duplicate_birth_year:
            score += 20
            reasons.append(f"same birth year: {birth_year}")

        birth_place = cls._normalize_text(person.get("birth_place"))
        duplicate_birth_place = cls._normalize_text(duplicate.get("birth_place"))
        if birth_place and birth_place == duplicate_birth_place:
            score += 15
            reasons.append("same birth place")

        sex = cls._normalize_text(person.get("sex"))
        duplicate_sex = cls._normalize_text(duplicate.get("sex"))
        if sex and sex == duplicate_sex:
            score += 5
            reasons.append("same sex")

        death_year = cls._year(person.get("death_date"))
        duplicate_death_year = cls._year(duplicate.get("death_date"))
        if death_year and death_year == duplicate_death_year:
            score += 10
            reasons.append(f"same death year: {death_year}")

        return DuplicateCandidate(
            person_id=int(person["id"]),
            duplicate_id=int(duplicate["id"]),
            score=min(score, 100),
            reasons=tuple(reasons),
        )

    @staticmethod
    def _normalize_text(value):
        text = str(value or "").strip().lower()
        text = "".join(TRANSLIT.get(char, char) for char in text)
        text = unicodedata.normalize("NFKD", text)
        text = "".join(char for char in text if not unicodedata.combining(char))
        text = re.sub(r"[^\w\s]", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _year(value):
        match = re.search(r"(?<!\d)(\d{4})(?!\d)", str(value or ""))
        return int(match.group(1)) if match else None
