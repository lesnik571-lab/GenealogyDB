from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from repository.relationship_service import RelationshipService


@dataclass(frozen=True)
class FamilyTreePerson:
    """Display-ready person data used by the family tree view."""
    database_id: int
    gedcom_id: str
    full_name: str
    birth_date: str
    death_date: str
    is_unnamed: bool


@dataclass(frozen=True)
class FamilyTreeModel:
    """A centered family tree model with relatives by role."""
    center: FamilyTreePerson
    parents: tuple[FamilyTreePerson, ...]
    partners: tuple[FamilyTreePerson, ...]
    children: tuple[FamilyTreePerson, ...]


class FamilyTreeViewService:
    """Build a read-only immediate-family model for the Viewer."""

    def __init__(self, relationship_service: RelationshipService) -> None:
        self.relationship_service = relationship_service

    @staticmethod
    def _person_card(person: Mapping[str, Any]) -> FamilyTreePerson:
        full_name = " ".join(
            value.strip()
            for value in (str(person.get("first_name") or ""), str(person.get("last_name") or ""))
            if value.strip()
        )
        return FamilyTreePerson(
            database_id=int(person["id"]),
            gedcom_id=str(person.get("gedcom_id") or ""),
            full_name=full_name or "Без имени",
            birth_date=str(person.get("birth_date") or ""),
            death_date=str(person.get("death_date") or ""),
            is_unnamed=not full_name,
        )

    @classmethod
    def _relative_cards(cls, links: Iterable[Mapping[str, Any]]) -> tuple[FamilyTreePerson, ...]:
        cards: list[FamilyTreePerson] = []
        seen_ids: set[int] = set()
        for link in links:
            person = link.get("person")
            if not person:
                continue
            card = cls._person_card(person)
            if card.database_id in seen_ids:
                continue
            seen_ids.add(card.database_id)
            cards.append(card)
        return tuple(cards)

    def build_tree(self, person_reference: object) -> FamilyTreeModel:
        """Return the selected person and only their immediate relatives."""
        state = self.relationship_service.get_relationship_editor_state(person_reference)
        return FamilyTreeModel(
            center=self._person_card(state["person"]),
            parents=self._relative_cards(state.get("parents", ())),
            partners=self._relative_cards(state.get("partners", ())),
            children=self._relative_cards(state.get("children", ())),
        )

    def build_card_presentation(self, model: FamilyTreeModel) -> dict[int, dict[str, str]]:
        """Return UI-only sex and relationship labels for cards in a tree model."""
        state = self.relationship_service.get_relationship_editor_state(model.center.database_id)
        parent_roles = self._link_metadata(state.get("parents", ()), "link_type")
        partner_types = self._link_metadata(state.get("partners", ()), "relationship_type")
        presentation = {
            model.center.database_id: {
                "sex": self._person_sex(model.center.database_id),
                "relationship": "Current person",
            }
        }
        for group, people in (
            ("parent", model.parents),
            ("partner", model.partners),
            ("child", model.children),
        ):
            for person in people:
                sex = self._person_sex(person.database_id)
                presentation[person.database_id] = {
                    "sex": sex,
                    "relationship": self._relationship_label(
                        group,
                        sex,
                        parent_role=parent_roles.get(person.database_id, ""),
                        relationship_type=partner_types.get(person.database_id, ""),
                    ),
                }
        return presentation

    @staticmethod
    def _link_metadata(links: Iterable[Mapping[str, Any]], field: str) -> dict[int, str]:
        return {
            int(link["person"]["id"]): str(link.get(field) or "")
            for link in links
            if link.get("person")
        }

    def _person_sex(self, person_id: int) -> str:
        person = self.relationship_service.repository.get_person_record(person_id)
        return str((person or {}).get("sex") or "").upper()

    @staticmethod
    def _relationship_label(
        group: str,
        sex: str,
        parent_role: str = "",
        relationship_type: str = "",
    ) -> str:
        if parent_role == "father":
            return "Father"
        if parent_role == "mother":
            return "Mother"
        if group == "partner" and relationship_type not in {"marriage", "former_spouse"}:
            return "Partner"
        labels = {
            ("parent", "M"): "Father",
            ("parent", "F"): "Mother",
            ("partner", "M"): "Husband",
            ("partner", "F"): "Wife",
            ("child", "M"): "Son",
            ("child", "F"): "Daughter",
        }
        defaults = {"parent": "Parent", "partner": "Partner", "child": "Child"}
        return labels.get((group, sex), defaults[group])