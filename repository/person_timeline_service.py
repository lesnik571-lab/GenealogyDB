from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from repository.person_repository import PersonRepository


MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


EVENT_LABELS = {
    "birth": "Рождение",
    "baptism": "Крещение",
    "residence": "Место жительства",
    "education": "Образование",
    "occupation": "Занятость",
    "military_service": "Военная служба",
    "marriage": "Брак",
    "divorce": "Развод",
    "immigration": "Иммиграция",
    "emigration": "Эмиграция",
    "census": "Перепись",
    "awards": "Награды",
    "custom": "Событие",
    "death": "Смерть",
    "burial": "Погребение",
}


@dataclass
class TimelineDate:
    """A sortable parsed date used by timeline entries."""
    raw: str
    known: bool
    earliest: date | None
    latest: date | None


class PersonTimelineService:
    """Build a chronological timeline for a person."""
    def __init__(self, repository: PersonRepository):
        self.repository = repository

    def build_timeline(self, person_id):
        person = self.repository.get_person(person_id)
        if not person:
            return []

        occupation = (person[8] or "").strip()
        entries = []
        sources = self.repository.list_person_sources(person_id)
        sources_by_id = {source.get("id"): source for source in sources}

        birth_date = person[4] or ""
        if birth_date.strip() or (person[5] or "").strip():
            entries.append(
                self._entry(
                    entry_id="person:birth",
                    person_id=person_id,
                    event_id=None,
                    event_type="birth",
                    event_label=EVENT_LABELS["birth"],
                    date_text=birth_date,
                    place=person[5] or "",
                    description="",
                    source=None,
                )
            )

        death_date = person[6] or ""
        if death_date.strip() or (person[7] or "").strip():
            entries.append(
                self._entry(
                    entry_id="person:death",
                    person_id=person_id,
                    event_id=None,
                    event_type="death",
                    event_label=EVENT_LABELS["death"],
                    date_text=death_date,
                    place=person[7] or "",
                    description="",
                    source=None,
                )
            )

        if occupation:
            entries.append(
                self._entry(
                    entry_id="person:occupation",
                    person_id=person_id,
                    event_id=None,
                    event_type="occupation",
                    event_label=EVENT_LABELS["occupation"],
                    date_text="",
                    place="",
                    description=occupation,
                    source=None,
                )
            )

        events = self.repository.list_person_events(person_id)
        for event in events:
            source = self._resolve_event_source(event, sources_by_id, sources)
            event_type = (event.get("event_type") or "custom").strip() or "custom"
            entries.append(
                self._entry(
                    entry_id=f"event:{event.get('id')}",
                    person_id=person_id,
                    event_id=event.get("id"),
                    event_type=event_type,
                    event_label=EVENT_LABELS.get(event_type, event_type),
                    date_text=event.get("date") or "",
                    place=event.get("place") or "",
                    description=event.get("description") or "",
                    source=source,
                )
            )

        self._mark_contradictions(entries)
        entries.sort(key=self._timeline_sort_key)
        return entries

    def export_timeline_csv(self, entries, destination_path):
        destination = Path(destination_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)

        with destination.open("w", encoding="utf-8", newline="") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(["date", "place", "event_type", "description", "source", "contradiction"])
            for entry in entries:
                writer.writerow(
                    [
                        entry.get("date_text", ""),
                        entry.get("place", ""),
                        entry.get("event_label", ""),
                        entry.get("description", ""),
                        entry.get("source_title", ""),
                        "yes" if entry.get("is_contradictory") else "",
                    ]
                )
        return destination

    def export_timeline_pdf(self, entries, destination_path):
        destination = Path(destination_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)

        lines = ["Timeline"]
        for entry in entries:
            contradiction_marker = " [!]" if entry.get("is_contradictory") else ""
            line = (
                f"{entry.get('date_text', '')} | {entry.get('place', '')} | "
                f"{entry.get('event_label', '')} | {entry.get('description', '')} | "
                f"{entry.get('source_title', '')}{contradiction_marker}"
            )
            lines.append(self._sanitize_pdf_text(line))

        pdf_bytes = self._build_simple_pdf(lines)
        destination.write_bytes(pdf_bytes)
        return destination

    def _entry(self, entry_id, person_id, event_id, event_type, event_label, date_text, place, description, source):
        parsed = self._parse_gedcom_date(date_text)
        source_title = ""
        source_id = None
        if source:
            source_title = source.get("title") or source.get("archive_reference") or ""
            source_id = source.get("id")

        return {
            "entry_id": entry_id,
            "person_id": person_id,
            "event_id": event_id,
            "event_type": event_type,
            "event_label": event_label,
            "date_text": date_text or "",
            "place": place or "",
            "description": description or "",
            "source_title": source_title,
            "source_id": source_id,
            "sort_known": parsed.known,
            "sort_earliest": parsed.earliest,
            "sort_latest": parsed.latest,
            "is_contradictory": False,
        }

    @staticmethod
    def _timeline_sort_key(entry):
        if not entry.get("sort_known"):
            return (1, 9999, 12, 31, entry.get("event_label", ""), entry.get("entry_id", ""))

        earliest = entry.get("sort_earliest")
        year = earliest.year if earliest else 9999
        month = earliest.month if earliest else 12
        day = earliest.day if earliest else 31
        return (0, year, month, day, entry.get("event_label", ""), entry.get("entry_id", ""))

    def _mark_contradictions(self, entries):
        birth_bound = None
        death_bound = None

        for entry in entries:
            if entry.get("event_type") == "birth" and entry.get("sort_known"):
                current = entry.get("sort_earliest")
                if current and (birth_bound is None or current < birth_bound):
                    birth_bound = current

            if entry.get("event_type") == "death" and entry.get("sort_known"):
                current = entry.get("sort_latest")
                if current and (death_bound is None or current > death_bound):
                    death_bound = current

        for entry in entries:
            if not entry.get("sort_known"):
                continue

            earliest = entry.get("sort_earliest")
            latest = entry.get("sort_latest")
            event_type = entry.get("event_type")

            if earliest and birth_bound and event_type not in {"birth"} and earliest < birth_bound:
                entry["is_contradictory"] = True

            if latest and death_bound and event_type not in {"death", "burial"} and latest > death_bound:
                entry["is_contradictory"] = True

            if birth_bound and death_bound and death_bound < birth_bound and event_type in {"birth", "death"}:
                entry["is_contradictory"] = True

    def _resolve_event_source(self, event, sources_by_id, sources):
        description = event.get("description") or ""
        match = re.search(r"(?:SRC|SOURCE)\s*[:#]\s*(\d+)", description, flags=re.IGNORECASE)
        if match:
            source_id = int(match.group(1))
            source = sources_by_id.get(source_id)
            if source:
                return source

        if len(sources) == 1:
            return sources[0]

        description_lower = description.lower()
        for source in sources:
            title = (source.get("title") or "").strip()
            if title and title.lower() in description_lower:
                return source
        return None

    @staticmethod
    def _parse_gedcom_date(raw):
        value = (raw or "").strip()
        if not value:
            return TimelineDate(raw=value, known=False, earliest=None, latest=None)

        upper = value.upper()

        match = re.search(r"\bBET\s+(.+)\s+AND\s+(.+)\b", upper)
        if match:
            left = PersonTimelineService._parse_single_date(match.group(1))
            right = PersonTimelineService._parse_single_date(match.group(2), end_of_period=True)
            if left and right:
                return TimelineDate(raw=value, known=True, earliest=left[0], latest=right[1])

        match = re.search(r"\bFROM\s+(.+)\s+TO\s+(.+)\b", upper)
        if match:
            left = PersonTimelineService._parse_single_date(match.group(1))
            right = PersonTimelineService._parse_single_date(match.group(2), end_of_period=True)
            if left and right:
                return TimelineDate(raw=value, known=True, earliest=left[0], latest=right[1])

        for marker in ("ABT", "ABOUT", "CAL", "EST"):
            if upper.startswith(f"{marker} "):
                part = upper[len(marker):].strip()
                parsed = PersonTimelineService._parse_single_date(part)
                if parsed:
                    return TimelineDate(raw=value, known=True, earliest=parsed[0], latest=parsed[1])

        if upper.startswith("BEF "):
            part = upper[4:].strip()
            parsed = PersonTimelineService._parse_single_date(part)
            if parsed:
                earliest = date(1, 1, 1)
                latest = parsed[0]
                return TimelineDate(raw=value, known=True, earliest=earliest, latest=latest)

        if upper.startswith("AFT "):
            part = upper[4:].strip()
            parsed = PersonTimelineService._parse_single_date(part, end_of_period=True)
            if parsed:
                earliest = parsed[1]
                latest = date(9999, 12, 31)
                return TimelineDate(raw=value, known=True, earliest=earliest, latest=latest)

        parsed = PersonTimelineService._parse_single_date(upper)
        if parsed:
            return TimelineDate(raw=value, known=True, earliest=parsed[0], latest=parsed[1])

        year_match = re.search(r"\b(\d{4})\b", upper)
        if year_match:
            year = int(year_match.group(1))
            return TimelineDate(raw=value, known=True, earliest=date(year, 1, 1), latest=date(year, 12, 31))

        return TimelineDate(raw=value, known=False, earliest=None, latest=None)

    @staticmethod
    def _parse_single_date(value, end_of_period=False):
        clean = (value or "").strip()
        if not clean:
            return None

        numeric_match = re.fullmatch(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})", clean)
        if numeric_match:
            day = int(numeric_match.group(1))
            month = int(numeric_match.group(2))
            year = int(numeric_match.group(3))
            try:
                parsed_date = date(year, month, day)
            except ValueError:
                return None
            return (parsed_date, parsed_date)

        full_match = re.search(r"\b(\d{1,2})\s+([A-Z]{3})\s+(\d{4})\b", clean)
        if full_match:
            day = int(full_match.group(1))
            month = MONTHS.get(full_match.group(2))
            year = int(full_match.group(3))
            if month is None:
                return None
            try:
                parsed_date = date(year, month, day)
            except ValueError:
                return None
            return (parsed_date, parsed_date)

        month_match = re.search(r"\b([A-Z]{3})\s+(\d{4})\b", clean)
        if month_match:
            month = MONTHS.get(month_match.group(1))
            year = int(month_match.group(2))
            if month is None:
                return None
            if end_of_period:
                day = PersonTimelineService._days_in_month(year, month)
                return (date(year, month, 1), date(year, month, day))
            return (date(year, month, 1), date(year, month, PersonTimelineService._days_in_month(year, month)))

        year_match = re.search(r"\b(\d{4})\b", clean)
        if year_match:
            year = int(year_match.group(1))
            return (date(year, 1, 1), date(year, 12, 31))

        return None

    @staticmethod
    def _days_in_month(year, month):
        if month == 2:
            leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
            return 29 if leap else 28
        if month in {1, 3, 5, 7, 8, 10, 12}:
            return 31
        return 30

    @staticmethod
    def _sanitize_pdf_text(text):
        clean = (text or "").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        return clean.encode("latin-1", errors="replace").decode("latin-1")

    @staticmethod
    def _build_simple_pdf(lines):
        page_width = 595
        page_height = 842
        margin_left = 40
        margin_top = 40
        line_height = 14
        usable_lines = max(1, (page_height - margin_top * 2) // line_height)

        pages = []
        for index in range(0, len(lines), usable_lines):
            pages.append(lines[index:index + usable_lines])

        objects = []

        def add_object(content):
            objects.append(content)
            return len(objects)

        page_object_ids = []
        font_id = add_object("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

        for page_lines in pages:
            text_lines = ["BT", f"/F1 10 Tf", f"{margin_left} {page_height - margin_top} Td"]
            for line_index, line in enumerate(page_lines):
                if line_index == 0:
                    text_lines.append(f"({line}) Tj")
                else:
                    text_lines.append(f"0 -{line_height} Td ({line}) Tj")
            text_lines.append("ET")
            stream = "\n".join(text_lines)
            content_stream = f"<< /Length {len(stream.encode('latin-1'))} >>\nstream\n{stream}\nendstream"
            content_id = add_object(content_stream)
            page_obj = (
                "<< /Type /Page /Parent 0 0 R "
                f"/MediaBox [0 0 {page_width} {page_height}] "
                f"/Contents {content_id} 0 R /Resources << /Font << /F1 {font_id} 0 R >> >> >>"
            )
            page_object_ids.append(add_object(page_obj))

        kids = " ".join(f"{obj_id} 0 R" for obj_id in page_object_ids)
        pages_id = add_object(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_object_ids)} >>")
        catalog_id = add_object(f"<< /Type /Catalog /Pages {pages_id} 0 R >>")

        for page_obj_id in page_object_ids:
            page_content = objects[page_obj_id - 1]
            objects[page_obj_id - 1] = page_content.replace("/Parent 0 0 R", f"/Parent {pages_id} 0 R")

        pdf_parts = [b"%PDF-1.4\n"]
        offsets = [0]
        current_offset = len(pdf_parts[0])
        for idx, content in enumerate(objects, start=1):
            encoded = f"{idx} 0 obj\n{content}\nendobj\n".encode("latin-1")
            offsets.append(current_offset)
            pdf_parts.append(encoded)
            current_offset += len(encoded)

        xref_start = current_offset
        xref_lines = [f"0 {len(objects) + 1}", "0000000000 65535 f "]
        for offset in offsets[1:]:
            xref_lines.append(f"{offset:010d} 00000 n ")

        xref = ("xref\n" + "\n".join(xref_lines) + "\n").encode("latin-1")
        trailer = (
            f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\n"
            f"startxref\n{xref_start}\n%%EOF\n"
        ).encode("latin-1")

        pdf_parts.append(xref)
        pdf_parts.append(trailer)
        return b"".join(pdf_parts)
