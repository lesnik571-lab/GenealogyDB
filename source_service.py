"""Reusable source, citation, browser, export, and statistics services."""

from __future__ import annotations

import csv
from pathlib import Path

from repository.person_repository import PersonRepository


TARGET_TYPES = ("person", "family", "event", "relationship")
SOURCE_FIELDS = ("title", "author", "publication", "repository", "call_number", "url", "notes")
CITATION_FIELDS = ("page", "quality", "transcription", "comment")


class SourceService:
    """Manage reusable sources and citations without exposing SQL to the UI."""

    def __init__(self, repository: PersonRepository):
        self.repository = repository

    def create_source(self, data):
        payload = self._source_payload(data)
        source_id = self.repository.create_source_record(payload)
        return self.repository.get_source_record(source_id)

    def update_source(self, source_id, data):
        payload = self._source_payload(data)
        if not self.repository.update_source_record(source_id, payload):
            raise ValueError("Источник не найден")
        return self.repository.get_source_record(source_id)

    def delete_source(self, source_id):
        return self.repository.delete_source_record(source_id)

    def list_sources(self):
        return self.repository.list_source_records()

    def get_source(self, source_id):
        return self.repository.get_source_record(source_id)

    def create_citation(self, source_id, target_type, target_id, **details):
        payload = self._citation_payload(source_id, target_type, target_id, details)
        citation_id = self.repository.create_citation_record(payload)
        return next(item for item in self.repository.list_citation_records(source_id) if item["id"] == citation_id)

    def update_citation(self, citation_id, source_id, target_type, target_id, **details):
        payload = self._citation_payload(source_id, target_type, target_id, details)
        if not self.repository.update_citation_record(citation_id, payload):
            raise ValueError("Цитата не найдена")
        return next(item for item in self.repository.list_citation_records(source_id) if item["id"] == citation_id)

    def delete_citation(self, citation_id):
        return self.repository.delete_citation_record(citation_id)

    def list_citations(self, source_id=None):
        return self.repository.list_citation_records(source_id)

    def browser_rows(self):
        rows = []
        for citation in self.repository.list_citation_records():
            try:
                target = self.resolve_target(citation["target_type"], citation["target_id"])
            except ValueError:
                target = {"target_label": "Недоступный объект", "linked_person_id": None}
            rows.append({**citation, **target})
        return rows

    def resolve_target(self, target_type, target_id):
        target_type = str(target_type or "").strip().lower()
        target_id = str(target_id or "").strip()
        if target_type not in TARGET_TYPES or not target_id:
            raise ValueError("Укажите допустимый тип и ID объекта")
        if target_type == "person":
            person = self.repository.get_person_record(int(target_id))
            if not person:
                raise ValueError("Человек не найден")
            return {"target_label": self.repository.format_person_label(person), "linked_person_id": person["id"]}
        if target_type == "event":
            event = self.repository.get_person_event(int(target_id))
            if not event:
                raise ValueError("Событие не найдено")
            person = self.repository.get_person_record(event["person_id"])
            label = self.repository.format_person_label(person) if person else str(event["person_id"])
            return {"target_label": f"{event['event_type']}: {label}", "linked_person_id": event["person_id"]}
        family = self.repository.get_family(int(target_id))
        if not family:
            raise ValueError("Семья не найдена")
        member_names = []
        linked_person_id = None
        for reference in (family.get("husband"), family.get("wife"), *family.get("children", [])):
            person_id = self.repository.resolve_person_reference(reference)
            if person_id is None:
                continue
            linked_person_id = linked_person_id or person_id
            person = self.repository.get_person_record(person_id)
            if person:
                member_names.append(self.repository.format_person_label(person))
        prefix = "Семья" if target_type == "family" else "Отношение"
        return {
            "target_label": f"{prefix} {target_id}: {', '.join(member_names) or 'без участников'}",
            "linked_person_id": linked_person_id,
        }

    def statistics(self):
        sources = self.repository.list_source_records()
        citations = self.repository.list_citation_records()
        repository_counts = {}
        for source in sources:
            name = source.get("repository") or "Не указан"
            repository_counts[name] = repository_counts.get(name, 0) + 1
        by_target_type = {target_type: 0 for target_type in TARGET_TYPES}
        for citation in citations:
            by_target_type[citation["target_type"]] += 1
        ranked = sorted(
            ((source["title"], source.get("citation_count", 0)) for source in sources),
            key=lambda item: (-item[1], item[0].casefold()),
        )
        return {
            "source_count": len(sources),
            "citation_count": len(citations),
            "orphan_sources": [source for source in sources if not source.get("citation_count")],
            "most_referenced": ranked,
            "by_target_type": by_target_type,
            "by_repository": dict(sorted(repository_counts.items(), key=lambda item: item[0].casefold())),
        }

    def export_csv(self, destination_path):
        destination = Path(destination_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        sources = {source["id"]: source for source in self.repository.list_source_records()}
        with destination.open("w", encoding="utf-8-sig", newline="") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow((*SOURCE_FIELDS, "target_type", "target_id", "target", *CITATION_FIELDS))
            for usage in self.browser_rows():
                source = sources[usage["source_id"]]
                writer.writerow((
                    *(source[field] for field in SOURCE_FIELDS),
                    usage["target_type"], usage["target_id"], usage["target_label"],
                    *(usage[field] for field in CITATION_FIELDS),
                ))
        return destination

    @staticmethod
    def _source_payload(data):
        payload = {field: str(data.get(field) or "").strip() for field in SOURCE_FIELDS}
        if not payload["title"]:
            raise ValueError("Название источника обязательно")
        return payload

    def _citation_payload(self, source_id, target_type, target_id, details):
        if self.repository.get_source_record(source_id) is None:
            raise ValueError("Источник не найден")
        target_type = str(target_type or "").strip().lower()
        target_id = str(target_id or "").strip()
        self.resolve_target(target_type, target_id)
        return {
            "source_id": source_id,
            "target_type": target_type,
            "target_id": target_id,
            **{field: str(details.get(field) or "").strip() for field in CITATION_FIELDS},
        }
