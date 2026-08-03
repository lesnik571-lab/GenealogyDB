import json

import pytest

from database import initialize_database
from repository.person_repository import PersonRepository
from timeline_studio_service import TimelineStudioFilters, TimelineStudioService


def repository(tmp_path):
    database_path = tmp_path / "timeline_studio.db"
    initialize_database(database_path)
    return PersonRepository(database_path)


def seed(repo):
    people = (
        ("I1", "Alex", "Smith", "1 JAN 1900", "1 JAN 1980"),
        ("I2", "Bea", "Smith", "1905", ""),
        ("I3", "Chris", "Smith", "1930", ""),
    )
    for gedcom_id, first_name, last_name, birth_date, death_date in people:
        repo.create_person({"gedcom_id": gedcom_id, "first_name": first_name, "last_name": last_name, "birth_date": birth_date, "death_date": death_date})
    repo.create_family({"gedcom_id": "F1", "husband": "I1", "wife": "I2", "children": ["I3"], "relationship_type": "exclusive"})
    events = (
        (1, "residence", "1920", "Moscow", "Home"),
        (1, "residence", "1920", "Paris", "Other home"),
        (1, "marriage", "1910", "Moscow", "Early marriage"),
        (1, "custom", "ABT 1925", "Moscow", "Custom event"),
        (2, "immigration", "1 JAN 1920", "Moscow", "Arrived"),
        (3, "baptism", "FEB 1930", "Moscow", ""),
        (3, "custom", "", "", "Unknown date"),
        (3, "custom", "1940", "Moscow", "Duplicate"),
        (3, "custom", "1940", "Moscow", "Duplicate"),
    )
    for person_id, event_type, event_date, event_place, description in events:
        repo.conn.execute("INSERT INTO person_events (person_id, event_type, event_date, event_place, description) VALUES (?, ?, ?, ?, ?)", (person_id, event_type, event_date, event_place, description))
    repo.conn.execute("INSERT INTO sources (title) VALUES ('Registry')")
    repo.conn.execute("INSERT INTO citations (source_id, target_type, target_id, quality) VALUES (1, 'event', '5', 'high')")
    repo.conn.commit()


def test_timeline_studio_deterministic_dates_scopes_lanes_filters_and_read_only(tmp_path):
    repo = repository(tmp_path)
    try:
        seed(repo)
        service = TimelineStudioService(repo, data_dir=tmp_path / "sidecar")
        before = repo.capture_command_state()
        model = service.build(scope="complete_database")
        assert model == service.build(scope="complete_database")
        assert model.events == tuple(sorted(model.events, key=service._event_key))
        assert {event.original_date for event in model.events} >= {"1 JAN 1900", "ABT 1925", "FEB 1930", ""}
        assert next(event for event in model.events if event.event_id == "event:4").age == 25
        assert service.build(scope="selected_person", selected_person_ids=(1,)).selected_person_ids == (1,)
        assert set(service.build(scope="immediate_family", selected_person_ids=(1,)).selected_person_ids) == {1, 2, 3}
        assert [lane.lane_id for lane in service.build(scope="selected_people", selected_person_ids=(1, 2), lane_order=("person:2",)).lanes][:1] == ["person:2"]
        filtered = service.filter(model, TimelineStudioFilters(year_from=1920, place="moscow", sourced="sourced"))
        assert [event.event_id for event in filtered] == ["event:5"]
        assert repo.capture_command_state() == before
    finally:
        repo.close()


def test_timeline_studio_comparison_conflicts_sidecars_exports_and_cancellation(tmp_path):
    repo = repository(tmp_path)
    try:
        seed(repo)
        service = TimelineStudioService(repo, data_dir=tmp_path / "sidecar")
        before = repo.capture_command_state()
        model = service.build(scope="complete_database")
        comparison = service.compare(model, (1, 2, 3))
        assert comparison.simultaneous_event_ids and "moscow" in comparison.shared_places
        conflicts = {conflict for event in model.events for conflict in event.conflicts}
        assert {"simultaneous_incompatible_residences", "duplicate_event"} <= conflicts
        with pytest.raises(RuntimeError):
            service.build(scope="complete_database", cancel_callback=lambda: (_ for _ in ()).throw(RuntimeError("cancelled")))
        service.save_view("Main", {"scope": "complete_database", "filters": {}})
        service.duplicate_view("Main", "Copy")
        service.rename_view("Copy", "Renamed")
        exported_view = service.export_view("Renamed", tmp_path / "view.json")
        service.import_view(exported_view)
        assert {item["name"] for item in service.list_views()} >= {"Main", "Renamed"}
        service.save_historical_event({"title": "War", "date": "1914", "place": "Europe", "note": "Context"})
        context_model = service.build(scope="selected_person", selected_person_ids=(1,), include_historical=True)
        assert any(event.subject_type == "historical" for event in context_model.events)
        visible = service.filter(model, TimelineStudioFilters())
        for extension in ("csv", "html", "svg", "png", "pdf"):
            export = service.export(model, visible, tmp_path / f"timeline.{extension}", extension, filters=TimelineStudioFilters())
            assert export.exists() and export.stat().st_size > 0
        assert json.loads(exported_view.read_text(encoding="utf-8"))["name"] == "Renamed"
        assert repo.capture_command_state() == before
    finally:
        repo.close()