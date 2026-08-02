from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, TypeAlias

from repository.person_repository import PersonRepository
from text_utils import normalize_search_text
from logging_service import log_operation


PersonData: TypeAlias = dict[str, Any]
FamilyContext: TypeAlias = dict[str, set[str]]
FamilyContexts: TypeAlias = dict[str, FamilyContext]
RecoveryIndexes: TypeAlias = tuple[
    list[PersonData],
    dict[str, PersonData],
    list[PersonData],
    dict[str, list[str]],
    dict[int, list[str]],
]

EDITABLE_PERSON_FIELDS = (
    "first_name",
    "last_name",
    "sex",
    "birth_date",
    "birth_place",
    "death_date",
    "death_place",
    "occupation",
    "note",
)
MATCH_WEIGHTS = {
    "first_name": 18,
    "last_name": 18,
    "birth_year": 15,
    "birth_place": 10,
    "parents": 11,
    "partners": 8,
    "children": 9,
    "families": 5,
    "note": 6,
}
MATCH_PREFIX_SIMILARITY = 0.85
MATCH_CONTAINS_SIMILARITY = 0.7
MAX_CONFIDENCE = 100


@dataclass(frozen=True)
class RecoveryRecord:
    """A person record eligible for guided recovery."""
    person_id: int
    gedcom_id: str
    first_name: str
    last_name: str
    sex: str
    birth_date: str
    birth_place: str
    death_date: str
    death_place: str
    occupation: str
    note: str
    parents: tuple[str, ...]
    partners: tuple[str, ...]
    children: tuple[str, ...]
    parent_links: tuple[tuple[str, int], ...]
    partner_links: tuple[tuple[str, int], ...]
    child_links: tuple[tuple[str, int], ...]
    event_count: int
    event_descriptions: tuple[str, ...]


@dataclass(frozen=True)
class RecoveryMatchCandidate:
    """A possible existing-person match with confidence details."""
    person_id: int
    gedcom_id: str
    full_name: str
    birth_date: str
    birth_place: str
    confidence: int


class RecoveryWizardService:
    """Read-only family context plus safe updates for incomplete people.

    The service never creates, deletes, merges, or relinks people. It only
    updates fields on an existing person record selected by database ID.
    """

    def __init__(self, repository: PersonRepository) -> None:
        """Initialize the service with an existing person repository."""
        self.repository = repository

    @staticmethod
    def _reference(person: Mapping[str, Any]) -> str:
        return (person.get("gedcom_id") or str(person.get("id") or "")).strip()

    @staticmethod
    def _display_name(person: Mapping[str, Any] | None) -> str:
        if not person:
            return "Неизвестный человек"
        name = f"{person.get('last_name', '')} {person.get('first_name', '')}".strip()
        if name:
            return name
        ref = person.get("gedcom_id") or person.get("id") or "?"
        return f"Без имени ({ref})"

    @staticmethod
    def _normalize(value: object) -> str:
        return normalize_search_text(value)

    @classmethod
    def _text_similarity(cls, left: object, right: object) -> float:
        left_text = cls._normalize(left)
        right_text = cls._normalize(right)
        if not left_text or not right_text:
            return 0.0
        if left_text == right_text:
            return 1.0
        if left_text.startswith(right_text) or right_text.startswith(left_text):
            return MATCH_PREFIX_SIMILARITY
        if left_text in right_text or right_text in left_text:
            return MATCH_CONTAINS_SIMILARITY
        return 0.0

    @staticmethod
    def _birth_year(value: object) -> str:
        match = re.search(r"\b(\d{4})\b", str(value or ""))
        return match.group(1) if match else ""

    @staticmethod
    def _set_similarity(left: Iterable[str] | None, right: Iterable[str] | None) -> float:
        left_values = set(left or ())
        right_values = set(right or ())
        if not left_values or not right_values:
            return 0.0
        return len(left_values & right_values) / max(len(left_values), len(right_values))

    @staticmethod
    def _empty_family_context() -> FamilyContext:
        return {"parents": set(), "partners": set(), "children": set(), "families": set()}

    def _build_family_contexts(
        self,
        people: list[PersonData],
        families: list[PersonData],
        children_by_family: dict[str, list[str]],
    ) -> FamilyContexts:
        aliases: dict[str, str] = {}
        contexts: FamilyContexts = {}
        for person in people:
            canonical = self._reference(person)
            contexts.setdefault(canonical, self._empty_family_context())
            for alias in (str(person.get("id") or ""), person.get("gedcom_id") or ""):
                if alias:
                    aliases[str(alias)] = canonical

        def canonical(reference: object) -> str:
            reference = str(reference or "")
            return aliases.get(reference, reference)

        for family in families:
            family_ref = str(family.get("gedcom_id") or family.get("id") or "")
            husband = canonical(family.get("husband_id"))
            wife = canonical(family.get("wife_id"))
            children = [canonical(value) for value in children_by_family.get(family_ref, []) if value]
            members = [value for value in (husband, wife, *children) if value]
            for member in members:
                contexts.setdefault(member, self._empty_family_context())
            for parent in (husband, wife):
                if parent:
                    contexts[parent]["families"].add(f"{family_ref}:parent")
            for child in children:
                contexts[child]["families"].add(f"{family_ref}:child")
            if husband and wife:
                contexts[husband]["partners"].add(wife)
                contexts[wife]["partners"].add(husband)
            for child in children:
                if husband:
                    contexts[child]["parents"].add(husband)
                    contexts[husband]["children"].add(child)
                if wife:
                    contexts[child]["parents"].add(wife)
                    contexts[wife]["children"].add(child)
        return contexts

    def _build_indexes(self) -> RecoveryIndexes:
        people = self.repository.list_people_full()
        people_by_ref: dict[str, dict] = {}
        for person in people:
            for ref in {str(person.get("id") or "").strip(), (person.get("gedcom_id") or "").strip()}:
                if ref:
                    people_by_ref[ref] = person

        families: list[PersonData] = self.repository.list_families_raw()
        children_by_family: dict[str, list[str]] = {}
        for row in self.repository.list_family_children_raw():
            children_by_family.setdefault(str(row.get("family_id") or ""), []).append(str(row.get("child_id") or ""))

        events_by_person: dict[int, list[str]] = {}
        for event in self.repository.list_all_person_events():
            person_id = event.get("person_id")
            try:
                person_id = int(person_id)
            except (TypeError, ValueError):
                continue
            description = (event.get("description") or "").strip()
            events_by_person.setdefault(person_id, []).append(description or "Без описания")

        return people, people_by_ref, families, children_by_family, events_by_person

    @classmethod
    def _relative_links(
        cls,
        references: Iterable[str],
        people_by_ref: Mapping[str, PersonData],
    ) -> tuple[tuple[str, int], ...]:
        links: list[tuple[str, int]] = []
        for reference in references:
            relative = people_by_ref.get(reference)
            if not relative:
                continue
            link = (cls._display_name(relative), int(relative["id"]))
            if link not in links:
                links.append(link)
        return tuple(links)

    @log_operation("Recovery Wizard list incomplete people")
    def list_incomplete_people(self) -> list[RecoveryRecord]:
        """Return every person whose first and last names are both empty."""
        people, people_by_ref, families, children_by_family, events_by_person = self._build_indexes()
        parents_by_child: dict[str, list[str]] = {}
        partners_by_person: dict[str, list[str]] = {}
        children_by_parent: dict[str, list[str]] = {}

        for family in families:
            family_ref = str(family.get("gedcom_id") or family.get("id") or "")
            family_children = children_by_family.get(family_ref, [])
            husband = str(family.get("husband_id") or "")
            wife = str(family.get("wife_id") or "")

            for child_ref in family_children:
                if husband:
                    parents_by_child.setdefault(child_ref, []).append(husband)
                if wife:
                    parents_by_child.setdefault(child_ref, []).append(wife)

            if husband and wife:
                partners_by_person.setdefault(husband, []).append(wife)
                partners_by_person.setdefault(wife, []).append(husband)
            for parent_ref in (husband, wife):
                if parent_ref:
                    children_by_parent.setdefault(parent_ref, []).extend(family_children)

        results: list[RecoveryRecord] = []
        for person in people:
            if (person.get("first_name") or "").strip() or (person.get("last_name") or "").strip():
                continue

            ref = self._reference(person)
            parents = [self._display_name(people_by_ref.get(value)) for value in parents_by_child.get(ref, [])]
            partners = [self._display_name(people_by_ref.get(value)) for value in partners_by_person.get(ref, [])]
            children = [self._display_name(people_by_ref.get(value)) for value in children_by_parent.get(ref, [])]

            results.append(
                RecoveryRecord(
                    person_id=int(person["id"]),
                    gedcom_id=person.get("gedcom_id") or "",
                    first_name=person.get("first_name") or "",
                    last_name=person.get("last_name") or "",
                    sex=person.get("sex") or "",
                    birth_date=person.get("birth_date") or "",
                    birth_place=person.get("birth_place") or "",
                    death_date=person.get("death_date") or "",
                    death_place=person.get("death_place") or "",
                    occupation=person.get("occupation") or "",
                    note=person.get("note") or "",
                    parents=tuple(dict.fromkeys(parents)),
                    partners=tuple(dict.fromkeys(partners)),
                    children=tuple(dict.fromkeys(children)),
                    parent_links=self._relative_links(parents_by_child.get(ref, []), people_by_ref),
                    partner_links=self._relative_links(partners_by_person.get(ref, []), people_by_ref),
                    child_links=self._relative_links(children_by_parent.get(ref, []), people_by_ref),
                    event_count=len(events_by_person.get(int(person["id"]), [])),
                    event_descriptions=tuple(events_by_person.get(int(person["id"]), [])),
                )
            )

        return results

    @staticmethod
    def _match_source_values(source: Mapping[str, Any], criteria: Mapping[str, Any]) -> dict[str, str]:
        return {
            field: str(criteria.get(field, source.get(field)) or "")
            for field in ("first_name", "last_name", "birth_date", "birth_place", "note")
        }

    def _score_match_candidate(
        self,
        source_values: Mapping[str, str],
        source_context: FamilyContext,
        candidate: Mapping[str, Any],
        candidate_context: FamilyContext,
    ) -> int:
        source_year = self._birth_year(source_values["birth_date"])
        candidate_year = self._birth_year(candidate.get("birth_date"))
        score = sum((
            MATCH_WEIGHTS["first_name"] * self._text_similarity(source_values["first_name"], candidate.get("first_name")),
            MATCH_WEIGHTS["last_name"] * self._text_similarity(source_values["last_name"], candidate.get("last_name")),
            MATCH_WEIGHTS["birth_year"] * float(bool(source_year and source_year == candidate_year)),
            MATCH_WEIGHTS["birth_place"] * self._text_similarity(source_values["birth_place"], candidate.get("birth_place")),
            MATCH_WEIGHTS["parents"] * self._set_similarity(source_context.get("parents"), candidate_context.get("parents")),
            MATCH_WEIGHTS["partners"] * self._set_similarity(source_context.get("partners"), candidate_context.get("partners")),
            MATCH_WEIGHTS["children"] * self._set_similarity(source_context.get("children"), candidate_context.get("children")),
            MATCH_WEIGHTS["families"] * self._set_similarity(source_context.get("families"), candidate_context.get("families")),
            MATCH_WEIGHTS["note"] * self._text_similarity(source_values["note"], candidate.get("note")),
        ))
        return min(MAX_CONFIDENCE, round(score))

    @log_operation("Recovery Wizard find matches")
    def find_matches(self, person_id: int, criteria: Mapping[str, Any]) -> list[RecoveryMatchCandidate]:
        """Return read-only match candidates ordered by confidence descending."""
        source = self.repository.get_person_record(person_id)
        if not source:
            raise ValueError("Человек не найден")

        people, _people_by_ref, families, children_by_family, _events = self._build_indexes()
        contexts = self._build_family_contexts(people, families, children_by_family)
        source_context = contexts.get(self._reference(source), {})
        source_values = self._match_source_values(source, criteria)

        matches = []
        for person in people:
            candidate_id = int(person["id"])
            if candidate_id == int(person_id):
                continue
            full_name = f"{person.get('last_name', '')} {person.get('first_name', '')}".strip()
            if not full_name:
                continue
            candidate_context = contexts.get(self._reference(person), {})
            confidence = self._score_match_candidate(source_values, source_context, person, candidate_context)
            matches.append(
                RecoveryMatchCandidate(
                    person_id=candidate_id,
                    gedcom_id=person.get("gedcom_id") or "",
                    full_name=full_name,
                    birth_date=person.get("birth_date") or "",
                    birth_place=person.get("birth_place") or "",
                    confidence=confidence,
                )
            )

        matches.sort(key=lambda candidate: (-candidate.confidence, candidate.full_name, candidate.person_id))
        return matches

    @log_operation("Recovery Wizard update person")
    def update_existing_person(self, person_id: int, data: Mapping[str, Any]) -> bool:
        """Update editable fields on one existing person in a transaction."""
        person = self.repository.get_person_record(person_id)
        if not person:
            raise ValueError("Человек не найден")

        first_name = (data.get("first_name") or "").strip()
        last_name = (data.get("last_name") or "").strip()
        if not first_name or not last_name:
            raise ValueError("Введите имя и фамилию")

        values = {
            "first_name": first_name,
            "last_name": last_name,
            "sex": str(data.get("sex", person.get("sex") or "") or "").strip(),
            "birth_date": str(data.get("birth_date", person.get("birth_date") or "") or "").strip(),
            "birth_place": str(data.get("birth_place", person.get("birth_place") or "") or "").strip(),
            "death_date": str(data.get("death_date", person.get("death_date") or "") or "").strip(),
            "death_place": str(data.get("death_place", person.get("death_place") or "") or "").strip(),
            "occupation": str(data.get("occupation", person.get("occupation") or "") or "").strip(),
            "note": (data.get("note") if data.get("note") is not None else person.get("note") or "").strip(),
        }
        changes = {field: value for field, value in values.items() if value != (person.get(field) or "")}
        with self.repository.transaction():
            return self.repository.update_person_fields(person_id, changes)

    @log_operation("Recovery Wizard restore person")
    def restore_existing_person(self, person_id: int, snapshot: Mapping[str, Any]) -> bool:
        """Restore editable fields for the most recently saved person."""
        if not self.repository.get_person_record(person_id):
            raise ValueError("Человек не найден")
        values = {field: str(snapshot.get(field) or "") for field in EDITABLE_PERSON_FIELDS}
        with self.repository.transaction():
            return self.repository.update_person_fields(person_id, values)
