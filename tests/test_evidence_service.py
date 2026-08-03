import csv
import inspect
import json
import sqlite3
from types import SimpleNamespace

import pytest

from audit_service import AuditService
from database import initialize_database
from evidence_service import (
    CONFIDENCE_LEVELS,
    EvidenceAppliedCommand,
    EvidenceOperation,
    EvidenceSafetyError,
    EvidenceService,
)
from repository.person_repository import PersonRepository
from undo_manager import UndoManager
from viewer import GenealogyViewer


@pytest.fixture
def evidence_context(tmp_path):
    database_path = tmp_path / "evidence.db"
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
    yield EvidenceService(repository), repository, person_id, family_id, event_id
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


def apply(service, *operations):
    return service.execute(service.preview(operations))


def test_evidence_model_round_trips_all_confidence_levels_proof_and_media(evidence_context, tmp_path):
    service, repository, person_id, _family_id, _event_id = evidence_context
    source = service.sources.create_source(source_data())
    media_path = tmp_path / "record.jpg"
    media_path.write_bytes(b"image")
    media_id = repository.create_person_media({
        "person_id": person_id, "media_type": "photo", "title": "Record",
        "file_path": str(media_path), "description": "",
    })

    for index, confidence in enumerate(CONFIDENCE_LEVELS, start=1):
        apply(service, EvidenceOperation(
            "attach_citation", source_id=source["id"], target_type="person",
            target_id=str(person_id), data={
                "page": str(index), "confidence": confidence,
                "proof_status": "Supported", "media_reference": str(media_id),
                "transcription": f"Text {index}", "comment": "Reviewed",
            },
        ))

    model = service.build_model()
    assert {citation["confidence"] for citation in model.citations} == set(CONFIDENCE_LEVELS)
    assert {citation["proof_status"] for citation in model.citations} == {"Supported"}
    assert {citation["media_reference"] for citation in model.citations} == {str(media_id)}
    assert all(citation["comment"] == "Reviewed" for citation in model.citations)
    assert not [issue for issue in model.issues if issue.kind == "broken_media"]


def test_people_events_and_relationships_accept_multiple_citations(evidence_context):
    service, _repository, person_id, family_id, event_id = evidence_context
    first = service.sources.create_source(source_data("Register A"))
    second = service.sources.create_source(source_data("Register B"))
    targets = (
        ("person", person_id), ("event", event_id),
        ("family", family_id), ("relationship", family_id),
    )

    operations = tuple(
        EvidenceOperation(
            "attach_citation", source_id=source["id"], target_type=target_type,
            target_id=str(target_id), data={"page": str(source["id"]), "confidence": "Strong"},
        )
        for target_type, target_id in targets
        for source in (first, second)
    )
    result = apply(service, *operations)

    assert len(result.operations) == 8
    assert len(service.build_model().citations) == 8
    assert {usage["target_type"] for usage in service.build_model().usages} == {
        "person", "event", "family", "relationship",
    }


def test_diagnostics_find_duplicates_orphans_missing_repositories_and_broken_media(evidence_context):
    service, repository, person_id, _family_id, _event_id = evidence_context
    first = service.sources.create_source(source_data("Duplicate", ""))
    service.sources.create_source(source_data("Duplicate", ""))
    citation = {
        "source_id": first["id"], "target_type": "person", "target_id": str(person_id),
        "page": "1", "quality": "Strong", "transcription": "", "comment": "",
    }
    repository.create_citation_record(citation)
    repository.create_citation_record(citation)
    repository.create_citation_record({
        **citation, "target_id": "999", "page": "orphan",
    })
    repository.create_citation_record({
        **citation, "page": "media", "comment": service._encode_comment("", "Unreviewed", "404"),
    })

    issues = service.build_model().issues
    kinds = {issue.kind for issue in issues}

    assert {"duplicate_source", "duplicate_citation", "orphan_citation", "missing_repository", "broken_media"} <= kinds


def test_create_edit_duplicate_merge_attach_edit_and_detach(evidence_context):
    service, _repository, person_id, _family_id, _event_id = evidence_context
    apply(service, EvidenceOperation("create_source", data=source_data("Primary")))
    primary = service.sources.list_sources()[0]
    apply(service, EvidenceOperation(
        "edit_source", source_id=primary["id"], data=source_data("Primary Updated"),
    ))
    apply(service, EvidenceOperation("duplicate_source", source_id=primary["id"]))
    sources = service.sources.list_sources()
    duplicate = next(source for source in sources if source["id"] != primary["id"])
    citation_result = apply(service, EvidenceOperation(
        "attach_citation", source_id=duplicate["id"], target_type="person",
        target_id=str(person_id), data={"confidence": "Probable", "page": "3"},
    ))
    assert citation_result.after_state != citation_result.before_state
    citation = service.build_model().citations[0]
    apply(service, EvidenceOperation(
        "edit_citation", citation_id=citation["id"], data={
            "confidence": "Proven", "proof_status": "Supported", "page": "4",
        },
    ))
    apply(service, EvidenceOperation(
        "merge_sources", source_id=primary["id"], source_ids=(duplicate["id"],),
    ))

    model = service.build_model()
    assert [source["title"] for source in model.sources] == ["Primary Updated"]
    assert model.citations[0]["source_id"] == primary["id"]
    assert model.citations[0]["confidence"] == "Proven"
    apply(service, EvidenceOperation("detach_citation", citation_id=model.citations[0]["id"]))
    assert service.build_model().citations == ()


def test_preview_blocks_duplicates_stale_plans_and_read_only_mutations(evidence_context):
    service, _repository, person_id, _family_id, _event_id = evidence_context
    source = service.sources.create_source(source_data())
    operation = EvidenceOperation(
        "attach_citation", source_id=source["id"], target_type="person",
        target_id=str(person_id), data={"page": "1", "confidence": "Strong"},
    )
    apply(service, operation)
    duplicate = service.preview((operation,))
    assert not duplicate.can_execute
    assert "уже существует" in duplicate.blockers[0]

    stale = service.preview((EvidenceOperation("duplicate_source", source_id=source["id"]),))
    service.sources.update_source(source["id"], source_data("Changed"))
    with pytest.raises(RuntimeError, match="изменились"):
        service.execute(stale)

    read_only = EvidenceService(service.repository, read_only=True)
    blocked = read_only.preview((EvidenceOperation("duplicate_source", source_id=source["id"]),))
    assert not blocked.can_execute
    with pytest.raises(EvidenceSafetyError, match="диагностики"):
        read_only.execute(blocked)


def test_batch_execution_rolls_back_and_successful_batch_is_undoable(evidence_context, monkeypatch):
    service, repository, person_id, _family_id, _event_id = evidence_context
    source = service.sources.create_source(source_data())
    operations = (
        EvidenceOperation(
            "attach_citation", source_id=source["id"], target_type="person",
            target_id=str(person_id), data={"page": "1", "confidence": "Strong"},
        ),
        EvidenceOperation("duplicate_source", source_id=source["id"]),
    )
    preview = service.preview(operations)
    original = service._execute_operation
    calls = 0

    def fail_second(operation):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("forced failure")
        original(operation)

    monkeypatch.setattr(service, "_execute_operation", fail_second)
    before = service._capture_state()
    with pytest.raises(RuntimeError, match="forced failure"):
        service.execute(preview)
    assert service._capture_state() == before

    monkeypatch.setattr(service, "_execute_operation", original)
    result = service.execute(service.preview(operations))
    manager = UndoManager()
    command = EvidenceAppliedCommand(repository, result)
    manager.record_applied(command)
    assert set(command.delta) == {"sources", "citations"}
    assert len(service.build_model().sources) == 2
    assert len(service.build_model().citations) == 1
    assert manager.undo()
    assert len(service.build_model().sources) == 1
    assert service.build_model().citations == ()
    assert manager.redo()
    assert len(service.build_model().sources) == 2
    assert len(service.build_model().citations) == 1


def test_csv_json_exports_and_audit_history(evidence_context, tmp_path):
    service, _repository, person_id, _family_id, _event_id = evidence_context
    source = service.sources.create_source(source_data())
    apply(service, EvidenceOperation(
        "attach_citation", source_id=source["id"], target_type="person",
        target_id=str(person_id), data={
            "page": "12", "confidence": "Proven", "proof_status": "Supported",
        },
    ))

    csv_path = service.export_csv(tmp_path / "evidence.csv")
    json_path = service.export_json(tmp_path / "evidence.json")
    with csv_path.open(encoding="utf-8-sig", newline="") as csv_file:
        rows = list(csv.DictReader(csv_file))
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    audit = AuditService.for_database(service.repository.db_name).list_records(service="evidence_service")

    assert rows[0]["confidence"] == "Proven"
    assert rows[0]["proof_status"] == "Supported"
    assert payload["sources"][0]["title"] == "Civil Register"
    assert payload["citations"][0]["target_type"] == "person"
    assert len(audit) == 1
    assert audit[0].operation_type == "evidence_change"
    assert set(audit[0].affected_tables) == {"citations"}


def test_viewer_evidence_button_three_panes_actions_and_workers_are_wired():
    widgets = inspect.getsource(GenealogyViewer._create_widgets)
    opening = inspect.getsource(GenealogyViewer.open_evidence_manager)
    loading = inspect.getsource(GenealogyViewer._load_evidence_manager)
    execution = inspect.getsource(GenealogyViewer._run_evidence_operations)
    exporting = inspect.getsource(GenealogyViewer._export_evidence)
    refreshing = inspect.getsource(GenealogyViewer.refresh_views)

    assert 'text="Источники и доказательства"' in widgets
    assert "command=self.open_evidence_manager" in widgets
    for pane in ("Источники", "Выбранная цитата", "Объекты, использующие источник"):
        assert f'text="{pane}"' in opening
    for action in (
        "Создать источник", "Изменить источник", "Дублировать",
        "Объединить дубликаты", "Прикрепить цитату", "Открепить цитату",
    ):
        assert action in opening
    assert "Только диагностика" in opening
    assert "CONFIDENCE_LEVELS" in inspect.getsource(GenealogyViewer._edit_evidence_citation_dialog)
    assert "PROOF_STATUSES" in inspect.getsource(GenealogyViewer._edit_evidence_citation_dialog)
    assert "_submit_repository_task" in loading
    assert "EvidenceService(repository, read_only=read_only).build_model()" in loading
    assert "service.preview(operations)" in execution
    assert "service.execute" in execution
    assert "_submit_repository_task" in exporting
    assert "export_csv" not in opening
    assert "_load_evidence_manager" in refreshing
    assert ".conn" not in opening + loading + execution + exporting


def test_headless_evidence_render_read_only_and_completion():
    class FakeTree:
        def __init__(self):
            self.rows = {}

        def get_children(self):
            return tuple(self.rows)

        def delete(self, item):
            self.rows.pop(item, None)

        def insert(self, _parent, _index, iid=None, values=()):
            self.rows[iid] = values
            return iid

    class FakeText:
        def __init__(self):
            self.value = ""

        def config(self, **_kwargs):
            return None

        def delete(self, *_args):
            self.value = ""

        def insert(self, _index, value):
            self.value = value

    class FakeButton:
        def __init__(self):
            self.state = "normal"

        def config(self, **kwargs):
            self.state = kwargs["state"]

    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer._evidence_source_tree = FakeTree()
    viewer._evidence_citation_tree = FakeTree()
    viewer._evidence_usage_tree = FakeTree()
    viewer._evidence_diagnostics_text = FakeText()
    viewer._evidence_mutation_buttons = [FakeButton(), FakeButton()]
    model = SimpleNamespace(
        sources=({"id": 1, "title": "Register", "repository": "Archive", "citation_count": 2},),
        citations=(), usages=(), read_only=True,
        issues=(SimpleNamespace(kind="orphan_citation"), SimpleNamespace(kind="orphan_citation")),
    )

    viewer._render_evidence_manager(model)

    assert viewer._evidence_source_tree.rows["source-1"][-1] == 2
    assert viewer._evidence_diagnostics_text.value == "orphan_citation: 2"
    assert all(button.state == "disabled" for button in viewer._evidence_mutation_buttons)

    calls = []
    viewer.repository = object()
    viewer._get_undo_manager = lambda: SimpleNamespace(
        record_applied=lambda command: calls.append(("undo", command.name))
    )
    viewer.refresh_views = lambda: calls.append(("refresh",))
    result = SimpleNamespace(
        before_state={"sources": (), "citations": ()},
        after_state={"sources": ((1,),), "citations": ()},
    )
    viewer._complete_evidence_operations(result)
    assert calls == [("undo", "Источники и доказательства"), ("refresh",)]
