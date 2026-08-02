import sqlite3
from pathlib import Path

from relationship_path_service import RelationshipPathService
from repository.person_repository import PersonRepository
from viewer import GenealogyViewer


def build_repository(tmp_path):
    database = tmp_path / "relationship-path.db"
    connection = sqlite3.connect(database)
    connection.executescript(Path("schema.sql").read_text(encoding="utf-8"))
    people = [
        ("I1", "Alex", "Root", "M"), ("I2", "Bob", "Father", "M"),
        ("I3", "Mia", "Mother", "F"), ("I4", "Ben", "Brother", "M"),
        ("I5", "Eva", "Spouse", "F"), ("I6", "Nora", "Inlaw", "F"),
        ("I7", "Greg", "Grandfather", "M"), ("I8", "Sam", "GreatUncle", "M"),
        ("I9", "Casey", "CousinParent", "F"), ("I10", "Taylor", "SecondCousin", "F"),
        ("I11", "George", "Ancestor", "M"),
    ]
    connection.executemany(
        "INSERT INTO people (gedcom_id, first_name, last_name, sex) VALUES (?, ?, ?, ?)", people
    )
    families = [
        ("F1", "I2", "I3", ["I1", "I4"]),
        ("F2", "I1", "I5", []),
        ("F3", "", "I6", ["I5"]),
        ("F4", "I11", "", ["I7", "I8"]),
        ("F5", "I8", "", ["I9"]),
        ("F6", "", "I9", ["I10"]),
        ("F7", "I7", "", ["I2"]),
    ]
    for family_id, husband, wife, children in families:
        connection.execute(
            "INSERT INTO families (gedcom_id, husband_id, wife_id) VALUES (?, ?, ?)",
            (family_id, husband, wife),
        )
        connection.executemany(
            "INSERT INTO family_children (family_id, child_id) VALUES (?, ?)",
            [(family_id, child) for child in children],
        )
    connection.commit()
    connection.close()
    return PersonRepository(str(database))


def test_finds_shortest_typed_paths_and_intermediate_people(tmp_path):
    repository = build_repository(tmp_path)
    try:
        service = RelationshipPathService(repository)
        people_before = repository.list_people_full()
        families_before = repository.list_families_raw()
        children_before = repository.list_family_children_raw()

        father = service.find_shortest_path("I1", "I2")
        grandfather = service.find_shortest_path(1, "I7")
        sibling = service.find_shortest_path("I1", "I4")
        in_law = service.find_shortest_path("I1", "I6")

        assert father.description == "Father"
        assert father.distance == 1
        assert father.generations == 1
        assert grandfather.description == "Grandfather"
        assert [person.database_id for person in grandfather.people] == [1, 2, 7]
        assert [step.relationship_type for step in sibling.steps] == ["sibling"]
        assert in_law.description == "Mother-in-law"
        assert [step.relationship_type for step in in_law.steps] == ["spouse", "parent"]
        assert repository.list_people_full() == people_before
        assert repository.list_families_raw() == families_before
        assert repository.list_family_children_raw() == children_before
    finally:
        repository.close()


def test_describes_second_cousin_and_exports_text(tmp_path):
    repository = build_repository(tmp_path)
    try:
        service = RelationshipPathService(repository)

        path = service.find_shortest_path("I1", "I10")
        exported = service.export_path_text(path, tmp_path / "path.txt")

        assert path.description == "Second cousin"
        assert [step.relationship_type for step in path.steps] == [
            "parent", "parent", "sibling", "child", "child"
        ]
        assert path.distance == 5
        assert path.generations == 4
        text = exported.read_text(encoding="utf-8")
        assert "Relationship: Second cousin" in text
        assert "--sibling-->" in text
        assert "Taylor SecondCousin" in text
    finally:
        repository.close()


class FakePathService:
    def __init__(self, path):
        self.path = path
        self.references = None

    def find_shortest_path(self, source_reference, target_reference):
        self.references = (source_reference, target_reference)
        return self.path


class FakePathTree:
    def identify_row(self, _position):
        return "row-1"

    def set(self, _item_id, column):
        assert column == "database_id"
        return "7"


def test_viewer_selects_two_people_and_displays_every_path_person(tmp_path):
    repository = build_repository(tmp_path)
    try:
        path = RelationshipPathService(repository).find_shortest_path("I1", "I7")
        viewer = GenealogyViewer.__new__(GenealogyViewer)
        choices = iter(("I1", "I7"))
        viewer._choose_person = lambda *_args, **_kwargs: next(choices)
        viewer.relationship_path_service = FakePathService(path)
        shown = []
        viewer._show_relationship_inspector = shown.append

        viewer.open_relationship_inspector()
        rows = viewer._relationship_path_rows(path)

        assert viewer.relationship_path_service.references == ("I1", "I7")
        assert shown == [path]
        assert [row[3] for row in rows] == [person.database_id for person in path.people]
        assert [row[1] for row in rows[1:]] == [step.relationship_type for step in path.steps]
    finally:
        repository.close()


def test_clicking_path_row_opens_person_in_viewer():
    viewer = GenealogyViewer.__new__(GenealogyViewer)
    opened = []
    viewer.show_person = opened.append
    event = type("Event", (), {"y": 10})()

    viewer._open_relationship_path_person(FakePathTree(), event)

    assert opened == [7]