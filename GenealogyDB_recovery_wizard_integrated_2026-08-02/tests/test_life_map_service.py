import sqlite3
import time
from pathlib import Path

from repository import PersonRepository
from repository.person_life_map_service import PersonLifeMapService
from viewer import GenealogyViewer


class _StubGeocoder:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def geocode(self, place):
        self.calls.append(place)
        return self.responses.get(place, {"status": "failed", "error": "not found", "latitude": None, "longitude": None})


def _build_repo(tmp_path, db_name="life_map.db"):
    db_path = tmp_path / db_name
    conn = sqlite3.connect(db_path)
    conn.executescript(Path("schema.sql").read_text(encoding="utf-8"))
    conn.commit()
    conn.close()
    return PersonRepository(str(db_path))


def test_life_map_place_collection(tmp_path):
    repo = _build_repo(tmp_path, "collect.db")
    person_id = repo.create_person(
        {
            "gedcom_id": "I1",
            "first_name": "Anna",
            "last_name": "Ivanova",
            "birth_date": "1900",
            "birth_place": "Moscow",
            "death_date": "1980",
            "death_place": "Paris",
        }
    )
    repo.create_person_event({"person_id": person_id, "event_type": "residence", "date": "1930", "place": "Berlin", "description": "Moved"})
    repo.create_person_event({"person_id": person_id, "event_type": "education", "date": "1920", "place": "Moscow", "description": "Study"})

    service = PersonLifeMapService(repo)
    markers = service.collect_place_events(person_id)

    places = {marker["place"] for marker in markers}
    assert "Moscow" in places
    assert "Paris" in places
    assert "Berlin" in places
    repo.close()


def test_life_map_normalization_and_cache_reuse(tmp_path):
    repo = _build_repo(tmp_path, "cache.db")
    person_id = repo.create_person({"gedcom_id": "I1", "first_name": "John", "last_name": "Doe", "birth_place": "Москва"})
    repo.create_person_event({"person_id": person_id, "event_type": "residence", "date": "1930", "place": " москва ", "description": "same place"})

    geocoder = _StubGeocoder({"москва": {"status": "ok", "error": "", "latitude": 55.75, "longitude": 37.61}})
    service = PersonLifeMapService(repo, geocoder=geocoder)

    first = service.update_missing_coordinates(person_id)
    second = service.update_missing_coordinates(person_id)

    assert first["updated"] == 1
    assert second["updated"] == 0
    assert len(geocoder.calls) == 1
    repo.close()


def test_life_map_chronological_marker_ordering(tmp_path):
    repo = _build_repo(tmp_path, "ordering.db")
    person_id = repo.create_person({"gedcom_id": "I1", "first_name": "Order", "last_name": "Test", "birth_date": "1900", "birth_place": "A"})
    repo.create_person_event({"person_id": person_id, "event_type": "residence", "date": "1930", "place": "C", "description": "late"})
    repo.create_person_event({"person_id": person_id, "event_type": "education", "date": "1910", "place": "B", "description": "early"})
    repo.create_person_event({"person_id": person_id, "event_type": "custom", "date": "UNKNOWN", "place": "Z", "description": "unknown"})

    geocoder = _StubGeocoder(
        {
            "A": {"status": "ok", "error": "", "latitude": 1.0, "longitude": 1.0},
            "B": {"status": "ok", "error": "", "latitude": 2.0, "longitude": 2.0},
            "C": {"status": "ok", "error": "", "latitude": 3.0, "longitude": 3.0},
            "Z": {"status": "ok", "error": "", "latitude": 4.0, "longitude": 4.0},
        }
    )
    service = PersonLifeMapService(repo, geocoder=geocoder)
    service.update_missing_coordinates(person_id)

    map_data = service.build_map_data(person_id)
    route_places = [item["place"] for item in map_data["route"]]

    assert route_places == ["A", "B", "C"]
    assert "Z" not in route_places
    repo.close()


def test_life_map_failed_geocoding(tmp_path):
    repo = _build_repo(tmp_path, "failed.db")
    person_id = repo.create_person({"gedcom_id": "I1", "first_name": "Fail", "last_name": "Geo", "birth_place": "Nowhere"})

    geocoder = _StubGeocoder({"Nowhere": {"status": "failed", "error": "not found", "latitude": None, "longitude": None}})
    service = PersonLifeMapService(repo, geocoder=geocoder)
    service.update_missing_coordinates(person_id)

    map_data = service.build_map_data(person_id)
    marker = next(item for item in map_data["markers"] if item["place"] == "Nowhere")
    assert marker["geocode_status"] == "failed"
    repo.close()


def test_life_map_manual_coordinate_correction(tmp_path):
    repo = _build_repo(tmp_path, "manual.db")
    person_id = repo.create_person({"gedcom_id": "I1", "first_name": "Manual", "last_name": "Fix", "birth_place": "Минск"})

    service = PersonLifeMapService(repo, geocoder=_StubGeocoder({}))
    service.set_manual_coordinates("Минск", 53.9, 27.56)

    map_data = service.build_map_data(person_id)
    marker = next(item for item in map_data["markers"] if item["place"] == "Минск")
    assert marker["geocode_status"] == "manual"
    assert marker["latitude"] == 53.9
    assert marker["longitude"] == 27.56
    repo.close()


def test_life_map_kml_export(tmp_path):
    repo = _build_repo(tmp_path, "kml.db")
    person_id = repo.create_person({"gedcom_id": "I1", "first_name": "Kml", "last_name": "User", "birth_date": "1900", "birth_place": "A"})
    repo.create_person_event({"person_id": person_id, "event_type": "residence", "date": "1910", "place": "B", "description": "move"})

    geocoder = _StubGeocoder(
        {
            "A": {"status": "ok", "error": "", "latitude": 10.0, "longitude": 20.0},
            "B": {"status": "ok", "error": "", "latitude": 30.0, "longitude": 40.0},
        }
    )
    service = PersonLifeMapService(repo, geocoder=geocoder)
    service.update_missing_coordinates(person_id)

    kml_path = service.export_kml(service.build_map_data(person_id), tmp_path / "life_map.kml")
    content = kml_path.read_text(encoding="utf-8")
    assert "<kml" in content
    assert "Маршрут жизни" in content
    assert "<coordinates>20.0,10.0,0" in content
    repo.close()


def test_life_map_background_worker_behavior(tmp_path):
    repo = _build_repo(tmp_path, "worker.db")
    person_id = repo.create_person({"gedcom_id": "I1", "first_name": "Worker", "last_name": "Case", "birth_place": "Moscow"})

    class FakeRoot:
        def __init__(self):
            self.callbacks = []

        def after(self, _delay, callback):
            self.callbacks.append(callback)

    class FakeLabel:
        def __init__(self):
            self.last = ""

        def config(self, **kwargs):
            if "text" in kwargs:
                self.last = kwargs["text"]

    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer.root = FakeRoot()
    viewer.repository = repo
    viewer._life_map_current_person_id = person_id
    viewer._life_map_geocode_running = False
    viewer._life_map_geocode_queue = None
    viewer._life_map_geocode_cancel_event = None
    viewer._life_map_geocode_thread = None
    viewer._life_map_progress_label = FakeLabel()
    viewer._refresh_life_map_data = lambda _pid: None

    original_service_class = __import__("viewer").PersonLifeMapService
    original_timeline_class = __import__("viewer").PersonTimelineService

    class FastService:
        def __init__(self, repository, timeline_service=None):
            self.repository = repository

        def update_missing_coordinates(self, _person_id, progress_callback=None, cancel_event=None):
            if progress_callback:
                progress_callback("Геокодирование", 1, 1, 100)
            return {"updated": 1, "failed": 0, "needs_key": False}

    import viewer as viewer_module

    viewer_module.PersonLifeMapService = FastService
    viewer_module.PersonTimelineService = lambda _repository: None

    try:
        viewer._start_life_map_geocoding()
        for _ in range(200):
            callbacks = list(viewer.root.callbacks)
            viewer.root.callbacks.clear()
            for callback in callbacks:
                callback()
            if not viewer._life_map_geocode_running:
                break
            time.sleep(0.01)

        assert viewer._life_map_geocode_running is False
        assert "завершено" in viewer._life_map_progress_label.last
    finally:
        viewer_module.PersonLifeMapService = original_service_class
        viewer_module.PersonTimelineService = original_timeline_class
        repo.close()
