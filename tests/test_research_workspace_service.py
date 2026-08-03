import pytest

from audit_service import AuditService
from database import initialize_database
from evidence_service import EvidenceOperation, EvidenceService
from repository.person_repository import PersonRepository
from research_workspace_service import ResearchWorkspaceService


def repository(tmp_path):
    path = tmp_path / "research.db"; initialize_database(path); return PersonRepository(path)


def seed(repo):
    person = repo.create_person({"gedcom_id": "I1", "first_name": "Anna", "last_name": "Smith", "birth_date": "1900", "birth_place": "Riga"})
    family = repo.create_family({"gedcom_id": "F1", "husband": "I1", "wife": "", "children": []})
    event = repo.create_person_event({"person_id": person, "event_type": "residence", "date": "1920", "place": "Paris", "description": "Moved"})
    evidence = EvidenceService(repo); source = evidence.sources.create_source({"title": "Register"})
    evidence.execute(evidence.preview((EvidenceOperation("attach_citation", source_id=source["id"], target_type="event", target_id=str(event), data={"confidence": "Strong", "proof_status": "Supported"}),)))
    return person, family, event, source["id"], evidence.build_model().citations[0]["id"]


def test_research_workspace_project_hypothesis_task_lifecycle_and_persistence(tmp_path):
    repo = repository(tmp_path)
    try:
        person, family, event, source, citation = seed(repo); service = ResearchWorkspaceService(repo, data_dir=tmp_path / "research")
        before = repo.capture_command_state(); workspace = service.create_project("Smith origin", "Trace ancestry")
        project_id = workspace.project.project_id
        workspace = service.create_hypothesis(project_id, "Same Anna", "Anna is the same person", state="Draft", people=(person,), families=(family,), events=(event,), sources=(source,), evidence=(citation,), notes="Compare records")
        hypothesis = workspace.hypotheses[0]
        workspace = service.update_hypothesis(project_id, hypothesis.hypothesis_id, state="Confirmed")
        assert workspace.hypotheses[0].state == "Confirmed" and workspace.hypotheses[0].people == (person,)
        workspace = service.create_task(project_id, "Find parish record", priority="High", due_date="2026-09-01", status="Backlog", hypothesis_id=hypothesis.hypothesis_id, people=(person,), attachments=("scan.jpg",))
        task = workspace.tasks[0]; workspace = service.update_task(project_id, task.task_id, status="Done")
        assert service.kanban(workspace)["Done"][0].task_id == task.task_id and service.calendar(workspace)[0].due_date == "2026-09-01"
        service.add_question(project_id, "Who were her parents?", hypothesis_id=hypothesis.hypothesis_id); service.add_conclusion(project_id, "Identity supported", hypothesis_id=hypothesis.hypothesis_id)
        loaded = service.load(project_id)
        assert loaded.questions and loaded.conclusions and service.list_projects()[0].project_id == project_id
        assert repo.capture_command_state() == before
        assert AuditService.for_database(repo.db_name).list_records(service="research_workspace_service")
    finally:
        repo.close()


def test_research_workspace_integrations_exports_ordering_cancellation_and_deletion(tmp_path):
    repo = repository(tmp_path)
    try:
        person, family, event, source, citation = seed(repo); service = ResearchWorkspaceService(repo, data_dir=tmp_path / "research")
        project = service.create_project("Evidence")
        project_id = project.project.project_id
        workspace = service.create_hypothesis(project_id, "Residence", "Anna lived in Paris", state="Active", people=(person,), families=(family,), events=(event,), sources=(source,), evidence=(citation,))
        hypothesis = workspace.hypotheses[0]
        assert service.evidence_summary(hypothesis)["supporting"] and service.evidence_summary(hypothesis)["confidence"] == "Strong"
        assert service.related_timeline(hypothesis) and service.linked_people(hypothesis) == (person,)
        assert service.validation_issues(hypothesis)
        for extension in ("markdown", "html", "pdf"):
            exported = service.export(workspace, tmp_path / f"research.{extension}", extension)
            assert exported.exists() and exported.stat().st_size > 0
        with pytest.raises(RuntimeError):
            service.load(project_id, cancel_callback=lambda: (_ for _ in ()).throw(RuntimeError("cancelled")))
        service.delete_hypothesis(project_id, hypothesis.hypothesis_id)
        service.delete_project(project_id)
        assert not service.list_projects()
    finally:
        repo.close()