from __future__ import annotations

from typing import Iterable

from repository.person_repository import PersonRepository
from logging_service import log_operation


class RelationshipService:
    """Validate and mutate family relationships."""
    RELATIONSHIP_TYPES = ("marriage", "former_spouse", "civil_partner", "unknown")
    PARENT_ROLE_TO_FIELD = {"father": "husband", "mother": "wife"}

    def __init__(self, repository: PersonRepository):
        self.repository = repository

    @log_operation("Relationship create family")
    def create_family(self, husband_gedcom_id="", wife_gedcom_id="", child_gedcom_ids=None, relationship_type="unknown"):
        child_gedcom_ids = child_gedcom_ids or []
        husband_ref = self._normalize_person_reference(husband_gedcom_id)
        wife_ref = self._normalize_person_reference(wife_gedcom_id)
        child_refs = [self._normalize_person_reference(value) for value in child_gedcom_ids if str(value).strip()]
        relationship_type = self._normalize_relationship_type(relationship_type)

        self._validate_relationships(husband_ref, wife_ref, child_refs, relationship_type)
        existing_family = self._find_compatible_parent_family(husband_ref, wife_ref)
        if existing_family:
            merged_children = self._merge_children(existing_family.get("children", []), child_refs)
            if merged_children == existing_family.get("children", []) and existing_family.get("relationship_type") == relationship_type:
                raise ValueError("Duplicate family link")
            self.repository.update_family(existing_family["id"], {
                "husband": husband_ref,
                "wife": wife_ref,
                "children": merged_children,
                "relationship_type": self._resolve_family_relationship_type(existing_family, relationship_type),
            })
            return existing_family["id"]

        family_gedcom_id = self._next_family_gedcom_id()
        family_id = self.repository.create_family({
            "gedcom_id": family_gedcom_id,
            "husband": husband_ref,
            "wife": wife_ref,
            "children": child_refs,
            "relationship_type": relationship_type,
        })
        return family_id

    @log_operation("Relationship update family")
    def update_family(self, family_id, husband_gedcom_id="", wife_gedcom_id="", child_gedcom_ids=None, relationship_type="unknown"):
        child_gedcom_ids = child_gedcom_ids or []
        husband_ref = self._normalize_person_reference(husband_gedcom_id)
        wife_ref = self._normalize_person_reference(wife_gedcom_id)
        child_refs = [self._normalize_person_reference(value) for value in child_gedcom_ids if str(value).strip()]
        relationship_type = self._normalize_relationship_type(relationship_type)

        self._validate_relationships(husband_ref, wife_ref, child_refs, relationship_type, family_id=family_id)
        return self.repository.update_family(family_id, {
            "husband": husband_ref,
            "wife": wife_ref,
            "children": child_refs,
            "relationship_type": relationship_type,
        })

    @log_operation("Relationship delete family")
    def delete_family(self, family_id):
        with self.repository.transaction():
            return self.repository.delete_family(family_id)

    def list_people(self, query="", exclude_person_reference=None):
        return self.repository.search_people_for_picker(query=query, exclude_reference=exclude_person_reference)

    def list_family_members(self, family_id):
        family = self.repository.get_family(family_id)
        if not family:
            return {"husband": None, "wife": None, "children": [], "relationship_type": "unknown"}
        return {
            "husband": family.get("husband"),
            "wife": family.get("wife"),
            "children": family.get("children", []),
            "relationship_type": family.get("relationship_type", "unknown"),
        }

    def get_relationship_editor_state(self, person_reference):
        current_person = self._require_person_record(person_reference)
        families = self.repository.list_person_families(current_person["reference"])
        current_refs = self.repository._expand_person_references(current_person["reference"])
        parent_links = []
        partner_links = []
        child_links = []

        for family in families:
            child_refs = family.get("children", [])
            family_child_refs = set()
            for child_ref in child_refs:
                family_child_refs.update(self.repository._expand_person_references(child_ref))

            if current_refs & family_child_refs:
                if family.get("husband"):
                    parent_links.append(self._build_relative_link(family, "father", family.get("husband")))
                if family.get("wife"):
                    parent_links.append(self._build_relative_link(family, "mother", family.get("wife")))

            current_is_husband = self._refs_match(current_person["reference"], family.get("husband"))
            current_is_wife = self._refs_match(current_person["reference"], family.get("wife"))
            if current_is_husband or current_is_wife:
                partner_ref = family.get("wife") if current_is_husband else family.get("husband")
                if partner_ref:
                    partner_links.append(self._build_relative_link(family, "partner", partner_ref))
                for child_ref in family.get("children", []):
                    child_links.append({
                        **self._build_relative_link(family, "child", child_ref),
                        "other_parent": self._person_record_or_none(
                            family.get("wife") if current_is_husband else family.get("husband")
                        ),
                    })

        return {
            "person": current_person,
            "parents": self._deduplicate_links(parent_links),
            "partners": self._deduplicate_links(partner_links),
            "children": self._deduplicate_links(child_links),
            "relationship_types": list(self.RELATIONSHIP_TYPES),
        }

    @log_operation("Relationship link parent")
    def link_parent(self, child_reference, parent_reference, parent_role):
        if parent_role not in self.PARENT_ROLE_TO_FIELD:
            raise ValueError("Unsupported parent role")
        child = self._require_person_record(child_reference)
        parent = self._require_person_record(parent_reference)
        if child["reference"] == parent["reference"]:
            raise ValueError("A person cannot be their own parent")
        if self._would_create_ancestry_cycle(parent["reference"], child["reference"]):
            raise ValueError("Direct ancestry cycle detected")

        field_name = self.PARENT_ROLE_TO_FIELD[parent_role]
        families = self._families_for_child(child["reference"])
        with self.repository.transaction():
            target_family = self._pick_family_for_parent_link(families, field_name, parent["reference"])
            if target_family is None:
                family_id = self.repository.create_family({
                    "gedcom_id": self._next_family_gedcom_id(),
                    "husband": parent["reference"] if field_name == "husband" else "",
                    "wife": parent["reference"] if field_name == "wife" else "",
                    "children": [child["reference"]],
                    "relationship_type": "unknown",
                })
                return self.repository.get_family(family_id)

            existing_value = target_family.get(field_name) or ""
            if existing_value and self._refs_match(existing_value, parent["reference"]):
                raise ValueError("Duplicate family link")
            if existing_value and not self._refs_match(existing_value, parent["reference"]):
                raise ValueError("Remove the existing parent link before adding another")

            payload = {
                "husband": target_family.get("husband", ""),
                "wife": target_family.get("wife", ""),
                "children": target_family.get("children", []),
                "relationship_type": target_family.get("relationship_type", "unknown"),
            }
            payload[field_name] = parent["reference"]
            self.repository.update_family(target_family["id"], payload)
            return self.repository.get_family(target_family["id"])

    @log_operation("Relationship create and link parent")
    def create_parent_and_link(self, child_reference, parent_role, person_data):
        with self.repository.transaction():
            parent_id = self.repository.create_person(self._person_payload(person_data))
            return self.link_parent(child_reference, str(parent_id), parent_role)

    @log_operation("Relationship remove parent")
    def remove_parent_link(self, child_reference, family_id, parent_role):
        if parent_role not in self.PARENT_ROLE_TO_FIELD:
            raise ValueError("Unsupported parent role")
        child = self._require_person_record(child_reference)
        family = self._require_child_family_member(family_id, child["reference"])
        field_name = self.PARENT_ROLE_TO_FIELD[parent_role]
        if not family.get(field_name):
            raise ValueError("Parent link not found")
        with self.repository.transaction():
            payload = {
                "husband": family.get("husband", ""),
                "wife": family.get("wife", ""),
                "children": family.get("children", []),
                "relationship_type": family.get("relationship_type", "unknown"),
            }
            payload[field_name] = ""
            self.repository.update_family(family_id, payload)
            return self.repository.get_family(family_id)

    @log_operation("Relationship link partner")
    def link_partner(self, person_reference, partner_reference, relationship_type="unknown"):
        person = self._require_person_record(person_reference)
        partner = self._require_person_record(partner_reference)
        if person["reference"] == partner["reference"]:
            raise ValueError("A person cannot be their own spouse")
        relationship_type = self._normalize_relationship_type(relationship_type)
        husband_ref, wife_ref = self._resolve_partner_slots(person, partner)

        with self.repository.transaction():
            existing_family = self._find_compatible_parent_family(husband_ref, wife_ref)
            if existing_family:
                if existing_family.get("relationship_type") == relationship_type:
                    raise ValueError("Duplicate family link")
                self.repository.update_family(existing_family["id"], {
                    "husband": husband_ref,
                    "wife": wife_ref,
                    "children": existing_family.get("children", []),
                    "relationship_type": relationship_type,
                })
                return self.repository.get_family(existing_family["id"])

            family_id = self.repository.create_family({
                "gedcom_id": self._next_family_gedcom_id(),
                "husband": husband_ref,
                "wife": wife_ref,
                "children": [],
                "relationship_type": relationship_type,
            })
            return self.repository.get_family(family_id)

    @log_operation("Relationship create and link partner")
    def create_partner_and_link(self, person_reference, person_data, relationship_type="unknown"):
        with self.repository.transaction():
            partner_id = self.repository.create_person(self._person_payload(person_data))
            return self.link_partner(person_reference, str(partner_id), relationship_type=relationship_type)

    @log_operation("Relationship remove partner")
    def remove_partner_link(self, person_reference, family_id):
        person = self._require_person_record(person_reference)
        family = self.repository.get_family(family_id)
        if not family:
            raise ValueError("Family not found")
        current_is_husband = self._refs_match(person["reference"], family.get("husband"))
        current_is_wife = self._refs_match(person["reference"], family.get("wife"))
        if not current_is_husband and not current_is_wife:
            raise ValueError("Partner link not found")
        if not family.get("children"):
            with self.repository.transaction():
                self.repository.delete_family(family_id)
                return None

        with self.repository.transaction():
            if current_is_husband:
                self.repository.update_family(family_id, {
                    "husband": person["reference"],
                    "wife": "",
                    "children": family.get("children", []),
                    "relationship_type": "unknown",
                })
            else:
                self.repository.update_family(family_id, {
                    "husband": "",
                    "wife": person["reference"],
                    "children": family.get("children", []),
                    "relationship_type": "unknown",
                })
            return self.repository.get_family(family_id)

    @log_operation("Relationship link child")
    def link_child(self, parent_reference, child_reference, other_parent_reference="", relationship_type="unknown"):
        parent = self._require_person_record(parent_reference)
        child = self._require_person_record(child_reference)
        other_parent = self._person_record_or_none(other_parent_reference)
        if parent["reference"] == child["reference"]:
            raise ValueError("A person cannot be their own child")
        if other_parent and other_parent["reference"] == child["reference"]:
            raise ValueError("A person cannot be their own child")
        if other_parent and other_parent["reference"] == parent["reference"]:
            raise ValueError("A person cannot be their own spouse")
        if self._would_create_ancestry_cycle(parent["reference"], child["reference"]):
            raise ValueError("Direct ancestry cycle detected")
        if other_parent and self._would_create_ancestry_cycle(other_parent["reference"], child["reference"]):
            raise ValueError("Direct ancestry cycle detected")

        relationship_type = self._normalize_relationship_type(relationship_type)
        husband_ref, wife_ref = self._resolve_partner_slots(parent, other_parent)
        child_families = self._families_for_child(child["reference"])
        target_family = self._find_compatible_child_family(child_families, husband_ref, wife_ref)

        with self.repository.transaction():
            if target_family:
                if child["reference"] in target_family.get("children", []):
                    raise ValueError("Duplicate family link")
                merged_children = self._merge_children(target_family.get("children", []), [child["reference"]])
                self.repository.update_family(target_family["id"], {
                    "husband": husband_ref,
                    "wife": wife_ref,
                    "children": merged_children,
                    "relationship_type": self._resolve_family_relationship_type(target_family, relationship_type),
                })
                return self.repository.get_family(target_family["id"])

            if child_families:
                exact_child_family = self._find_exact_child_family(child_families, child["reference"])
                if exact_child_family:
                    updated_family = self._apply_child_family_parent_update(exact_child_family, husband_ref, wife_ref, relationship_type)
                    return updated_family
                raise ValueError("Child is already linked to a different family")

            reusable_family = self._find_compatible_parent_family(husband_ref, wife_ref)
            if reusable_family:
                merged_children = self._merge_children(reusable_family.get("children", []), [child["reference"]])
                if merged_children == reusable_family.get("children", []):
                    raise ValueError("Duplicate family link")
                self.repository.update_family(reusable_family["id"], {
                    "husband": husband_ref,
                    "wife": wife_ref,
                    "children": merged_children,
                    "relationship_type": self._resolve_family_relationship_type(reusable_family, relationship_type),
                })
                return self.repository.get_family(reusable_family["id"])

            family_id = self.repository.create_family({
                "gedcom_id": self._next_family_gedcom_id(),
                "husband": husband_ref,
                "wife": wife_ref,
                "children": [child["reference"]],
                "relationship_type": relationship_type,
            })
            return self.repository.get_family(family_id)

    @log_operation("Relationship create and link child")
    def create_child_and_link(self, parent_reference, person_data, other_parent_reference="", relationship_type="unknown"):
        with self.repository.transaction():
            child_id = self.repository.create_person(self._person_payload(person_data))
            return self.link_child(parent_reference, str(child_id), other_parent_reference, relationship_type=relationship_type)

    @log_operation("Relationship remove child")
    def remove_child_link(self, family_id, child_reference):
        child = self._require_person_record(child_reference)
        family = self._require_child_family_member(family_id, child["reference"])
        remaining_children = [value for value in family.get("children", []) if not self._refs_match(value, child["reference"])]
        with self.repository.transaction():
            if not remaining_children and not family.get("husband") and not family.get("wife"):
                self.repository.delete_family(family_id)
                return None
            self.repository.update_family(family_id, {
                "husband": family.get("husband", ""),
                "wife": family.get("wife", ""),
                "children": remaining_children,
                "relationship_type": family.get("relationship_type", "unknown"),
            })
            return self.repository.get_family(family_id)

    def _validate_relationships(self, husband_gedcom_id, wife_gedcom_id, child_gedcom_ids, relationship_type, family_id=None):
        self._ensure_people_exist(husband_gedcom_id, wife_gedcom_id, child_gedcom_ids)
        self._normalize_relationship_type(relationship_type)
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
            person_id = self.repository.resolve_person_reference(gedcom_id)
            if person_id is None:
                raise ValueError(f"Unknown person: {gedcom_id}")

    def _normalize_person_reference(self, value):
        reference = str(value or "").strip()
        if not reference:
            return ""
        person = self._require_person_record(reference)
        return person["reference"]

    def _normalize_relationship_type(self, relationship_type):
        normalized = str(relationship_type or "unknown").strip() or "unknown"
        if normalized not in self.RELATIONSHIP_TYPES:
            raise ValueError("Unsupported relationship type")
        return normalized

    def _ensure_no_circular_family_links(self, husband_gedcom_id, wife_gedcom_id, child_gedcom_ids, family_id=None):
        parent_ids = [value for value in [husband_gedcom_id, wife_gedcom_id] if value]
        rows = []
        for family in self.repository.list_families_raw():
            full_family = self.repository.get_family(family["id"])
            rows.append(full_family)

        existing_children = {
            child_ref
            for family in rows
            if family and (family_id is None or family["id"] != family_id)
            for child_ref in family.get("children", [])
            if child_ref
        }
        existing_parents = {
            parent_ref
            for family in rows
            if family and (family_id is None or family["id"] != family_id)
            for parent_ref in [family.get("husband"), family.get("wife")]
            if parent_ref
        }

        if any(child_id in existing_children for child_id in child_gedcom_ids if child_id):
            raise ValueError("A person cannot be a child in multiple families")
        if any(parent_id in existing_children for parent_id in parent_ids):
            raise ValueError("A person cannot be both a parent/spouse and a child in another family")
        if any(child_id in existing_parents for child_id in child_gedcom_ids if child_id):
            raise ValueError("A person cannot be both a parent/spouse and a child in another family")

        for parent_id in parent_ids:
            for child_id in child_gedcom_ids:
                if self._would_create_ancestry_cycle(parent_id, child_id, family_id=family_id):
                    raise ValueError("Direct ancestry cycle detected")

    def _find_compatible_parent_family(self, husband_ref, wife_ref):
        for family in self.repository.list_families_raw():
            if self._refs_match(family.get("husband_id"), husband_ref) and self._refs_match(family.get("wife_id"), wife_ref):
                return self.repository.get_family(family["id"])
        return None

    def _families_for_child(self, child_reference):
        child = self._require_person_record(child_reference)
        families = []
        for family in self.repository.list_person_families(child["reference"]):
            if any(self._refs_match(child["reference"], child_ref) for child_ref in family.get("children", [])):
                families.append(family)
        return families

    def _find_compatible_child_family(self, families, husband_ref, wife_ref):
        for family in families:
            if not self._family_matches_parent_slots(family, husband_ref, wife_ref):
                continue
            return family
        return None

    def _find_exact_child_family(self, families, child_reference):
        for family in families:
            if any(self._refs_match(child_reference, child_ref) for child_ref in family.get("children", [])):
                return family
        return None

    def _apply_child_family_parent_update(self, family, husband_ref, wife_ref, relationship_type):
        existing_husband = family.get("husband") or ""
        existing_wife = family.get("wife") or ""
        if existing_husband and husband_ref and not self._refs_match(existing_husband, husband_ref):
            raise ValueError("Child is already linked to another father")
        if existing_wife and wife_ref and not self._refs_match(existing_wife, wife_ref):
            raise ValueError("Child is already linked to another mother")
        if husband_ref == "" and existing_husband and wife_ref == "":
            raise ValueError("Child is already linked to a different family")
        self.repository.update_family(family["id"], {
            "husband": husband_ref or existing_husband,
            "wife": wife_ref or existing_wife,
            "children": family.get("children", []),
            "relationship_type": self._resolve_family_relationship_type(family, relationship_type),
        })
        return self.repository.get_family(family["id"])

    def _family_matches_parent_slots(self, family, husband_ref, wife_ref):
        existing_husband = family.get("husband") or ""
        existing_wife = family.get("wife") or ""
        return self._refs_match(existing_husband, husband_ref) and self._refs_match(existing_wife, wife_ref)

    def _pick_family_for_parent_link(self, families, field_name, parent_reference):
        for family in families:
            existing_value = family.get(field_name) or ""
            if not existing_value or self._refs_match(existing_value, parent_reference):
                return family
        return None

    def _require_child_family_member(self, family_id, child_reference):
        family = self.repository.get_family(family_id)
        if not family:
            raise ValueError("Family not found")
        if not any(self._refs_match(child_reference, value) for value in family.get("children", [])):
            raise ValueError("Child link not found")
        return family

    def _would_create_ancestry_cycle(self, parent_reference, child_reference, family_id=None):
        parent = self._require_person_record(parent_reference)
        child = self._require_person_record(child_reference)
        target_descendant = parent["reference"]
        stack = [child["reference"]]
        visited = set()
        adjacency = {}
        for family in self.repository.list_families_raw():
            if family_id is not None and family["id"] == family_id:
                continue
            full_family = self.repository.get_family(family["id"])
            for parent_ref in [full_family.get("husband"), full_family.get("wife")]:
                if not parent_ref:
                    continue
                parent_record = self._person_record_or_none(parent_ref)
                if not parent_record:
                    continue
                adjacency.setdefault(parent_record["reference"], set())
                for family_child_ref in full_family.get("children", []):
                    child_record = self._person_record_or_none(family_child_ref)
                    if child_record:
                        adjacency[parent_record["reference"]].add(child_record["reference"])

        while stack:
            current = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            for next_ref in adjacency.get(current, set()):
                if next_ref == target_descendant:
                    return True
                stack.append(next_ref)
        return False

    def _person_payload(self, person_data):
        return {
            "gedcom_id": (person_data or {}).get("gedcom_id") or None,
            "first_name": (person_data or {}).get("first_name") or "",
            "last_name": (person_data or {}).get("last_name") or "",
            "sex": (person_data or {}).get("sex") or "",
            "birth_date": (person_data or {}).get("birth_date") or "",
            "birth_place": (person_data or {}).get("birth_place") or "",
            "death_date": (person_data or {}).get("death_date") or "",
            "death_place": (person_data or {}).get("death_place") or "",
            "occupation": (person_data or {}).get("occupation") or "",
            "note": (person_data or {}).get("note") or "",
        }

    def _build_relative_link(self, family, link_type, person_reference):
        person = self._person_record_or_none(person_reference)
        return {
            "family_id": family["id"],
            "family_gedcom_id": family.get("gedcom_id", ""),
            "relationship_type": family.get("relationship_type", "unknown"),
            "link_type": link_type,
            "person": person,
            "person_reference": person["reference"] if person else "",
            "child_count": len(family.get("children", [])),
        }

    def _person_record_or_none(self, person_reference):
        if not str(person_reference or "").strip():
            return None
        return self.repository.get_person_record_by_reference(person_reference)

    def _require_person_record(self, person_reference):
        person = self._person_record_or_none(person_reference)
        if not person:
            raise ValueError(f"Unknown person: {person_reference}")
        return person

    def _refs_match(self, left_reference, right_reference):
        left = self.repository._expand_person_references(left_reference)
        right = self.repository._expand_person_references(right_reference)
        if not left and not right:
            return True
        if not left or not right:
            return False
        return bool(left & right)

    def _resolve_partner_slots(self, person, partner=None):
        if partner is None:
            return person["reference"], ""
        person_sex = (person.get("sex") or "").upper()
        partner_sex = (partner.get("sex") or "").upper()
        if person_sex == "F" and partner_sex != "F":
            return partner["reference"], person["reference"]
        if person_sex == "M" and partner_sex != "M":
            return person["reference"], partner["reference"]
        if partner_sex == "M" and person_sex != "M":
            return partner["reference"], person["reference"]
        return person["reference"], partner["reference"]

    def _resolve_family_relationship_type(self, family, requested_type):
        current_type = family.get("relationship_type") or "unknown"
        if current_type in ("", "unknown"):
            return requested_type
        return current_type if requested_type == "unknown" else requested_type

    def _next_family_gedcom_id(self):
        families = self.repository.cur.execute("SELECT id FROM families ORDER BY id").fetchall()
        return f"F{len(families) + 1}"

    @staticmethod
    def _merge_children(existing_children: Iterable[str], new_children: Iterable[str]):
        merged = []
        seen = set()
        for child_ref in [*(existing_children or []), *(new_children or [])]:
            normalized = str(child_ref or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            merged.append(normalized)
        return merged

    @staticmethod
    def _deduplicate_links(items):
        unique = []
        seen = set()
        for item in items:
            person = item.get("person") or {}
            key = (item.get("family_id"), item.get("link_type"), person.get("reference"))
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return unique
