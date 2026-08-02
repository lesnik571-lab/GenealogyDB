import sqlite3
import inspect
import time
from pathlib import Path

from repository import PersonRepository
from life_map_service import LifeMapService, PersonLifeMapService
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


def test_life_map_collects_every_required_event_type(tmp_path):
    repo = _build_repo(tmp_path, "event_types.db")
    person_id = repo.create_person(
        {
            "gedcom_id": "I1",
            "first_name": "Event",
            "last_name": "Collector",
            "birth_date": "1900",
            "birth_place": "Birth Place",
            "death_date": "1980",
            "death_place": "Death Place",
        }
    )
    required_types = (
        "baptism", "residence", "marriage", "occupation", "immigration",
        "emigration", "burial", "custom",
    )
    for index, event_type in enumerate(required_types, start=1):
        repo.create_person_event(
            {
                "person_id": person_id,
                "event_type": event_type,
                "date": str(1900 + index),
                "place": f"{event_type} place",
                "description": f"{event_type} notes",
            }
        )

    markers = PersonLifeMapService(repo).collect_place_events(person_id)
    collected_types = {marker["event_type"] for marker in markers}

    assert {"birth", "death", *required_types} <= collected_types
    assert all(marker["person_name"] == "Event Collector" for marker in markers)
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


def test_life_map_html_and_png_exports_include_marker_details(tmp_path):
    repo = _build_repo(tmp_path, "exports.db")
    person_id = repo.create_person({"gedcom_id": "I1", "first_name": "Map", "last_name": "User", "birth_date": "1900", "birth_place": "A"})
    repo.create_person_event({"person_id": person_id, "event_type": "immigration", "date": "1920", "place": "B", "description": "Arrived"})
    geocoder = _StubGeocoder({
        "A": {"status": "ok", "error": "", "latitude": 10.0, "longitude": 20.0},
        "B": {"status": "ok", "error": "", "latitude": 30.0, "longitude": 40.0},
    })
    service = LifeMapService(repo, geocoder=geocoder)
    service.update_missing_coordinates(person_id)
    map_data = service.build_map_data(person_id)

    html_path = service.export_html(map_data, tmp_path / "life_map.html")
    png_path = service.export_png(map_data, tmp_path / "life_map.png", width=400, height=200)

    html_text = html_path.read_text(encoding="utf-8")
    assert "Map User" in html_text
    assert "Arrived" in html_text
    assert "L.polyline" in html_text
    assert png_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert len(png_path.read_bytes()) > 100
    repo.close()


def test_life_map_viewer_toolbar_and_read_only_controls_are_wired():
    widget_source = inspect.getsource(GenealogyViewer._create_widgets)
    tab_source = inspect.getsource(GenealogyViewer._build_life_map_tab)

    assert 'text="Карта жизни"' in widget_source
    assert "command=self.open_life_map" in widget_source
    assert 'text="Экспорт HTML"' in tab_source
    assert 'text="Экспорт PNG"' in tab_source
    assert "Исправить координаты" not in tab_source
    assert "_select_life_map_tree_marker" in tab_source
    assert "_open_selected_life_map_person" in tab_source


def test_life_map_marker_selection_shows_details_and_double_click_opens_person():
    class Label:
        text = ""

        def config(self, **values):
            self.text = values.get("text", self.text)

    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer._life_map_detail_label = Label()
    viewer._life_map_current_person_id = 42
    opened = []
    viewer.show_person = opened.append
    marker = {
        "event_label": "Иммиграция",
        "date_text": "1920",
        "person_name": "Map User",
        "description": "Arrived",
        "person_id": 42,
    }

    viewer._select_life_map_marker(marker)
    viewer._open_life_map_event_details(marker)

    assert "Иммиграция" in viewer._life_map_detail_label.text
    assert "1920" in viewer._life_map_detail_label.text
    assert "Map User" in viewer._life_map_detail_label.text
    assert "Arrived" in viewer._life_map_detail_label.text
    assert opened == [42]


def test_main_life_map_action_resolves_selected_person_and_builds_window():
    class Repository:
        def resolve_person_reference(self, reference):
            assert reference == "I42"
            return 42

    class Window:
        def title(self, _value): pass
        def geometry(self, _value): pass
        def minsize(self, _width, _height): pass
        def protocol(self, _name, _callback): pass

    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer.root = object()
    viewer.repository = Repository()
    viewer._life_map_window = None
    viewer._choose_person = lambda _title: "I42"
    viewer._create_dialog = lambda _parent: Window()
    built = []
    viewer._build_life_map_tab = lambda window, person_id: built.append((window, person_id))

    viewer.open_life_map()

    assert built == [(viewer._life_map_window, 42)]


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
