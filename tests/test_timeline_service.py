import csv
import inspect

import pytest

from timeline_service import FamilyTimelineService, TimelineFilters
from viewer import GenealogyViewer


class TimelineRepository:
    def list_people_full(self):
        return [
            {
                "id": 1,
                "first_name": "Anna",
                "last_name": "Smith",
                "birth_date": "1 JAN 1900",
                "birth_place": "Riga",
                "death_date": "1980",
                "death_place": "Paris",
                "occupation": "Doctor",
            },
            {
                "id": 2,
                "first_name": "Борис",
                "last_name": "Иванов",
                "birth_date": "",
                "birth_place": "",
                "death_date": "",
                "death_place": "",
                "occupation": "",
            },
        ]

    def list_all_person_events(self):
        event_types = (
            "baptism",
            "marriage",
            "divorce",
            "residence",
            "occupation",
            "military_service",
            "immigration",
            "emigration",
            "burial",
        )
        events = [
            {
                "person_id": 1,
                "event_type": event_type,
                "date": str(1901 + index),
                "place": "Riga" if event_type == "residence" else "Berlin",
                "description": "",
            }
            for index, event_type in enumerate(event_types)
        ]
        events.extend((
            {
                "person_id": 1,
                "event_type": "custom",
                "date": "1915",
                "place": "London & Paris",
                "description": "Custom GEDCOM event",
            },
            {
                "person_id": 2,
                "event_type": "EVEN_FAMILY_STORY",
                "date": "",
                "place": "Unknown",
                "description": "Custom GEDCOM event",
            },
        ))
        return events


def test_build_timeline_includes_all_events_ages_and_unknown_dates_last():
    entries = FamilyTimelineService(TimelineRepository()).build_timeline()
    event_types = {entry.event_type for entry in entries}

    assert {
        "birth", "baptism", "marriage", "divorce", "residence",
        "occupation", "military_service", "immigration", "emigration",
        "death", "burial", "custom", "EVEN_FAMILY_STORY",
    } <= event_types
    marriage = next(entry for entry in entries if entry.event_type == "marriage")
    assert marriage.normalized_year == 1902
    assert marriage.age == 2
    first_unknown = next(index for index, entry in enumerate(entries) if entry.normalized_year is None)
    assert all(entry.normalized_year is None for entry in entries[first_unknown:])
    assert entries[-1].person_id in {1, 2}


def test_filter_timeline_combines_year_event_surname_and_place():
    service = FamilyTimelineService(TimelineRepository())
    entries = service.build_timeline()

    visible = service.filter_timeline(entries, TimelineFilters(
        year_from=1903,
        year_to=1910,
        event_type="residence",
        surname="SMÍTH",
        place="riga",
    ))

    assert len(visible) == 1
    assert visible[0].event_type == "residence"
    assert service.filter_timeline(entries, TimelineFilters(year_from=2000)) == ()


def test_export_visible_timeline_to_csv_and_html(tmp_path):
    service = FamilyTimelineService(TimelineRepository())
    entries = service.filter_timeline(
        service.build_timeline(),
        TimelineFilters(event_type="custom"),
    )

    csv_path = service.export_csv(entries, tmp_path / "timeline.csv")
    html_path = service.export_html(entries, tmp_path / "timeline.html")

    with csv_path.open(encoding="utf-8-sig", newline="") as csv_file:
        rows = list(csv.reader(csv_file))
    assert rows[0] == ["date", "normalized_year", "person", "event_type", "place", "age"]
    assert rows[1][3] == "Событие"
    document = html_path.read_text(encoding="utf-8")
    assert "<table>" in document
    assert "London &amp; Paris" in document
    assert "GenealogyDB - Хронология" in document


def test_timeline_button_follows_family_tree_on_main_toolbar():
    source = inspect.getsource(GenealogyViewer._create_widgets)
    family_tree = source.index('text="Семейное дерево"')
    timeline = source.index('text="Хронология"')
    relationship_inspector = source.index('text="Связь между людьми"')
    assert family_tree < timeline < relationship_inspector
    assert "command=self.open_family_timeline" in source


@pytest.mark.parametrize("value, expected", (("", None), ("1900", 1900), ("9999", 9999)))
def test_optional_timeline_year_accepts_valid_values(value, expected):
    assert GenealogyViewer._optional_timeline_year(value) == expected


@pytest.mark.parametrize("value", ("0", "10000", "19x0", "-1"))
def test_optional_timeline_year_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        GenealogyViewer._optional_timeline_year(value)


def test_timeline_double_click_opens_person_card():
    application = GenealogyViewer.__new__(GenealogyViewer)
    application._family_timeline_tree = type(
        "Tree", (), {"selection": lambda self: ("row-1",)}
    )()
    application._family_timeline_person_ids = {"row-1": 42}
    opened = []
    application.show_person = opened.append

    application._open_family_timeline_person()

    assert opened == [42]