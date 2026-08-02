import csv
import sqlite3

from data_quality_service import CATEGORY_DEFINITIONS, DataQualityIssue, DataQualityService
from repository.person_repository import PersonRepository
from viewer import GenealogyViewer


class FakeSelectionTree:
    def selection(self):
        return ("selected",)


def build_repository(tmp_path):
    database = tmp_path / "quality.db"
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
        """
    )
    people = [
        (1, "I1", "", "", "", "1981", ""),
        (2, "I2", "Alex", "Duplicate", "M", "10.11.1957", "1950"),
        (3, "I2", "Alex", "Duplicate", "M", "ABT 1957", ""),
        (4, "I4", "Young", "Parent", "M", "2000", ""),
        (5, "I5", "Older", "Child", "F", "1990", ""),
        (6, "I6", "Under", "Parent", "F", "1985", ""),
        (7, "I7", "Ten", "Child", "M", "1995", ""),
        (8, "I8", "Old", "Parent", "M", "1900", ""),
        (9, "I9", "Late", "Child", "F", "1990", ""),
        (10, "I10", "No", "Family", "", "BEF 1981", ""),
        (11, "I11", "Bad", "Date", "", "31 FEB 2000", ""),
        (12, "I12", "GEDCOM", "Date", "", "22 OCT 1934", "AFT 1981"),
    ]
    connection.executemany(
        "INSERT INTO people (id, gedcom_id, first_name, last_name, sex, birth_date, death_date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)", people,
    )
    families = [
        (1, "F1", "I4", "", "unknown"),
        (2, "F2", "", "I6", "unknown"),
        (3, "F3", "I8", "", "unknown"),
        (4, "FDUP", "I2", "I12", "marriage"),
        (5, "FDUP", "I2", "I12", "marriage"),
        (6, "FEMPTY", "", "", "unknown"),
        (7, "FBROKEN", "MISSING", "", "unknown"),
    ]
    connection.executemany(
        "INSERT INTO families (id, gedcom_id, husband_id, wife_id, relationship_type) "
        "VALUES (?, ?, ?, ?, ?)", families,
    )
    connection.executemany(
        "INSERT INTO family_children (family_id, child_id) VALUES (?, ?)",
        [("F1", "I5"), ("F2", "I7"), ("F3", "I9"), ("F1", "MISSING"), ("UNKNOWN", "I1")],
    )
    connection.commit()
    connection.close()
    return PersonRepository(str(database))


def test_analysis_is_deterministic_complete_and_read_only(tmp_path):
    repository = build_repository(tmp_path)
    try:
        service = DataQualityService(repository)
        people_before = repository.list_people_full()
        families_before = repository.list_families_raw()
        children_before = repository.list_family_children_raw()

        first = service.analyze()
        second = service.analyze()

        expected_categories = [key for key, _label in CATEGORY_DEFINITIONS]
        assert list(first.categories) == expected_categories
        assert first == second
        assert all(first.categories[category] for category in expected_categories)
        assert repository.list_people_full() == people_before
        assert repository.list_families_raw() == families_before
        assert repository.list_family_children_raw() == children_before
    finally:
        repository.close()


def test_date_parser_tolerates_required_mixed_formats():
    values = ["", "22 OCT 1934", "1981", "10.11.1957", "ABT 1981", "BEF 1981", "AFT 1981", "EST 1981"]

    parsed = [DataQualityService.parse_date(value) for value in values]

    assert parsed[0].valid is True
    assert all(item.valid and (item.parseable or not item.raw) for item in parsed)
    assert DataQualityService.parse_date("31 FEB 2000").valid is False
    assert DataQualityService.parse_date("not a date").parseable is False


def test_csv_export_contains_all_deterministic_issues(tmp_path):
    repository = build_repository(tmp_path)
    try:
        service = DataQualityService(repository)
        report = service.analyze()

        output = service.export_csv(report, tmp_path / "quality.csv")

        with output.open(encoding="utf-8", newline="") as csv_file:
            rows = list(csv.reader(csv_file))
        assert rows[0] == [
            "category", "issue_type", "severity", "entity_type", "database_id",
            "gedcom_id", "display_name", "explanation",
        ]
        assert len(rows) == len(report.issues) + 1
        assert rows[1][0] == report.issues[0].category
    finally:
        repository.close()


def test_viewer_filters_by_category_and_severity(tmp_path):
    repository = build_repository(tmp_path)
    try:
        report = DataQualityService(repository).analyze()

        issues = GenealogyViewer._filter_data_quality_issues(
            report, "dangling_person_references", "Critical"
        )

        assert issues
        assert all(issue.category == "dangling_person_references" for issue in issues)
        assert all(issue.severity == "Critical" for issue in issues)
    finally:
        repository.close()


def test_viewer_opens_person_card_or_read_only_family_context():
    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer._data_quality_issue_tree = FakeSelectionTree()
    opened_people = []
    opened_families = []
    viewer.show_person = opened_people.append
    viewer._show_data_quality_family_context = opened_families.append
    person_issue = DataQualityIssue(
        "unnamed_people", "Unnamed people", "Warning", "person", 7,
        "I7", "Без имени", "Both names are empty.", 7,
    )
    family_issue = DataQualityIssue(
        "families_without_spouses", "Families without spouses", "Information",
        "family", 3, "F3", "Family F3", "No spouse references.",
    )

    viewer._data_quality_issue_map = {"selected": person_issue}
    viewer._open_selected_data_quality_issue()
    viewer._data_quality_issue_map = {"selected": family_issue}
    viewer._open_selected_data_quality_issue()

    assert opened_people == [7]
    assert opened_families == [family_issue]