from __future__ import annotations

import csv
import json
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any, Mapping

from repository.person_repository import PersonRepository
from text_utils import normalize_search_text


@dataclass(frozen=True)
class AdvancedSearchFilters:
    """Structured criteria for an advanced person search."""
    first_name: str = ""
    last_name: str = ""
    patronymic: str = ""
    sex: str = ""
    birth_year_from: int | None = None
    birth_year_to: int | None = None
    death_year_from: int | None = None
    death_year_to: int | None = None
    birth_place: str = ""
    death_place: str = ""
    occupation: str = ""
    note_contains: str = ""
    gedcom_id: str = ""
    database_id: int | None = None
    has_parents: bool = False
    has_spouses: bool = False
    has_children: bool = False
    has_events: bool = False
    has_attachments: bool = False


@dataclass(frozen=True)
class AdvancedSearchResult:
    """A person matched by advanced search with display metadata."""
    database_id: int
    gedcom_id: str
    first_name: str
    last_name: str
    sex: str
    birth_date: str
    birth_place: str
    death_date: str
    death_place: str
    occupation: str
    note: str

    @property
    def display_name(self) -> str:
        return " ".join(value for value in (self.first_name, self.last_name) if value)


class AdvancedSearchService:
    """Apply read-only person filters using repository APIs."""

    def __init__(self, repository: PersonRepository, state_path: str | Path | None = None) -> None:
        self.repository = repository
        self.state_path = Path(state_path) if state_path is not None else None

    def search(self, filters: AdvancedSearchFilters) -> tuple[AdvancedSearchResult, ...]:
        """Return deterministic results matching every active filter."""
        self._validate_ranges(filters)
        people = self.repository.list_people_full()
        relation_flags = (
            self._relation_flags(people)
            if filters.has_parents or filters.has_spouses or filters.has_children
            else {}
        )
        event_person_ids = self._event_person_ids() if filters.has_events else set()
        attachment_person_ids = (
            {int(person["id"]) for person in people if self.repository.list_person_media(person["id"])}
            if filters.has_attachments else set()
        )
        results = []
        for person in people:
            person_id = int(person["id"])
            if not self._matches_person(person, filters):
                continue
            flags = relation_flags.get(person_id, set())
            if filters.has_parents and "parents" not in flags:
                continue
            if filters.has_spouses and "spouses" not in flags:
                continue
            if filters.has_children and "children" not in flags:
                continue
            if filters.has_events and person_id not in event_person_ids:
                continue
            if filters.has_attachments and person_id not in attachment_person_ids:
                continue
            results.append(self._to_result(person))
        return tuple(sorted(results, key=lambda item: item.database_id))

    def save_last_search(self, filters: AdvancedSearchFilters) -> Path | None:
        """Persist the last submitted filters when a state path is configured."""
        if self.state_path is None:
            return None
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(asdict(filters), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return self.state_path

    def load_last_search(self) -> AdvancedSearchFilters:
        """Load persisted filters, falling back safely when state is absent or invalid."""
        if self.state_path is None or not self.state_path.exists():
            return AdvancedSearchFilters()
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
            allowed = {field.name for field in fields(AdvancedSearchFilters)}
            values = {key: value for key, value in payload.items() if key in allowed}
            return AdvancedSearchFilters(**values)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return AdvancedSearchFilters()

    @staticmethod
    def export_csv(results: tuple[AdvancedSearchResult, ...], destination: str | Path) -> Path:
        """Export the current result list to UTF-8 CSV."""
        output = Path(destination)
        with output.open("w", encoding="utf-8", newline="") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow([
                "database_id", "gedcom_id", "first_name", "last_name", "sex",
                "birth_date", "birth_place", "death_date", "death_place",
                "occupation", "note",
            ])
            for result in results:
                writer.writerow([
                    result.database_id, result.gedcom_id, result.first_name,
                    result.last_name, result.sex, result.birth_date,
                    result.birth_place, result.death_date, result.death_place,
                    result.occupation, result.note,
                ])
        return output

    def _matches_person(
        self, person: Mapping[str, Any], filters: AdvancedSearchFilters
    ) -> bool:
        given_name, patronymic = self._split_given_name(person.get("first_name"))
        text_filters = (
            (given_name, filters.first_name),
            (person.get("last_name"), filters.last_name),
            (patronymic, filters.patronymic),
            (person.get("birth_place"), filters.birth_place),
            (person.get("death_place"), filters.death_place),
            (person.get("occupation"), filters.occupation),
            (person.get("note"), filters.note_contains),
            (person.get("gedcom_id"), filters.gedcom_id),
        )
        if any(not self._contains(value, query) for value, query in text_filters):
            return False
        if filters.sex and self._normalize(person.get("sex")) != self._normalize(filters.sex):
            return False
        if filters.database_id is not None and int(person["id"]) != filters.database_id:
            return False
        if not self._year_in_range(
            person.get("birth_date"), filters.birth_year_from, filters.birth_year_to
        ):
            return False
        return self._year_in_range(
            person.get("death_date"), filters.death_year_from, filters.death_year_to
        )

    def _relation_flags(self, people: list[Mapping[str, Any]]) -> dict[int, set[str]]:
        aliases: dict[str, int] = {}
        for person in people:
            person_id = int(person["id"])
            aliases[str(person_id)] = person_id
            gedcom_id = str(person.get("gedcom_id") or "").strip()
            if gedcom_id:
                aliases[gedcom_id] = person_id
        family_aliases: dict[str, int] = {}
        families = self.repository.list_families_raw()
        for family in families:
            family_id = int(family["id"])
            family_aliases[str(family_id)] = family_id
            gedcom_id = str(family.get("gedcom_id") or "").strip()
            if gedcom_id:
                family_aliases[gedcom_id] = family_id
        children_by_family: dict[int, set[int]] = {}
        for link in self.repository.list_family_children_raw():
            family_id = family_aliases.get(str(link.get("family_id") or "").strip())
            child_id = aliases.get(str(link.get("child_id") or "").strip())
            if family_id is not None and child_id is not None:
                children_by_family.setdefault(family_id, set()).add(child_id)
        flags: dict[int, set[str]] = {int(person["id"]): set() for person in people}
        for family in families:
            family_id = int(family["id"])
            parents = {
                aliases.get(str(family.get(field) or "").strip())
                for field in ("husband_id", "wife_id")
            } - {None}
            children = children_by_family.get(family_id, set())
            if len(parents) > 1:
                for parent_id in parents:
                    flags[parent_id].add("spouses")
            if children:
                for parent_id in parents:
                    flags[parent_id].add("children")
                for child_id in children:
                    if parents:
                        flags[child_id].add("parents")
        return flags

    def _event_person_ids(self) -> set[int]:
        if hasattr(self.repository, "list_person_events_for_integrity"):
            return {
                int(event["person_id"])
                for event in self.repository.list_person_events_for_integrity()
                if event.get("person_id") is not None
            }
        return {
            int(person["id"])
            for person in self.repository.list_people_full()
            if self.repository.list_person_events(person["id"])
        }

    @staticmethod
    def _split_given_name(value: object) -> tuple[str, str]:
        parts = str(value or "").strip().split()
        return (parts[0] if parts else "", " ".join(parts[1:]))

    @classmethod
    def _contains(cls, value: object, query: object) -> bool:
        normalized_query = cls._normalize(query)
        return not normalized_query or normalized_query in cls._normalize(value)

    @staticmethod
    def _normalize(value: object) -> str:
        return normalize_search_text(value)

    @staticmethod
    def _extract_year(value: object) -> int | None:
        match = re.search(r"(?<!\d)(\d{4})(?!\d)", str(value or ""))
        return int(match.group(1)) if match else None

    @classmethod
    def _year_in_range(
        cls, value: object, lower: int | None, upper: int | None
    ) -> bool:
        if lower is None and upper is None:
            return True
        year = cls._extract_year(value)
        if year is None:
            return False
        return (lower is None or year >= lower) and (upper is None or year <= upper)

    @staticmethod
    def _validate_ranges(filters: AdvancedSearchFilters) -> None:
        for label, lower, upper in (
            ("birth year", filters.birth_year_from, filters.birth_year_to),
            ("death year", filters.death_year_from, filters.death_year_to),
        ):
            if lower is not None and upper is not None and lower > upper:
                raise ValueError(f"Invalid {label} range: from must not exceed to.")

    @staticmethod
    def _to_result(person: Mapping[str, Any]) -> AdvancedSearchResult:
        return AdvancedSearchResult(
            database_id=int(person["id"]),
            gedcom_id=str(person.get("gedcom_id") or ""),
            first_name=str(person.get("first_name") or ""),
            last_name=str(person.get("last_name") or ""),
            sex=str(person.get("sex") or ""),
            birth_date=str(person.get("birth_date") or ""),
            birth_place=str(person.get("birth_place") or ""),
            death_date=str(person.get("death_date") or ""),
            death_place=str(person.get("death_place") or ""),
            occupation=str(person.get("occupation") or ""),
            note=str(person.get("note") or ""),
        )