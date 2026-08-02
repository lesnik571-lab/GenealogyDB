import csv
import inspect

import pytest

from database import initialize_database
from repository.person_repository import PersonRepository
from source_service import SourceService
from viewer import GenealogyViewer


@pytest.fixture
def source_context(tmp_path):
    database_path = tmp_path / "sources.db"
    initialize_database(database_path)
    repository = PersonRepository(database_path)
    person_id = repository.create_person({
        "gedcom_id": "I1", "first_name": "Anna", "last_name": "Smith",
    })
    family_id = repository.create_family({
        "gedcom_id": "F1", "husband": "I1", "wife": "", "children": [],
    })
    event_id = repository.create_person_event({
        "person_id": person_id, "event_type": "residence", "date": "1900",
        "place": "Riga", "description": "",
    })
    service = SourceService(repository)
    yield service, repository, person_id, family_id, event_id
    repository.close()


def source_data(title="Civil Register", repository="State Archive"):
    return {
        "title": title,
        "author": "A. Archivist",
        "publication": "Archive Edition",
        "repository": repository,
        "call_number": "F-12",
        "url": "https://example.test/source",
        "notes": "Verified",
    }


def test_source_crud_supports_all_required_fields(source_context):
    service, _repository, _person_id, _family_id, _event_id = source_context
    created = service.create_source(source_data())

    assert created["title"] == "Civil Register"
    assert created["author"] == "A. Archivist"
    assert created["publication"] == "Archive Edition"
    assert created["repository"] == "State Archive"
    assert created["call_number"] == "F-12"
    assert created["url"] == "https://example.test/source"
    assert created["notes"] == "Verified"

    updated = service.update_source(created["id"], source_data("Updated Register"))
    assert updated["title"] == "Updated Register"
    assert service.delete_source(created["id"]) is True
    assert service.list_sources() == []
    with pytest.raises(ValueError, match="Название"):
        service.create_source({})


def test_citations_attach_source_to_every_supported_target(source_context):
    service, _repository, person_id, family_id, event_id = source_context
    source = service.create_source(source_data())
    targets = (
        ("person", person_id),
        ("family", family_id),
        ("event", event_id),
        ("relationship", family_id),
    )

    for target_type, target_id in targets:
        citation = service.create_citation(
            source["id"], target_type, target_id,
            page="p. 12", quality="high", transcription="Record text", comment="Checked",
        )
        assert citation["page"] == "p. 12"
        assert citation["quality"] == "high"
        assert citation["transcription"] == "Record text"
        assert citation["comment"] == "Checked"

    rows = service.browser_rows()
    assert {row["target_type"] for row in rows} == {"person", "family", "event", "relationship"}
    assert all(row["target_label"] for row in rows)
    assert all(row["linked_person_id"] == person_id for row in rows)


def test_browser_statistics_find_orphans_rank_sources_and_repositories(source_context):
    service, _repository, person_id, _family_id, _event_id = source_context
    referenced = service.create_source(source_data("Referenced", "Archive A"))
    orphan = service.create_source(source_data("Orphan", "Archive B"))
    service.create_citation(referenced["id"], "person", person_id)
    service.create_citation(referenced["id"], "person", person_id, page="2")

    statistics = service.statistics()

    assert statistics["source_count"] == 2
    assert statistics["citation_count"] == 2
    assert [source["id"] for source in statistics["orphan_sources"]] == [orphan["id"]]
    assert statistics["most_referenced"][0] == ("Referenced", 2)
    assert statistics["by_target_type"]["person"] == 2
    assert statistics["by_repository"] == {"Archive A": 1, "Archive B": 1}


def test_csv_export_contains_source_usage_and_citation_fields(source_context, tmp_path):
    service, _repository, person_id, _family_id, _event_id = source_context
    source = service.create_source(source_data())
    service.create_citation(
        source["id"], "person", person_id,
        page="p. 3", quality="medium", transcription="Text", comment="Note",
    )

    destination = service.export_csv(tmp_path / "sources.csv")

    with destination.open(encoding="utf-8-sig", newline="") as csv_file:
        rows = list(csv.DictReader(csv_file))
    assert rows[0]["title"] == "Civil Register"
    assert rows[0]["target_type"] == "person"
    assert rows[0]["page"] == "p. 3"
    assert rows[0]["quality"] == "medium"


def test_linked_object_deletion_removes_its_citations(source_context):
    service, repository, person_id, family_id, event_id = source_context
    source = service.create_source(source_data())
    person_citation = service.create_citation(source["id"], "person", person_id)
    event_citation = service.create_citation(source["id"], "event", event_id)
    family_citation = service.create_citation(source["id"], "family", family_id)
    relationship_citation = service.create_citation(source["id"], "relationship", family_id)

    repository.delete_person_event(event_id)
    repository.delete_family(family_id)
    remaining_ids = {citation["id"] for citation in service.list_citations()}

    assert person_citation["id"] in remaining_ids
    assert event_citation["id"] not in remaining_ids
    assert family_citation["id"] not in remaining_ids
    assert relationship_citation["id"] not in remaining_ids


def test_source_manager_button_and_browser_are_wired_into_viewer():
    source = inspect.getsource(GenealogyViewer._create_widgets)
    assert 'text="Источники"' in source
    assert "command=self.open_source_manager" in source
    browser_source = inspect.getsource(GenealogyViewer._build_source_browser_tab)
    assert 'text="Экспорт CSV"' in browser_source
    assert 'tree.bind("<Double-1>", self._open_source_usage)' in browser_source


def test_source_browser_double_click_opens_linked_person():
    application = GenealogyViewer.__new__(GenealogyViewer)
    application._source_browser_tree = type(
        "Tree", (), {"selection": lambda self: ("usage-1",)}
    )()
    application._source_usage_map = {
        "usage-1": {
            "target_type": "person",
            "target_id": "42",
            "linked_person_id": 42,
        }
    }
    opened = []
    application.show_person = opened.append

    application._open_source_usage()

    assert opened == [42]
