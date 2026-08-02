from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from repository.person_repository import PersonRepository


@dataclass(frozen=True)
class RelationshipPathPerson:
    """A person participating in a relationship path."""
    database_id: int
    gedcom_id: str
    full_name: str
    sex: str


@dataclass(frozen=True)
class RelationshipPathStep:
    """One directed relationship edge in a path."""
    source: RelationshipPathPerson
    target: RelationshipPathPerson
    relationship_type: str


@dataclass(frozen=True)
class RelationshipPath:
    """The shortest known relationship path between two people."""
    people: tuple[RelationshipPathPerson, ...]
    steps: tuple[RelationshipPathStep, ...]
    distance: int
    generations: int
    description: str


class RelationshipPathService:
    """Build and inspect a read-only graph of family relationships."""

    EDGE_PRIORITY = {"parent": 0, "child": 1, "spouse": 2, "sibling": 3}

    def __init__(self, repository: PersonRepository) -> None:
        self.repository = repository

    def find_shortest_path(self, source_reference: object, target_reference: object) -> RelationshipPath | None:
        """Return the shortest typed path between two people, or None."""
        people = self._people_by_id()
        source_id = self._resolve_person_id(source_reference)
        target_id = self._resolve_person_id(target_reference)
        if source_id not in people or target_id not in people:
            raise ValueError("Человек не найден")
        graph = self._build_graph(set(people))
        route = self._breadth_first_search(graph, source_id, target_id)
        if route is None:
            return None
        path_people = tuple(self._person(people[person_id]) for person_id, _edge in route)
        steps = tuple(
            RelationshipPathStep(path_people[index - 1], path_people[index], route[index][1])
            for index in range(1, len(route))
        )
        relationship_types = tuple(step.relationship_type for step in steps)
        return RelationshipPath(
            people=path_people,
            steps=steps,
            distance=len(steps),
            generations=sum(edge in {"parent", "child"} for edge in relationship_types),
            description=self._describe_relationship(relationship_types, path_people[-1]),
        )

    def format_path_text(self, path: RelationshipPath) -> str:
        """Format a relationship path for plain-text export."""
        lines = [
            f"Relationship: {path.description}",
            f"Distance: {path.distance}",
            f"Generations: {path.generations}",
            "",
            "Path:",
            self._format_person(path.people[0]),
        ]
        for step in path.steps:
            lines.append(f"  --{step.relationship_type}--> {self._format_person(step.target)}")
        return "\n".join(lines) + "\n"

    def export_path_text(self, path: RelationshipPath, destination: str | Path) -> Path:
        """Write a relationship path to a UTF-8 text file."""
        output = Path(destination)
        output.write_text(self.format_path_text(path), encoding="utf-8")
        return output

    def _people_by_id(self) -> dict[int, dict[str, Any]]:
        return {int(person["id"]): person for person in self.repository.list_people_full()}

    def _resolve_person_id(self, reference: object) -> int:
        person_id = self.repository.resolve_person_reference(reference)
        if person_id is None:
            raise ValueError("Человек не найден")
        return int(person_id)

    def _build_graph(self, valid_ids: set[int]) -> dict[int, set[tuple[int, str]]]:
        graph: dict[int, set[tuple[int, str]]] = {person_id: set() for person_id in valid_ids}
        children_by_family: dict[str, set[int]] = {}
        for child_link in self.repository.list_family_children_raw():
            child_id = self._resolved_id_or_none(child_link.get("child_id"))
            if child_id in valid_ids:
                children_by_family.setdefault(str(child_link.get("family_id") or ""), set()).add(child_id)

        for family in self.repository.list_families_raw():
            family_refs = {str(family.get("id") or ""), str(family.get("gedcom_id") or "")}
            children = set().union(*(children_by_family.get(reference, set()) for reference in family_refs))
            parents = [
                person_id
                for person_id in (
                    self._resolved_id_or_none(family.get("husband_id")),
                    self._resolved_id_or_none(family.get("wife_id")),
                )
                if person_id in valid_ids
            ]
            if len(parents) == 2:
                self._add_pair(graph, parents[0], parents[1], "spouse", "spouse")
            for parent_id in parents:
                for child_id in children:
                    self._add_pair(graph, parent_id, child_id, "parent", "child")
            for child_id in children:
                for sibling_id in children:
                    if child_id < sibling_id:
                        self._add_pair(graph, child_id, sibling_id, "sibling", "sibling")
        return graph

    def _resolved_id_or_none(self, reference: object) -> int | None:
        if not str(reference or "").strip():
            return None
        person_id = self.repository.resolve_person_reference(reference)
        return int(person_id) if person_id is not None else None

    @staticmethod
    def _add_pair(
        graph: dict[int, set[tuple[int, str]]],
        left_id: int,
        right_id: int,
        right_to_left: str,
        left_to_right: str,
    ) -> None:
        graph[left_id].add((right_id, left_to_right))
        graph[right_id].add((left_id, right_to_left))

    def _breadth_first_search(
        self,
        graph: Mapping[int, set[tuple[int, str]]],
        source_id: int,
        target_id: int,
    ) -> list[tuple[int, str]] | None:
        queue = deque([source_id])
        previous: dict[int, tuple[int, str] | None] = {source_id: None}
        while queue:
            current = queue.popleft()
            if current == target_id:
                break
            neighbors = sorted(
                graph.get(current, ()),
                key=lambda item: (self.EDGE_PRIORITY[item[1]], item[0]),
            )
            for next_id, edge in neighbors:
                if next_id in previous:
                    continue
                previous[next_id] = (current, edge)
                queue.append(next_id)
        if target_id not in previous:
            return None
        route: list[tuple[int, str]] = []
        current_id = target_id
        while current_id != source_id:
            previous_id, edge = previous[current_id]  # type: ignore[misc]
            route.append((current_id, edge))
            current_id = previous_id
        route.append((source_id, ""))
        route.reverse()
        return route

    @staticmethod
    def _person(person: Mapping[str, Any]) -> RelationshipPathPerson:
        full_name = " ".join(
            value for value in (str(person.get("first_name") or "").strip(), str(person.get("last_name") or "").strip())
            if value
        ) or "Без имени"
        return RelationshipPathPerson(
            database_id=int(person["id"]),
            gedcom_id=str(person.get("gedcom_id") or ""),
            full_name=full_name,
            sex=str(person.get("sex") or "").upper(),
        )

    @classmethod
    def _describe_relationship(
        cls,
        relationship_types: tuple[str, ...],
        target: RelationshipPathPerson,
    ) -> str:
        if not relationship_types:
            return "Same person"
        if len(relationship_types) == 1:
            return cls._direct_relationship(relationship_types[0], target.sex)
        if set(relationship_types) == {"parent"}:
            return cls._ancestor_description(len(relationship_types), target.sex)
        if set(relationship_types) == {"child"}:
            return cls._descendant_description(len(relationship_types), target.sex)
        if relationship_types == ("spouse", "parent"):
            return {"M": "Father-in-law", "F": "Mother-in-law"}.get(target.sex, "Parent-in-law")
        if "sibling" in relationship_types:
            sibling_index = relationship_types.index("sibling")
            before = relationship_types[:sibling_index]
            after = relationship_types[sibling_index + 1:]
            if before and after and set(before) == {"parent"} and set(after) == {"child"}:
                degree = min(len(before), len(after))
                removal = abs(len(before) - len(after))
                description = f"{cls._ordinal(degree)} cousin"
                if removal:
                    description += f" {removal} time{'s' if removal != 1 else ''} removed"
                return description
        return " → ".join(relationship_types)

    @staticmethod
    def _direct_relationship(relationship_type: str, sex: str) -> str:
        labels = {
            "parent": {"M": "Father", "F": "Mother"},
            "child": {"M": "Son", "F": "Daughter"},
            "spouse": {"M": "Husband", "F": "Wife"},
            "sibling": {"M": "Brother", "F": "Sister"},
        }
        defaults = {"parent": "Parent", "child": "Child", "spouse": "Spouse", "sibling": "Sibling"}
        return labels[relationship_type].get(sex, defaults[relationship_type])

    @staticmethod
    def _ancestor_description(distance: int, sex: str) -> str:
        noun = {"M": "father", "F": "mother"}.get(sex, "parent")
        if distance == 2:
            return f"Grand{noun}"
        return f"{'Great-' * (distance - 2)}grand{noun}".capitalize()

    @staticmethod
    def _descendant_description(distance: int, sex: str) -> str:
        noun = {"M": "son", "F": "daughter"}.get(sex, "child")
        if distance == 2:
            return f"Grand{noun}"
        return f"{'Great-' * (distance - 2)}grand{noun}".capitalize()

    @staticmethod
    def _ordinal(number: int) -> str:
        words = {
            1: "First",
            2: "Second",
            3: "Third",
            4: "Fourth",
            5: "Fifth",
            6: "Sixth",
            7: "Seventh",
            8: "Eighth",
            9: "Ninth",
            10: "Tenth",
        }
        if number in words:
            return words[number]
        if 10 <= number % 100 <= 20:
            suffix = "th"
        else:
            suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
        return f"{number}{suffix}"

    @staticmethod
    def _format_person(person: RelationshipPathPerson) -> str:
        return (
            f"{person.full_name} [Database ID: {person.database_id}; "
            f"GEDCOM ID: {person.gedcom_id or '-'}]"
        )