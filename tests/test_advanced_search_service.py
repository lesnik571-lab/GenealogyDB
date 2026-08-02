import csv
import sqlite3

import pytest

from advanced_search_service import AdvancedSearchFilters, AdvancedSearchResult, AdvancedSearchService
from repository.person_repository import PersonRepository
from viewer import GenealogyViewer


class FakeVariable:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value


class FakeTree:
    def __init__(self):
        self.rows = []

    def get_children(self):
        return ()

    def insert(self, _parent, _position, values):
        self.rows.append(values)


class FakeStatus:
    def __init__(self):
        self.text = ""

    def config(self, **values):
        self.text = values.get("text", self.text)


class FakeSearchService:
    def __init__(self, results):
        self.results = results
        self.searched = None
        self.saved = None

    def search(self, filters):
        self.searched = filters
        return self.results

    def save_last_search(self, filters):
        self.saved = filters


def build_repository(tmp_path):
    database = tmp_path / "advanced_search.db"
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE people (
            id INTEGER PRIMARY KEY, gedcom_id TEXT, first_name TEXT, last_name TEXT,
            sex TEXT, birth_date TEXT, birth_place TEXT, death_date TEXT,
            death_place TEXT, occupation TEXT, note TEXT
        );
        CREATE TABLE families (
            id INTEGER PRIMARY KEY, gedcom_id TEXT, husband_id TEXT, wife_id TEXT,
            relationship_type TEXT NOT NULL DEFAULT 'unknown'
        );
        CREATE TABLE family_children (family_id TEXT, child_id TEXT);
        CREATE TABLE person_events (
            id INTEGER PRIMARY KEY, person_id INTEGER, event_type TEXT,
            event_date TEXT, event_place TEXT, description TEXT
        );
        CREATE TABLE person_media (
            id INTEGER PRIMARY KEY, person_id INTEGER, media_type TEXT,
            title TEXT, file_path TEXT, description TEXT, created_at TEXT
        );
        """
    )
    connection.executemany(
        "INSERT INTO people VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "I1", "Ivan Petrovich", "Sidorov", "M", "10 NOV 1950", "Moscow", "2020", "Tver", "Engineer", "navy veteran"),
            (2, "I2", "Anna", "Sidorova", "F", "1975", "Tver", "", "", "Doctor", ""),
            (3, "I3", "Petr Ivanovich", "Sidorov", "M", "2001", "Moscow", "", "", "Student", "archive note"),
            (4, "I4", "Ivan Pavlovich", "Other", "M", "1948", "Moscow", "2010", "Kazan", "Engineer", "navy veteran"),
        ],
    )
    connection.executemany(
        "INSERT INTO families VALUES (?, ?, ?, ?, ?)",
        [(1, "F1", "I1", "2", "marriage")],
    )
    connection.execute("INSERT INTO family_children VALUES ('F1', 'I3')")
    connection.execute("INSERT INTO person_events VALUES (1, 1, 'residence', '1980', 'Moscow', '')")
    connection.execute("INSERT INTO person_media VALUES (1, 1, 'photo', '', 'photo.jpg', '', '2026-01-01')")
    connection.commit()
    connection.close()
    return PersonRepository(str(database))


def test_simultaneous_filters_use_and_partial_matching_and_ranges(tmp_path):
    repository = build_repository(tmp_path)
    try:
        service = AdvancedSearchService(repository)
        filters = AdvancedSearchFilters(
            first_name="iva", patronymic="pet", last_name="sid", sex="m",
            birth_year_from=1949, birth_year_to=1951, death_year_from=2019,
            death_year_to=2021, birth_place="mos", death_place="tve",
            occupation="gin", note_contains="veter", gedcom_id="i1",
            database_id=1, has_spouses=True, has_children=True,
            has_events=True, has_attachments=True,
        )

        results = service.search(filters)

        assert [result.database_id for result in results] == [1]
        assert service.search(AdvancedSearchFilters(first_name="iva", last_name="other"))[0].database_id == 4
        assert service.search(AdvancedSearchFilters(first_name="iva", last_name="sidor", occupation="doctor")) == ()
    finally:
        repository.close()


def test_parent_flag_supports_mixed_numeric_and_gedcom_references(tmp_path):
    repository = build_repository(tmp_path)
    try:
        results = AdvancedSearchService(repository).search(AdvancedSearchFilters(has_parents=True))
        assert [result.database_id for result in results] == [3]
    finally:
        repository.close()


def test_last_search_round_trip_and_invalid_range(tmp_path):
    repository = build_repository(tmp_path)
    try:
        service = AdvancedSearchService(repository, tmp_path / "last-search.json")
        filters = AdvancedSearchFilters(first_name="Иван", birth_year_from=1900, has_events=True)

        service.save_last_search(filters)

        assert service.load_last_search() == filters
        with pytest.raises(ValueError, match="birth year"):
            service.search(AdvancedSearchFilters(birth_year_from=2000, birth_year_to=1900))
    finally:
        repository.close()


def test_export_csv_uses_current_result_order(tmp_path):
    repository = build_repository(tmp_path)
    try:
        service = AdvancedSearchService(repository)
        results = service.search(AdvancedSearchFilters(last_name="sidor"))

        output = service.export_csv(results, tmp_path / "results.csv")

        with output.open(encoding="utf-8", newline="") as csv_file:
            rows = list(csv.reader(csv_file))
        assert rows[0][:4] == ["database_id", "gedcom_id", "first_name", "last_name"]
        assert [row[0] for row in rows[1:]] == ["1", "2", "3"]
    finally:
        repository.close()


def test_viewer_populates_results_updates_counter_and_saves_filters():
    viewer = GenealogyViewer.__new__(GenealogyViewer)
    defaults = AdvancedSearchFilters(first_name="Ivan", has_children=True)
    viewer._advanced_search_vars = {
        field: FakeVariable(getattr(defaults, field))
        for field in defaults.__dataclass_fields__
    }
    result = AdvancedSearchResult(
        7, "I7", "Ivan Petrovich", "Sidorov", "M", "1950", "Moscow",
        "2020", "Tver", "Engineer", "note",
    )
    viewer.advanced_search_service = FakeSearchService((result,))
    viewer.tree = FakeTree()
    viewer.status_label = FakeStatus()
    viewer.root = type("Root", (), {"update_idletasks": lambda self: None})()
    viewer._advanced_search_after_id = "scheduled"
    viewer._clear_tree = lambda: None

    viewer.search_people()

    assert viewer.advanced_search_service.searched == defaults
    assert viewer.advanced_search_service.saved == defaults
    assert viewer.tree.rows == [(7, "Ivan Petrovich Sidorov", "1950", "2020")]
    assert viewer.status_label.text == "Найдено: 1"