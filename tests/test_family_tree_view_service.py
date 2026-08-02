import sqlite3
from pathlib import Path

from family_tree_view_service import FamilyTreePerson, FamilyTreeViewService
from repository.person_repository import PersonRepository
from repository.relationship_service import RelationshipService
from viewer import GenealogyViewer


def build_repository(tmp_path):
    db_path = tmp_path / "family-tree-view.db"
    connection = sqlite3.connect(db_path)
    connection.executescript(Path("schema.sql").read_text(encoding="utf-8"))
    connection.executemany(
        "INSERT INTO people (gedcom_id, first_name, last_name, sex, birth_date, death_date) VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("I1", "Alex", "Center", "M", "1970", ""),
            ("I2", "Pat", "Parent", "M", "1940", "2020"),
            ("I3", "", "", "F", "1972", ""),
            ("I4", "Chris", "Child", "M", "2000", ""),
        ],
    )
    connection.execute(
        "INSERT INTO families (gedcom_id, husband_id, wife_id) VALUES ('F1', 'I2', '')"
    )
    connection.execute("INSERT INTO family_children (family_id, child_id) VALUES ('F1', 'I1')")
    connection.execute(
        "INSERT INTO families (gedcom_id, husband_id, wife_id, relationship_type) "
        "VALUES ('F2', 'I1', '3', 'marriage')"
    )
    connection.execute("INSERT INTO family_children (family_id, child_id) VALUES ('F2', 'I4')")
    connection.commit()
    connection.close()
    return PersonRepository(str(db_path))


def test_builds_read_only_immediate_family_model(tmp_path):
    repository = build_repository(tmp_path)
    try:
        service = FamilyTreeViewService(RelationshipService(repository))
        people_before = repository.list_people_full()
        families_before = repository.list_families_raw()
        children_before = repository.list_family_children_raw()

        model = service.build_tree(1)

        assert model.center.database_id == 1
        assert model.center.full_name == "Alex Center"
        assert [(person.database_id, person.full_name) for person in model.parents] == [(2, "Pat Parent")]
        assert len(model.partners) == 1
        assert model.partners[0].database_id == 3
        assert model.partners[0].full_name == "Без имени"
        assert model.partners[0].is_unnamed is True
        assert model.partners[0].birth_date == "1972"
        assert [(person.database_id, person.gedcom_id) for person in model.children] == [(4, "I4")]
        assert repository.list_people_full() == people_before
        assert repository.list_families_raw() == families_before
        assert repository.list_family_children_raw() == children_before
    finally:
        repository.close()


def test_builds_ui_relationship_labels_without_changing_model(tmp_path):
    repository = build_repository(tmp_path)
    try:
        service = FamilyTreeViewService(RelationshipService(repository))
        model = service.build_tree(1)

        presentation = service.build_card_presentation(model)

        assert presentation[1] == {"sex": "M", "relationship": "Current person"}
        assert presentation[2] == {"sex": "M", "relationship": "Father"}
        assert presentation[3] == {"sex": "F", "relationship": "Wife"}
        assert presentation[4] == {"sex": "M", "relationship": "Son"}
    finally:
        repository.close()


def test_relationship_labels_cover_gender_and_partner_fallbacks():
    label = FamilyTreeViewService._relationship_label

    assert label("parent", "M", parent_role="father") == "Father"
    assert label("parent", "F", parent_role="mother") == "Mother"
    assert label("partner", "M", relationship_type="marriage") == "Husband"
    assert label("partner", "F", relationship_type="marriage") == "Wife"
    assert label("partner", "M", relationship_type="civil_partner") == "Partner"
    assert label("child", "M") == "Son"
    assert label("child", "F") == "Daughter"
    assert label("child", "") == "Child"


def test_can_recenter_from_a_relative_database_id(tmp_path):
    repository = build_repository(tmp_path)
    try:
        service = FamilyTreeViewService(RelationshipService(repository))

        model = service.build_tree(4)

        assert model.center.database_id == 4
        assert {person.database_id for person in model.parents} == {1, 3}
        assert model.partners == ()
        assert model.children == ()
    finally:
        repository.close()


class FakeButton:
    def __init__(self):
        self.state = None

    def configure(self, **kwargs):
        self.state = kwargs.get("state")


class FakeTreeService:
    def build_tree(self, person_id):
        return type("Model", (), {"center_id": person_id})()


def test_family_tree_history_navigation_is_headless():
    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer.family_tree_view_service = FakeTreeService()
    viewer._family_tree_window = None
    viewer._family_tree_back_button = FakeButton()
    viewer._family_tree_forward_button = FakeButton()
    rendered = []
    viewer._render_family_tree = lambda model: rendered.append(model.center_id)

    viewer._start_family_tree_history(1)
    viewer._load_family_tree_person(2)
    viewer._family_tree_back()
    viewer._family_tree_forward()
    viewer._family_tree_return_to_original()

    assert rendered == [1, 2, 1, 2, 1]
    assert viewer._family_tree_original_person_id == 1
    assert viewer._family_tree_history == [1, 2, 1]
    assert viewer._family_tree_history_index == 2
    assert viewer._family_tree_back_button.state == "normal"
    assert viewer._family_tree_forward_button.state == "disabled"


def test_family_tree_zoom_and_card_colors_are_headless():
    viewer = GenealogyViewer.__new__(GenealogyViewer)
    current = FamilyTreePerson(1, "I1", "Alex Center", "1970", "", False)
    unnamed = FamilyTreePerson(2, "I2", "Без имени", "", "", True)
    viewer._family_tree_model = type("Model", (), {"center": current})()

    assert viewer._clamp_family_tree_zoom(0.1) == 0.7
    assert viewer._clamp_family_tree_zoom(2.5) == 2.0
    assert viewer._family_tree_card_background(current) == "#dceeff"
    assert viewer._family_tree_card_background(unnamed) == "#fff3bf"
    assert viewer._family_tree_card_border(current, "M") == "#79b9e7"
    assert viewer._family_tree_card_border(current, "F") == "#e8a1b7"
    assert viewer._family_tree_card_border(unnamed, "M") == "#c63c3c"