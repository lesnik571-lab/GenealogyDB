import json

import pytest

from database import initialize_database
from geo_map_studio_service import GeoMapFilters, GeoMapStudioService
from repository.person_repository import PersonRepository


class Geocoder:
    def geocode(self, place):
        return {"A": {"status": "ok", "latitude": 10.0, "longitude": 20.0, "error": ""}, "B": {"status": "ok", "latitude": 30.0, "longitude": 40.0, "error": ""}}.get(place, {"status": "failed", "latitude": None, "longitude": None, "error": "not found"})


def repository(tmp_path):
    path = tmp_path / "geo.db"; initialize_database(path); return PersonRepository(path)


def seed(repo):
    for gedcom_id, first_name, birth_place in (("I1", "Alex", "A"), ("I2", "Bea", "A"), ("I3", "Chris", "Unknown")):
        repo.create_person({"gedcom_id": gedcom_id, "first_name": first_name, "last_name": "Smith", "birth_date": "1900", "birth_place": birth_place, "death_date": "1980", "death_place": "B"})
    repo.create_family({"gedcom_id": "F1", "husband": "I1", "wife": "I2", "children": ["I3"], "relationship_type": "exclusive"})
    for person_id, event_type, event_date, place, description in ((1, "residence", "1920", "B", "Moved"), (1, "immigration", "1930", "A", "Arrived"), (2, "emigration", "1920", "B", "Left"), (3, "custom", "", "Unknown", "No date")):
        repo.create_person_event({"person_id": person_id, "event_type": event_type, "date": event_date, "place": place, "description": description})


def test_geo_map_cache_unresolved_routes_clusters_scopes_filters_and_read_only(tmp_path):
    repo = repository(tmp_path)
    try:
        seed(repo); service = GeoMapStudioService(repo, data_dir=tmp_path / "sidecar", geocoder=Geocoder())
        before = repo.capture_command_state(); initial = service.build(scope="complete_database")
        assert initial == service.build(scope="complete_database")
        assert any(marker.geocode_status == "missing" for marker in initial.markers)
        assert service.update_missing_coordinates(initial)["updated"] == 2
        model = service.build(scope="complete_database")
        assert any(marker.geocode_status == "failed" and marker.place == "Unknown" for marker in model.markers)
        assert model.routes and model.total_distance_km > 0 and any(len(items) > 1 for items in model.clusters.values())
        assert set(service.build(scope="immediate_family", selected_person_ids=(1,)).selected_person_ids) == {1, 2, 3}
        assert [marker.marker_id for marker in service.filter(model, GeoMapFilters(event_type="immigration"))] == ["event:2"]
        assert service.filter(model, GeoMapFilters(unresolved_only=True))
        assert repo.get_geocoding_cache("a")["status"] == "ok"
        assert repo.capture_command_state() == before  # Geocoding changes only cache rows, not genealogy data.
        after_cache = repo.capture_command_state()
        service.filter(model, GeoMapFilters(text="moved"))
        assert repo.capture_command_state() == after_cache
    finally:
        repo.close()


def test_geo_map_synchronization_manual_coordinates_views_exports_and_cancellation(tmp_path):
    repo = repository(tmp_path)
    try:
        seed(repo); service = GeoMapStudioService(repo, data_dir=tmp_path / "sidecar", geocoder=Geocoder())
        model = service.build(scope="complete_database"); service.update_missing_coordinates(model); model = service.build(scope="complete_database")
        timeline_marker = service.marker_for_timeline_event(model, "event:1")
        assert timeline_marker and timeline_marker.place == "B"
        assert service.markers_for_tree_person(model, 1)
        service.set_manual_coordinates("Unknown", 53.9, 27.56)
        assert next(marker for marker in service.build(scope="complete_database").markers if marker.place == "Unknown").geocode_status == "manual"
        with pytest.raises(RuntimeError): service.build(scope="complete_database", cancel_callback=lambda: (_ for _ in ()).throw(RuntimeError("cancelled")))
        service.save_view("World", {"zoom": 4, "center": [10, 20], "layers": ["people", "routes"], "filters": {}})
        exported_view = service.export_view("World", tmp_path / "view.json"); service.import_view(exported_view)
        assert service.load_view("World")["configuration"]["zoom"] == 4
        visible = service.filter(model, GeoMapFilters())
        for extension in ("html", "svg", "png", "pdf"):
            exported = service.export(model, visible, tmp_path / f"map.{extension}", extension, filters=GeoMapFilters(), layers=("people", "routes"))
            assert exported.exists() and exported.stat().st_size > 0
        assert json.loads(exported_view.read_text(encoding="utf-8"))["name"] == "World"
    finally:
        repo.close()