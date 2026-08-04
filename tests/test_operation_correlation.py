import pytest

from audit_service import AuditService
from collaboration_service import CollaborationService
from database import initialize_database
from operation_correlation import OperationContext, validate_correlation
from repository.person_repository import PersonRepository
from undo_manager import RepositoryDeltaCommand


def test_operation_context_validates_shared_identity_and_lifecycle():
    context = OperationContext.create(operation_type="merge", project_uuid="12345678-1234-5678-1234-567812345678", dataset_uuid="87654321-4321-8765-4321-876543218765", operation_uuid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", now="2026-08-04T00:00:00+00:00")
    assert context.transition("running", now="2026-08-04T00:00:01+00:00").complete().status == "completed"
    with pytest.raises(ValueError): context.transition("completed")


def test_shared_context_creates_one_audit_collaboration_and_history_counterpart(tmp_path):
    database = tmp_path / "correlation.db"; initialize_database(database); repository = PersonRepository(database); data = tmp_path / "data"
    try:
        collaboration = CollaborationService(database, data_dir=data, editor_identity="Ada"); identity = collaboration.identity(); before = repository.capture_command_state()
        person_id = repository.create_person({"gedcom_id": "I1", "first_name": "Ada", "last_name": "Lovelace"}); context = OperationContext.create(operation_type="person_create", project_uuid=identity.project_uuid, dataset_uuid=identity.dataset_uuid, operation_uuid="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", session_uuid=collaboration.session_id).transition("running").complete()
        delta = RepositoryDeltaCommand._build_delta(before, repository.capture_command_state())
        audit = AuditService.for_database(database); audit.record_delta("person_create", delta, description="Created", service="test", operation_context=context)
        collaboration.record_change("create_person", references={"person": (str(person_id),)}, summary="Created", operation_context=context)
        assert validate_correlation(audit.list_records(), collaboration.changes())["complete"]
        assert audit.record_delta("person_create", delta, description="Retry", service="test", operation_context=context).operation_uuid == context.operation_uuid
        assert len(audit.list_records()) == 1
    finally:
        repository.close()