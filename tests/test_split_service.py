import inspect
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from audit_service import AuditService
from repository.person_repository import PersonRepository
from split_service import SplitSafetyError, SplitService
from undo_manager import AppliedDeltaCommand, UndoManager
from viewer import GenealogyViewer


def build_repository(tmp_path, name="split.db"):
    database = tmp_path / name
    connection = sqlite3.connect(database)
    connection.executescript(Path("schema.sql").read_text(encoding="utf-8"))
    connection.close()
    return PersonRepository(str(database))


def add_person(repository, number, first_name=None, last_name="Person", **values):
    return repository.create_person({
        "gedcom_id": f"I{number}",
        "first_name": first_name or f"Person{number}",
        "last_name": last_name,
        **values,
    })


def add_family(repository, number, husband="", wife="", children=(), relationship_type="unknown"):
    return repository.create_family({
        "gedcom_id": f"F{number}",
        "husband": husband,
        "wife": wife,
        "children": list(children),
        "relationship_type": relationship_type,
    })


def valid_names(source="Source", new="Second"):
    return {
        "source_values": {"first_name": source, "last_name": "Person"},
        "new_values": {"first_name": new, "last_name": "Person"},
    }


def test_field_split_moves_selected_values_and_keeps_independent_previews(tmp_path):
    repository = build_repository(tmp_path, "fields.db")
    try:
        source_id = add_person(
            repository, 1, first_name="Combined", occupation="Teacher",
            birth_date="1900", birth_place="Paris", note="Second biography",
        )
        service = SplitService(repository, tmp_path / "backups")

        plan = service.plan_split(
            source_id,
            {"fields": ("first_name", "last_name", "birth_date", "birth_place", "occupation", "note")},
            source_values={"first_name": "Remaining", "last_name": "Person"},
        )

        assert plan.can_execute
        assert repository.list_people_full()[0]["occupation"] == "Teacher"
        assert plan.source_preview["occupation"] == ""
        assert plan.new_person_preview["occupation"] == "Teacher"
        result = service.execute(plan)
        assert repository.get_person_record(source_id)["occupation"] == ""
        assert repository.get_person_record(result.new_person_id)["occupation"] == "Teacher"
        assert repository.get_person_record(source_id)["first_name"] == "Remaining"
        assert repository.get_person_record(result.new_person_id)["first_name"] == "Combined"
        assert repository.get_person_record(result.new_person_id)["birth_place"] == "Paris"
        assert result.backup_path.exists()
    finally:
        repository.close()


def test_event_and_event_citations_stay_linked_when_event_moves(tmp_path):
    repository = build_repository(tmp_path, "events.db")
    try:
        source_id = add_person(repository, 1)
        event_id = repository.create_person_event({
            "person_id": source_id, "event_type": "residence", "date": "1920",
            "place": "Rome", "description": "Moved",
        })
        source_record_id = repository.create_source_record({"title": "Register"})
        citation_id = repository.create_citation_record({
            "source_id": source_record_id, "target_type": "event", "target_id": event_id,
        })
        service = SplitService(repository, tmp_path / "backups")
        plan = service.plan_split(source_id, {"events": (event_id,)}, **valid_names())

        result = service.execute(plan)

        assert repository.list_person_events(source_id) == []
        assert [item["id"] for item in repository.list_person_events(result.new_person_id)] == [event_id]
        citation = next(item for item in repository.list_citation_records() if item["id"] == citation_id)
        assert citation["target_id"] == str(event_id)
    finally:
        repository.close()


def test_attachment_source_and_person_citation_move_independently(tmp_path):
    repository = build_repository(tmp_path, "collections.db")
    try:
        source_id = add_person(repository, 1)
        attachment_id = repository.create_person_media({
            "person_id": source_id, "media_type": "document", "title": "File", "file_path": "file.pdf",
        })
        person_source_id = repository.create_person_source({"person_id": source_id, "title": "Notebook"})
        global_source_id = repository.create_source_record({"title": "Archive"})
        citation_id = repository.create_citation_record({
            "source_id": global_source_id, "target_type": "person", "target_id": source_id,
        })
        service = SplitService(repository, tmp_path / "backups")
        plan = service.plan_split(
            source_id,
            {"attachments": (attachment_id,), "sources": (person_source_id,), "citations": (citation_id,)},
            **valid_names(),
        )

        result = service.execute(plan)

        assert repository.list_person_media(source_id) == []
        assert repository.list_person_sources(source_id) == []
        assert repository.list_person_media(result.new_person_id)[0]["id"] == attachment_id
        assert repository.list_person_sources(result.new_person_id)[0]["id"] == person_source_id
        citation = next(item for item in repository.list_citation_records() if item["id"] == citation_id)
        assert citation["target_id"] == str(result.new_person_id)
    finally:
        repository.close()


def test_parent_and_partner_relationships_move_with_exact_preview(tmp_path):
    repository = build_repository(tmp_path, "relationships.db")
    try:
        source_id = add_person(repository, 1)
        parent_id = add_person(repository, 2)
        partner_id = add_person(repository, 3)
        child_id = add_person(repository, 4)
        parent_family = add_family(repository, 1, "I2", "", ("I1",))
        partner_family = add_family(repository, 2, "I1", "I3", ("I4",), "civil_partner")
        service = SplitService(repository, tmp_path / "backups")
        initial = service.plan_split(source_id, {}, **valid_names())
        parent_key = next(item.key for item in initial.relationships if item.category == "parents")
        partner_key = next(item.key for item in initial.relationships if item.category == "partners")

        plan = service.plan_split(
            source_id, {"relationships": (parent_key, partner_key)}, **valid_names()
        )

        selected = [item for item in plan.relationships if item.selected]
        assert len(selected) == 2
        assert any(item.implicit_effects for item in selected if item.category == "partners")
        result = service.execute(plan)
        assert {row[2] for row in repository.get_parents(result.new_person_id)} == {"I2"}
        assert repository.get_parents(source_id) == []
        assert {row[2] for row in repository.get_spouses(result.new_person_id)} == {"I3"}
        assert {row[2] for row in repository.get_children(result.new_person_id)} == {"I4"}
        assert repository.get_spouses(source_id) == []
        assert parent_id == 2 and partner_id == 3 and child_id == 4
        assert parent_family == 1 and partner_family == 2
    finally:
        repository.close()


def test_individual_child_move_clones_family_and_never_orphans_child(tmp_path):
    repository = build_repository(tmp_path, "child.db")
    try:
        source_id = add_person(repository, 1)
        other_parent_id = add_person(repository, 2)
        moved_child_id = add_person(repository, 3)
        kept_child_id = add_person(repository, 4)
        add_family(repository, 1, "I1", "I2", ("I3", "I4"), "marriage")
        service = SplitService(repository, tmp_path / "backups")
        initial = service.plan_split(source_id, {}, **valid_names())
        child_key = next(
            item.key for item in initial.relationships
            if item.category == "children" and item.key.endswith(":I3")
        )

        plan = service.plan_split(source_id, {"relationships": (child_key,)}, **valid_names())
        result = service.execute(plan)

        assert {row[2] for row in repository.get_children(source_id)} == {"I4"}
        assert {row[2] for row in repository.get_children(result.new_person_id)} == {"I3"}
        assert {row[2] for row in repository.get_parents(moved_child_id)} == {str(result.new_person_id), "I2"}
        assert {row[2] for row in repository.get_parents(kept_child_id)} == {"I1", "I2"}
        assert other_parent_id == 2
    finally:
        repository.close()


def test_cycle_and_self_relationship_data_block_execution(tmp_path):
    repository = build_repository(tmp_path, "cycle.db")
    try:
        source_id = add_person(repository, 1)
        other_id = add_person(repository, 2)
        add_family(repository, 1, "I1", "", ("I2",))
        add_family(repository, 2, "I2", "", ("I1",))
        service = SplitService(repository, tmp_path / "backups")
        initial = service.plan_split(source_id, {}, **valid_names())
        adult_key = next(item.key for item in initial.relationships if item.key.startswith("adult:"))
        parent_key = next(item.key for item in initial.relationships if item.key.startswith("parent:"))

        plan = service.plan_split(
            source_id, {"relationships": (adult_key, parent_key)}, **valid_names()
        )

        assert any("цикл" in blocker for blocker in plan.blockers)
        with pytest.raises(SplitSafetyError):
            service.execute(plan)
        assert repository.get_person_record(other_id) is not None
    finally:
        repository.close()


def test_self_partner_and_orphaning_parent_moves_are_blocked(tmp_path):
    repository = build_repository(tmp_path, "self-orphan.db")
    try:
        source_id = add_person(repository, 1)
        add_family(repository, 1, "I1", "I1", (), "marriage")
        repository.conn.execute(
            "INSERT INTO families (gedcom_id, husband_id, wife_id, relationship_type) VALUES (?, ?, ?, ?)",
            ("F2", "", "", "unknown"),
        )
        repository.conn.execute(
            "INSERT INTO family_children (family_id, child_id) VALUES (?, ?)", ("F2", "I1")
        )
        repository.conn.commit()
        service = SplitService(repository, tmp_path / "backups")
        initial = service.plan_split(source_id, {}, **valid_names())
        adult_keys = tuple(item.key for item in initial.relationships if item.family_id == 1)
        orphan_key = next(item.key for item in initial.relationships if item.family_id == 2)

        self_plan = service.plan_split(source_id, {"relationships": adult_keys}, **valid_names())
        orphan_plan = service.plan_split(source_id, {"relationships": (orphan_key,)}, **valid_names())

        assert any("самим собой" in blocker for blocker in self_plan.blockers)
        assert any("без родителя" in blocker for blocker in orphan_plan.blockers)
    finally:
        repository.close()


def test_transaction_rolls_back_on_post_split_failure(tmp_path, monkeypatch):
    repository = build_repository(tmp_path, "rollback.db")
    try:
        source_id = add_person(repository, 1, occupation="Teacher")
        event_id = repository.create_person_event({"person_id": source_id, "event_type": "custom"})
        service = SplitService(repository, tmp_path / "backups")
        plan = service.plan_split(
            source_id, {"fields": ("occupation",), "events": (event_id,)}, **valid_names()
        )
        before = repository.capture_command_state()
        monkeypatch.setattr(service, "_validate_post_split", lambda *_args: (_ for _ in ()).throw(RuntimeError("forced failure")))

        with pytest.raises(RuntimeError, match="forced failure"):
            service.execute(plan)

        assert repository.capture_command_state() == before
        assert repository.get_person_record(source_id)["occupation"] == "Teacher"
    finally:
        repository.close()


def test_split_delta_supports_undo_and_redo(tmp_path):
    ui_repository = build_repository(tmp_path, "undo.db")
    source_id = add_person(ui_repository, 1, occupation="Teacher")
    worker_repository = PersonRepository(ui_repository.db_name)
    try:
        service = SplitService(worker_repository, tmp_path / "backups")
        result = service.execute(service.plan_split(
            source_id, {"fields": ("occupation",)}, **valid_names()
        ))
        worker_repository.close()
        manager = UndoManager()
        manager.record_applied(AppliedDeltaCommand("Разделение человека", ui_repository, result.delta, result))

        assert ui_repository.get_person_record(result.new_person_id) is not None
        assert manager.undo()
        assert ui_repository.get_person_record(result.new_person_id) is None
        assert ui_repository.get_person_record(source_id)["occupation"] == "Teacher"
        assert manager.redo()
        assert ui_repository.get_person_record(result.new_person_id)["occupation"] == "Teacher"
        assert ui_repository.get_person_record(source_id)["occupation"] == ""
    finally:
        try:
            worker_repository.close()
        except sqlite3.ProgrammingError:
            pass
        ui_repository.close()


def test_split_writes_complete_audit_history(tmp_path):
    repository = build_repository(tmp_path, "audit.db")
    try:
        source_id = add_person(repository, 1, note="Move me")
        service = SplitService(repository, tmp_path / "backups")
        result = service.execute(service.plan_split(
            source_id, {"fields": ("note",)}, **valid_names()
        ))

        records = AuditService.for_database(repository.db_name).list_records(operation="split")
        assert len(records) == 1
        record = records[0]
        assert record.database_id == f"{source_id},{result.new_person_id}"
        assert record.service == "split_service"
        assert "people" in record.affected_tables
        assert len(record.after_snapshot["people"]) == 2
    finally:
        repository.close()


def test_dry_run_and_csv_json_exports_do_not_modify_database(tmp_path):
    repository = build_repository(tmp_path, "dry-run.db")
    try:
        source_id = add_person(repository, 1, occupation="Teacher")
        service = SplitService(repository, tmp_path / "backups")
        before = repository.capture_command_state()

        plan = service.plan_split(
            source_id, {"fields": ("occupation",)}, **valid_names()
        )
        json_path = service.export_json(plan, tmp_path / "split.json")
        csv_path = service.export_csv(plan, tmp_path / "split.csv")

        assert repository.capture_command_state() == before
        assert json.loads(json_path.read_text(encoding="utf-8"))["source"]["id"] == source_id
        assert "occupation" in csv_path.read_text(encoding="utf-8-sig")
    finally:
        repository.close()


def test_viewer_split_button_preview_categories_and_task_routing_are_wired():
    widget_source = inspect.getsource(GenealogyViewer._create_widgets)
    open_source = inspect.getsource(GenealogyViewer.open_split_wizard)
    dialog_source = inspect.getsource(GenealogyViewer._show_split_wizard)
    fields_source = inspect.getsource(GenealogyViewer._build_split_fields_tab)
    collections_source = inspect.getsource(GenealogyViewer._build_split_collections_tab)
    relationships_source = inspect.getsource(GenealogyViewer._build_split_relationships_tab)
    preview_source = inspect.getsource(GenealogyViewer._preview_split)
    execute_source = inspect.getsource(GenealogyViewer._execute_split_plan)

    assert 'text="Разделить человека"' in widget_source
    assert "command=self.open_split_wizard" in widget_source
    assert "self.current_person_id" in open_source
    assert "_submit_repository_task" in open_source
    assert "SplitService(repository).plan_split" in open_source
    assert 'state="normal" if plan.can_execute else "disabled"' in dialog_source
    for field in ("first_name", "last_name", "birth_date", "death_date", "occupation", "note"):
        assert f'"{field}"' in fields_source or field == "first_name"
    for collection in ("events", "sources", "citations", "attachments"):
        assert f'"{collection}"' in collections_source
    assert "implicit_effects" in relationships_source
    assert "selectmode=\"extended\"" in relationships_source
    assert "_submit_repository_task" in preview_source
    assert "_submit_repository_task" in execute_source
    assert "SplitService(repository).execute" in execute_source
    assert ".conn" not in dialog_source + preview_source + execute_source
    assert ".execute(" not in dialog_source + preview_source


def test_viewer_split_completion_registers_one_undo_refreshes_and_opens_new_person(monkeypatch):
    calls = []

    class FakeUndoManager:
        def record_applied(self, command):
            calls.append(("undo", command.name, command.result.new_person_id))

    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer.repository = object()
    viewer.root = object()
    viewer._get_undo_manager = lambda: FakeUndoManager()
    viewer._close_split_wizard = lambda: calls.append(("close",))
    viewer.refresh_views = lambda: calls.append(("refresh",))
    viewer.show_person = lambda person_id: calls.append(("show", person_id))
    monkeypatch.setattr("viewer.messagebox.showinfo", lambda *_args, **_kwargs: calls.append(("info",)))
    result = SimpleNamespace(
        source_id=7,
        new_person_id=8,
        backup_path=Path("backup.db"),
        delta={"people": object()},
    )

    viewer._complete_split(result)

    assert calls[:4] == [
        ("undo", "Разделение человека", 8),
        ("close",),
        ("refresh",),
        ("show", 8),
    ]
    assert calls.count(("undo", "Разделение человека", 8)) == 1
