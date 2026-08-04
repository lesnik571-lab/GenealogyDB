import json
import zipfile
from pathlib import Path

import pytest

from change_exchange_service import ChangeExchangeService
from collaboration_service import CollaborationService
from database import initialize_database
from repository.person_repository import PersonRepository


def repository(tmp_path, name="exchange.db"):
    path = tmp_path / name; initialize_database(path); return PersonRepository(path)


def prepare(repo, data):
    person_id = repo.create_person({"gedcom_id": "I1", "first_name": "Ada", "last_name": "Lovelace"})
    ledger = CollaborationService(repo.db_name, data_dir=data, editor_identity="Ada")
    ledger.record_change("edit_person", references={"person": (str(person_id),)}, summary="Edited", timestamp="2026-08-03T00:00:00+00:00", operation_id="12345678-1234-5678-1234-567812345678")
    return person_id, ledger


def test_deterministic_export_checksums_inspection_and_reports(tmp_path):
    repo = repository(tmp_path); data = tmp_path / "data"
    try:
        _, ledger = prepare(repo, data); service = ChangeExchangeService(repo, data_dir=data)
        first = service.export(tmp_path / "one.zip"); second = service.export(tmp_path / "two.zip")
        with zipfile.ZipFile(first) as archive: manifest = json.loads(archive.read("manifest.json")); assert manifest["files"]["changes.json"]
        inspected = service.inspect(first); assert inspected.status == "Inspected" and inspected.operations and all(path.exists() for path in service.export_all_reports(inspected))
        assert service.preview_against_current(inspected).already_applied
    finally: repo.close()


def test_untrusted_package_rejection_and_inspection_isolation(tmp_path):
    repo = repository(tmp_path); data = tmp_path / "data"
    try:
        prepare(repo, data); service = ChangeExchangeService(repo, data_dir=data); before = repo.capture_command_state()
        unsafe = tmp_path / "unsafe.zip"
        with zipfile.ZipFile(unsafe, "w") as archive: archive.writestr("../bad.exe", b"x")
        assert service.inspect(unsafe).status == "Rejected" and repo.capture_command_state() == before
        package = service.export(tmp_path / "valid.zip")
        with pytest.warns(UserWarning, match=r"Duplicate name.*changes\.json"):
            with zipfile.ZipFile(package, "a") as archive:
                archive.writestr("changes.json", b"[]")
        assert service.inspect(package).status == "Rejected"
        unlisted = service.export(tmp_path / "unlisted.zip")
        with zipfile.ZipFile(unlisted, "a") as archive: archive.writestr("notes.txt", "unexpected")
        assert service.inspect(unlisted).status == "Rejected"
    finally: repo.close()


def test_snapshot_copy_merge_conversion_dependencies_cancellation_and_cleanup(tmp_path):
    repo = repository(tmp_path); data = tmp_path / "data"
    try:
        _, ledger = prepare(repo, data); service = ChangeExchangeService(repo, data_dir=data)
        snapshot = tmp_path / "snapshot.db"; initialize_database(snapshot)
        attachment = tmp_path / "note.txt"; attachment.write_text("evidence", encoding="utf-8")
        package = service.export(tmp_path / "snapshot.zip", include_snapshot=snapshot, attachment_paths=(attachment,))
        inspected = service.inspect(package); copied = service.create_incoming_copy(inspected, tmp_path / "incoming.db"); assert copied.exists()
        assert inspected.attachments and inspected.attachments[0]["package_path"].startswith("attachments/")
        converted = service.to_project_merge_input(inspected, tmp_path / "merge-input.db"); assert converted.exists()
        with pytest.raises(RuntimeError): service.export(tmp_path / "cancel.zip", cancel_callback=lambda: (_ for _ in ()).throw(RuntimeError("cancel")))
        assert not (tmp_path / "cancel.zip").exists()
    finally: repo.close()


def test_version_duplicate_operations_and_dataset_mismatch_are_rejected(tmp_path):
    repo = repository(tmp_path); data = tmp_path / "data"
    try:
        _, ledger = prepare(repo, data); service = ChangeExchangeService(repo, data_dir=data); package = service.export(tmp_path / "base.zip")
        with zipfile.ZipFile(package) as archive: manifest = json.loads(archive.read("manifest.json")); changes = json.loads(archive.read("changes.json"))
        manifest["format_version"] = 99
        bad = tmp_path / "version.zip"
        with zipfile.ZipFile(bad, "w") as archive: archive.writestr("manifest.json", json.dumps(manifest)); archive.writestr("changes.json", json.dumps(changes)); archive.writestr("attachments.json", "[]")
        assert service.inspect(bad).status == "Rejected"
        duplicate = tmp_path / "duplicate.zip"; changes.append(changes[0]); manifest["format_version"] = 1; manifest["files"]["changes.json"] = service._sha256(service._json_bytes(changes)); manifest["files"]["attachments.json"] = service._sha256(service._json_bytes([])); unsigned = dict(manifest); unsigned.pop("digital_integrity", None); manifest["digital_integrity"] = {"package_checksum": service._sha256(service._json_bytes(manifest["files"])), "manifest_checksum": service._sha256(service._json_bytes(unsigned))}
        with zipfile.ZipFile(duplicate, "w") as archive: archive.writestr("manifest.json", service._json_bytes(manifest)); archive.writestr("changes.json", service._json_bytes(changes)); archive.writestr("attachments.json", service._json_bytes([]))
        assert service.inspect(duplicate).status == "Rejected"
        manifest["dataset_identity"] = "00000000-0000-0000-0000-000000000001"; unsigned = dict(manifest); unsigned.pop("digital_integrity", None); manifest["digital_integrity"] = {"package_checksum": service._sha256(service._json_bytes(manifest["files"])), "manifest_checksum": service._sha256(service._json_bytes(unsigned))}
        mismatch = tmp_path / "mismatch.zip"; changes = changes[:1]; manifest["files"]["changes.json"] = service._sha256(service._json_bytes(changes)); unsigned = dict(manifest); unsigned.pop("digital_integrity", None); manifest["digital_integrity"] = {"package_checksum": service._sha256(service._json_bytes(manifest["files"])), "manifest_checksum": service._sha256(service._json_bytes(unsigned))}
        with zipfile.ZipFile(mismatch, "w") as archive: archive.writestr("manifest.json", service._json_bytes(manifest)); archive.writestr("changes.json", service._json_bytes(changes)); archive.writestr("attachments.json", service._json_bytes([]))
        assert "Dataset UUID mismatch" in service.inspect(mismatch).blockers
    finally: repo.close()