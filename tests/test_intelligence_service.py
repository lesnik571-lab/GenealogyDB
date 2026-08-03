import inspect

from database import initialize_database
from evidence_service import EvidenceOperation, EvidenceService
from intelligence_service import IntelligenceService
from repository.person_repository import PersonRepository
from viewer import GenealogyViewer


def build_repository(tmp_path):
    path = tmp_path / "intelligence.db"; initialize_database(path); repository = PersonRepository(path)
    people = (
        {"gedcom_id": "I1", "first_name": "Anna", "last_name": "Smith", "birth_date": "1900", "death_date": "1890", "birth_place": "St. Petersburg"},
        {"gedcom_id": "I2", "first_name": "Anna", "last_name": "Smith", "birth_date": "1900", "birth_place": "Saint Petersburg"},
        {"gedcom_id": "I3", "first_name": "Young", "last_name": "Parent", "birth_date": "2000"},
        {"gedcom_id": "I4", "first_name": "Older", "last_name": "Child", "birth_date": "1990"},
        {"gedcom_id": "I5", "first_name": "Sibling", "last_name": "Child", "birth_date": "1992"},
        {"gedcom_id": "I6", "first_name": "Solo", "last_name": "Person", "birth_date": "1970"},
    )
    ids = [repository.create_person(person) for person in people]
    family = repository.create_family({"gedcom_id": "F1", "husband": "I3", "wife": "", "children": ["I4", "I5"]})
    evidence = EvidenceService(repository); first = evidence.sources.create_source({"title": "Parish Register"}); second = evidence.sources.create_source({"title": "parish register"})
    evidence.execute(evidence.preview((EvidenceOperation("attach_citation", source_id=first["id"], target_type="person", target_id=str(ids[0]), data={}), EvidenceOperation("attach_citation", source_id=second["id"], target_type="person", target_id=str(ids[1]), data={}))))
    return repository, ids, family


def test_intelligence_detects_duplicates_chronology_confidence_and_is_read_only(tmp_path):
    repository, ids, _family = build_repository(tmp_path)
    try:
        service = IntelligenceService(repository, tmp_path / "sidecar")
        before = repository.capture_command_state(); first = service.analyze(); second = service.analyze()
        categories = {item.category for item in first.suggestions}
        duplicate = next(item for item in first.suggestions if item.category == "probable_duplicate_people")
        chronology = next(item for item in first.suggestions if item.category == "chronology")
        assert first.suggestions == second.suggestions
        assert {"probable_duplicate_people", "chronology", "parent_age", "possible_sibling_group", "isolated_person", "duplicate_source"} <= categories
        assert duplicate.confidence == 94 and chronology.confidence == 99
        assert duplicate.explanation and duplicate.supporting_records and duplicate.reason_codes
        assert repository.capture_command_state() == before
    finally:
        repository.close()


def test_ignore_filters_persists_and_exports_all_formats(tmp_path):
    repository, _ids, _family = build_repository(tmp_path)
    try:
        service = IntelligenceService(repository, tmp_path / "sidecar"); report = service.analyze()
        target = report.suggestions[0]; service.ignore(target.suggestion_id); service.bookmark(target.suggestion_id)
        fresh = IntelligenceService(repository, tmp_path / "sidecar")
        assert target.suggestion_id not in {item.suggestion_id for item in fresh.filter(report)}
        assert target.suggestion_id in fresh._dispositions()["bookmarks"]
        for extension in ("csv", "json", "markdown", "html", "pdf"):
            output = service.export(report, tmp_path / f"intelligence.{extension}", extension)
            assert output.exists() and output.stat().st_size > 0
    finally:
        repository.close()


def test_cancellation_and_filters(tmp_path):
    repository, ids, family = build_repository(tmp_path)
    try:
        service = IntelligenceService(repository, tmp_path / "sidecar")
        try:
            service.analyze(cancel_callback=lambda: (_ for _ in ()).throw(RuntimeError("cancelled")))
        except RuntimeError as error:
            assert str(error) == "cancelled"
        report = service.analyze()
        assert service.filter(report, confidence=90)
        assert all(ids[0] in item.person_ids or ids[1] in item.person_ids for item in service.filter(report, person_id=ids[0]) if item.person_ids)
        assert service.filter(report, family_id=family)
    finally:
        repository.close()

def test_analysis_menu_registers_intelligence_center_without_opening_tkinter():
    source = inspect.getsource(GenealogyViewer._create_widgets)
    assert 'label="Analysis"' in source
    assert 'label="Intelligence Center"' in source
