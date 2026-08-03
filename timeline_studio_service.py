"""Read-only multi-lane timeline analysis, saved views, and exports."""

from __future__ import annotations

import csv
import html
import json
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable
from xml.sax.saxutils import escape

from config import DATA_DIR
from repository.person_timeline_service import EVENT_LABELS, PersonTimelineService
from text_utils import normalize_search_text


SCOPES = ("selected_person", "immediate_family", "selected_branch", "selected_people", "complete_database")
CONTEXT_FILE = "historical_context.json"


@dataclass(frozen=True)
class TimelineStudioFilters:
    year_from: int | None = None
    year_to: int | None = None
    surname: str = ""
    person: str = ""
    family: str = ""
    place: str = ""
    event_type: str = ""
    sourced: str = ""  # "sourced" / "unsourced"
    confidence: str = ""
    only_conflicts: bool = False
    text: str = ""


@dataclass(frozen=True)
class TimelineStudioEvent:
    event_id: str
    lane_id: str
    subject_type: str
    subject_id: int | None
    person_id: int | None
    family_id: int | None
    normalized_date: str
    original_date: str
    event_type: str
    event_label: str
    subject_label: str
    place: str
    description: str
    age: int | None
    source_count: int
    citation_count: int
    confidence: str
    notes_present: bool
    earliest: date | None
    latest: date | None
    conflicts: tuple[str, ...] = ()
    color: str = "#376b85"


@dataclass(frozen=True)
class TimelineLane:
    lane_id: str
    label: str
    kind: str
    collapsed: bool = False


@dataclass(frozen=True)
class TimelineStudioModel:
    scope: str
    selected_person_ids: tuple[int, ...]
    lanes: tuple[TimelineLane, ...]
    events: tuple[TimelineStudioEvent, ...]
    year_min: int | None
    year_max: int | None
    generated_at: str


@dataclass(frozen=True)
class TimelineComparison:
    people: tuple[int, ...]
    simultaneous_event_ids: tuple[str, ...]
    shared_places: tuple[str, ...]
    overlapping_residences: tuple[tuple[str, str], ...]
    age_differences: dict[str, int]


class TimelineStudioService:
    """Assemble Timeline Studio data without modifying genealogy records."""

    def __init__(self, repository, *, data_dir=None):
        self.repository = repository
        self.data_dir = Path(data_dir or DATA_DIR) / "timeline_views"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.context_path = self.data_dir / CONTEXT_FILE

    def build(self, *, scope="selected_person", selected_person_ids=(), include_historical=False,
              lane_order=(), collapsed_lane_ids=(), progress_callback=None, cancel_callback=None):
        if scope not in SCOPES:
            raise ValueError("Неподдерживаемая область хронологии")
        self._cancel(cancel_callback)
        people = self.repository.list_people_full()
        families = self.repository.list_families_raw()
        children = self.repository.list_family_children_raw()
        events = self.repository.list_all_person_events()
        citations = self.repository.list_citation_records()
        selected = self._scope_people(scope, selected_person_ids, people, families, children)
        if progress_callback:
            progress_callback("Загрузка событий", 1, 3)
        self._cancel(cancel_callback)
        model_events = self._events(people, families, children, events, citations, selected)
        if include_historical:
            model_events.extend(self._historical_events())
        if progress_callback:
            progress_callback("Проверка конфликтов", 2, 3)
        model_events = self._with_conflicts(model_events, people, families, children)
        self._cancel(cancel_callback)
        lanes = self._lanes(model_events, lane_order, collapsed_lane_ids)
        visible = tuple(event for event in model_events if event.lane_id not in set(collapsed_lane_ids))
        known_years = [event.earliest.year for event in visible if event.earliest]
        if progress_callback:
            progress_callback("Хронология готова", 3, 3)
        return TimelineStudioModel(scope, tuple(sorted(selected)), lanes, tuple(sorted(visible, key=self._event_key)), min(known_years, default=None), max(known_years, default=None), self._timestamp())

    def filter(self, model: TimelineStudioModel, filters: TimelineStudioFilters):
        def contains(value, query): return not query or normalize_search_text(query) in normalize_search_text(value)
        result = []
        for event in model.events:
            year = event.earliest.year if event.earliest else None
            if filters.year_from is not None and (year is None or year < filters.year_from): continue
            if filters.year_to is not None and (year is None or year > filters.year_to): continue
            if filters.surname and not contains(event.subject_label.split(",", 1)[0], filters.surname): continue
            if filters.person and not contains(event.subject_label, filters.person): continue
            if filters.family and not contains(event.subject_label, filters.family): continue
            if filters.place and not contains(event.place, filters.place): continue
            if filters.event_type and not (contains(event.event_type, filters.event_type) or contains(event.event_label, filters.event_type)): continue
            if filters.sourced == "sourced" and not event.source_count: continue
            if filters.sourced == "unsourced" and event.source_count: continue
            if filters.confidence and normalize_search_text(event.confidence) != normalize_search_text(filters.confidence): continue
            if filters.only_conflicts and not event.conflicts: continue
            if filters.text and not any(contains(value, filters.text) for value in (event.description, event.place, event.subject_label, event.event_label, event.original_date)): continue
            result.append(event)
        return tuple(result)

    def compare(self, model: TimelineStudioModel, person_ids: Iterable[int]) -> TimelineComparison:
        people = tuple(sorted({int(person_id) for person_id in person_ids}))
        if not 2 <= len(people) <= 10:
            raise ValueError("Для сравнения выберите от 2 до 10 людей")
        visible = [event for event in model.events if event.person_id in people and event.earliest]
        by_day = defaultdict(list)
        by_place = defaultdict(set)
        for event in visible:
            by_day[event.earliest].append(event)
            if event.place: by_place[normalize_search_text(event.place)].add(event.person_id)
        simultaneous = tuple(sorted(event.event_id for group in by_day.values() if len({event.person_id for event in group}) > 1 for event in group))
        shared_places = tuple(sorted(place for place, ids in by_place.items() if len(ids) > 1))
        residences = [event for event in visible if event.event_type == "residence"]
        overlapping = tuple(sorted((left.event_id, right.event_id) for index, left in enumerate(residences) for right in residences[index + 1:] if left.person_id != right.person_id and self._overlaps(left, right)))
        births = {event.person_id: event.earliest.year for event in visible if event.event_type == "birth" and event.person_id is not None}
        baseline = births.get(people[0])
        differences = {str(person_id): births[person_id] - baseline for person_id in people[1:] if baseline is not None and person_id in births}
        return TimelineComparison(people, simultaneous, shared_places, overlapping, differences)

    def time_scale(self, events):
        exact = any(event.earliest and event.latest == event.earliest for event in events)
        years = [event.earliest.year for event in events if event.earliest]
        span = max(years) - min(years) if len(years) > 1 else 0
        return "days" if exact and span <= 1 else "months" if exact and span <= 10 else "years" if span <= 100 else "decades" if span <= 1000 else "centuries"

    def save_view(self, name, configuration):
        path = self._view_path(name)
        payload = {"name": str(name).strip(), "configuration": configuration, "updated_at": self._timestamp()}
        if not payload["name"]: raise ValueError("Название представления обязательно")
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def list_views(self):
        return tuple(sorted((json.loads(path.read_text(encoding="utf-8")) for path in self.data_dir.glob("*.json") if path.name != CONTEXT_FILE), key=lambda item: item["name"].casefold()))

    def load_view(self, name): return json.loads(self._view_path(name).read_text(encoding="utf-8"))
    def rename_view(self, old_name, new_name):
        payload = self.load_view(old_name); self.delete_view(old_name); self.save_view(new_name, payload["configuration"])
    def duplicate_view(self, name, duplicate_name): self.save_view(duplicate_name, self.load_view(name)["configuration"])
    def delete_view(self, name): self._view_path(name).unlink(missing_ok=True)
    def export_view(self, name, destination):
        Path(destination).write_text(json.dumps(self.load_view(name), ensure_ascii=False, indent=2), encoding="utf-8"); return Path(destination)
    def import_view(self, source):
        payload = json.loads(Path(source).read_text(encoding="utf-8")); return self.save_view(payload["name"], payload["configuration"])

    def list_historical_events(self): return tuple(self._load_context())
    def save_historical_event(self, event):
        events = self._load_context(); event = {**event, "id": event.get("id") or f"historical-{len(events) + 1}"}; events = [item for item in events if item["id"] != event["id"]] + [event]
        self.context_path.write_text(json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8"); return event["id"]
    def delete_historical_event(self, event_id):
        self.context_path.write_text(json.dumps([item for item in self._load_context() if item.get("id") != event_id], ensure_ascii=False, indent=2), encoding="utf-8")

    def export(self, model, events, destination, export_format, *, title="Хронология 2.0", filters=None, scale=None):
        destination = Path(destination); destination.parent.mkdir(parents=True, exist_ok=True)
        metadata = {"title": title, "scope": model.scope, "scale": scale or self.time_scale(events), "filters": asdict(filters) if filters else {}, "lanes": [asdict(lane) for lane in model.lanes], "generated_at": self._timestamp()}
        if export_format == "csv":
            with destination.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=("normalized_date", "original_date", "event_type", "person_family", "place", "age", "source_count", "citation_count", "confidence", "notes", "conflicts")); writer.writeheader()
                writer.writerows({"normalized_date": event.normalized_date, "original_date": event.original_date, "event_type": event.event_label, "person_family": event.subject_label, "place": event.place, "age": event.age if event.age is not None else "", "source_count": event.source_count, "citation_count": event.citation_count, "confidence": event.confidence, "notes": event.notes_present, "conflicts": "; ".join(event.conflicts)} for event in events)
        elif export_format == "html": self._export_html(destination, events, metadata)
        elif export_format == "svg": self._export_svg(destination, events, metadata)
        elif export_format == "png": self._export_png(destination, events, metadata)
        elif export_format == "pdf": self._export_pdf(destination, events, metadata)
        else: raise ValueError("Неподдерживаемый формат экспорта")
        return destination

    def _events(self, people, families, children, raw_events, citations, selected):
        people_by_id = {person["id"]: person for person in people}; citations_by_target = defaultdict(list)
        person_source_counts = {person_id: len(self.repository.list_person_sources(person_id)) for person_id in selected}
        for citation in citations: citations_by_target[(citation["target_type"], str(citation["target_id"]))].append(citation)
        result = []
        for person in people:
            if person["id"] not in selected: continue
            label = self._person_label(person); birth = PersonTimelineService._parse_gedcom_date(person["birth_date"])
            for event_type, raw_date, place, description, event_id in (("birth", person["birth_date"], person["birth_place"], person["note"], f"person:{person['id']}:birth"), ("occupation", "", "", person["occupation"], f"person:{person['id']}:occupation"), ("death", person["death_date"], person["death_place"], person["note"], f"person:{person['id']}:death")):
                if raw_date or place or description: result.append(self._event(event_id, f"person:{person['id']}", "person", person["id"], None, event_type, raw_date, place, description, label, birth, citations_by_target.get(("person", str(person["id"])), ()), person_source_counts[person["id"]]))
        for raw in raw_events:
            if raw["person_id"] in selected:
                person = people_by_id.get(raw["person_id"]); birth = PersonTimelineService._parse_gedcom_date(person["birth_date"] if person else "")
                result.append(self._event(f"event:{raw['id']}", f"person:{raw['person_id']}", "person", raw["person_id"], None, raw["event_type"] or "custom", raw["date"], raw["place"], raw["description"], self._person_label(person), birth, citations_by_target.get(("event", str(raw["id"])), ()), person_source_counts.get(raw["person_id"], 0)))
        aliases = self._aliases(people)
        for family in families:
            member_ids = {aliases.get(str(family[key] or "")) for key in ("husband_id", "wife_id")} - {None}
            if member_ids & selected:
                label = "Семья: " + " / ".join(self._person_label(people_by_id[item]) for item in sorted(member_ids))
                event_type = "marriage" if family["relationship_type"] in {"marriage", "exclusive"} else "family"
                result.append(self._event(f"family:{family['id']}", f"family:{family['id']}", "family", None, family["id"], event_type, "", "", family["relationship_type"], label, None, citations_by_target.get(("family", str(family["id"])), ())))
        return result

    def _event(self, event_id, lane_id, subject_type, person_id, family_id, event_type, raw_date, place, description, label, birth, citations, direct_source_count=0):
        parsed = PersonTimelineService._parse_gedcom_date(raw_date); age = parsed.earliest.year - birth.earliest.year if parsed.earliest and birth and birth.earliest and parsed.earliest.year >= birth.earliest.year else None
        confidence = self._confidence(citations)
        return TimelineStudioEvent(event_id, lane_id, subject_type, person_id if subject_type == "person" else family_id, person_id, family_id, parsed.earliest.isoformat() if parsed.earliest else "", raw_date or "", event_type, EVENT_LABELS.get(event_type, event_type), label, place or "", description or "", age, len({citation["source_id"] for citation in citations}) + direct_source_count, len(citations), confidence, bool(description), parsed.earliest, parsed.latest, (), self._color(event_type))

    def _with_conflicts(self, events, people, families, children):
        conflicts = defaultdict(list); people_by_id = {person["id"]: person for person in people}; birth_death = defaultdict(dict)
        for event in events:
            if event.person_id and event.event_type in {"birth", "death"} and event.earliest: birth_death[event.person_id][event.event_type] = event.earliest
        for event in events:
            if not event.person_id or not event.earliest: continue
            bounds = birth_death[event.person_id]
            if event.event_type != "birth" and bounds.get("birth") and event.earliest < bounds["birth"]: conflicts[event.event_id].append("event_before_birth")
            if event.event_type not in {"death", "burial"} and bounds.get("death") and event.earliest > bounds["death"]: conflicts[event.event_id].append("event_after_death")
            if event.event_type == "marriage" and event.age is not None and event.age < 14: conflicts[event.event_id].append("marriage_before_plausible_age")
            if bounds.get("birth") and bounds.get("death") and bounds["death"] < bounds["birth"] and event.event_type in {"birth", "death"}: conflicts[event.event_id].append("contradictory_birth_death")
        grouped = defaultdict(list)
        for event in events: grouped[(event.person_id, event.event_type, event.original_date, event.place, event.description)].append(event)
        for group in grouped.values():
            if group[0].person_id and len(group) > 1:
                for event in group: conflicts[event.event_id].append("duplicate_event")
        residences = defaultdict(list)
        for event in events:
            if event.person_id and event.event_type == "residence" and event.earliest: residences[(event.person_id, event.earliest)].append(event)
        for group in residences.values():
            if len({event.place for event in group}) > 1:
                for event in group: conflicts[event.event_id].append("simultaneous_incompatible_residences")
        aliases = self._aliases(people)
        births = {person_id: bounds.get("birth") for person_id, bounds in birth_death.items()}
        person_events = defaultdict(list)
        for event in events:
            if event.person_id and event.earliest:
                person_events[event.person_id].append(event)
        for family in families:
            family_refs = {str(family["id"]), str(family["gedcom_id"])}
            parent_ids = {aliases.get(str(family[key] or "")) for key in ("husband_id", "wife_id")} - {None}
            for link in children:
                if str(link["family_id"]) not in family_refs: continue
                child_id = aliases.get(str(link["child_id"])); child_birth = births.get(child_id)
                for parent_id in parent_ids:
                    parent_birth = births.get(parent_id)
                    if child_birth and parent_birth and child_birth.year - parent_birth.year < 12:
                        for event in person_events[child_id]:
                            if event.event_type == "birth": conflicts[event.event_id].append("child_birth_before_plausible_parent_age")
        exclusive_people = {aliases.get(str(family[key] or "")) for family in families if family.get("relationship_type") == "exclusive" for key in ("husband_id", "wife_id")} - {None}
        for person_id in exclusive_people:
            marriages = [event for event in person_events[person_id] if event.event_type == "marriage"]
            for index, left in enumerate(marriages):
                for right in marriages[index + 1:]:
                    if self._overlaps(left, right):
                        conflicts[left.event_id].append("overlapping_exclusive_marriages")
                        conflicts[right.event_id].append("overlapping_exclusive_marriages")
        return [self._replace_conflicts(event, conflicts[event.event_id]) for event in events]

    @staticmethod
    def _replace_conflicts(event, values): return TimelineStudioEvent(**{**asdict(event), "earliest": event.earliest, "latest": event.latest, "conflicts": tuple(sorted(set(values)))})
    @staticmethod
    def _event_key(event): return (event.earliest is None, event.earliest or date.max, normalize_search_text(event.subject_label), normalize_search_text(event.event_label), event.event_id)
    @staticmethod
    def _overlaps(left, right): return bool(left.earliest and right.earliest and left.earliest == right.earliest)
    @staticmethod
    def _person_label(person): return "Без имени" if not person else ", ".join(value for value in (person.get("last_name", ""), person.get("first_name", "")) if value) or person.get("gedcom_id", "") or f"Person {person['id']}"
    @staticmethod
    def _aliases(people): return {str(value): person["id"] for person in people for value in (person["id"], person["gedcom_id"]) if value not in (None, "")}
    @staticmethod
    def _confidence(citations):
        values = [str(citation.get("quality") or "").strip() for citation in citations]
        return next((value for value in ("high", "primary", "medium", "secondary", "low") if value in values), "unknown")
    @staticmethod
    def _color(event_type): return {"birth": "#2b7a3d", "death": "#9a3535", "marriage": "#80559b", "residence": "#277da1", "historical": "#8c6418"}.get(event_type, "#546e7a")
    def _lanes(self, events, lane_order, collapsed):
        labels = {event.lane_id: event.subject_label for event in events}; default = sorted(labels, key=lambda lane: (not lane.startswith("person:"), normalize_search_text(labels[lane]), lane)); order = [lane for lane in lane_order if lane in labels] + [lane for lane in default if lane not in lane_order]
        return tuple(TimelineLane(lane, labels[lane], "person" if lane.startswith("person:") else "family" if lane.startswith("family:") else "historical", lane in collapsed) for lane in order)
    def _scope_people(self, scope, selected_ids, people, families, children):
        all_ids, selected = {person["id"] for person in people}, {int(person_id) for person_id in selected_ids if int(person_id) in {person["id"] for person in people}}
        if scope == "complete_database": return all_ids
        if scope in {"selected_person", "selected_people"}: return selected
        aliases = self._aliases(people); graph = defaultdict(set)
        for family in families:
            members = [aliases.get(str(family[key] or "")) for key in ("husband_id", "wife_id")]; members = [member for member in members if member]
            for left in members:
                for right in members:
                    if left != right: graph[left].add(right)
            family_refs = {str(family["id"]), str(family["gedcom_id"])}
            for child in children:
                if str(child["family_id"]) in family_refs:
                    child_id = aliases.get(str(child["child_id"]))
                    for parent in members:
                        if child_id: graph[parent].add(child_id); graph[child_id].add(parent)
        if scope == "immediate_family": return selected | {relative for person_id in selected for relative in graph[person_id]}
        result, queue = set(selected), deque(selected)
        while queue:
            person_id = queue.popleft()
            for relative in graph[person_id]:
                if relative not in result: result.add(relative); queue.append(relative)
        return result
    def _historical_events(self):
        return [self._event(str(item["id"]), "historical", "historical", None, None, "historical", item.get("date", ""), item.get("place", ""), item.get("note", ""), item.get("title", "Исторический контекст"), None, ()) for item in self._load_context()]
    def _load_context(self):
        try:
            data = json.loads(self.context_path.read_text(encoding="utf-8")); return data if isinstance(data, list) else []
        except (OSError, ValueError): return []
    def _view_path(self, name):
        safe = "".join(char for char in str(name).strip() if char.isalnum() or char in " -_").strip();
        if not safe: raise ValueError("Недопустимое название представления")
        return self.data_dir / f"{safe}.json"
    def _export_html(self, path, events, metadata):
        rows = "".join(f"<tr style='color:{escape(event.color)}'><td>{escape(event.normalized_date)}</td><td>{escape(event.original_date)}</td><td>{escape(event.subject_label)}</td><td>{escape(event.event_label)}</td><td>{escape(event.place)}</td><td>{event.age if event.age is not None else ''}</td></tr>" for event in events)
        path.write_text(f"<!doctype html><meta charset='utf-8'><title>{escape(metadata['title'])}</title><h1>{escape(metadata['title'])}</h1><p>Scale: {metadata['scale']} | Legend: colored event types</p><table><tr><th>Normalized</th><th>Original</th><th>Lane</th><th>Event</th><th>Place</th><th>Age</th></tr>{rows}</table><script type='application/json' id='metadata'>{html.escape(json.dumps(metadata, ensure_ascii=False))}</script>", encoding="utf-8")
    def _export_svg(self, path, events, metadata):
        lanes = {event.lane_id: index for index, event in enumerate(sorted(events, key=lambda item: item.lane_id))}; years = [event.earliest.year for event in events if event.earliest]; start = min(years, default=0); width, height = 1200, max(180, 80 + 55 * max(1, len(lanes)))
        lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}"><metadata>{escape(json.dumps(metadata, ensure_ascii=False))}</metadata><rect width="100%" height="100%" fill="#f7f8fa"/><text x="20" y="28" font-size="18">{escape(metadata["title"])}</text><text x="20" y="48" font-size="11">Scale: {metadata["scale"]} | Legend: event colors</text>']
        for lane, index in lanes.items(): lines.append(f'<line x1="140" y1="{75 + index * 55}" x2="1160" y2="{75 + index * 55}" stroke="#b8c2cc"/><text x="20" y="{79 + index * 55}" font-size="11">{escape(lane)}</text>')
        span = max(1, max(years, default=start) - start)
        for event in events:
            if event.earliest: lines.append(f'<circle cx="{140 + (event.earliest.year - start) * 1000 / span:.1f}" cy="{75 + lanes[event.lane_id] * 55}" r="6" fill="{event.color}"><title>{escape(event.event_label + " " + event.original_date)}</title></circle>')
        lines.append("</svg>"); path.write_text("\n".join(lines), encoding="utf-8")
    def _export_png(self, path, events, metadata):
        try: from PIL import Image, ImageDraw, PngImagePlugin
        except ImportError as error: raise RuntimeError("Для PNG требуется Pillow") from error
        image = Image.new("RGB", (1200, max(180, 100 + 48 * max(1, len({event.lane_id for event in events})))), "#f7f8fa"); draw = ImageDraw.Draw(image); draw.text((20, 16), f"{metadata['title']} | {metadata['scale']}", fill="#1f2933")
        for index, event in enumerate(events[:100]): draw.text((24, 48 + index * 16), f"{event.normalized_date} {event.subject_label}: {event.event_label}", fill=event.color)
        info = PngImagePlugin.PngInfo(); info.add_text("GenealogyDB", json.dumps(metadata, ensure_ascii=False)); image.save(path, format="PNG", pnginfo=info)
    def _export_pdf(self, path, events, metadata):
        lines = [metadata["title"], f"Scale: {metadata['scale']} | Legend: event colors"] + [f"{event.normalized_date} | {event.subject_label} | {event.event_label} | {event.place}" for event in events]
        path.write_bytes(PersonTimelineService._build_simple_pdf([self._pdf_text(line) for line in lines]));
    @staticmethod
    def _pdf_text(value): return str(value).encode("latin-1", errors="replace").decode("latin-1")
    @staticmethod
    def _timestamp(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
    @staticmethod
    def _cancel(callback):
        if callback: callback()