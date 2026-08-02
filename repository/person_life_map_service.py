from __future__ import annotations

import json
import re
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

from config import GEOCODING_API_KEY, GEOCODING_PROVIDER
from repository.person_repository import PersonRepository
from repository.person_timeline_service import PersonTimelineService


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


class GeocodingConfigurationError(Exception):
    """Report missing or invalid geocoding configuration."""
    pass


class OpenCageGeocoder:
    """Resolve place names through the OpenCage geocoding API."""
    def __init__(self, api_key):
        self.api_key = (api_key or "").strip()

    def geocode(self, place):
        if not self.api_key:
            raise GeocodingConfigurationError("Не настроен API-ключ геокодирования.")

        query = urllib.parse.urlencode({"q": place, "key": self.api_key, "limit": 1, "language": "ru", "no_annotations": 1})
        url = f"https://api.opencagedata.com/geocode/v1/json?{query}"
        request = urllib.request.Request(url, headers={"User-Agent": "GenealogyDB/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                payload = response.read().decode("utf-8")
        except Exception as error:
            return {"status": "failed", "error": str(error), "latitude": None, "longitude": None}

        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return {"status": "failed", "error": "Некорректный ответ геокодера", "latitude": None, "longitude": None}

        results = data.get("results") or []
        if not results:
            return {"status": "failed", "error": "Координаты не найдены", "latitude": None, "longitude": None}

        geometry = results[0].get("geometry") or {}
        lat = geometry.get("lat")
        lng = geometry.get("lng")
        if lat is None or lng is None:
            return {"status": "failed", "error": "Ответ без координат", "latitude": None, "longitude": None}

        return {"status": "ok", "error": "", "latitude": float(lat), "longitude": float(lng)}


class PersonLifeMapService:
    """Build and enrich map locations from a person's life events."""
    def __init__(self, repository: PersonRepository, timeline_service: PersonTimelineService | None = None, geocoder=None):
        self.repository = repository
        self.timeline_service = timeline_service or PersonTimelineService(repository)
        self._geocoder = geocoder

    def collect_place_events(self, person_id):
        timeline = self.timeline_service.build_timeline(person_id)
        markers = []
        for entry in timeline:
            place = (entry.get("place") or "").strip()
            if not place:
                continue
            normalized_place = self.normalize_place(place)
            if not normalized_place:
                continue

            marker = {
                "event_id": entry.get("event_id"),
                "event_type": entry.get("event_type", "custom"),
                "event_label": EVENT_LABELS.get(entry.get("event_type", "custom"), entry.get("event_type", "custom")),
                "date_text": entry.get("date_text", ""),
                "place": place,
                "normalized_place": normalized_place,
                "description": entry.get("description", ""),
                "sort_known": bool(entry.get("sort_known")),
                "sort_earliest": entry.get("sort_earliest"),
                "sort_latest": entry.get("sort_latest"),
                "latitude": None,
                "longitude": None,
                "geocode_status": "missing",
                "geocode_error": "",
            }
            markers.append(marker)

        return markers

    def build_map_data(self, person_id):
        markers = self.collect_place_events(person_id)
        normalized_places = [marker["normalized_place"] for marker in markers]
        cache_by_place = self.repository.get_geocoding_cache_batch(normalized_places)

        for marker in markers:
            cache = cache_by_place.get(marker["normalized_place"])
            if not cache:
                continue
            marker["latitude"] = cache.get("latitude")
            marker["longitude"] = cache.get("longitude")
            marker["geocode_status"] = cache.get("status", "missing")
            marker["geocode_error"] = cache.get("error_message", "")

        unique_places = {}
        for marker in markers:
            if marker["normalized_place"] not in unique_places:
                unique_places[marker["normalized_place"]] = marker["place"]

        route = self._route_points(markers)
        return {
            "markers": markers,
            "route": route,
            "unique_places": unique_places,
            "missing_places": [
                normalized
                for normalized in unique_places
                if normalized not in cache_by_place or cache_by_place[normalized].get("status") not in {"ok", "manual"}
            ],
            "geocoding_enabled": bool(self._resolve_geocoder()),
        }

    def update_missing_coordinates(self, person_id, progress_callback=None, cancel_event=None):
        map_data = self.build_map_data(person_id)
        unique_places = map_data["unique_places"]
        missing = map_data["missing_places"]

        geocoder = self._resolve_geocoder()
        if not geocoder:
            for normalized_place in missing:
                original = unique_places.get(normalized_place, "")
                self.repository.upsert_geocoding_cache(
                    normalized_place,
                    original_place=original,
                    latitude=None,
                    longitude=None,
                    status="needs_key",
                    provider=GEOCODING_PROVIDER,
                    error_message="Не настроен API-ключ геокодирования.",
                )
            if progress_callback:
                progress_callback("Нет ключа геокодирования", len(missing), len(missing), 100)
            return {"updated": 0, "failed": len(missing), "needs_key": True}

        total = len(missing)
        updated = 0
        failed = 0
        if progress_callback:
            progress_callback("Геокодирование", 0, total, 0 if total else 100)

        for index, normalized_place in enumerate(missing, start=1):
            if cancel_event is not None and cancel_event.is_set():
                break

            original_place = unique_places.get(normalized_place, "")
            result = geocoder.geocode(original_place)
            status = result.get("status", "failed")

            if status == "ok":
                updated += 1
                self.repository.upsert_geocoding_cache(
                    normalized_place,
                    original_place=original_place,
                    latitude=result.get("latitude"),
                    longitude=result.get("longitude"),
                    status="ok",
                    provider=GEOCODING_PROVIDER,
                    error_message="",
                )
            else:
                failed += 1
                self.repository.upsert_geocoding_cache(
                    normalized_place,
                    original_place=original_place,
                    latitude=None,
                    longitude=None,
                    status="failed",
                    provider=GEOCODING_PROVIDER,
                    error_message=result.get("error", "Координаты не найдены"),
                )

            if progress_callback:
                percent = int((index / total) * 100) if total else 100
                progress_callback("Геокодирование", index, total, percent)

        return {"updated": updated, "failed": failed, "needs_key": False}

    def set_manual_coordinates(self, place, latitude, longitude):
        normalized_place = self.normalize_place(place)
        if not normalized_place:
            raise ValueError("Укажите место")
        lat = float(latitude)
        lng = float(longitude)
        if lat < -90 or lat > 90:
            raise ValueError("Широта должна быть в диапазоне от -90 до 90")
        if lng < -180 or lng > 180:
            raise ValueError("Долгота должна быть в диапазоне от -180 до 180")

        return self.repository.upsert_geocoding_cache(
            normalized_place,
            original_place=place,
            latitude=lat,
            longitude=lng,
            status="manual",
            provider="manual",
            error_message="",
        )

    def export_kml(self, map_data, destination_path):
        destination = Path(destination_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)

        markers = [marker for marker in map_data.get("markers", []) if marker.get("latitude") is not None and marker.get("longitude") is not None]
        route = [marker for marker in map_data.get("route", []) if marker.get("latitude") is not None and marker.get("longitude") is not None]

        lines = [
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>",
            "<kml xmlns=\"http://www.opengis.net/kml/2.2\">",
            "  <Document>",
            "    <name>Life Map</name>",
        ]

        for marker in markers:
            name = self._xml_escape(f"{marker.get('event_label', '')}: {marker.get('place', '')}")
            description = self._xml_escape(
                f"Дата: {marker.get('date_text', '')}\nОписание: {marker.get('description', '')}\nТип: {marker.get('event_label', '')}"
            )
            lines.extend(
                [
                    "    <Placemark>",
                    f"      <name>{name}</name>",
                    f"      <description>{description}</description>",
                    "      <Point>",
                    f"        <coordinates>{marker.get('longitude')},{marker.get('latitude')},0</coordinates>",
                    "      </Point>",
                    "    </Placemark>",
                ]
            )

        if len(route) >= 2:
            route_coordinates = " ".join(
                f"{marker.get('longitude')},{marker.get('latitude')},0"
                for marker in route
            )
            lines.extend(
                [
                    "    <Placemark>",
                    "      <name>Маршрут жизни</name>",
                    "      <LineString>",
                    "        <tessellate>1</tessellate>",
                    f"        <coordinates>{route_coordinates}</coordinates>",
                    "      </LineString>",
                    "    </Placemark>",
                ]
            )

        lines.extend(["  </Document>", "</kml>"])
        destination.write_text("\n".join(lines), encoding="utf-8")
        return destination

    @staticmethod
    def normalize_place(value):
        text = (value or "").strip().lower()
        text = text.replace("ё", "е")
        text = unicodedata.normalize("NFKD", text)
        text = "".join(char for char in text if not unicodedata.combining(char))
        text = re.sub(r"[^\w\s\-]", " ", text, flags=re.UNICODE)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    @staticmethod
    def _route_points(markers):
        known = [marker for marker in markers if marker.get("sort_known") and marker.get("latitude") is not None and marker.get("longitude") is not None]
        known.sort(
            key=lambda marker: (
                marker.get("sort_earliest") or marker.get("sort_latest"),
                marker.get("event_label", ""),
                marker.get("place", ""),
            )
        )
        return known

    @staticmethod
    def _xml_escape(value):
        text = value or ""
        text = text.replace("&", "&amp;")
        text = text.replace("<", "&lt;")
        text = text.replace(">", "&gt;")
        text = text.replace('"', "&quot;")
        return text

    def _resolve_geocoder(self):
        if self._geocoder is not None:
            return self._geocoder
        if GEOCODING_PROVIDER != "opencage":
            return None
        if not GEOCODING_API_KEY:
            return None
        return OpenCageGeocoder(GEOCODING_API_KEY)
