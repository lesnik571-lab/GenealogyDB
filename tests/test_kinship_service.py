import csv
import inspect

import pytest

from kinship_service import KinshipService
from viewer import GenealogyViewer


class PedigreeRepository:
    def __init__(self, people, families, children):
        self.people = people
        self.families = families
        self.children = children

    def list_people_full(self):
        return self.people

    def list_families_raw(self):
        return self.families

    def list_family_children_raw(self):
        return self.children

    def resolve_person_reference(self, reference):
        text = str(reference)
        for person in self.people:
            if text in {str(person["id"]), person["gedcom_id"]}:
                return person["id"]
        return None


def person(person_id, name):
    return {
        "id": person_id,
        "gedcom_id": f"I{person_id}",
        "first_name": name,
        "last_name": "Family",
    }


def family(family_id, husband, wife):
    return {
        "id": family_id,
        "gedcom_id": f"F{family_id}",
        "husband_id": f"I{husband}" if husband else "",
        "wife_id": f"I{wife}" if wife else "",
    }


def child(family_id, person_id):
    return {"family_id": f"F{family_id}", "child_id": f"I{person_id}"}


def test_direct_parent_child_path_and_metrics():
    repository = PedigreeRepository(
        [person(1, "Parent"), person(2, "Child")],
        [family(1, 1, None)],
        [child(1, 2)],
    )

    analysis = KinshipService(repository).analyze(1, 2)

    assert analysis.shortest_path.edges == ("child",)
    assert analysis.shortest_path.is_direct_blood is True
    assert analysis.blood_relationship is True
    assert analysis.relationship_degree == 1
    assert analysis.generation_distance == (0, 1)
    assert analysis.nearest_common_ancestors[0].person.database_id == 1
    assert analysis.coefficient_of_relationship == pytest.approx(0.5)


def test_full_siblings_have_two_blood_paths_and_half_relationship_coefficient():
    repository = PedigreeRepository(
        [person(1, "Father"), person(2, "Mother"), person(3, "Anna"), person(4, "Bob")],
        [family(1, 1, 2)],
        [child(1, 3), child(1, 4)],
    )

    analysis = KinshipService(repository).analyze(3, 4)

    assert analysis.relationship_degree == 2
    assert analysis.generation_distance == (1, 1)
    assert {item.person.database_id for item in analysis.nearest_common_ancestors} == {1, 2}
    assert analysis.coefficient_of_relationship == pytest.approx(0.5)
    assert len([path for path in analysis.paths if path.is_blood]) == 2
    assert len([path for path in analysis.paths if path.is_direct_blood]) == 2
    assert any(not path.is_blood for path in analysis.paths)


def cousin_pedigree(include_child=False):
    people = [
        person(1, "Grandfather"), person(2, "Grandmother"),
        person(3, "SiblingA"), person(4, "SiblingB"),
        person(5, "PartnerA"), person(6, "PartnerB"),
        person(7, "CousinA"), person(8, "CousinB"),
    ]
    families = [family(1, 1, 2), family(2, 3, 5), family(3, 4, 6)]
    children = [child(1, 3), child(1, 4), child(2, 7), child(3, 8)]
    if include_child:
        people.append(person(9, "InbredChild"))
        families.append(family(4, 7, 8))
        children.append(child(4, 9))
    return PedigreeRepository(people, families, children)


def test_first_cousins_have_expected_common_ancestors_and_coefficient():
    analysis = KinshipService(cousin_pedigree()).analyze(7, 8)

    assert analysis.relationship_degree == 4
    assert analysis.generation_distance == (2, 2)
    assert {item.person.database_id for item in analysis.nearest_common_ancestors} == {1, 2}
    assert analysis.coefficient_of_relationship == pytest.approx(0.125)
    assert analysis.blood_relationship is True


def test_inbreeding_coefficient_for_child_of_first_cousins():
    analysis = KinshipService(cousin_pedigree(include_child=True)).analyze(9, 1)

    coefficients = {item.database_id: coefficient for item, coefficient in analysis.inbreeding_coefficients}
    assert coefficients[9] == pytest.approx(0.0625)


def test_spouses_have_social_path_but_no_blood_relationship():
    repository = PedigreeRepository(
        [person(1, "Husband"), person(2, "Wife")],
        [family(1, 1, 2)],
        [],
    )

    analysis = KinshipService(repository).analyze(1, 2)

    assert analysis.shortest_path.edges == ("spouse",)
    assert analysis.blood_relationship is False
    assert analysis.relationship_degree is None
    assert analysis.common_ancestors == ()
    assert analysis.coefficient_of_relationship == 0


def test_disconnected_people_have_no_paths_or_blood_metrics():
    repository = PedigreeRepository(
        [person(1, "First"), person(2, "Second")],
        [],
        [],
    )

    analysis = KinshipService(repository).analyze(1, 2)

    assert analysis.shortest_path is None
    assert analysis.paths == ()
    assert analysis.blood_relationship is False
    assert analysis.relationship_degree is None
    assert analysis.generation_distance is None


def test_same_person_has_zero_degree_and_unit_coefficient():
    repository = PedigreeRepository([person(1, "Same")], [], [])

    analysis = KinshipService(repository).analyze(1, 1)

    assert analysis.shortest_path.distance == 0
    assert analysis.shortest_path.is_direct_blood is True
    assert analysis.relationship_degree == 0
    assert analysis.generation_distance == (0, 0)
    assert analysis.coefficient_of_relationship == 1


def test_exports_include_all_visible_analysis_paths(tmp_path):
    analysis = KinshipService(cousin_pedigree()).analyze(7, 8)
    service = KinshipService(cousin_pedigree())

    csv_path = service.export_csv(analysis, tmp_path / "kinship.csv")
    html_path = service.export_html(analysis, tmp_path / "kinship.html")
    pdf_path = service.export_pdf(analysis, tmp_path / "kinship.pdf")

    with csv_path.open(encoding="utf-8-sig", newline="") as csv_file:
        rows = list(csv.DictReader(csv_file))
    assert len(rows) == len(analysis.paths)
    assert "Coefficient of relationship" not in rows[0]
    html_document = html_path.read_text(encoding="utf-8")
    assert "Coefficient of relationship" in html_document
    assert "class=blood" in html_document or "class=direct" in html_document
    assert pdf_path.read_bytes().startswith(b"%PDF-1.4")


def test_viewer_toolbar_exposes_kinship_analyzer():
    source = inspect.getsource(GenealogyViewer._create_widgets)

    assert 'text="Анализ родства"' in source
    assert "command=self.open_kinship_analyzer" in source


def test_viewer_selects_two_people_and_opens_analysis():
    repository = PedigreeRepository(
        [person(1, "Parent"), person(2, "Child")],
        [family(1, 1, None)],
        [child(1, 2)],
    )
    analysis = KinshipService(repository).analyze(1, 2)

    class FakeService:
        references = None

        def analyze(self, source, target):
            self.references = (source, target)
            return analysis

    viewer = GenealogyViewer.__new__(GenealogyViewer)
    choices = iter(("I1", "I2"))
    viewer._choose_person = lambda *_args, **_kwargs: next(choices)
    viewer.kinship_service = FakeService()
    shown = []
    viewer._show_kinship_analysis = shown.append

    viewer.open_kinship_analyzer()

    assert viewer.kinship_service.references == ("I1", "I2")
    assert shown == [analysis]
