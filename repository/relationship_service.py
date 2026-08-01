from __future__ import annotations

from typing import Iterable

from repository.person_repository import PersonRepository


class RelationshipService:
    def __init__(self, repository: PersonRepository):
        self.repository = repository

    def create_family(self, husband_gedcom_id="", wife_gedcom_id="", child_gedcom_ids=None):
        child_gedcom_ids = child_gedcom_ids or []
        self._validate_relationships(husband_gedcom_id, wife_gedcom_id, child_gedcom_ids)
        family_gedcom_id = self._next_family_gedcom_id()
        family_id = self.repository.create_family({
            "gedcom_id": family_gedcom_id,
            "husband": husband_gedcom_id,
            "wife": wife_gedcom_id,
            "children": child_gedcom_ids,
        })
        return family_id

    def update_family(self, family_id, husband_gedcom_id="", wife_gedcom_id="", child_gedcom_ids=None):
        child_gedcom_ids = child_gedcom_ids or []
        self._validate_relationships(husband_gedcom_id, wife_gedcom_id, child_gedcom_ids, family_id=family_id)
        return self.repository.update_family(family_id, {
            "husband": husband_gedcom_id,
            "wife": wife_gedcom_id,
            "children": child_gedcom_ids,
        })

    def delete_family(self, family_id):
        return self.repository.delete_family(family_id)

    def list_people(self, query=""):
        if not query:
            return self.repository.list_people()
        return self.repository.list_people(first_name=query, last_name=query, surname=query)

    def list_family_members(self, family_id):
        family = self.repository.get_family(family_id)
        if not family:
            return {"husband": None, "wife": None, "children": []}
        return {
            "husband": family.get("husband"),
            "wife": family.get("wife"),
            "children": family.get("children", []),
        }

    def _validate_relationships(self, husband_gedcom_id, wife_gedcom_id, child_gedcom_ids, family_id=None):
        self._ensure_people_exist(husband_gedcom_id, wife_gedcom_id, child_gedcom_ids)
        if husband_gedcom_id and wife_gedcom_id and husband_gedcom_id == wife_gedcom_id:
            raise ValueError("Spouses must be different people")
        if husband_gedcom_id and husband_gedcom_id in child_gedcom_ids:
            raise ValueError("A person cannot be both parent and child")
        if wife_gedcom_id and wife_gedcom_id in child_gedcom_ids:
            raise ValueError("A person cannot be both parent and child")
        self._ensure_no_circular_family_links(husband_gedcom_id, wife_gedcom_id, child_gedcom_ids, family_id)

    def _ensure_people_exist(self, husband_gedcom_id, wife_gedcom_id, child_gedcom_ids):
        for gedcom_id in [husband_gedcom_id, wife_gedcom_id, *child_gedcom_ids]:
            if not gedcom_id:
                continue
            person = self.repository.get_person_by_gedcom_id(gedcom_id)
            if not person:
                raise ValueError(f"Unknown person: {gedcom_id}")

    def _ensure_no_circular_family_links(self, husband_gedcom_id, wife_gedcom_id, child_gedcom_ids, family_id=None):
        child_ids = {child_id for child_id in child_gedcom_ids if child_id}
        parent_ids = {candidate for candidate in [husband_gedcom_id, wife_gedcom_id] if candidate}

        self.repository.cur.execute(
            """
            SELECT f.id, f.gedcom_id, f.husband_id, f.wife_id, fc.child_id
            FROM families f
            LEFT JOIN family_children fc ON fc.family_id = f.gedcom_id
            """
        )
        rows = self.repository.cur.fetchall()
        existing_children = {
            row[4]
            for row in rows
            if row[4] and (family_id is None or row[0] != family_id)
        }
        existing_parents = {
            value
            for row in rows
            if (family_id is None or row[0] != family_id)
            for value in [row[2], row[3]]
            if value
        }

        if child_ids & existing_children:
            raise ValueError("A person cannot be a child in multiple families")
        if parent_ids & existing_children:
            raise ValueError("A person cannot be both a parent/spouse and a child in another family")
        if child_ids & existing_parents:
            raise ValueError("A person cannot be both a parent/spouse and a child in another family")

    def _next_family_gedcom_id(self):
        families = self.repository.cur.execute("SELECT id FROM families ORDER BY id").fetchall()
        return f"F{len(families) + 1}"
