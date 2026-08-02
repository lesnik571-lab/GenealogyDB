"""Read-only chronological timeline across all people and available events."""

from __future__ import annotations

import csv
import html
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable

from repository.person_repository import PersonRepository
from repository.person_timeline_service import EVENT_LABELS, PersonTimelineService
from text_utils import normalize_search_text


SUPPORTED_EVENT_TYPES = (
    "birth",
    "baptism",
    "marriage",
    "divorce",
    "residence",
    "occupation",
    "military_service",
    "immigration",
    "emigration",
    "death",
    "burial",
    "custom",
)


@dataclass(frozen=True)
class TimelineFilters:
    """Filters applied to the aggregate family timeline."""

    year_from: int | None = None
    year_to: int | None = None
    event_type: str = ""
    surname: str = ""
    place: str = ""


@dataclass(frozen=True)
class FamilyTimelineEntry:
    """One display-ready event in the aggregate family timeline."""

    person_id: int
    date: str
    normalized_year: int | None
    person: str
    surname: str
    event_type: str
    event_label: str
    place: str
    age: int | None
    description: str = ""
    sort_date: date | None = None


class FamilyTimelineService:
    """Build, filter, and export one timeline for all people."""

    def __init__(self, repository: PersonRepository):
        self.repository = repository

    def build_timeline(self) -> tuple[FamilyTimelineEntry, ...]:
        people = self.repository.list_people_full()
        people_by_id = {person["id"]: person for person in people}
        birth_years = {
            person["id"]: self._normalized_year(person.get("birth_date", ""))
            for person in people
        }
        entries = []

        for person in people:
            if person.get("birth_date") or person.get("birth_place"):
                entries.append(self._entry(
                    person,
                    "birth",
                    person.get("birth_date", ""),
                    person.get("birth_place", ""),
                    "",
                    birth_years[person["id"]],
                ))
            if person.get("occupation"):
                entries.append(self._entry(
                    person,
                    "occupation",
                    "",
                    "",
                    person.get("occupation", ""),
                    birth_years[person["id"]],
                ))
            if person.get("death_date") or person.get("death_place"):
                entries.append(self._entry(
                    person,
                    "death",
                    person.get("death_date", ""),
                    person.get("death_place", ""),
                    "",
                    birth_years[person["id"]],
                ))

        for event in self.repository.list_all_person_events():
            person = people_by_id.get(event.get("person_id"))
            if person is None:
                continue
            event_type = (event.get("event_type") or "custom").strip() or "custom"
            entries.append(self._entry(
                person,
                event_type,
                event.get("date", ""),
                event.get("place", ""),
                event.get("description", ""),
                birth_years[person["id"]],
            ))

        entries.sort(key=self._sort_key)
        return tuple(entries)

    def filter_timeline(
        self,
        entries: Iterable[FamilyTimelineEntry],
        filters: TimelineFilters,
    ) -> tuple[FamilyTimelineEntry, ...]:
        surname = normalize_search_text(filters.surname)
        place = normalize_search_text(filters.place)
        event_type = normalize_search_text(filters.event_type)
        visible = []
        for entry in entries:
            year = entry.normalized_year
            if filters.year_from is not None and (year is None or year < filters.year_from):
                continue
            if filters.year_to is not None and (year is None or year > filters.year_to):
                continue
            if event_type and event_type not in {
                normalize_search_text(entry.event_type),
                normalize_search_text(entry.event_label),
            }:
                continue
            if surname and surname not in normalize_search_text(entry.surname):
                continue
            if place and place not in normalize_search_text(entry.place):
                continue
            visible.append(entry)
        return tuple(visible)

    def export_csv(
        self,
        entries: Iterable[FamilyTimelineEntry],
        destination_path: str | Path,
    ) -> Path:
        destination = Path(destination_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8-sig", newline="") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(("date", "normalized_year", "person", "event_type", "place", "age"))
            for entry in entries:
                writer.writerow(self._export_row(entry))
        return destination

    def export_html(
        self,
        entries: Iterable[FamilyTimelineEntry],
        destination_path: str | Path,
    ) -> Path:
        destination = Path(destination_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        rows = "\n".join(
            "<tr>" + "".join(f"<td>{html.escape(str(value))}</td>" for value in self._export_row(entry)) + "</tr>"
            for entry in entries
        )
        document = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>GenealogyDB - Хронология</title>
<style>body{{font-family:Segoe UI,sans-serif;margin:24px}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #bbb;padding:6px;text-align:left}}th{{background:#f0f2f4}}</style>
</head>
<body>
<h1>Хронология</h1>
<table>
<thead><tr><th>Дата</th><th>Год</th><th>Человек</th><th>Событие</th><th>Место</th><th>Возраст</th></tr></thead>
<tbody>{rows}</tbody>
</table>
</body>
</html>
"""
        destination.write_text(document, encoding="utf-8")
        return destination

    @staticmethod
    def _entry(person, event_type, date_text, place, description, birth_year):
        parsed = PersonTimelineService._parse_gedcom_date(date_text)
        year = FamilyTimelineService._normalized_year(date_text) if parsed.known else None
        age = year - birth_year if year is not None and birth_year is not None and year >= birth_year else None
        person_name = " ".join(
            value for value in (person.get("last_name", ""), person.get("first_name", ""))
            if value
        ) or "Без имени"
        return FamilyTimelineEntry(
            person_id=int(person["id"]),
            date=date_text or "",
            normalized_year=year,
            person=person_name,
            surname=person.get("last_name", "") or "",
            event_type=event_type,
            event_label=EVENT_LABELS.get(event_type, event_type),
            place=place or "",
            age=age,
            description=description or "",
            sort_date=parsed.earliest,
        )

    @staticmethod
    def _normalized_year(date_text) -> int | None:
        match = re.search(r"\b(\d{4})\b", str(date_text or ""))
        return int(match.group(1)) if match else None

    @staticmethod
    def _sort_key(entry: FamilyTimelineEntry):
        return (
            entry.normalized_year is None,
            entry.sort_date or "",
            normalize_search_text(entry.person),
            normalize_search_text(entry.event_label),
        )

    @staticmethod
    def _export_row(entry: FamilyTimelineEntry):
        return (
            entry.date,
            "" if entry.normalized_year is None else entry.normalized_year,
            entry.person,
            entry.event_label,
            entry.place,
            "" if entry.age is None else entry.age,
        )