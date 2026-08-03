"""Read, validate, preview, and atomically edit the genealogy graph."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Mapping

from audit_service import AuditService
from repository.person_repository import PersonRepository
from repository.relationship_service import RelationshipService
from undo_manager import RepositoryDeltaCommand, TableDelta


MODIFICATION_KINDS = (
    "link_parent", "remove_parent", "reattach_child", "change_parent",
    "add_spouse", "remove_spouse",
)


@dataclass(frozen=True)
class GraphNode:
    person_id: int
    gedcom_id: str
    full_name: str
    sex: str
    birth_date: str
    death_date: str


@dataclass(frozen=True)
class GraphFamily:
    family_id: int
    gedcom_id: str
    husband_id: int | None
    wife_id: int | None
    child_ids: tuple[int, ...]
    relationship_type: str


@dataclass(frozen=True)
class GraphEdge:
    key: str
    family_id: int
    kind: str
    source_id: int
    target_id: int
    relationship_type: str


@dataclass(frozen=True)
class GraphIssue:
    kind: str
    description: str
    node_ids: tuple[int, ...] = ()
    edge_keys: tuple[str, ...] = ()


@dataclass(frozen=True)
class GraphModel:
    nodes: tuple[GraphNode, ...]
    families: tuple[GraphFamily, ...]
    edges: tuple[GraphEdge, ...]
    issues: tuple[GraphIssue, ...]


@dataclass(frozen=True)
class GraphModification:
    kind: str
    person_id: int
    related_person_id: int = 0
    family_id: int = 0
    role: str = ""
    other_parent_id: int = 0
    relationship_type: str = "unknown"
    old_parent_id: int = 0


@dataclass(frozen=True)
class GraphPreview:
    before: GraphModel
    after: GraphModel
    modifications: tuple[GraphModification, ...]
    descriptions: tuple[str, ...]
    blockers: tuple[str, ...]
    source_fingerprint: tuple[Any, ...]

    @property
    def can_execute(self) -> bool:
        return not self.blockers


@dataclass(frozen=True)
class GraphExecutionResult:
    delta: dict[str, TableDelta]
    modifications: tuple[GraphModification, ...]
    descriptions: tuple[str, ...]


class GraphEditorSafetyError(ValueError):
    """Raised when a graph modification is invalid or unsafe."""


class GraphEditorService:
    """Provide a service-owned editable graph over existing relationship APIs."""

    def __init__(self, repository: PersonRepository) -> None:
        self.repository = repository
        self.relationships = RelationshipService(repository)

    def build_graph(self) -> GraphModel:
        people = self.repository.list_people_full()
        nodes = tuple(
            GraphNode(
                person_id=int(person["id"]),
                gedcom_id=str(person.get("gedcom_id") or ""),
                full_name=" ".join(
                    value for value in (
                        str(person.get("first_name") or "").strip(),
                        str(person.get("last_name") or "").strip(),
                    ) if value
                ) or "Без имени",
                sex=str(person.get("sex") or ""),
                birth_date=str(person.get("birth_date") or ""),
                death_date=str(person.get("death_date") or ""),
            )
            for person in people
        )
        families = tuple(self._family_models())
        edges = tuple(self._edges(families))
        issues = tuple((*self._validate(nodes, families, edges), *self._unresolved_reference_issues()))
        return GraphModel(nodes, families, edges, issues)

    def preview(self, modifications) -> GraphPreview:
        normalized = tuple(self._normalize_modification(item) for item in modifications)
        if not normalized:
            raise ValueError("Не выбрано изменение графа")
        before = self.build_graph()
        people = {node.person_id for node in before.nodes}
        mutable = [self._mutable_family(family) for family in before.families]
        descriptions = []
        blockers = []
        for modification in normalized:
            if modification.kind not in MODIFICATION_KINDS:
                blockers.append(f"Неподдерживаемое изменение: {modification.kind}.")
                continue
            missing = self._missing_people(modification, people)
            if missing:
                blockers.append(f"Человек ID {missing[0]} не найден.")
                continue
            try:
                descriptions.append(self._simulate(mutable, modification))
            except GraphEditorSafetyError as error:
                blockers.append(str(error))
        after_families = tuple(self._freeze_family(family) for family in mutable)
        after_edges = tuple(self._edges(after_families))
        after_issues = tuple(self._validate(before.nodes, after_families, after_edges))
        after = GraphModel(before.nodes, after_families, after_edges, after_issues)
        before_critical = {self._issue_signature(issue) for issue in before.issues if issue.kind != "orphan"}
        for issue in after_issues:
            if issue.kind != "orphan" and self._issue_signature(issue) not in before_critical:
                blockers.append(issue.description)
        return GraphPreview(
            before,
            after,
            normalized,
            tuple(descriptions),
            tuple(dict.fromkeys(blockers)),
            self._fingerprint(before),
        )

    def execute(self, preview: GraphPreview) -> GraphExecutionResult:
        if preview.blockers:
            raise GraphEditorSafetyError("; ".join(preview.blockers))
        current = self.build_graph()
        if self._fingerprint(current) != preview.source_fingerprint:
            raise RuntimeError("Граф изменился после предварительного просмотра")
        before_state = self.repository.capture_command_state()
        with self.repository.transaction():
            for modification in preview.modifications:
                self._execute_modification(modification)
            after_graph = self.build_graph()
            new_critical = {
                self._issue_signature(issue) for issue in after_graph.issues if issue.kind != "orphan"
            } - {
                self._issue_signature(issue) for issue in current.issues if issue.kind != "orphan"
            }
            if new_critical:
                raise GraphEditorSafetyError("Изменение создало недопустимые связи")
        after_state = self.repository.capture_command_state()
        delta = RepositoryDeltaCommand._build_delta(before_state, after_state)
        AuditService.for_database(self.repository.db_name).record_delta(
            "relationship_change",
            delta,
            database_id=self._affected_people(preview.modifications),
            description=" ".join(preview.descriptions),
            service="graph_editor_service",
        )
        return GraphExecutionResult(delta, preview.modifications, preview.descriptions)

    def _family_models(self):
        raw_families = self.repository.list_families_raw()
        children_by_family = defaultdict(list)
        for row in self.repository.list_family_children_raw():
            children_by_family[str(row["family_id"])].append(row["child_id"])
        for family in raw_families:
            references = {str(family["id"]), str(family.get("gedcom_id") or "")} - {""}
            children = [
                child for reference in references for child in children_by_family.get(reference, ())
            ]
            yield GraphFamily(
                family_id=int(family["id"]),
                gedcom_id=str(family.get("gedcom_id") or ""),
                husband_id=self._resolve(family.get("husband_id")),
                wife_id=self._resolve(family.get("wife_id")),
                child_ids=tuple(
                    child_id for child_id in (self._resolve(child) for child in children)
                    if child_id is not None
                ),
                relationship_type=str(family.get("relationship_type") or "unknown"),
            )

    @staticmethod
    def _edges(families):
        edges = []
        for family in families:
            if family.husband_id is not None and family.wife_id is not None:
                edges.append(GraphEdge(
                    f"spouse:{family.family_id}:{family.husband_id}:{family.wife_id}",
                    family.family_id, "spouse", family.husband_id, family.wife_id,
                    family.relationship_type,
                ))
            for parent_id in (family.husband_id, family.wife_id):
                if parent_id is None:
                    continue
                for child_id in family.child_ids:
                    edges.append(GraphEdge(
                        f"parent:{family.family_id}:{parent_id}:{child_id}",
                        family.family_id, "parent", parent_id, child_id,
                        family.relationship_type,
                    ))
        return edges

    def _validate(self, nodes, families, edges):
        node_ids = {node.person_id for node in nodes}
        linked = set()
        edge_counter = Counter(
            (
                edge.kind,
                *(sorted((edge.source_id, edge.target_id)) if edge.kind == "spouse" else (edge.source_id, edge.target_id)),
            )
            for edge in edges
        )
        for signature, count in edge_counter.items():
            if count > 1:
                matching = tuple(
                    edge.key for edge in edges
                    if (
                        edge.kind,
                        *(sorted((edge.source_id, edge.target_id)) if edge.kind == "spouse" else (edge.source_id, edge.target_id)),
                    ) == signature
                )
                yield GraphIssue(
                    "duplicate", "Обнаружены повторяющиеся родственные связи.",
                    tuple(sorted({signature[1], signature[2]})), matching,
                )
        graph = defaultdict(set)
        for edge in edges:
            linked.update((edge.source_id, edge.target_id))
            if edge.source_id == edge.target_id:
                yield GraphIssue(
                    "invalid", "Человек связан с самим собой.",
                    (edge.source_id,), (edge.key,),
                )
            if edge.source_id not in node_ids or edge.target_id not in node_ids:
                yield GraphIssue(
                    "invalid", "Связь указывает на отсутствующего человека.",
                    tuple(value for value in (edge.source_id, edge.target_id) if value in node_ids),
                    (edge.key,),
                )
            if edge.kind == "parent":
                graph[edge.source_id].add(edge.target_id)
        for cycle in self._cycles(graph):
            cycle_nodes = tuple(sorted(cycle))
            cycle_edges = tuple(
                edge.key for edge in edges
                if edge.kind == "parent" and edge.source_id in cycle and edge.target_id in cycle
            )
            yield GraphIssue("cycle", "Обнаружен цикл в родословной.", cycle_nodes, cycle_edges)
        for family in families:
            if family.child_ids and family.husband_id is None and family.wife_id is None:
                yield GraphIssue(
                    "invalid", "Семья с детьми не имеет родителя.",
                    tuple(sorted(set(family.child_ids))),
                )
        for person_id in sorted(node_ids - linked):
            yield GraphIssue("orphan", "Человек не связан с деревом.", (person_id,))

    def _unresolved_reference_issues(self):
        for family in self.repository.list_families_raw():
            for reference in (family.get("husband_id"), family.get("wife_id")):
                if str(reference or "").strip() and self._resolve(reference) is None:
                    yield GraphIssue(
                        "invalid",
                        f"Семья {family['id']} указывает на отсутствующего человека {reference}.",
                    )
        family_references = {
            reference
            for family in self.repository.list_families_raw()
            for reference in (str(family["id"]), str(family.get("gedcom_id") or ""))
            if reference
        }
        for row in self.repository.list_family_children_raw():
            if str(row["family_id"]) not in family_references:
                yield GraphIssue(
                    "invalid", f"Связь ребёнка указывает на отсутствующую семью {row['family_id']}.",
                )
            if str(row.get("child_id") or "").strip() and self._resolve(row["child_id"]) is None:
                yield GraphIssue(
                    "invalid", f"Связь указывает на отсутствующего ребёнка {row['child_id']}.",
                )

    def _simulate(self, families, modification):
        kind = modification.kind
        if kind == "add_spouse":
            self._ensure_distinct(modification.person_id, modification.related_person_id, "Супруги должны быть разными людьми.")
            if self._find_spouse_family(families, modification.person_id, modification.related_person_id):
                raise GraphEditorSafetyError("Такая супружеская связь уже существует.")
            families.append({
                "family_id": self._next_temporary_id(families), "gedcom_id": "",
                "husband_id": modification.person_id, "wife_id": modification.related_person_id,
                "child_ids": [], "relationship_type": modification.relationship_type,
            })
            return f"Добавить супруга/партнёра ID {modification.related_person_id} для ID {modification.person_id}."
        if kind == "remove_spouse":
            family = self._require_family(families, modification.family_id)
            if modification.person_id not in (family["husband_id"], family["wife_id"]):
                raise GraphEditorSafetyError("Супружеская связь не найдена.")
            other = "wife_id" if family["husband_id"] == modification.person_id else "husband_id"
            removed = family[other]
            if family["child_ids"]:
                family[other] = None
                family["relationship_type"] = "unknown"
            else:
                families.remove(family)
            return f"Удалить супружескую связь ID {modification.person_id} и ID {removed}."
        if kind == "link_parent":
            return self._simulate_link_parent(families, modification)
        if kind == "remove_parent":
            family = self._require_family(families, modification.family_id)
            field = self._role_field(modification.role)
            if family[field] != modification.related_person_id or modification.person_id not in family["child_ids"]:
                raise GraphEditorSafetyError("Родительская связь не найдена.")
            other_field = "wife_id" if field == "husband_id" else "husband_id"
            if family[other_field] is None:
                family["child_ids"].remove(modification.person_id)
                if not family["child_ids"]:
                    families.remove(family)
            else:
                family[field] = None
            return f"Удалить родителя ID {modification.related_person_id} у ID {modification.person_id}."
        if kind == "reattach_child":
            old_family = self._require_family(families, modification.family_id)
            if modification.person_id not in old_family["child_ids"]:
                raise GraphEditorSafetyError("Связь ребёнка не найдена.")
            old_family["child_ids"].remove(modification.person_id)
            linked = GraphModification(
                "link_parent", modification.person_id, modification.related_person_id,
                role=modification.role or "father",
                other_parent_id=modification.other_parent_id,
                relationship_type=modification.relationship_type,
            )
            self._simulate_link_parent(families, linked)
            return f"Перепривязать ребёнка ID {modification.person_id} к родителю ID {modification.related_person_id}."
        if kind == "change_parent":
            old_family = self._require_family(families, modification.family_id)
            field = self._role_field(modification.role)
            if old_family[field] != modification.old_parent_id or modification.person_id not in old_family["child_ids"]:
                raise GraphEditorSafetyError("Исходный родитель не найден.")
            self._ensure_distinct(modification.person_id, modification.related_person_id, "Человек не может быть собственным родителем.")
            old_family[field] = modification.related_person_id
            return f"Заменить родителя ID {modification.old_parent_id} на ID {modification.related_person_id} у ID {modification.person_id}."
        raise GraphEditorSafetyError("Неподдерживаемое изменение графа.")

    def _simulate_link_parent(self, families, modification):
        self._ensure_distinct(modification.person_id, modification.related_person_id, "Человек не может быть собственным родителем.")
        field = self._role_field(modification.role)
        child_families = [family for family in families if modification.person_id in family["child_ids"]]
        if child_families:
            family = child_families[0]
            if family[field] is not None:
                if family[field] == modification.related_person_id:
                    raise GraphEditorSafetyError("Такая родительская связь уже существует.")
                raise GraphEditorSafetyError("Сначала удалите или замените существующего родителя.")
            family[field] = modification.related_person_id
            if modification.other_parent_id:
                other_field = "wife_id" if field == "husband_id" else "husband_id"
                if family[other_field] not in (None, modification.other_parent_id):
                    raise GraphEditorSafetyError("У ребёнка уже указан другой второй родитель.")
                family[other_field] = modification.other_parent_id
        else:
            family = {
                "family_id": self._next_temporary_id(families), "gedcom_id": "",
                "husband_id": modification.related_person_id if field == "husband_id" else modification.other_parent_id or None,
                "wife_id": modification.related_person_id if field == "wife_id" else modification.other_parent_id or None,
                "child_ids": [modification.person_id],
                "relationship_type": modification.relationship_type,
            }
            families.append(family)
        return f"Добавить родителя ID {modification.related_person_id} для ID {modification.person_id}."

    def _execute_modification(self, modification):
        kind = modification.kind
        if kind == "add_spouse":
            return self.relationships.link_partner(
                modification.person_id, modification.related_person_id,
                modification.relationship_type,
            )
        if kind == "remove_spouse":
            return self.relationships.remove_partner_link(modification.person_id, modification.family_id)
        if kind == "link_parent":
            return self.relationships.link_parent(
                modification.person_id, modification.related_person_id,
                modification.role,
            )
        if kind == "remove_parent":
            family = self.relationships.list_family_members(modification.family_id)
            field = "husband" if modification.role == "father" else "wife"
            other_field = "wife" if field == "husband" else "husband"
            if not family.get(other_field):
                return self.relationships.remove_child_link(
                    modification.family_id, modification.person_id,
                )
            return self.relationships.remove_parent_link(
                modification.person_id, modification.family_id, modification.role,
            )
        if kind == "reattach_child":
            self.relationships.remove_child_link(modification.family_id, modification.person_id)
            return self.relationships.link_child(
                modification.related_person_id, modification.person_id,
                modification.other_parent_id, modification.relationship_type,
            )
        if kind == "change_parent":
            self.relationships.remove_parent_link(
                modification.person_id, modification.family_id, modification.role,
            )
            return self.relationships.link_parent(
                modification.person_id, modification.related_person_id,
                modification.role,
            )
        raise ValueError("Неподдерживаемое изменение графа")

    @staticmethod
    def _mutable_family(family):
        return {
            "family_id": family.family_id, "gedcom_id": family.gedcom_id,
            "husband_id": family.husband_id, "wife_id": family.wife_id,
            "child_ids": list(family.child_ids), "relationship_type": family.relationship_type,
        }

    @staticmethod
    def _freeze_family(family):
        return GraphFamily(
            int(family["family_id"]), str(family.get("gedcom_id") or ""),
            family.get("husband_id"), family.get("wife_id"),
            tuple(family.get("child_ids", ())), str(family.get("relationship_type") or "unknown"),
        )

    @staticmethod
    def _normalize_modification(value):
        if isinstance(value, GraphModification):
            return value
        if isinstance(value, Mapping):
            return GraphModification(**value)
        raise TypeError("Изменение графа должно быть GraphModification или словарём")

    @staticmethod
    def _missing_people(modification, people):
        values = [modification.person_id, modification.related_person_id]
        if modification.other_parent_id:
            values.append(modification.other_parent_id)
        if modification.old_parent_id:
            values.append(modification.old_parent_id)
        return [value for value in values if value and value not in people]

    @staticmethod
    def _role_field(role):
        if role == "father":
            return "husband_id"
        if role == "mother":
            return "wife_id"
        raise GraphEditorSafetyError("Роль родителя должна быть father или mother.")

    @staticmethod
    def _ensure_distinct(first, second, message):
        if int(first) == int(second):
            raise GraphEditorSafetyError(message)

    @staticmethod
    def _require_family(families, family_id):
        family = next((item for item in families if item["family_id"] == int(family_id)), None)
        if family is None:
            raise GraphEditorSafetyError("Семья не найдена.")
        return family

    @staticmethod
    def _find_spouse_family(families, first, second):
        pair = {int(first), int(second)}
        return next((
            family for family in families
            if None not in (family["husband_id"], family["wife_id"])
            and {family["husband_id"], family["wife_id"]} == pair
        ), None)

    @staticmethod
    def _next_temporary_id(families):
        negative = [family["family_id"] for family in families if family["family_id"] < 0]
        return min(negative, default=0) - 1

    @staticmethod
    def _cycles(graph):
        cycles = set()
        path = []
        visiting = set()
        visited = set()

        def visit(node):
            if node in visiting:
                start = path.index(node)
                cycles.add(frozenset(path[start:]))
                return
            if node in visited:
                return
            visiting.add(node)
            path.append(node)
            for child in graph.get(node, ()):
                visit(child)
            path.pop()
            visiting.remove(node)
            visited.add(node)

        for node in set(graph) | {child for children in graph.values() for child in children}:
            visit(node)
        return tuple(cycles)

    def _resolve(self, reference):
        if not str(reference or "").strip():
            return None
        value = self.repository.resolve_person_reference(reference)
        return int(value) if value is not None else None

    @staticmethod
    def _issue_signature(issue):
        return issue.kind, issue.node_ids, issue.edge_keys

    @staticmethod
    def _fingerprint(model):
        return (
            tuple((node.person_id, node.gedcom_id, node.full_name) for node in model.nodes),
            tuple((family.family_id, family.gedcom_id, family.husband_id, family.wife_id, family.child_ids, family.relationship_type) for family in model.families),
        )

    @staticmethod
    def _affected_people(modifications):
        values = []
        for item in modifications:
            values.extend((item.person_id, item.related_person_id, item.other_parent_id, item.old_parent_id))
        return tuple(dict.fromkeys(value for value in values if value))
