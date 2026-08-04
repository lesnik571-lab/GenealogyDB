import csv
import inspect

from database import initialize_database
from repository.person_repository import PersonRepository
from source_analysis_service import SourceAnalysisService
from source_service import SourceService
from viewer import GenealogyViewer


def build_repository(tmp_path):
    database_path = tmp_path / "source_analysis.db"
    initialize_database(database_path)
    repository = PersonRepository(database_path)
    person_id = repository.create_person({
        "gedcom_id": "I1", "first_name": "Anna", "last_name": "Smith",
        "birth_date": "1900", "birth_place": "Riga",
    })
    uncited_person_id = repository.create_person({
        "gedcom_id": "I2", "first_name": "Maria", "last_name": "Smith",
        "birth_date": "1902",
    })
    event_id = repository.create_person_event({
        "person_id": person_id, "event_type": "residence", "date": "1920", "place": "Riga", "description": "",
    })
    source_service = SourceService(repository)
    primary = source_service.create_source({"title": "Parish Register", "repository": "State Archive"})
    orphan_source = source_service.create_source({"title": "Unused Register", "repository": "State Archive"})
    conflict_source = source_service.create_source({"title": "Civil Register", "repository": "State Archive!"})
    source_service.create_citation(primary["id"], "person", person_id, page="12", quality="Weak", transcription="Place: Riga; 1900")
    source_service.create_citation(primary["id"], "person", person_id, page="12", quality="Weak", transcription="Place: Riga; 1900")
    source_service.create_citation(conflict_source["id"], "person", person_id, page="14", quality="Strong", transcription="Place: Tallinn; 1901")
    repository.conn.execute(
        "INSERT INTO citations (source_id, target_type, target_id, page, quality, transcription, comment) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (primary["id"], "event", "99999", "13", "Unknown", "Place: Tallinn; 1901", ""),
    )
    repository.conn.commit()
    return repository, person_id, uncited_person_id, event_id, primary, orphan_source


def test_source_analysis_detects_orphans_duplicates_and_unsupported_records_read_only(tmp_path):
    repository, person_id, uncited_person_id, event_id, primary, orphan_source = build_repository(tmp_path)
    try:
        before = repository.capture_command_state()
        report = SourceAnalysisService(repository, tmp_path / "sidecars").analyze()
        after = repository.capture_command_state()

        categories = {finding.category for finding in report.findings}
        assert {"orphan_citation", "duplicate_citation", "uncited_person", "uncited_event", "unsupported_conclusion", "source_without_links", "weak_evidence_chain", "conflicting_source_dates", "conflicting_place_references", "duplicated_repository"} <= categories
        assert before == after
        assert any(primary["id"] in finding.source_ids for finding in report.findings if finding.category == "duplicate_citation")
        assert any(uncited_person_id in finding.person_ids for finding in report.findings if finding.category == "unsupported_conclusion")
        assert any(event_id in finding.event_ids for finding in report.findings if finding.category == "uncited_event")
        assert any(orphan_source["id"] in finding.source_ids for finding in report.findings if finding.category == "source_without_links")
        assert all(finding.explanation and finding.linked_records and finding.suggested_actions for finding in report.findings)
    finally:
        repository.close()


def test_statistics_are_deterministic_and_filters_work(tmp_path):
    repository, person_id, _uncited_person_id, event_id, _primary, _orphan_source = build_repository(tmp_path)
    try:
        service = SourceAnalysisService(repository, tmp_path / "sidecars")
        first = service.analyze()
        second = service.analyze()

        assert first.statistics == second.statistics
        assert first.statistics == {
            "total_sources": 3,
            "citations": 4,
            "average_citations_per_person": 1.5,
            "evidence_coverage": 0.3333,
            "unsupported_records": 2,
            "duplicate_rate": 0.25,
        }
        assert service.filter(first, severity="critical")
        assert service.filter(first, person_id=person_id)
        assert service.filter(first, event_id=event_id)
        assert service.filter(first, repository="state archive")
    finally:
        repository.close()


def test_analysis_menu_registers_source_analysis_without_opening_tkinter():
    source = inspect.getsource(GenealogyViewer._create_widgets)
    assert 'label="Анализ"' in source
    assert 'label="Анализ источников"' in source


def test_ignore_persists_and_exports_all_formats(tmp_path):
    repository, _person_id, _uncited_person_id, _event_id, _primary, _orphan_source = build_repository(tmp_path)
    try:
        sidecars = tmp_path / "sidecars"
        service = SourceAnalysisService(repository, sidecars)
        report = service.analyze()
        service.ignore(report.findings[0].finding_id)

        reloaded = SourceAnalysisService(repository, sidecars)
        assert report.findings[0].finding_id not in {item.finding_id for item in reloaded.filter(report)}
        assert reloaded.dispositions_path.exists()
        for export_format, suffix in (("csv", "csv"), ("json", "json"), ("markdown", "md"), ("html", "html"), ("pdf", "pdf")):
            destination = reloaded.export(report, tmp_path / f"report.{suffix}", export_format)
            assert destination.exists() and destination.stat().st_size > 0
        with (tmp_path / "report.csv").open(encoding="utf-8-sig", newline="") as handle:
            assert next(csv.DictReader(handle))["category"]
    finally:
        repository.close()
