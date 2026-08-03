"""Read-only source and citation analysis with separate finding dispositions."""

from __future__ import annotations

import csv
import html
import json
import re
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from config import DATA_DIR


@dataclass(frozen=True)
class SourceAnalysisFinding:
    finding_id: str
    category: str
    severity: str
    confidence: int
    explanation: str
    linked_records: tuple[dict[str, Any], ...]
    suggested_actions: tuple[str, ...]
    source_ids: tuple[int, ...] = ()
    repositories: tuple[str, ...] = ()
    person_ids: tuple[int, ...] = ()
    event_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class SourceAnalysisReport:
    findings: tuple[SourceAnalysisFinding, ...]
    statistics: dict[str, float | int]
    duration_seconds: float
    ignored_count: int

    @property
    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for finding in self.findings:
            counts[finding.category] += 1
        return dict(sorted(counts.items()))


class SourceAnalysisService:
    """Analyze existing source evidence without mutating genealogy data."""

    SEVERITIES = ("critical", "high", "medium", "low")

    def __init__(self, repository, data_dir: str | Path | None = None) -> None:
        self.repository = repository
        self.data_dir = Path(data_dir or DATA_DIR) / "source_analysis"
        self.dispositions_path = self.data_dir / "findings.json"

    def analyze(
        self,
        *,
        progress_callback: Callable[[str, int, int], None] | None = None,
        cancel_callback: Callable[[], None] | None = None,
    ) -> SourceAnalysisReport:
        started = time.perf_counter()
        sources = sorted(self.repository.list_source_records(), key=lambda item: int(item["id"]))
        citations = sorted(self.repository.list_citation_records(), key=lambda item: int(item["id"]))
        people = sorted(self.repository.list_people_full(), key=lambda item: int(item["id"]))
        events = sorted(self.repository.list_all_person_events(), key=lambda item: int(item["id"]))
        families = sorted(self.repository.list_families_raw(), key=lambda item: int(item["id"]))
        stages = (
            ("Coverage", self._coverage_findings),
            ("Citation integrity", self._citation_findings),
            ("Source consistency", self._source_findings),
            ("Evidence strength", self._evidence_findings),
        )
        findings: list[SourceAnalysisFinding] = []
        for index, (label, analyzer) in enumerate(stages, 1):
            if cancel_callback:
                cancel_callback()
            findings.extend(analyzer(sources, citations, people, events, families))
            if progress_callback:
                progress_callback(label, index, len(stages))
        ignored = set(self._dispositions().get("ignored", ()))
        visible = tuple(
            finding for finding in sorted(findings, key=lambda item: (item.severity, item.category, item.finding_id))
            if finding.finding_id not in ignored
        )
        return SourceAnalysisReport(
            visible,
            self._statistics(sources, citations, people, events, findings),
            time.perf_counter() - started,
            len(ignored),
        )

    def filter(
        self,
        report: SourceAnalysisReport,
        *,
        severity: str = "",
        source_id: int | None = None,
        repository: str = "",
        person_id: int | None = None,
        event_id: int | None = None,
        unresolved_only: bool = True,
    ) -> tuple[SourceAnalysisFinding, ...]:
        ignored = set(self._dispositions().get("ignored", ())) if unresolved_only else set()
        repository_key = self._normal(repository)
        return tuple(
            finding for finding in report.findings
            if (not severity or finding.severity == severity)
            and (source_id is None or source_id in finding.source_ids)
            and (not repository_key or any(repository_key in self._normal(value) for value in finding.repositories))
            and (person_id is None or person_id in finding.person_ids)
            and (event_id is None or event_id in finding.event_ids)
            and finding.finding_id not in ignored
        )

    def ignore(self, finding_id: str) -> None:
        state = self._dispositions()
        ignored = state.setdefault("ignored", [])
        if finding_id not in ignored:
            ignored.append(finding_id)
        self._save_dispositions(state)

    def export(self, report: SourceAnalysisReport, destination: str | Path, export_format: str) -> Path:
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        if export_format == "csv":
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(("id", "category", "severity", "confidence", "explanation", "actions", "sources", "repositories", "people", "events"))
                for item in report.findings:
                    writer.writerow((item.finding_id, item.category, item.severity, item.confidence, item.explanation, "; ".join(item.suggested_actions), "; ".join(map(str, item.source_ids)), "; ".join(item.repositories), "; ".join(map(str, item.person_ids)), "; ".join(map(str, item.event_ids))))
        elif export_format == "json":
            path.write_text(json.dumps({"statistics": report.statistics, "counts": report.counts, "duration_seconds": report.duration_seconds, "ignored_count": report.ignored_count, "findings": [asdict(item) for item in report.findings]}, ensure_ascii=False, indent=2), encoding="utf-8")
        elif export_format in {"markdown", "html", "pdf"}:
            markdown = self._markdown(report)
            if export_format == "markdown":
                path.write_text(markdown, encoding="utf-8")
            elif export_format == "html":
                path.write_text(f"<!doctype html><html><meta charset=\"utf-8\"><body><pre>{html.escape(markdown)}</pre></body></html>", encoding="utf-8")
            else:
                self._write_pdf(path, markdown)
        else:
            raise ValueError(f"Unsupported export format: {export_format}")
        return path

    def _coverage_findings(self, sources, citations, people, events, _families):
        findings = []
        person_citations = {self._as_int(item["target_id"]) for item in citations if item["target_type"] == "person"}
        event_citations = {self._as_int(item["target_id"]) for item in citations if item["target_type"] == "event"}
        for person in people:
            person_id = int(person["id"])
            if person_id not in person_citations:
                findings.append(self._finding("uncited_person", "medium", 95, "Person has no linked citation.", ("Attach or review evidence for this person.",), person_ids=(person_id,), records=({"person_id": person_id, "name": self._person_name(person)},)))
                if any(person.get(field) for field in ("birth_date", "death_date", "birth_place", "death_place")):
                    findings.append(self._finding("unsupported_conclusion", "high", 88, "Recorded person conclusion has no direct citation.", ("Review the conclusion and attach supporting evidence.",), person_ids=(person_id,), records=({"person_id": person_id, "name": self._person_name(person)},)))
        for event in events:
            event_id = int(event["id"])
            if event_id not in event_citations:
                findings.append(self._finding("uncited_event", "medium", 95, "Event has no linked citation.", ("Attach or review evidence for this event.",), person_ids=(int(event["person_id"]),), event_ids=(event_id,), records=({"event_id": event_id, "event_type": event.get("event_type", "")},)))
                if event.get("date") or event.get("place") or event.get("description"):
                    findings.append(self._finding("unsupported_conclusion", "high", 88, "Recorded event conclusion has no direct citation.", ("Review the conclusion and attach supporting evidence.",), person_ids=(int(event["person_id"]),), event_ids=(event_id,), records=({"event_id": event_id, "event_type": event.get("event_type", "")},)))
        cited_sources = {int(item["source_id"]) for item in citations}
        for source in sources:
            if int(source["id"]) not in cited_sources:
                findings.append(self._finding("source_without_links", "low", 99, "Source has no linked records.", ("Link the source to a supported record or archive it.",), source_ids=(int(source["id"]),), repositories=(str(source.get("repository") or ""),), records=({"source_id": int(source["id"]), "title": source.get("title", "")},)))
        return findings

    def _citation_findings(self, sources, citations, people, events, families):
        findings = []
        source_by_id = {int(item["id"]): item for item in sources}
        valid = {
            "person": {str(item["id"]) for item in people} | {str(item["gedcom_id"]) for item in people if item.get("gedcom_id")},
            "event": {str(item["id"]) for item in events},
            "family": {str(item["id"]) for item in families} | {str(item["gedcom_id"]) for item in families if item.get("gedcom_id")},
            "relationship": {str(item["id"]) for item in families} | {str(item["gedcom_id"]) for item in families if item.get("gedcom_id")},
        }
        duplicate_groups: dict[tuple[str, ...], list[dict]] = defaultdict(list)
        by_target: dict[tuple[str, str], list[dict]] = defaultdict(list)
        for citation in citations:
            source = source_by_id.get(int(citation["source_id"]), {})
            source_id = int(citation["source_id"])
            target_type, target_id = str(citation.get("target_type") or ""), str(citation.get("target_id") or "")
            if target_type not in valid or target_id not in valid[target_type]:
                findings.append(self._finding("orphan_citation", "critical", 99, "Citation points to a missing or unsupported target.", ("Open Evidence Manager and retarget or remove the citation.",), source_ids=(source_id,), repositories=(str(source.get("repository") or ""),), records=({"citation_id": int(citation["id"]), "target_type": target_type, "target_id": target_id},)))
            duplicate_groups[self._citation_signature(citation)].append(citation)
            by_target[(target_type, target_id)].append(citation)
        for items in duplicate_groups.values():
            if len(items) > 1:
                source_ids = tuple(sorted({int(item["source_id"]) for item in items}))
                findings.append(self._finding("duplicate_citation", "high", 98, "Equivalent citations are linked to the same record.", ("Compare the citations and retain the best documented record.",), source_ids=source_ids, repositories=tuple(sorted({str(source_by_id.get(value, {}).get("repository") or "") for value in source_ids})), records=tuple({"citation_id": int(item["id"])} for item in items)))
        for (target_type, target_id), items in by_target.items():
            years = {year for item in items for year in self._years(item) }
            if len(years) > 1:
                findings.append(self._finding("conflicting_source_dates", "high", 72, "Citation text contains conflicting years for one linked record.", ("Compare the source transcriptions and resolve the date evidence.",), source_ids=tuple(sorted({int(item["source_id"]) for item in items})), person_ids=(self._as_int(target_id),) if target_type == "person" and self._as_int(target_id) else (), event_ids=(self._as_int(target_id),) if target_type == "event" and self._as_int(target_id) else (), records=tuple({"citation_id": int(item["id"]), "years": self._years(item)} for item in items)))
            places = {place for item in items for place in self._places(item)}
            if len(places) > 1:
                findings.append(self._finding("conflicting_place_references", "high", 72, "Citation text contains conflicting places for one linked record.", ("Compare the source transcriptions and resolve the place evidence.",), source_ids=tuple(sorted({int(item["source_id"]) for item in items})), person_ids=(self._as_int(target_id),) if target_type == "person" and self._as_int(target_id) else (), event_ids=(self._as_int(target_id),) if target_type == "event" and self._as_int(target_id) else (), records=tuple({"citation_id": int(item["id"]), "places": self._places(item)} for item in items)))
        return findings

    def _source_findings(self, sources, _citations, _people, _events, _families):
        repositories: dict[str, list[dict]] = defaultdict(list)
        for source in sources:
            name = str(source.get("repository") or "").strip()
            if name:
                repositories[self._normal(name)].append(source)
        findings = []
        for values in repositories.values():
            names = {str(item.get("repository") or "").strip() for item in values}
            if len(names) > 1:
                findings.append(self._finding("duplicated_repository", "low", 80, "Repository names normalize to the same value but use different spellings.", ("Standardize the repository naming convention.",), source_ids=tuple(sorted(int(item["id"]) for item in values)), repositories=tuple(sorted(names)), records=tuple({"source_id": int(item["id"]), "repository": item.get("repository", "")} for item in values)))
        return findings

    def _evidence_findings(self, sources, citations, _people, _events, _families):
        source_by_id = {int(item["id"]): item for item in sources}
        findings = []
        for citation in citations:
            quality = self._normal(citation.get("quality"))
            if quality in {"", "weak", "unknown", "low"} or not str(citation.get("transcription") or "").strip():
                source_id = int(citation["source_id"])
                source = source_by_id.get(source_id, {})
                target_type, target_id = citation.get("target_type", ""), self._as_int(citation.get("target_id"))
                findings.append(self._finding("weak_evidence_chain", "medium", 82, "Citation has weak, unknown, or undocumented evidence detail.", ("Review citation quality, transcription, and linked evidence.",), source_ids=(source_id,), repositories=(str(source.get("repository") or ""),), person_ids=(target_id,) if target_type == "person" and target_id else (), event_ids=(target_id,) if target_type == "event" and target_id else (), records=({"citation_id": int(citation["id"]), "quality": citation.get("quality", "")},)))
        return findings

    def _statistics(self, sources, citations, people, events, findings):
        valid_people = {str(item["id"]) for item in people}
        valid_events = {str(item["id"]) for item in events}
        direct_people = {str(item["target_id"]) for item in citations if item.get("target_type") == "person"}
        cited_records = (
            direct_people & valid_people
        ) | {
            f"event:{item['target_id']}"
            for item in citations
            if item.get("target_type") == "event" and str(item["target_id"]) in valid_events
        }
        record_count = len(people) + len(events)
        duplicate_count = sum(1 for item in findings if item.category == "duplicate_citation")
        unsupported = sum(1 for item in findings if item.category == "unsupported_conclusion")
        return {
            "total_sources": len(sources),
            "citations": len(citations),
            "average_citations_per_person": round(sum(1 for item in citations if item.get("target_type") == "person") / len(people) if people else 0, 4),
            "evidence_coverage": round(len(cited_records) / record_count if record_count else 0, 4),
            "unsupported_records": unsupported,
            "duplicate_rate": round(duplicate_count / len(citations) if citations else 0, 4),
        }

    def _finding(self, category, severity, confidence, explanation, actions, *, source_ids=(), repositories=(), person_ids=(), event_ids=(), records=()):
        source_ids = tuple(sorted(set(int(item) for item in source_ids)))
        repositories = tuple(sorted(set(str(item) for item in repositories if str(item))))
        person_ids = tuple(sorted(set(int(item) for item in person_ids)))
        event_ids = tuple(sorted(set(int(item) for item in event_ids)))
        identifier = f"{category}:s{','.join(map(str, source_ids))}:r{','.join(self._normal(item) for item in repositories)}:p{','.join(map(str, person_ids))}:e{','.join(map(str, event_ids))}:{self._normal(explanation)}"
        return SourceAnalysisFinding(identifier, category, severity, max(0, min(100, int(confidence))), explanation, tuple(records), tuple(actions), source_ids, repositories, person_ids, event_ids)

    def _dispositions(self):
        try:
            state = json.loads(self.dispositions_path.read_text(encoding="utf-8"))
            return state if isinstance(state, dict) else {}
        except (OSError, ValueError, json.JSONDecodeError):
            return {}

    def _save_dispositions(self, state):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.dispositions_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _citation_signature(citation):
        return tuple(str(citation.get(field) or "").strip().casefold() for field in ("source_id", "target_type", "target_id", "page", "transcription"))

    @staticmethod
    def _normal(value):
        return re.sub(r"[^a-z0-9а-я]+", "", str(value or "").casefold())

    @staticmethod
    def _as_int(value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _person_name(person):
        return " ".join(part for part in (person.get("first_name"), person.get("last_name")) if part).strip()

    @staticmethod
    def _years(citation):
        return tuple(sorted(set(re.findall(r"(?<!\d)(?:1[5-9]\d{2}|20\d{2})(?!\d)", " ".join(str(citation.get(field) or "") for field in ("page", "transcription", "comment"))))))

    @staticmethod
    def _places(citation):
        text = " ".join(str(citation.get(field) or "") for field in ("transcription", "comment"))
        return tuple(sorted(set(match.strip().casefold() for match in re.findall(r"(?:place|место)\s*[:=]\s*([^;\n]+)", text, flags=re.IGNORECASE) if match.strip())))

    @staticmethod
    def _markdown(report):
        lines = ["# Source Analysis Center", "", "## Statistics"]
        lines.extend(f"- {key}: {value}" for key, value in report.statistics.items())
        lines.extend(("", f"Duration: {report.duration_seconds:.4f}s", ""))
        for item in report.findings:
            lines.extend((f"## {item.severity}: {item.category} ({item.confidence}%)", item.explanation, f"Suggested review: {'; '.join(item.suggested_actions)}", ""))
        return "\n".join(lines)

    @staticmethod
    def _write_pdf(path, text):
        escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream = "BT /F1 9 Tf 36 780 Td " + " ".join(f"({line[:105]}) Tj 0 -11 Td" for line in escaped.splitlines()[:140]) + " ET"
        objects = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>", "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", f"<< /Length {len(stream.encode('latin-1', 'replace'))} >>\nstream\n{stream}\nendstream"]
        content = ["%PDF-1.4\n"]
        offsets = [0]
        for index, item in enumerate(objects, 1):
            offsets.append(sum(len(part.encode("latin-1", "replace")) for part in content))
            content.append(f"{index} 0 obj\n{item}\nendobj\n")
        start = sum(len(part.encode("latin-1", "replace")) for part in content)
        content.append("xref\n0 6\n0000000000 65535 f \n" + "".join(f"{offset:010d} 00000 n \n" for offset in offsets[1:]) + f"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n")
        Path(path).write_bytes("".join(content).encode("latin-1", "replace"))
