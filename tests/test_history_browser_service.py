from pathlib import Path

import pytest

from audit_service import AuditService
from collaboration_service import CollaborationService
from database import initialize_database
from history_browser_service import HistoryBrowserService
from repository.person_repository import PersonRepository


def repository(tmp_path, name="history.db"):
    path = tmp_path / name; initialize_database(path); return PersonRepository(path)


def test_unified_order_filters_grouping_bookmarks_and_reports(tmp_path):
    repo = repository(tmp_path); data = tmp_path / "data"
    try:
        person_id = repo.create_person({"gedcom_id": "I1", "first_name": "Ada", "last_name": "Lovelace"})
        audit = AuditService.for_database(repo.db_name); audit.record("import", database_id=person_id, description="Imported Ada", service="importer", timestamp="2026-08-01T00:00:00+00:00")
        collaboration = CollaborationService(repo.db_name, data_dir=data); collaboration.record_change("edit_person", references={"person": (str(person_id),)}, summary="Edited Ada", author="Ada", timestamp="2026-08-02T00:00:00+00:00")
        service = HistoryBrowserService(repo, data_dir=data); entries = service.entries()
        assert [entry.timestamp for entry in entries] == sorted((entry.timestamp for entry in entries), reverse=True)
        service.bookmark(entries[0].entry_id, tags=("important",), note="review")
        assert service.entries(filters={"bookmarked": True, "search": "important"})
        assert service.entries(group_by="author")["Ada"] and service.entries(group_by="session")
        assert all(path.exists() for path in service.export_all(entries))
    finally: repo.close()


def test_snapshot_preview_comparison_restore_copy_retention_and_cancellation(tmp_path):
    repo = repository(tmp_path); data = tmp_path / "data"
    try:
        person_id = repo.create_person({"gedcom_id": "I1", "first_name": "Ada", "last_name": "Lovelace"}); service = HistoryBrowserService(repo, data_dir=data, backup_dir=tmp_path / "backups")
        snapshot_id, snapshot = service.create_snapshot(label="before")
        repo.update_person_fields(person_id, {"first_name": "Grace"}); second_id, second = service.create_snapshot(label="after")
        first = type("Entry", (), {"after_snapshot_reference": f"snapshot:{snapshot}", "entry_id": "snapshot:before"})()
        second_entry = type("Entry", (), {"after_snapshot_reference": f"snapshot:{second}", "entry_id": "snapshot:after"})()
        comparison = service.compare(first, second_entry); assert comparison["modified"]["people"]
        preview = service.historical_preview(first); assert Path(preview.temporary_path).exists() and repo.get_person_record(person_id)["first_name"] == "Grace"; service.close_preview(preview); assert not Path(preview.temporary_path).exists()
        restore = service.restore_preview(first); result = service.restore(restore, mode="Create historical project copy", destination=tmp_path / "historical.db", confirmed=True); assert Path(result.target_path).exists() and repo.get_person_record(person_id)["first_name"] == "Grace"
        with pytest.raises(RuntimeError): service.create_snapshot(cancel_callback=lambda: (_ for _ in ()).throw(RuntimeError("cancelled")))
        service.enforce_retention(1, 90, 10**9); assert len(service._metadata()["snapshots"]) == 1
    finally: repo.close()


def test_restore_requires_preview_backup_rollback_and_provenance(tmp_path, monkeypatch):
    repo = repository(tmp_path); data = tmp_path / "data"
    try:
        person_id = repo.create_person({"gedcom_id": "I1", "first_name": "Ada", "last_name": "Lovelace"}); service = HistoryBrowserService(repo, data_dir=data, backup_dir=tmp_path / "backups")
        _, snapshot = service.create_snapshot(); repo.update_person_fields(person_id, {"first_name": "Grace"})
        entry = type("Entry", (), {"after_snapshot_reference": f"snapshot:{snapshot}", "entry_id": "snapshot:one"})()
        restore = service.restore_preview(entry)
        with pytest.raises(ValueError): service.restore(restore)
        original = service._relationship_blockers
        monkeypatch.setattr(service, "_relationship_blockers", lambda *_args: ("forced rollback",))
        with pytest.raises(ValueError): service.restore(restore, confirmed=True)
        assert repo.get_person_record(person_id)["first_name"] == "Grace"
        monkeypatch.setattr(service, "_relationship_blockers", original)
        result = service.restore(restore, confirmed=True); assert Path(result.backup_path).exists() and repo.get_person_record(person_id)["first_name"] == "Ada"
        assert AuditService.for_database(repo.db_name).list_records(service="history_browser_service") and CollaborationService(repo.db_name, data_dir=data).changes()
    finally: repo.close()


def test_incompatible_dataset_blocks_current_restore_but_not_copy(tmp_path):
    repo = repository(tmp_path); data = tmp_path / "data"
    try:
        repo.create_person({"gedcom_id": "I1", "first_name": "Ada", "last_name": "Lovelace"}); service = HistoryBrowserService(repo, data_dir=data)
        _, snapshot = service.create_snapshot(); metadata = service._metadata()
        next(iter(metadata["snapshots"].values()))["dataset_uuid"] = "00000000-0000-0000-0000-000000000001"; service._write_metadata(metadata)
        entry = type("Entry", (), {"after_snapshot_reference": f"snapshot:{snapshot}", "entry_id": "snapshot:incompatible"})()
        restore = service.restore_preview(entry); assert not restore.dataset_compatible and restore.blockers
        with pytest.raises(ValueError): service.restore(restore, confirmed=True)
        copied = service.restore(restore, mode="Create historical project copy", destination=tmp_path / "other.db", confirmed=True)
        assert Path(copied.target_path).exists()
    finally: repo.close()