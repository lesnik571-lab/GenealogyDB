"""Aggregate, sidecar-backed geographic map analysis for GenealogyDB."""

from __future__ import annotations

import csv
import html
import json
import math
import re
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from config import DATA_DIR
from repository.person_life_map_service import EVENT_LABELS, PersonLifeMapService
from repository.person_timeline_service import PersonTimelineService
from text_utils import normalize_search_text


SCOPES = ("current_person", "immediate_family", "selected_branch", "selected_people", "complete_database")


@dataclass(frozen=True)
class GeoMapFilters:
    surname: str = ""
    person: str = ""
    family: str = ""
    event_type: str = ""
    year_from: int | None = None
    year_to: int | None = None
    country: str = ""
    text: str = ""
    unresolved_only: bool = False


@dataclass(frozen=True)
class GeoMarker:
    marker_id: str
    person_id: int
    person_name: str
    family_ids: tuple[int, ...]
    event_id: int | None
    event_type: str
    event_label: str
    date_text: str
    year: int | None
    place: str
    normalized_place: str
    description: str
    latitude: float | None
    longitude: float | None
    geocode_status: str
    geocode_error: str
    color: str


@dataclass(frozen=True)
class GeoRoute:
    person_id: int
    marker_ids: tuple[str, ...]
    distance_km: float


@dataclass(frozen=True)
class GeoMapModel:
    scope: str
    selected_person_ids: tuple[int, ...]
    markers: tuple[GeoMarker, ...]
    routes: tuple[GeoRoute, ...]
    clusters: dict[str, tuple[str, ...]]
    total_distance_km: float
    generated_at: str


class GeoMapStudioService:
    """Build map data through repository APIs; only geocoding cache writes mutate DB."""

    def __init__(self, repository, *, data_dir=None, geocoder=None):
        self.repository = repository
        self.data_dir = Path(data_dir or DATA_DIR) / "map_views"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.geocoder = geocoder

    def build(self, *, scope="current_person", selected_person_ids=(), progress_callback=None, cancel_callback=None):
        if scope not in SCOPES: raise ValueError("Неподдерживаемая область карты")
        self._cancel(cancel_callback)
        people = self.repository.list_people_full(); families = self.repository.list_families_raw(); children = self.repository.list_family_children_raw()
        selected = self._scope_people(scope, selected_person_ids, people, families, children)
        if progress_callback: progress_callback("Сбор мест", 1, 3)
        markers = self._markers(people, families, selected)
        self._cancel(cancel_callback)
        cache = self.repository.get_geocoding_cache_batch([marker.normalized_place for marker in markers])
        markers = tuple(self._apply_cache(marker, cache.get(marker.normalized_place)) for marker in markers)
        if progress_callback: progress_callback("Построение маршрутов", 2, 3)
        routes = self._routes(markers); clusters = self.cluster_markers(markers)
        self._cancel(cancel_callback)
        if progress_callback: progress_callback("Карта готова", 3, 3)
        return GeoMapModel(scope, tuple(sorted(selected)), tuple(sorted(markers, key=self._marker_key)), routes, clusters, round(sum(route.distance_km for route in routes), 2), self._timestamp())

    def filter(self, model, filters: GeoMapFilters):
        def has(value, query): return not query or normalize_search_text(query) in normalize_search_text(value)
        visible = []
        for marker in model.markers:
            if filters.surname and not has(marker.person_name.split(",", 1)[0], filters.surname): continue
            if filters.person and not has(marker.person_name, filters.person): continue
            if filters.family and not has(" ".join(map(str, marker.family_ids)), filters.family): continue
            if filters.event_type and not (has(marker.event_type, filters.event_type) or has(marker.event_label, filters.event_type)): continue
            if filters.year_from is not None and (marker.year is None or marker.year < filters.year_from): continue
            if filters.year_to is not None and (marker.year is None or marker.year > filters.year_to): continue
            if filters.country and not has(marker.place.split(",")[-1], filters.country): continue
            if filters.text and not any(has(value, filters.text) for value in (marker.place, marker.description, marker.person_name, marker.event_label)): continue
            if filters.unresolved_only and marker.latitude is not None: continue
            visible.append(marker)
        return tuple(visible)

    def update_missing_coordinates(self, model, *, progress_callback=None, cancel_callback=None):
        missing = {marker.normalized_place: marker.place for marker in model.markers if marker.latitude is None}
        if not missing: return {"updated": 0, "failed": 0}
        updated = failed = 0
        for index, (normalized, original) in enumerate(sorted(missing.items()), 1):
            self._cancel(cancel_callback)
            result = self.geocoder.geocode(original) if self.geocoder else {"status": "needs_key", "error": "Не настроен геокодер", "latitude": None, "longitude": None}
            status = result.get("status", "failed")
            if status == "ok": updated += 1
            else: failed += 1
            self.repository.upsert_geocoding_cache(normalized, original_place=original, latitude=result.get("latitude"), longitude=result.get("longitude"), status=status, provider="geo_map_studio" if self.geocoder else "", error_message=result.get("error", ""))
            if progress_callback: progress_callback("Геокодирование", index, len(missing))
        return {"updated": updated, "failed": failed}

    def set_manual_coordinates(self, place, latitude, longitude):
        normalized = PersonLifeMapService.normalize_place(place)
        if not normalized: raise ValueError("Укажите место")
        latitude, longitude = float(latitude), float(longitude)
        if not -90 <= latitude <= 90 or not -180 <= longitude <= 180: raise ValueError("Недопустимые координаты")
        return self.repository.upsert_geocoding_cache(normalized, original_place=str(place), latitude=latitude, longitude=longitude, status="manual", provider="manual", error_message="")

    def cluster_markers(self, markers, precision=1):
        clusters = defaultdict(list)
        for marker in markers:
            key = "unresolved" if marker.latitude is None else f"{round(marker.latitude, precision):.{precision}f},{round(marker.longitude, precision):.{precision}f}"
            clusters[key].append(marker.marker_id)
        return {key: tuple(sorted(value)) for key, value in sorted(clusters.items())}

    def marker_for_timeline_event(self, model, event_id):
        token = str(event_id).replace("event:", "")
        return next((marker for marker in model.markers if str(marker.event_id) == token or marker.marker_id == str(event_id)), None)
    def markers_for_tree_person(self, model, person_id): return tuple(marker for marker in model.markers if marker.person_id == int(person_id))
    def markers_at_year(self, markers, year): return tuple(marker for marker in markers if marker.year is not None and marker.year <= int(year))

    def save_view(self, name, configuration):
        path = self._view_path(name); payload = {"name": str(name).strip(), "configuration": configuration, "updated_at": self._timestamp()}
        if not payload["name"]: raise ValueError("Название представления обязательно")
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"); return path
    def list_views(self): return tuple(sorted((json.loads(path.read_text(encoding="utf-8")) for path in self.data_dir.glob("*.json")), key=lambda item: item["name"].casefold()))
    def load_view(self, name): return json.loads(self._view_path(name).read_text(encoding="utf-8"))
    def delete_view(self, name): self._view_path(name).unlink(missing_ok=True)
    def export_view(self, name, destination): Path(destination).write_text(json.dumps(self.load_view(name), ensure_ascii=False, indent=2), encoding="utf-8"); return Path(destination)
    def import_view(self, source):
        payload = json.loads(Path(source).read_text(encoding="utf-8")); return self.save_view(payload["name"], payload["configuration"])

    def export(self, model, markers, destination, export_format, *, title="Карта", filters=None, layers=()):
        path = Path(destination); path.parent.mkdir(parents=True, exist_ok=True)
        metadata = {"title": title, "scope": model.scope, "filters": asdict(filters) if filters else {}, "layers": list(layers), "total_distance_km": model.total_distance_km, "generated_at": self._timestamp()}
        if export_format == "html": self._html(path, markers, metadata)
        elif export_format == "svg": self._svg(path, markers, metadata)
        elif export_format == "png": self._png(path, markers, metadata)
        elif export_format == "pdf": self._pdf(path, markers, metadata)
        else: raise ValueError("Неподдерживаемый формат экспорта")
        return path

    def _markers(self, people, families, selected):
        by_id = {person["id"]: person for person in people}; aliases = self._aliases(people); family_ids = defaultdict(list)
        for family in families:
            for ref in (family["husband_id"], family["wife_id"]):
                person_id = aliases.get(str(ref));
                if person_id: family_ids[person_id].append(family["id"])
        result = []
        for person_id in sorted(selected):
            person = by_id[person_id]; label = self._person_label(person)
            for kind, raw_date, place, description, event_id in (("birth", person["birth_date"], person["birth_place"], person["note"], None), ("occupation", "", "", person["occupation"], None), ("death", person["death_date"], person["death_place"], person["note"], None)):
                if place: result.append(self._marker(person_id, label, family_ids[person_id], event_id, kind, raw_date, place, description))
            for event in self.repository.list_person_events(person_id):
                coordinates = self._description_coordinates(event.get("description") or "")
                if event.get("place") or coordinates:
                    result.append(self._marker(person_id, label, family_ids[person_id], event["id"], event.get("event_type") or "custom", event.get("date") or "", event.get("place") or "Координаты события", event.get("description") or "", coordinates))
        return result

    def _marker(self, person_id, name, family_ids, event_id, event_type, date_text, place, description, coordinates=None):
        parsed = PersonTimelineService._parse_gedcom_date(date_text); normalized = PersonLifeMapService.normalize_place(place)
        token = f"event:{event_id}" if event_id is not None else f"person:{person_id}:{event_type}:{normalized}"
        latitude, longitude = coordinates or (None, None)
        return GeoMarker(token, person_id, name, tuple(sorted(family_ids)), event_id, event_type, EVENT_LABELS.get(event_type, event_type), date_text, parsed.earliest.year if parsed.earliest else None, place, normalized, description, latitude, longitude, "event_coordinates" if coordinates else "missing", "", self._color(event_type))
    @staticmethod
    def _apply_cache(marker, cache):
        if not cache or marker.geocode_status == "event_coordinates": return marker
        return GeoMarker(**{**asdict(marker), "latitude": cache.get("latitude"), "longitude": cache.get("longitude"), "geocode_status": cache.get("status", "missing"), "geocode_error": cache.get("error_message", "")})
    def _routes(self, markers):
        grouped = defaultdict(list)
        for marker in markers:
            if marker.latitude is not None and marker.year is not None: grouped[marker.person_id].append(marker)
        routes = []
        for person_id, items in sorted(grouped.items()):
            ordered = sorted(items, key=self._marker_key); distance = sum(self._distance(left, right) for left, right in zip(ordered, ordered[1:]))
            if len(ordered) > 1: routes.append(GeoRoute(person_id, tuple(item.marker_id for item in ordered), round(distance, 2)))
        return tuple(routes)
    @staticmethod
    def _distance(left, right):
        radius = 6371.0088; lat1, lon1, lat2, lon2 = map(math.radians, (left.latitude, left.longitude, right.latitude, right.longitude)); return 2 * radius * math.asin(math.sqrt(math.sin((lat2-lat1)/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin((lon2-lon1)/2)**2))
    @staticmethod
    def _marker_key(marker): return (marker.year is None, marker.year or 9999, normalize_search_text(marker.person_name), marker.event_label, marker.marker_id)
    @staticmethod
    def _person_label(person): return ", ".join(value for value in (person.get("last_name", ""), person.get("first_name", "")) if value) or person.get("gedcom_id", "") or f"Person {person['id']}"
    @staticmethod
    def _aliases(people): return {str(value): person["id"] for person in people for value in (person["id"], person["gedcom_id"]) if value not in (None, "")}
    @staticmethod
    def _color(event_type): return {"birth":"#2b7a3d", "death":"#9a3535", "residence":"#277da1", "immigration":"#8c6418", "emigration":"#80559b"}.get(event_type, "#546e7a")
    @staticmethod
    def _description_coordinates(description):
        match = re.search(r"(?:coords?|coordinates?)\s*[:=]?\s*(-?\d+(?:\.\d+)?)\s*[,; ]\s*(-?\d+(?:\.\d+)?)", description, flags=re.IGNORECASE)
        if not match: return None
        latitude, longitude = float(match.group(1)), float(match.group(2))
        return (latitude, longitude) if -90 <= latitude <= 90 and -180 <= longitude <= 180 else None
    def _scope_people(self, scope, selected_ids, people, families, children):
        all_ids = {person["id"] for person in people}; selected = {int(value) for value in selected_ids if int(value) in all_ids}
        if scope == "complete_database": return all_ids
        if scope in {"current_person", "selected_people"}: return selected
        aliases = self._aliases(people); graph = defaultdict(set)
        for family in families:
            members = [aliases.get(str(family[key] or "")) for key in ("husband_id", "wife_id")]; members = [item for item in members if item]
            for left in members:
                for right in members:
                    if left != right: graph[left].add(right)
            refs = {str(family["id"]), str(family["gedcom_id"])}
            for child in children:
                if str(child["family_id"]) in refs:
                    child_id = aliases.get(str(child["child_id"]))
                    for parent in members:
                        if child_id: graph[parent].add(child_id); graph[child_id].add(parent)
        if scope == "immediate_family": return selected | {relative for item in selected for relative in graph[item]}
        result, queue = set(selected), deque(selected)
        while queue:
            item = queue.popleft()
            for relative in graph[item]:
                if relative not in result: result.add(relative); queue.append(relative)
        return result
    def _view_path(self, name):
        safe = "".join(char for char in str(name).strip() if char.isalnum() or char in " -_").strip()
        if not safe: raise ValueError("Недопустимое название представления")
        return self.data_dir / f"{safe}.json"
    def _html(self, path, markers, metadata):
        data = [asdict(marker) for marker in markers]; path.write_text(f"<!doctype html><meta charset='utf-8'><title>{html.escape(metadata['title'])}</title><h1>{html.escape(metadata['title'])}</h1><p>Distance: {metadata['total_distance_km']} km</p><script type='application/json' id='map-data'>{html.escape(json.dumps({'metadata': metadata, 'markers': data}, ensure_ascii=False))}</script><pre>{html.escape(json.dumps(data, ensure_ascii=False, indent=2, default=str))}</pre>", encoding="utf-8")
    def _svg(self, path, markers, metadata):
        lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="600"><metadata>{escape(json.dumps(metadata, ensure_ascii=False))}</metadata><rect width="100%" height="100%" fill="#f7f8fa"/><text x="20" y="30">{escape(metadata["title"])}</text>']
        for marker in markers:
            if marker.latitude is not None:
                x, y = (marker.longitude + 180) * 3.2, (90 - marker.latitude) * 3.0; lines.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="{marker.color}"><title>{escape(marker.person_name + ": " + marker.place)}</title></circle>')
        lines.append("</svg>"); path.write_text("\n".join(lines), encoding="utf-8")
    def _png(self, path, markers, metadata):
        try: from PIL import Image, ImageDraw, PngImagePlugin
        except ImportError as error: raise RuntimeError("Для PNG требуется Pillow") from error
        image = Image.new("RGB", (1200, 600), "#f7f8fa"); draw = ImageDraw.Draw(image); draw.text((20, 16), metadata["title"], fill="#1f2933")
        for marker in markers:
            if marker.latitude is not None: draw.ellipse(((marker.longitude+180)*3.2-4, (90-marker.latitude)*3-4, (marker.longitude+180)*3.2+4, (90-marker.latitude)*3+4), fill=marker.color)
        info = PngImagePlugin.PngInfo(); info.add_text("GenealogyDB", json.dumps(metadata, ensure_ascii=False)); image.save(path, format="PNG", pnginfo=info)
    def _pdf(self, path, markers, metadata):
        lines = [metadata["title"], f"Distance: {metadata['total_distance_km']} km"] + [f"{marker.person_name} | {marker.event_label} | {marker.place}" for marker in markers]
        path.write_bytes(PersonTimelineService._build_simple_pdf([str(line).encode("latin-1", errors="replace").decode("latin-1") for line in lines]))
    @staticmethod
    def _timestamp(): return datetime.now(timezone.utc).isoformat(timespec="seconds")
    @staticmethod
    def _cancel(callback):
        if callback: callback()