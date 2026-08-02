"""Read-only kinship analysis, path enumeration, coefficients, and exports."""

from __future__ import annotations

import csv
import html
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

from repository.person_repository import PersonRepository


@dataclass(frozen=True)
class KinshipPerson:
    """Display-ready person in a kinship analysis."""

    database_id: int
    gedcom_id: str
    full_name: str


@dataclass(frozen=True)
class KinshipPath:
    """One simple relationship path between the selected people."""

    people: tuple[KinshipPerson, ...]
    edges: tuple[str, ...]
    is_blood: bool
    is_direct_blood: bool

    @property
    def distance(self) -> int:
        return len(self.edges)

    @property
    def description(self) -> str:
        parts = [self.people[0].full_name]
        for edge, person in zip(self.edges, self.people[1:]):
            parts.append(f"--{edge}--> {person.full_name}")
        return " ".join(parts)


@dataclass(frozen=True)
class CommonAncestor:
    """A shared ancestor with generation distances from both people."""

    person: KinshipPerson
    source_distance: int
    target_distance: int


@dataclass(frozen=True)
class KinshipAnalysis:
    """Complete read-only kinship calculation for two people."""

    source: KinshipPerson
    target: KinshipPerson
    shortest_path: KinshipPath | None
    paths: tuple[KinshipPath, ...]
    blood_relationship: bool
    relationship_degree: int | None
    common_ancestors: tuple[CommonAncestor, ...]
    nearest_common_ancestors: tuple[CommonAncestor, ...]
    generation_distance: tuple[int, int] | None
    coefficient_of_relationship: float
    inbreeding_coefficients: tuple[tuple[KinshipPerson, float], ...]


class KinshipService:
    """Analyze biological and social paths using repository family records."""

    def __init__(self, repository: PersonRepository, max_paths: int = 50, max_depth: int = 14):
        self.repository = repository
        self.max_paths = max_paths
        self.max_depth = max_depth

    def analyze(self, source_reference, target_reference) -> KinshipAnalysis:
        people = {int(person["id"]): person for person in self.repository.list_people_full()}
        source_id = self._resolve(source_reference)
        target_id = self._resolve(target_reference)
        if source_id not in people or target_id not in people:
            raise ValueError("Человек не найден")
        parents, graph = self._build_pedigree(set(people))
        raw_paths = self._all_simple_paths(graph, source_id, target_id)
        paths = tuple(self._path(raw, people) for raw in raw_paths)
        shortest_blood_distance = min(
            (path.distance for path in paths if path.is_blood),
            default=None,
        )
        paths = tuple(
            replace(
                path,
                is_direct_blood=(
                    path.is_blood and path.distance == shortest_blood_distance
                ),
            )
            for path in paths
        )
        source_ancestors = self._ancestor_distances(source_id, parents)
        target_ancestors = self._ancestor_distances(target_id, parents)
        common_ids = set(source_ancestors) & set(target_ancestors)
        common = tuple(sorted(
            (
                CommonAncestor(
                    self._person(people[person_id]),
                    source_ancestors[person_id],
                    target_ancestors[person_id],
                )
                for person_id in common_ids
            ),
            key=lambda item: (
                item.source_distance + item.target_distance,
                max(item.source_distance, item.target_distance),
                item.person.full_name.casefold(),
            ),
        ))
        nearest_ids = self._lowest_common_ancestors(common_ids, parents)
        nearest = tuple(item for item in common if item.person.database_id in nearest_ids)
        relationship_coefficient = sum(
            0.5 ** (item.source_distance + item.target_distance)
            for item in nearest
        )
        degree = min(
            (item.source_distance + item.target_distance for item in nearest),
            default=None,
        )
        generation_distance = None
        if nearest:
            closest = min(nearest, key=lambda item: (
                item.source_distance + item.target_distance,
                max(item.source_distance, item.target_distance),
            ))
            generation_distance = (closest.source_distance, closest.target_distance)
        inbreeding = []
        for person_id in (source_id, target_id):
            parent_ids = tuple(parents.get(person_id, ()))
            if len(parent_ids) == 2:
                parent_coefficient = self._relationship_coefficient(parent_ids[0], parent_ids[1], parents)
                coefficient = parent_coefficient / 2
                if coefficient > 0:
                    inbreeding.append((self._person(people[person_id]), coefficient))
        return KinshipAnalysis(
            source=self._person(people[source_id]),
            target=self._person(people[target_id]),
            shortest_path=paths[0] if paths else None,
            paths=paths,
            blood_relationship=any(path.is_blood for path in paths),
            relationship_degree=degree,
            common_ancestors=common,
            nearest_common_ancestors=nearest,
            generation_distance=generation_distance,
            coefficient_of_relationship=min(1.0, relationship_coefficient),
            inbreeding_coefficients=tuple(inbreeding),
        )

    def export_csv(self, analysis: KinshipAnalysis, destination_path) -> Path:
        destination = Path(destination_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8-sig", newline="") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(("path", "distance", "blood", "direct_blood", "description"))
            for index, path in enumerate(analysis.paths, start=1):
                writer.writerow((index, path.distance, path.is_blood, path.is_direct_blood, path.description))
        return destination

    def export_html(self, analysis: KinshipAnalysis, destination_path) -> Path:
        destination = Path(destination_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        path_rows = "".join(
            f"<tr class={'direct' if path.is_direct_blood else 'blood' if path.is_blood else 'social'}>"
            f"<td>{index}</td><td>{path.distance}</td><td>{'yes' if path.is_blood else 'no'}</td>"
            f"<td>{html.escape(path.description)}</td></tr>"
            for index, path in enumerate(analysis.paths, start=1)
        )
        ancestors = ", ".join(html.escape(item.person.full_name) for item in analysis.common_ancestors) or "None"
        document = f"""<!doctype html><html><head><meta charset="utf-8"><title>Kinship analysis</title>
<style>body{{font-family:Segoe UI,sans-serif;margin:24px}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #bbb;padding:6px}}.direct{{background:#d9f2df}}.blood{{background:#eef7f0}}</style></head><body>
<h1>{html.escape(analysis.source.full_name)} / {html.escape(analysis.target.full_name)}</h1>
<p>Blood relationship: {'yes' if analysis.blood_relationship else 'no'}<br>Relationship degree: {analysis.relationship_degree if analysis.relationship_degree is not None else '-'}<br>Coefficient of relationship: {analysis.coefficient_of_relationship:.6f}<br>Common ancestors: {ancestors}</p>
<table><thead><tr><th>#</th><th>Distance</th><th>Blood</th><th>Path</th></tr></thead><tbody>{path_rows}</tbody></table></body></html>"""
        destination.write_text(document, encoding="utf-8")
        return destination

    def export_pdf(self, analysis: KinshipAnalysis, destination_path) -> Path:
        destination = Path(destination_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "Kinship analysis",
            f"Source: {analysis.source.full_name}",
            f"Target: {analysis.target.full_name}",
            f"Blood relationship: {'yes' if analysis.blood_relationship else 'no'}",
            f"Relationship degree: {analysis.relationship_degree if analysis.relationship_degree is not None else '-'}",
            f"Coefficient: {analysis.coefficient_of_relationship:.6f}",
        ]
        lines.extend(f"Path {index}: {path.description}" for index, path in enumerate(analysis.paths, start=1))
        destination.write_bytes(self._simple_pdf(lines))
        return destination

    def _build_pedigree(self, valid_ids):
        parents = defaultdict(set)
        graph = {person_id: set() for person_id in valid_ids}
        children_by_family = defaultdict(set)
        for link in self.repository.list_family_children_raw():
            child_id = self._resolve_optional(link.get("child_id"))
            if child_id in valid_ids:
                children_by_family[str(link.get("family_id") or "")].add(child_id)
        for family in self.repository.list_families_raw():
            family_keys = {str(family.get("id") or ""), str(family.get("gedcom_id") or "")}
            children = set().union(*(children_by_family[key] for key in family_keys))
            parent_ids = [
                person_id for person_id in (
                    self._resolve_optional(family.get("husband_id")),
                    self._resolve_optional(family.get("wife_id")),
                ) if person_id in valid_ids
            ]
            if len(parent_ids) == 2:
                self._add_edge(graph, parent_ids[0], parent_ids[1], "spouse", "spouse")
            for parent_id in parent_ids:
                for child_id in children:
                    parents[child_id].add(parent_id)
                    self._add_edge(graph, parent_id, child_id, "child", "parent")
        return dict(parents), graph

    def _all_simple_paths(self, graph, source_id, target_id):
        found = []
        queue = deque([((source_id,), ())])
        while queue and len(found) < self.max_paths:
            nodes, edges = queue.popleft()
            current = nodes[-1]
            if current == target_id:
                found.append(tuple(zip(nodes, ("", *edges))))
                continue
            if len(edges) >= self.max_depth:
                continue
            for next_id, edge in sorted(graph.get(current, ()), key=lambda item: (item[1] == "spouse", item[0])):
                if next_id not in nodes:
                    queue.append(((*nodes, next_id), (*edges, edge)))
        found.sort(key=lambda route: (
            len(route),
            any(edge == "spouse" for _person_id, edge in route),
            tuple(person_id for person_id, _edge in route),
        ))
        return found

    def _path(self, route, people):
        person_ids = tuple(item[0] for item in route)
        edges = tuple(item[1] for item in route[1:])
        blood = "spouse" not in edges
        return KinshipPath(
            tuple(self._person(people[person_id]) for person_id in person_ids),
            edges,
            blood,
            False,
        )

    def _ancestor_distances(self, person_id, parents):
        distances = {person_id: 0}
        queue = deque([person_id])
        while queue:
            current = queue.popleft()
            if distances[current] >= self.max_depth:
                continue
            for parent_id in parents.get(current, ()):
                next_distance = distances[current] + 1
                if next_distance < distances.get(parent_id, self.max_depth + 1):
                    distances[parent_id] = next_distance
                    queue.append(parent_id)
        return distances

    def _lowest_common_ancestors(self, common_ids, parents):
        lowest = set(common_ids)
        for candidate in common_ids:
            ancestors = self._ancestor_distances(candidate, parents)
            lowest.difference_update(set(ancestors) & set(common_ids) - {candidate})
        return lowest

    def _relationship_coefficient(self, left_id, right_id, parents):
        left = self._ancestor_distances(left_id, parents)
        right = self._ancestor_distances(right_id, parents)
        common_ids = set(left) & set(right)
        lowest = self._lowest_common_ancestors(common_ids, parents)
        return min(1.0, sum(0.5 ** (left[item] + right[item]) for item in lowest))

    def _resolve(self, reference):
        person_id = self.repository.resolve_person_reference(reference)
        if person_id is None:
            raise ValueError("Человек не найден")
        return int(person_id)

    def _resolve_optional(self, reference):
        if not str(reference or "").strip():
            return None
        person_id = self.repository.resolve_person_reference(reference)
        return int(person_id) if person_id is not None else None

    @staticmethod
    def _add_edge(graph, left, right, left_to_right, right_to_left):
        graph[left].add((right, left_to_right))
        graph[right].add((left, right_to_left))

    @staticmethod
    def _person(person):
        name = " ".join(value for value in (person.get("first_name", ""), person.get("last_name", "")) if value) or "Без имени"
        return KinshipPerson(int(person["id"]), str(person.get("gedcom_id") or ""), name)

    @staticmethod
    def _simple_pdf(lines: Iterable[str]) -> bytes:
        sanitized = [str(line).encode("latin-1", errors="replace").decode("latin-1").replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in lines]
        stream_lines = ["BT", "/F1 10 Tf", "40 800 Td"]
        for index, line in enumerate(sanitized):
            stream_lines.append(f"({line}) Tj" if index == 0 else f"0 -14 Td ({line}) Tj")
        stream_lines.append("ET")
        stream = "\n".join(stream_lines)
        objects = [
            "<< /Type /Catalog /Pages 2 0 R >>",
            "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            f"<< /Length {len(stream.encode('latin-1'))} >>\nstream\n{stream}\nendstream",
            "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ]
        output = bytearray(b"%PDF-1.4\n")
        offsets = [0]
        for index, obj in enumerate(objects, start=1):
            offsets.append(len(output))
            output.extend(f"{index} 0 obj\n{obj}\nendobj\n".encode("latin-1"))
        xref = len(output)
        output.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
        for offset in offsets[1:]:
            output.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
        output.extend(f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii"))
        return bytes(output)
