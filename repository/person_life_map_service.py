from __future__ import annotations

import json
import html
import re
import struct
import unicodedata
import urllib.parse
import urllib.request
import zlib
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
        person = self.repository.get_person(person_id)
        person_name = " ".join(
            value for value in ((person[2] if person else ""), (person[1] if person else ""))
            if value
        ) or "Без имени"
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
                "person_id": person_id,
                "person_name": person_name,
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

    def export_html(self, map_data, destination_path):
        destination = Path(destination_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        markers = [
            marker for marker in map_data.get("markers", [])
            if marker.get("latitude") is not None and marker.get("longitude") is not None
        ]
        route = [
            [marker["latitude"], marker["longitude"]]
            for marker in map_data.get("route", [])
            if marker.get("latitude") is not None and marker.get("longitude") is not None
        ]
        marker_data = [
            {
                "latitude": marker["latitude"],
                "longitude": marker["longitude"],
                "event": marker.get("event_label", ""),
                "date": marker.get("date_text", ""),
                "person": marker.get("person_name", ""),
                "notes": marker.get("description", ""),
                "place": marker.get("place", ""),
            }
            for marker in markers
        ]
        title = marker_data[0]["person"] if marker_data else "Карта жизни"
        document = f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)} - Карта жизни</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<style>html,body,#map{{height:100%;margin:0}}.popup{{line-height:1.45}}.popup strong{{display:inline-block;min-width:72px}}</style></head>
<body><div id="map"></div><script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script><script>
const markers={json.dumps(marker_data, ensure_ascii=False).replace('</', '<\\/')};
const route={json.dumps(route)};
const map=L.map('map').setView([20,0],2);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png',{{maxZoom:19,attribution:'&copy; OpenStreetMap'}}).addTo(map);
const bounds=[];
markers.forEach(item=>{{
  const popup=`<div class="popup"><strong>Событие:</strong> ${{escapeHtml(item.event)}}<br><strong>Дата:</strong> ${{escapeHtml(item.date)}}<br><strong>Человек:</strong> ${{escapeHtml(item.person)}}<br><strong>Место:</strong> ${{escapeHtml(item.place)}}<br><strong>Заметки:</strong> ${{escapeHtml(item.notes)}}</div>`;
  L.marker([item.latitude,item.longitude]).addTo(map).bindPopup(popup); bounds.push([item.latitude,item.longitude]);
}});
if(route.length>1)L.polyline(route,{{color:'#2878b5',weight:3}}).addTo(map);
if(bounds.length)map.fitBounds(bounds,{{padding:[30,30],maxZoom:12}});
function escapeHtml(value){{const node=document.createElement('div');node.textContent=value||'';return node.innerHTML;}}
</script></body></html>"""
        destination.write_text(document, encoding="utf-8")
        return destination

    def export_png(self, map_data, destination_path, width=1200, height=600):
        destination = Path(destination_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        width = max(320, int(width))
        height = max(180, int(height))
        pixels = bytearray([248, 250, 252] * width * height)

        def point(marker):
            x = round(((float(marker["longitude"]) + 180.0) / 360.0) * (width - 1))
            y = round(((90.0 - float(marker["latitude"])) / 180.0) * (height - 1))
            return x, y

        def set_pixel(x, y, color):
            if 0 <= x < width and 0 <= y < height:
                offset = (y * width + x) * 3
                pixels[offset:offset + 3] = bytes(color)

        def line(start, end, color):
            x1, y1 = start
            x2, y2 = end
            steps = max(abs(x2 - x1), abs(y2 - y1), 1)
            for step in range(steps + 1):
                x = round(x1 + (x2 - x1) * step / steps)
                y = round(y1 + (y2 - y1) * step / steps)
                for offset in (-1, 0, 1):
                    set_pixel(x, y + offset, color)

        for longitude in range(-180, 181, 30):
            x = round(((longitude + 180) / 360) * (width - 1))
            line((x, 0), (x, height - 1), (222, 229, 235))
        for latitude in range(-60, 61, 30):
            y = round(((90 - latitude) / 180) * (height - 1))
            line((0, y), (width - 1, y), (222, 229, 235))

        route_points = [
            point(marker) for marker in map_data.get("route", [])
            if marker.get("latitude") is not None and marker.get("longitude") is not None
        ]
        for start, end in zip(route_points, route_points[1:]):
            line(start, end, (40, 120, 181))
        for marker in map_data.get("markers", []):
            if marker.get("latitude") is None or marker.get("longitude") is None:
                continue
            center_x, center_y = point(marker)
            for y_offset in range(-6, 7):
                for x_offset in range(-6, 7):
                    if x_offset * x_offset + y_offset * y_offset <= 36:
                        set_pixel(center_x + x_offset, center_y + y_offset, (36, 132, 78))

        raw = b"".join(b"\x00" + bytes(pixels[row * width * 3:(row + 1) * width * 3]) for row in range(height))
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
        png = b"\x89PNG\r\n\x1a\n"
        png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        png += chunk(b"IDAT", zlib.compress(raw, 9))
        png += chunk(b"IEND", b"")
        destination.write_bytes(png)
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
