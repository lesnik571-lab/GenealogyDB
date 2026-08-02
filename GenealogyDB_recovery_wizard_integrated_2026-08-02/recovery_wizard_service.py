from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class RecoveryRecord:
    person_id: int
    gedcom_id: str
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
    event_count: int


class RecoveryWizardService:
    """Read-only family context plus safe updates for incomplete people.

    The service never creates, deletes, merges, or relinks people. It only
    updates fields on an existing person record selected by database ID.
    """

    def __init__(self, repository):
        self.repository = repository

    @staticmethod
    def _reference(person: dict) -> str:
        return (person.get("gedcom_id") or str(person.get("id") or "")).strip()

    @staticmethod
    def _display_name(person: dict | None) -> str:
        if not person:
            return "Неизвестный человек"
        name = f"{person.get('last_name', '')} {person.get('first_name', '')}".strip()
        if name:
            return name
        ref = person.get("gedcom_id") or person.get("id") or "?"
        return f"Без имени ({ref})"

    def _build_indexes(self):
        people = self.repository.list_people_full()
        people_by_ref: dict[str, dict] = {}
        for person in people:
            for ref in {str(person.get("id") or "").strip(), (person.get("gedcom_id") or "").strip()}:
                if ref:
                    people_by_ref[ref] = person

        families = self.repository.list_families_raw()
        children_by_family: dict[str, list[str]] = {}
        for row in self.repository.list_family_children_raw():
            children_by_family.setdefault(str(row.get("family_id") or ""), []).append(str(row.get("child_id") or ""))

        event_counts: dict[int, int] = {}
        for event in self.repository.list_all_person_events():
            person_id = event.get("person_id")
            try:
                person_id = int(person_id)
            except (TypeError, ValueError):
                continue
            event_counts[person_id] = event_counts.get(person_id, 0) + 1

        return people, people_by_ref, families, children_by_family, event_counts

    def list_incomplete_people(self) -> list[RecoveryRecord]:
        people, people_by_ref, families, children_by_family, event_counts = self._build_indexes()
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
                    event_count=event_counts.get(int(person["id"]), 0),
                )
            )

        return results

    def update_existing_person(self, person_id: int, data: dict) -> bool:
        person = self.repository.get_person_record(person_id)
        if not person:
            raise ValueError("Человек не найден")

        first_name = (data.get("first_name") or "").strip()
        last_name = (data.get("last_name") or "").strip()
        if not first_name or not last_name:
            raise ValueError("Введите имя и фамилию")

        payload = {
            "first_name": first_name,
            "last_name": last_name,
            "sex": (data.get("sex") or person.get("sex") or "").strip(),
            "birth_date": (data.get("birth_date") or person.get("birth_date") or "").strip(),
            "birth_place": (data.get("birth_place") or person.get("birth_place") or "").strip(),
            "death_date": (data.get("death_date") or person.get("death_date") or "").strip(),
            "death_place": (data.get("death_place") or person.get("death_place") or "").strip(),
            "occupation": (data.get("occupation") or person.get("occupation") or "").strip(),
            "note": (data.get("note") if data.get("note") is not None else person.get("note") or "").strip(),
        }
        with self.repository.transaction():
            return self.repository.update_person(person_id, payload)
