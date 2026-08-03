"""Read-only genealogy suggestion engine with sidecar dispositions."""

from __future__ import annotations

import csv
import html
import json
import re
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from config import DATA_DIR
from data_quality_service import DataQualityService


@dataclass(frozen=True)
class IntelligenceSuggestion:
    suggestion_id: str
    category: str
    explanation: str
    supporting_records: tuple[dict[str, Any], ...]
    confidence: int
    reason_codes: tuple[str, ...]
    person_ids: tuple[int, ...] = ()
    family_ids: tuple[int, ...] = ()
    source_ids: tuple[int, ...] = ()


@dataclass(frozen=True)
class IntelligenceReport:
    suggestions: tuple[IntelligenceSuggestion, ...]
    duration_seconds: float
    dataset_size: dict[str, int]
    ignored_count: int

    @property
    def counts(self) -> dict[str, int]:
        result: dict[str, int] = defaultdict(int)
        for suggestion in self.suggestions:
            result[suggestion.category] += 1
        return dict(sorted(result.items()))


class IntelligenceService:
    """Build deterministic suggestions without changing repository state."""

    def __init__(self, repository, data_dir: str | Path | None = None) -> None:
        self.repository = repository
        self.data_dir = Path(data_dir or DATA_DIR) / "intelligence"
        self.dispositions_path = self.data_dir / "suggestions.json"

    def analyze(self, *, progress_callback: Callable[[str, int, int], None] | None = None, cancel_callback: Callable[[], None] | None = None) -> IntelligenceReport:
        started = time.perf_counter()
        people = sorted(self.repository.list_people_full(), key=lambda item: int(item["id"]))
        families = sorted(self.repository.list_families_raw(), key=lambda item: int(item["id"]))
        children = sorted(self.repository.list_family_children_raw(), key=lambda item: (str(item["family_id"]), str(item["child_id"])))
        events = sorted(self.repository.list_all_person_events(), key=lambda item: int(item["id"]))
        citations = sorted(self.repository.list_citation_records(), key=lambda item: int(item["id"]))
        ignored = set(self._dispositions().get("ignored", ()))
        steps = (
            ("Дубликаты и фамилии", self._duplicates_and_surnames), ("Хронология", self._chronology),
            ("Семьи", self._families), ("Места и источники", self._places_and_sources),
        )
        suggestions: list[IntelligenceSuggestion] = []
        for index, (label, analyzer) in enumerate(steps, 1):
            if cancel_callback: cancel_callback()
            suggestions.extend(analyzer(people, families, children, events, citations))
            if progress_callback: progress_callback(label, index, len(steps))
        visible = tuple(item for item in sorted(suggestions, key=lambda item: (item.category, item.suggestion_id)) if item.suggestion_id not in ignored)
        return IntelligenceReport(visible, time.perf_counter() - started, {"people": len(people), "families": len(families), "events": len(events), "citations": len(citations)}, len(ignored))

    def filter(self, report: IntelligenceReport, *, confidence: int = 0, category: str = "", person_id: int | None = None, family_id: int | None = None, source_id: int | None = None, unresolved_only: bool = True) -> tuple[IntelligenceSuggestion, ...]:
        ignored = set(self._dispositions().get("ignored", ())) if unresolved_only else set()
        return tuple(item for item in report.suggestions if item.confidence >= int(confidence) and (not category or item.category == category) and (person_id is None or person_id in item.person_ids) and (family_id is None or family_id in item.family_ids) and (source_id is None or source_id in item.source_ids) and item.suggestion_id not in ignored)

    def ignore(self, suggestion_id: str) -> None:
        state = self._dispositions(); state.setdefault("ignored", [])
        if suggestion_id not in state["ignored"]: state["ignored"].append(suggestion_id)
        self._save_dispositions(state)

    def bookmark(self, suggestion_id: str) -> None:
        state = self._dispositions(); state.setdefault("bookmarks", [])
        if suggestion_id not in state["bookmarks"]: state["bookmarks"].append(suggestion_id)
        self._save_dispositions(state)

    def export(self, report: IntelligenceReport, destination: str | Path, export_format: str) -> Path:
        path = Path(destination); path.parent.mkdir(parents=True, exist_ok=True)
        rows = [asdict(item) for item in report.suggestions]
        if export_format == "csv":
            with path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.writer(handle); writer.writerow(("id", "category", "confidence", "reason_codes", "explanation", "people", "families", "sources"))
                for item in report.suggestions: writer.writerow((item.suggestion_id, item.category, item.confidence, "; ".join(item.reason_codes), item.explanation, "; ".join(map(str, item.person_ids)), "; ".join(map(str, item.family_ids)), "; ".join(map(str, item.source_ids))))
        elif export_format == "json":
            path.write_text(json.dumps({"diagnostics": {"counts": report.counts, "duration_seconds": report.duration_seconds, "dataset_size": report.dataset_size, "ignored_count": report.ignored_count}, "suggestions": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
        elif export_format in {"markdown", "html", "pdf"}:
            markdown = self._markdown(report)
            if export_format == "markdown": path.write_text(markdown, encoding="utf-8")
            elif export_format == "html": path.write_text("<!doctype html><html><meta charset=\"utf-8\"><body><pre>" + html.escape(markdown) + "</pre></body></html>", encoding="utf-8")
            else: self._write_pdf(path, markdown)
        else: raise ValueError(f"Unsupported export format: {export_format}")
        return path

    @staticmethod
    def _year(value: object) -> int | None:
        match = re.search(r"(?<!\d)(\d{4})(?!\d)", str(value or ""))
        return int(match.group(1)) if match else None

    @staticmethod
    def _normal(value: object) -> str:
        return re.sub(r"[^a-z0-9а-я]+", "", str(value or "").casefold())

    def _duplicates_and_surnames(self, people, *_unused):
        suggestions = []
        groups: dict[tuple[str, str, int | None], list[dict]] = defaultdict(list)
        surnames: dict[str, list[dict]] = defaultdict(list)
        for person in people:
            key = (self._normal(person["first_name"]), self._normal(person["last_name"]), self._year(person["birth_date"]))
            if key[0] and key[1]: groups[key].append(person)
            if person["last_name"]: surnames[self._normal(person["last_name"])].append(person)
        for key, members in groups.items():
            if len(members) > 1:
                ids = tuple(int(item["id"]) for item in members); suggestions.append(self._suggest("probable_duplicate_people", ids, (), (), 94 if key[2] else 82, ("NAME_MATCH", "BIRTH_YEAR_MATCH" if key[2] else "NAME_MATCH_ONLY"), f"Совпадают имя и фамилия{' и год рождения' if key[2] else ''}."))
        variants: dict[str, list[dict]] = defaultdict(list)
        for person in people:
            stem = self._normal(person["last_name"])[:5]
            if stem: variants[stem].append(person)
        for members in variants.values():
            names = {self._normal(item["last_name"]) for item in members}
            if len(names) > 1:
                ids = tuple(sorted(int(item["id"]) for item in members)); suggestions.append(self._suggest("surname_variation", ids, (), (), 65, ("SURNAME_STEM",), "Похожие варианты фамилии требуют проверки."))
        return suggestions

    def _chronology(self, people, *_unused):
        suggestions = []
        for person in people:
            birth, death = self._year(person["birth_date"]), self._year(person["death_date"])
            if birth and death and death < birth:
                suggestions.append(self._suggest("chronology", (int(person["id"]),), (), (), 99, ("DEATH_BEFORE_BIRTH",), "Дата смерти раньше даты рождения."))
        return suggestions

    def _families(self, people, families, children, *_unused):
        people_by_ref = {str(item["id"]): item for item in people} | {str(item["gedcom_id"]): item for item in people if item["gedcom_id"]}
        family_by_ref = {str(item["id"]): int(item["id"]) for item in families} | {str(item["gedcom_id"]): int(item["id"]) for item in families if item["gedcom_id"]}
        children_by_family: dict[str, list[str]] = defaultdict(list)
        for item in children:
            family_id = family_by_ref.get(str(item["family_id"]))
            if family_id is not None:
                children_by_family[str(family_id)].append(str(item["child_id"]))
        suggestions, links = [], defaultdict(set)
        for family in families:
            family_id = int(family["id"]); parents = [people_by_ref.get(str(family[key])) for key in ("husband_id", "wife_id") if family.get(key)]
            kids = [people_by_ref.get(value) for value in children_by_family[str(family_id)]]
            for parent in parents:
                if parent:
                    for child in kids:
                        if child: links[int(parent["id"])].add(int(child["id"])); links[int(child["id"])].add(int(parent["id"]))
                        age = self._year(child["birth_date"]) - self._year(parent["birth_date"]) if child and self._year(child["birth_date"]) and self._year(parent["birth_date"]) else None
                        if age is not None and (age < 13 or age > 75): suggestions.append(self._suggest("parent_age", (int(parent["id"]), int(child["id"])), (family_id,), (), 96, ("PARENT_AGE_IMPLAUSIBLE",), "Возраст родителя при рождении ребёнка вне ожидаемого диапазона."))
            if len(parents) == 2:
                years = [self._year(item["birth_date"]) for item in parents]
                if all(years) and abs(years[0] - years[1]) > 45: suggestions.append(self._suggest("unlikely_marriage", tuple(int(item["id"]) for item in parents), (family_id,), (), 60, ("SPOUSE_AGE_GAP",), "Большая разница возраста партнёров требует проверки."))
            known_kids = [item for item in kids if item]
            if len(known_kids) > 1: suggestions.append(self._suggest("possible_sibling_group", tuple(sorted(int(item["id"]) for item in known_kids)), (family_id,), (), 85, ("SHARED_PARENTS",), "Дети с общими указанными родителями образуют группу братьев и сестёр."))
        for person in people:
            if int(person["id"]) not in links: suggestions.append(self._suggest("isolated_person", (int(person["id"]),), (), (), 75, ("NO_FAMILY_LINKS",), "Человек не связан с семейной ветвью."))
            note = self._normal(person.get("note", ""))
            if ("father" in note or "mother" in note or "отец" in note or "мать" in note) and int(person["id"]) not in links:
                suggestions.append(self._suggest("missing_parent_evidence", (int(person["id"]),), (), (), 68, ("PARENT_MENTION_IN_NOTE", "NO_FAMILY_LINKS"), "Заметка упоминает родителя, но семейная связь не указана."))
        remaining = {int(person["id"]) for person in people if int(person["id"]) in links}
        components = []
        while remaining:
            seed, component, frontier = remaining.pop(), set(), []
            frontier.append(seed)
            while frontier:
                current = frontier.pop()
                if current in component: continue
                component.add(current); frontier.extend(links[current] - component); remaining.discard(current)
            components.append(component)
        if len(components) > 1:
            for component in components:
                suggestions.append(self._suggest("disconnected_family_branch", tuple(sorted(component)), (), (), 72, ("DISCONNECTED_GRAPH_COMPONENT",), "Семейная ветвь не связана с другими семейными компонентами."))
        return suggestions

    def _places_and_sources(self, people, _families, _children, events, citations):
        suggestions = []; places: dict[str, list[dict]] = defaultdict(list); sources: dict[str, list[dict]] = defaultdict(list)
        for person in people:
            for place in (person.get("birth_place"), person.get("death_place")):
                if place: places[self._normal(place)].append({"person_id": int(person["id"]), "place": place})
        for event in events:
            if event.get("place"): places[self._normal(event["place"])].append({"person_id": event.get("person_id"), "event_id": event["id"], "place": event["place"]})
        for entries in places.values():
            variants = {str(item["place"]).casefold() for item in entries}
            if len(variants) > 1: suggestions.append(self._suggest("duplicate_place", tuple(sorted({int(item["person_id"]) for item in entries if item.get("person_id")})), (), (), 70, ("NORMALIZED_PLACE_MATCH",), "Разные написания места нормализуются одинаково.", records=tuple(entries)))
        for citation in citations:
            title = citation.get("source_title", "")
            if title: sources[self._normal(title)].append(citation)
        for entries in sources.values():
            titles = {item["source_title"].casefold() for item in entries}
            source_ids = tuple(sorted({int(item["source_id"]) for item in entries}))
            if len(source_ids) > 1 or len(titles) > 1: suggestions.append(self._suggest("duplicate_source", (), (), source_ids, 78, ("NORMALIZED_SOURCE_TITLE_MATCH",), "Источники имеют совпадающие или похожие названия.", records=tuple({"source_id": item["source_id"], "title": item["source_title"]} for item in entries)))
        return suggestions

    def _suggest(self, category, person_ids, family_ids, source_ids, confidence, reasons, explanation, records=()):
        identifier = f"{category}:p{','.join(map(str, sorted(set(person_ids))))}:f{','.join(map(str, sorted(set(family_ids))))}:s{','.join(map(str, sorted(set(source_ids))))}:{','.join(reasons)}"
        supporting = records or tuple({"person_id": value} for value in sorted(set(person_ids)))
        return IntelligenceSuggestion(identifier, category, explanation, tuple(supporting), max(0, min(100, int(confidence))), tuple(reasons), tuple(sorted(set(person_ids))), tuple(sorted(set(family_ids))), tuple(sorted(set(source_ids))))

    def _dispositions(self):
        try:
            value = json.loads(self.dispositions_path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, ValueError, json.JSONDecodeError): return {}

    def _save_dispositions(self, state):
        self.data_dir.mkdir(parents=True, exist_ok=True); self.dispositions_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _markdown(report):
        lines = ["# Intelligence Center", "", f"Duration: {report.duration_seconds:.4f}s", f"Dataset: {report.dataset_size}", ""]
        for item in report.suggestions: lines.extend((f"## {item.category} ({item.confidence}%)", item.explanation, f"Reasons: {', '.join(item.reason_codes)}", ""))
        return "\n".join(lines)

    @staticmethod
    def _write_pdf(path, text):
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)"); stream = "BT /F1 9 Tf 36 780 Td " + " ".join(f"({line[:105]}) Tj 0 -11 Td" for line in safe.splitlines()[:140]) + " ET"; objects = ["<< /Type /Catalog /Pages 2 0 R >>", "<< /Type /Pages /Kids [3 0 R] /Count 1 >>", "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>", "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", f"<< /Length {len(stream.encode('latin-1', 'replace'))} >>\nstream\n{stream}\nendstream"]; content = ["%PDF-1.4\n"]; offsets = [0]
        for index, item in enumerate(objects, 1): offsets.append(sum(len(part.encode("latin-1", "replace")) for part in content)); content.append(f"{index} 0 obj\n{item}\nendobj\n")
        start = sum(len(part.encode("latin-1", "replace")) for part in content); content.append(f"xref\n0 6\n0000000000 65535 f \n" + "".join(f"{offset:010d} 00000 n \n" for offset in offsets[1:]) + f"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n"); Path(path).write_bytes("".join(content).encode("latin-1", "replace"))
