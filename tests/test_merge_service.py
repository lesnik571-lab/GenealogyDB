import inspect
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from merge_service import MergeSafetyError, MergeService
from repository.person_repository import PersonRepository
from undo_manager import AppliedDeltaCommand, UndoManager
from viewer import GenealogyViewer


def build_repository(tmp_path, name="merge.db"):
    database = tmp_path / name
    connection = sqlite3.connect(database)
    connection.executescript(Path("schema.sql").read_text(encoding="utf-8"))
    connection.close()
    return PersonRepository(str(database))


def add_person(repository, number, first_name=None, **values):
    return repository.create_person(
        {
            "gedcom_id": f"I{number}",
            "first_name": first_name or f"Person{number}",
            "last_name": "Merge",
            **values,
        }
    )


def add_family(repository, number, husband="", wife="", children=(), relationship_type="unknown"):
    return repository.create_family(
        {
            "gedcom_id": f"F{number}",
            "husband": husband,
            "wife": wife,
            "children": list(children),
            "relationship_type": relationship_type,
        }
    )


def add_source_and_citation(repository, person_id, title="Archive", **citation_values):
    source_id = repository.create_source_record({"title": title, "author": "Archivist"})
    citation_id = repository.create_citation_record(
        {
            "source_id": source_id,
            "target_type": "person",
            "target_id": str(person_id),
            "page": citation_values.get("page", "1"),
            "comment": citation_values.get("comment", "Evidence"),
        }
    )
    return source_id, citation_id


def test_scalar_conflict_selection_supports_primary_duplicate_and_manual(tmp_path):
    repository = build_repository(tmp_path, "scalars.db")
    try:
        primary = add_person(repository, 1, first_name="Primary", occupation="Teacher", note="Primary note")
        duplicate = add_person(repository, 2, first_name="Duplicate", occupation="Doctor", note="Duplicate note")
        service = MergeService(repository, tmp_path / "backups")

        plan = service.plan_merge(
            primary,
            duplicate,
            {
                "first_name": "duplicate",
                "occupation": ("manual", "Engineer"),
                "note": "primary",
            },
        )
        selected = {item.field: item for item in plan.scalar_resolutions}

        assert selected["first_name"].result_value == "Duplicate"
        assert selected["occupation"].result_value == "Engineer"
        assert selected["note"].result_value == "Primary note"
        assert selected["occupation"].conflicting is True
        assert repository.get_person_record(primary)["occupation"] == "Teacher"
    finally:
        repository.close()


def test_events_sources_citations_attachments_and_notes_are_deduplicated(tmp_path):
    repository = build_repository(tmp_path, "collections.db")
    try:
        primary = add_person(repository, 1, note="Same paragraph")
        duplicate = add_person(repository, 2, note="Same paragraph\n\nExtra paragraph")
        for person_id in (primary, duplicate):
            repository.create_person_event(
                {"person_id": person_id, "event_type": "residence", "date": "1920", "place": "Paris", "description": "Moved"}
            )
            repository.create_person_source(
                {"person_id": person_id, "title": "Register", "source_url": "https://example.test", "archive_reference": "A1", "note": "Page"}
            )
            repository.create_person_media(
                {"person_id": person_id, "media_type": "document", "title": "Record", "file_path": "record.pdf", "description": "Scan"}
            )
        source_id = repository.create_source_record({"title": "Archive", "author": "Archivist"})
        for person_id in (primary, duplicate):
            repository.create_citation_record(
                {"source_id": source_id, "target_type": "person", "target_id": person_id, "page": "1", "comment": "Evidence"}
            )
        service = MergeService(repository, tmp_path / "backups")
        plan = service.plan_merge(primary, duplicate)

        result = service.execute(plan)

        assert result.primary_id == primary
        assert repository.get_person_record(duplicate) is None
        assert repository.get_person_record(primary)["note"] == "Same paragraph\n\nExtra paragraph"
        assert len(repository.list_person_events(primary)) == 1
        assert len(repository.list_person_sources(primary)) == 1
        assert len(repository.list_person_media(primary)) == 1
        person_citations = [
            item for item in repository.list_citation_records()
            if item["target_type"] == "person" and item["target_id"] == str(primary)
        ]
        assert len(person_citations) == 1
    finally:
        repository.close()


def test_parent_spouse_child_rewiring_and_identical_family_deduplication(tmp_path):
    repository = build_repository(tmp_path, "relationships.db")
    try:
        primary = add_person(repository, 1)
        duplicate = add_person(repository, 2)
        partner = add_person(repository, 3)
        child = add_person(repository, 4)
        parent = add_person(repository, 5)
        other_parent = add_person(repository, 6)
        add_family(repository, 1, "I1", "I3", ("I4",), "civil_partner")
        add_family(repository, 2, "I2", "I3", ("I4",), "civil_partner")
        add_family(repository, 3, "I5", "I6", ("I2",), "unknown")
        service = MergeService(repository, tmp_path / "backups")

        plan = service.plan_merge(primary, duplicate)

        assert any(change.action == "deduplicate" for change in plan.relationship_changes)
        assert plan.can_execute is True
        service.execute(plan)

        families = repository.list_families_raw()
        assert len(families) == 2
        partner_families = [family for family in families if family["relationship_type"] == "civil_partner"]
        assert len(partner_families) == 1
        assert partner_families[0]["husband_id"] == "I1"
        assert {row[2] for row in repository.get_parents(primary)} == {"I5", "I6"}
        assert {row[2] for row in repository.get_spouses(primary)} == {"I3"}
        assert {row[2] for row in repository.get_children(primary)} == {"I4"}
        assert parent == 5 and other_parent == 6 and child == 4
    finally:
        repository.close()


def test_ancestor_descendant_and_resulting_cycle_are_blocked(tmp_path):
    repository = build_repository(tmp_path, "ancestor.db")
    try:
        ancestor = add_person(repository, 1)
        middle = add_person(repository, 2)
        descendant = add_person(repository, 3)
        add_family(repository, 1, "I1", "", ("I2",))
        add_family(repository, 2, "I2", "", ("I3",))
        before = repository.capture_command_state()
        service = MergeService(repository, tmp_path / "backups")

        plan = service.plan_merge(ancestor, descendant)

        assert any("предка" in blocker for blocker in plan.blockers)
        assert any("цикл" in blocker for blocker in plan.blockers)
        with pytest.raises(MergeSafetyError):
            service.execute(plan)
        assert repository.capture_command_state() == before
        assert repository.get_person_record(middle) is not None
    finally:
        repository.close()


def test_self_and_self_spouse_merges_are_blocked_without_writes(tmp_path):
    repository = build_repository(tmp_path, "self.db")
    try:
        primary = add_person(repository, 1)
        duplicate = add_person(repository, 2)
        add_family(repository, 1, "I1", "I2", (), "marriage")
        service = MergeService(repository, tmp_path / "backups")
        before = repository.capture_command_state()

        identity_plan = service.plan_merge(primary, primary)
        spouse_plan = service.plan_merge(primary, duplicate)

        assert identity_plan.can_execute is False
        assert any("самим собой" in item for item in identity_plan.blockers)
        assert any("супруга" in item for item in spouse_plan.blockers)
        assert repository.capture_command_state() == before
    finally:
        repository.close()


def test_transaction_rolls_back_completely_on_post_merge_error(tmp_path, monkeypatch):
    repository = build_repository(tmp_path, "rollback.db")
    try:
        primary = add_person(repository, 1, occupation="Teacher")
        duplicate = add_person(repository, 2, occupation="Doctor")
        child = add_person(repository, 3)
        add_family(repository, 1, "I2", "", ("I3",))
        service = MergeService(repository, tmp_path / "backups")
        plan = service.plan_merge(primary, duplicate, {"occupation": "duplicate"})
        before = repository.capture_command_state()

        monkeypatch.setattr(service, "_validate_post_merge", lambda *_args: (_ for _ in ()).throw(RuntimeError("forced failure")))

        with pytest.raises(RuntimeError, match="forced failure"):
            service.execute(plan)

        assert repository.capture_command_state() == before
        assert repository.get_person_record(primary)["occupation"] == "Teacher"
        assert repository.get_person_record(duplicate) is not None
        assert {row[2] for row in repository.get_parents(child)} == {"I2"}
    finally:
        repository.close()


def test_backup_created_and_absorbed_identity_returned(tmp_path):
    repository = build_repository(tmp_path, "backup.db")
    try:
        primary = add_person(repository, 1)
        duplicate = add_person(repository, 2)
        service = MergeService(repository, tmp_path / "backups")

        result = service.execute(service.plan_merge(primary, duplicate))

        assert result.backup_path.exists()
        assert result.backup_path.parent == tmp_path / "backups"
        assert result.absorbed_id == duplicate
        assert result.absorbed_gedcom_id == "I2"
    finally:
        repository.close()


def test_merge_delta_supports_undo_and_redo_in_ui_repository(tmp_path):
    ui_repository = build_repository(tmp_path, "undo.db")
    primary = add_person(ui_repository, 1, occupation="Teacher")
    duplicate = add_person(ui_repository, 2, occupation="Doctor")
    source_id, _citation_id = add_source_and_citation(ui_repository, duplicate)
    worker_repository = PersonRepository(ui_repository.db_name)
    try:
        service = MergeService(worker_repository, tmp_path / "backups")
        result = service.execute(
            service.plan_merge(primary, duplicate, {"occupation": "duplicate"})
        )
        worker_repository.close()
        manager = UndoManager()
        manager.record_applied(AppliedDeltaCommand("Merge", ui_repository, result.delta, result))

        assert ui_repository.get_person_record(duplicate) is None
        assert manager.undo() is True
        assert ui_repository.get_person_record(duplicate)["occupation"] == "Doctor"
        restored = [item for item in ui_repository.list_citation_records(source_id) if item["target_id"] == str(duplicate)]
        assert len(restored) == 1
        assert manager.redo() is True
        assert ui_repository.get_person_record(duplicate) is None
        assert ui_repository.get_person_record(primary)["occupation"] == "Doctor"
    finally:
        try:
            worker_repository.close()
        except sqlite3.ProgrammingError:
            pass
        ui_repository.close()


def test_dry_run_and_exports_leave_database_unchanged(tmp_path):
    repository = build_repository(tmp_path, "dry_run.db")
    try:
        primary = add_person(repository, 1, occupation="Teacher")
        duplicate = add_person(repository, 2, occupation="Doctor")
        service = MergeService(repository, tmp_path / "backups")
        before = repository.capture_command_state()

        plan = service.plan_merge(primary, duplicate, {"occupation": "duplicate"})
        json_path = service.export_json(plan, tmp_path / "preview.json")
        csv_path = service.export_csv(plan, tmp_path / "preview.csv")

        assert repository.capture_command_state() == before
        assert json.loads(json_path.read_text(encoding="utf-8"))["primary"]["id"] == primary
        assert "occupation" in csv_path.read_text(encoding="utf-8-sig")
    finally:
        repository.close()


def test_no_dangling_person_or_event_citation_references_after_merge(tmp_path):
    repository = build_repository(tmp_path, "references.db")
    try:
        primary = add_person(repository, 1)
        duplicate = add_person(repository, 2)
        primary_event = repository.create_person_event(
            {"person_id": primary, "event_type": "custom", "date": "1900", "place": "A", "description": "Same"}
        )
        duplicate_event = repository.create_person_event(
            {"person_id": duplicate, "event_type": "custom", "date": "1900", "place": "A", "description": "Same"}
        )
        source_id = repository.create_source_record({"title": "Evidence"})
        repository.create_citation_record(
            {"source_id": source_id, "target_type": "event", "target_id": duplicate_event, "comment": "Event evidence"}
        )
        repository.create_citation_record(
            {"source_id": source_id, "target_type": "person", "target_id": duplicate, "comment": "Person evidence"}
        )
        service = MergeService(repository, tmp_path / "backups")

        service.execute(service.plan_merge(primary, duplicate))

        citations = repository.list_citation_records(source_id)
        assert all(item["target_id"] not in {str(duplicate), str(duplicate_event)} for item in citations)
        assert any(item["target_type"] == "event" and item["target_id"] == str(primary_event) for item in citations)
        assert any(item["target_type"] == "person" and item["target_id"] == str(primary) for item in citations)
        assert repository.conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        repository.close()


def test_viewer_merge_button_conflict_preview_and_task_routing_are_wired():
    widget_source = inspect.getsource(GenealogyViewer._create_widgets)
    open_source = inspect.getsource(GenealogyViewer.open_merge_wizard)
    dialog_source = inspect.getsource(GenealogyViewer._show_merge_wizard)
    scalar_source = inspect.getsource(GenealogyViewer._build_merge_scalar_tab)
    collections_source = inspect.getsource(GenealogyViewer._build_merge_collections_tab)
    relationships_source = inspect.getsource(GenealogyViewer._build_merge_relationships_tab)
    refresh_source = inspect.getsource(GenealogyViewer._refresh_merge_plan)
    execute_source = inspect.getsource(GenealogyViewer._execute_merge_plan)

    assert 'text="Объединить людей"' in widget_source
    assert "command=self.open_merge_wizard" in widget_source
    assert "exclude_reference=primary_reference" in open_source
    assert "_submit_repository_task" in open_source
    assert "MergeService(repository).plan_merge" in open_source
    assert '"Оставить основной"' in scalar_source
    assert '"Использовать дубликат"' in scalar_source
    assert '"Ввести вручную"' in scalar_source
    assert 'state="normal" if plan.can_execute else "disabled"' in dialog_source
    assert "primary_collections" in collections_source
    assert "duplicate_collections" in collections_source
    assert "relationship_changes" in relationships_source
    assert "_submit_repository_task" in refresh_source
    assert "_submit_repository_task" in execute_source
    assert "service.execute" in execute_source


def test_viewer_merge_resolution_controls_preserve_each_choice():
    class FakeVariable:
        def __init__(self, value):
            self.value = value

        def get(self):
            return self.value

    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer._merge_scalar_vars = {
        "first_name": (FakeVariable("Оставить основной"), FakeVariable("Primary")),
        "occupation": (FakeVariable("Использовать дубликат"), FakeVariable("Doctor")),
        "note": (FakeVariable("Ввести вручную"), FakeVariable("Combined note")),
    }

    assert viewer._merge_resolutions() == {
        "first_name": ("primary", "Primary"),
        "occupation": ("duplicate", "Doctor"),
        "note": ("manual", "Combined note"),
    }


def test_viewer_merge_completion_registers_one_undo_refreshes_and_opens_primary(monkeypatch):
    calls = []

    class FakeUndoManager:
        def record_applied(self, command):
            calls.append(("undo", command.name, command.result.primary_id))

    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer.repository = object()
    viewer.root = object()
    viewer._get_undo_manager = lambda: FakeUndoManager()
    viewer._close_merge_wizard = lambda: calls.append(("close",))
    viewer.refresh_views = lambda: calls.append(("refresh",))
    viewer.show_person = lambda person_id: calls.append(("show", person_id))
    monkeypatch.setattr("viewer.messagebox.showinfo", lambda *_args, **_kwargs: calls.append(("info",)))
    result = SimpleNamespace(
        primary_id=7,
        absorbed_id=8,
        backup_path=Path("backup.db"),
        delta={"people": object()},
    )

    viewer._complete_merge(result)

    assert calls[:4] == [
        ("undo", "Объединение людей", 7),
        ("close",),
        ("refresh",),
        ("show", 7),
    ]
    assert calls.count(("undo", "Объединение людей", 7)) == 1
