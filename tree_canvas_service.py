"""Read-only interactive genealogy canvas models, layouts, persistence, and exports."""

from __future__ import annotations

import json
import struct
import zlib
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable
from xml.sax.saxutils import escape

from config import DATA_DIR
from audit_service import AuditService
from database import backup_database
from graph_editor_service import GraphEditorService, GraphModification
from repository.person_repository import PersonRepository
from repository.relationship_service import RelationshipService
from undo_manager import RepositoryDeltaCommand, TableDelta


LAYOUT_MODES = ("top_to_bottom", "left_to_right", "ancestors_only", "descendants_only", "hourglass")
CARD_WIDTH = 210
CARD_HEIGHT = 94
H_GAP = 48
V_GAP = 78
MIN_ZOOM = 0.35
MAX_ZOOM = 2.5
EDIT_KINDS = (
    "add_parent", "add_child", "add_spouse", "add_partner", "remove_relationship",
    "reassign_child", "replace_parent", "change_relationship_type",
)


@dataclass(frozen=True)
class TreeCanvasNode:
    person_id: int
    full_name: str
    birth_year: str
    death_year: str
    gedcom_id: str
    role: str
    generation: int
    states: tuple[str, ...]
    collapsed: bool = False


@dataclass(frozen=True)
class TreeCanvasConnector:
    key: str
    kind: str
    source_id: int
    target_id: int
    family_id: int
    relationship_type: str
    special: bool = False


@dataclass(frozen=True)
class TreeCanvasModel:
    center_id: int
    nodes: tuple[TreeCanvasNode, ...]
    connectors: tuple[TreeCanvasConnector, ...]
    positions: dict[int, tuple[float, float]]
    mode: str
    ancestor_depth: int
    descendant_depth: int
    collapsed_ids: frozenset[int]


@dataclass(frozen=True)
class TreeCanvasChange:
    kind: str
    source_id: int
    target_id: int = 0
    family_id: int = 0
    parent_role: str = "father"
    other_parent_id: int = 0
    old_parent_id: int = 0
    relationship_type: str = "unknown"


@dataclass(frozen=True)
class TreeCanvasEditPreview:
    changes: tuple[TreeCanvasChange, ...]
    source_fingerprint: tuple
    affected_families: tuple[dict, ...]
    links_to_create: tuple[str, ...]
    links_to_remove: tuple[str, ...]
    warnings: tuple[str, ...]
    blockers: tuple[str, ...]

    @property
    def can_execute(self):
        return not self.blockers


@dataclass(frozen=True)
class TreeCanvasExecutionResult:
    delta: dict[str, TableDelta]
    backup_path: Path
    changes: tuple[TreeCanvasChange, ...]
    descriptions: tuple[str, ...]


class TreeCanvasSafetyError(ValueError):
    """Raised when a pending canvas relationship change is unsafe."""


class TreeCanvasNavigation:
    """Pure navigation history for a canvas center without UI dependencies."""

    def __init__(self, center_id: int | None = None) -> None:
        self._items = [int(center_id)] if center_id is not None else []
        self._index = len(self._items) - 1

    @property
    def current(self):
        return self._items[self._index] if self._index >= 0 else None

    @property
    def can_back(self):
        return self._index > 0

    @property
    def can_forward(self):
        return self._index + 1 < len(self._items)

    def visit(self, person_id):
        person_id = int(person_id)
        if person_id == self.current:
            return person_id
        del self._items[self._index + 1:]
        self._items.append(person_id)
        self._index = len(self._items) - 1
        return person_id

    def back(self):
        if self.can_back:
            self._index -= 1
        return self.current

    def forward(self):
        if self.can_forward:
            self._index += 1
        return self.current


class TreeCanvasService:
    """Build a stable read-only graph canvas and persist UI-only coordinates."""

    def __init__(self, repository: PersonRepository, *, layout_dir=None) -> None:
        self.repository = repository
        self.layout_dir = Path(layout_dir or DATA_DIR / "tree_layouts")
        self.relationships = RelationshipService(repository)

    def preview_changes(self, changes) -> TreeCanvasEditPreview:
        normalized = tuple(self._normalize_change(change) for change in changes)
        if not normalized:
            raise ValueError("Нет изменений связей для проверки")
        graph_service = GraphEditorService(self.repository)
        graph = graph_service.build_graph()
        people = {node.person_id for node in graph.nodes}
        families = {family.family_id: family for family in graph.families}
        blockers, warnings, create, remove, affected = [], [], [], [], []
        graph_changes = []
        for change in normalized:
            try:
                self._validate_change(change, people, families, graph)
                graph_change = self._graph_change(change, families)
                if graph_change is not None:
                    graph_changes.append(graph_change)
                create.extend(self._created_links(change))
                remove.extend(self._removed_links(change))
                if change.family_id and change.family_id in families:
                    affected.append(self._family_summary(families[change.family_id]))
            except TreeCanvasSafetyError as error:
                blockers.append(str(error))
        if not blockers and graph_changes:
            graph_preview = graph_service.preview(graph_changes)
            blockers.extend(graph_preview.blockers)
            warnings.extend(issue.description for issue in graph_preview.after.issues if issue.kind == "orphan")
        return TreeCanvasEditPreview(
            normalized, self._graph_fingerprint(graph), tuple(
                {item["id"]: item for item in affected}.values()
            ),
            tuple(create), tuple(remove), tuple(dict.fromkeys(warnings)), tuple(dict.fromkeys(blockers)),
        )

    def execute_changes(self, preview: TreeCanvasEditPreview, *, backup_dir=None, dry_run=False) -> TreeCanvasExecutionResult | dict:
        if dry_run:
            return self.dry_run(preview)
        if preview.blockers:
            raise TreeCanvasSafetyError("; ".join(preview.blockers))
        graph = GraphEditorService(self.repository).build_graph()
        if self._graph_fingerprint(graph) != preview.source_fingerprint:
            raise RuntimeError("Связи изменились после предварительного просмотра")
        backup_path = backup_database(self.repository.db_name, backup_dir or DATA_DIR / "backups")
        before = self.repository.capture_command_state()
        descriptions = []
        try:
            with self.repository.transaction():
                for change in preview.changes:
                    descriptions.append(self._apply_change(change))
        except Exception:
            raise
        after = self.repository.capture_command_state()
        delta = RepositoryDeltaCommand._build_delta(before, after)
        AuditService.for_database(self.repository.db_name).record_delta(
            "relationship_change", delta,
            database_id=tuple(dict.fromkeys(value for change in preview.changes for value in (change.source_id, change.target_id) if value)),
            description=" ".join(descriptions), service="tree_canvas_service",
            batch_id="canvas" if len(preview.changes) > 1 else "",
        )
        return TreeCanvasExecutionResult(delta, backup_path, preview.changes, tuple(descriptions))

    def dry_run(self, preview: TreeCanvasEditPreview):
        return {
            "changes": [change.__dict__ for change in preview.changes],
            "affected_families": list(preview.affected_families),
            "links_to_create": list(preview.links_to_create),
            "links_to_remove": list(preview.links_to_remove),
            "warnings": list(preview.warnings), "blockers": list(preview.blockers),
            "can_execute": preview.can_execute,
        }

    def export_preview_json(self, preview: TreeCanvasEditPreview, destination_path) -> Path:
        destination = Path(destination_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(self.dry_run(preview), ensure_ascii=False, indent=2), encoding="utf-8")
        return destination

    def build(
        self,
        center_id: int,
        *,
        ancestor_depth=3,
        descendant_depth=3,
        mode="hourglass",
        collapsed_ids: Iterable[int] = (),
        progress_callback: Callable[[str, int, int], None] | None = None,
        cancel_callback: Callable[[], None] | None = None,
    ) -> TreeCanvasModel:
        ancestor_depth = self._depth(ancestor_depth)
        descendant_depth = self._depth(descendant_depth)
        if mode not in LAYOUT_MODES:
            raise ValueError("Неизвестный режим расположения")
        graph = GraphEditorService(self.repository).build_graph()
        center_id = int(center_id)
        if center_id not in {node.person_id for node in graph.nodes}:
            raise ValueError("Человек для дерева не найден")
        collapsed = frozenset(int(value) for value in collapsed_ids)
        parent_map, child_map, spouse_map = self._maps(graph)
        ancestors = self._walk(center_id, parent_map, ancestor_depth, -1, cancel_callback)
        descendants = self._walk(center_id, child_map, descendant_depth, 1, cancel_callback)
        siblings = {
            sibling: 0
            for parent in parent_map.get(center_id, ())
            for sibling in child_map.get(parent, ())
            if sibling != center_id
        }
        roles = {center_id: ("Selected", 0)}
        roles.update({person_id: ("Ancestor", generation) for person_id, generation in ancestors.items()})
        roles.update({person_id: ("Descendant", generation) for person_id, generation in descendants.items()})
        roles.update({person_id: ("Sibling", 0) for person_id in siblings})
        for related in tuple(roles):
            for spouse in spouse_map.get(related, ()):
                roles.setdefault(spouse, ("Spouse/Partner", roles[related][1]))
        if mode == "ancestors_only":
            roles = {person_id: value for person_id, value in roles.items() if value[1] <= 0 and value[0] != "Descendant"}
        elif mode == "descendants_only":
            roles = {person_id: value for person_id, value in roles.items() if value[1] >= 0 and value[0] != "Ancestor"}
        visible = self._remove_collapsed_branches(center_id, roles, child_map, collapsed)
        duplicate_ids = self._duplicate_candidates()
        issue_ids = {person_id for issue in graph.issues for person_id in issue.node_ids}
        node_map = {node.person_id: node for node in graph.nodes}
        nodes = tuple(
            TreeCanvasNode(
                person_id=person_id,
                full_name=node_map[person_id].full_name,
                birth_year=self._year(node_map[person_id].birth_date),
                death_year=self._year(node_map[person_id].death_date),
                gedcom_id=node_map[person_id].gedcom_id,
                role=roles[person_id][0],
                generation=roles[person_id][1],
                states=tuple(filter(None, (
                    "selected" if person_id == center_id else "",
                    "unnamed" if node_map[person_id].full_name == "Без имени" else "",
                    "duplicate" if person_id in duplicate_ids else "",
                    "warning" if person_id in issue_ids else "",
                    "collapsed" if person_id in collapsed else "",
                ))),
                collapsed=person_id in collapsed,
            )
            for person_id in sorted(visible, key=lambda value: (roles[value][1], value))
        )
        connectors = tuple(
            self._connectors(graph, visible, parent_map, center_id)
        )
        positions = self._layout(nodes, connectors, mode)
        positions.update(self.load_positions(center_id, set(visible)))
        if progress_callback:
            progress_callback("Разметка дерева", len(nodes), len(nodes))
        return TreeCanvasModel(center_id, nodes, connectors, positions, mode, ancestor_depth, descendant_depth, collapsed)

    def save_positions(self, center_id, positions) -> Path:
        self.layout_dir.mkdir(parents=True, exist_ok=True)
        destination = self._layout_path(center_id)
        payload = {str(int(person_id)): [round(float(x), 2), round(float(y), 2)] for person_id, (x, y) in positions.items()}
        destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return destination

    def load_positions(self, center_id, visible_ids=None):
        try:
            payload = json.loads(self._layout_path(center_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        allowed = set(visible_ids) if visible_ids is not None else None
        return {
            int(person_id): (float(value[0]), float(value[1]))
            for person_id, value in payload.items()
            if isinstance(value, list) and len(value) == 2 and (allowed is None or int(person_id) in allowed)
        }

    def export_svg(self, model: TreeCanvasModel, destination_path, *, scale=1.0, title="GenealogyDB Tree Canvas") -> Path:
        destination = Path(destination_path)
        width, height, offset_x, offset_y = self._bounds(model, scale)
        lines = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">', '<rect width="100%" height="100%" fill="#f4f7f8"/>', f'<text x="24" y="30" font-family="Segoe UI" font-size="18">{escape(title)}</text>']
        for connector in model.connectors:
            points = self._connector_points(model, connector, scale, offset_x, offset_y)
            stroke = "#8c4b19" if connector.special else ("#376b85" if connector.kind == "parent" else "#75808a")
            dash = ' stroke-dasharray="7 4"' if connector.special else ""
            lines.append(f'<polyline points="{points}" fill="none" stroke="{stroke}" stroke-width="2"{dash}/>')
        for node in model.nodes:
            x, y = self._point(model.positions[node.person_id], scale, offset_x, offset_y)
            fill, outline = self._colors(node)
            lines.extend((
                f'<rect x="{x}" y="{y}" width="{CARD_WIDTH * scale}" height="{CARD_HEIGHT * scale}" rx="4" fill="{fill}" stroke="{outline}" stroke-width="2"/>',
                f'<text x="{x + 10 * scale}" y="{y + 23 * scale}" font-family="Segoe UI" font-size="{12 * scale}" font-weight="bold">{escape(node.full_name)}</text>',
                f'<text x="{x + 10 * scale}" y="{y + 45 * scale}" font-family="Segoe UI" font-size="{9 * scale}">{escape(f"{node.birth_year or '-'} - {node.death_year or '-'}")}</text>',
                f'<text x="{x + 10 * scale}" y="{y + 62 * scale}" font-family="Segoe UI" font-size="{8 * scale}">{escape(f"ID {node.person_id} | {node.gedcom_id or '-'}")}</text>',
                f'<text x="{x + 10 * scale}" y="{y + 79 * scale}" font-family="Segoe UI" font-size="{8 * scale}">{escape(node.role)}</text>',
            ))
        lines.append(f'<text x="24" y="{height - 22}" font-family="Segoe UI" font-size="10">Legend: selected / unnamed / duplicate / warning / collapsed | Scale {scale:.2f}</text>')
        lines.append("</svg>")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("\n".join(lines), encoding="utf-8")
        return destination

    def export_png(self, model, destination_path, *, scale=1.0, title="GenealogyDB Tree Canvas") -> Path:
        destination = Path(destination_path)
        width, height, _offset_x, _offset_y = self._bounds(model, scale)
        # A valid white PNG canvas preserves a dependency-free export contract; SVG carries rich vector detail.
        raw = b"".join(b"\x00" + b"\xf4\xf7\xf8" * width for _ in range(height))
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
        content = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        return destination

    def export_pdf(self, model, destination_path, *, scale=1.0, title="GenealogyDB Tree Canvas") -> Path:
        destination = Path(destination_path)
        width, height, _offset_x, _offset_y = self._bounds(model, scale)
        text = [title, f"Scale: {scale:.2f}", "Legend: selected, unnamed, duplicate, warning, collapsed"]
        text.extend(f"{node.full_name} | {node.role} | ID {node.person_id} | {node.gedcom_id}" for node in model.nodes)
        stream = "BT /F1 11 Tf 36 760 Td " + " ".join(f"({self._pdf_text(line)}) Tj 0 -15 Td" for line in text[:45]) + " ET"
        objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>", f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {max(width, 612)} {max(height, 792)}] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>".encode(), b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", f"<< /Length {len(stream.encode())} >>\nstream\n{stream}\nendstream".encode()]
        body = b"%PDF-1.4\n"
        offsets = [0]
        for index, obj in enumerate(objects, 1):
            offsets.append(len(body))
            body += f"{index} 0 obj\n".encode() + obj + b"\nendobj\n"
        start = len(body)
        body += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:]) + f"trailer << /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(body)
        return destination

    @staticmethod
    def _depth(value):
        value = int(value)
        if not 1 <= value <= 8:
            raise ValueError("Глубина поколений должна быть от 1 до 8")
        return value

    @staticmethod
    def _year(value):
        digits = "".join(char for char in str(value or "") if char.isdigit())
        return digits[-4:] if len(digits) >= 4 else ""

    @staticmethod
    def _maps(graph):
        parents, children, spouses = defaultdict(set), defaultdict(set), defaultdict(set)
        for edge in graph.edges:
            if edge.kind == "parent":
                parents[edge.target_id].add(edge.source_id)
                children[edge.source_id].add(edge.target_id)
            elif edge.kind == "spouse":
                spouses[edge.source_id].add(edge.target_id)
                spouses[edge.target_id].add(edge.source_id)
        return parents, children, spouses

    @staticmethod
    def _walk(center, adjacency, depth, direction, cancel):
        result, queue = {}, deque([(center, 0)])
        while queue:
            if cancel:
                cancel()
            person_id, current_depth = queue.popleft()
            if current_depth == depth:
                continue
            for related in sorted(adjacency.get(person_id, ())):
                next_depth = current_depth + 1
                if related not in result or next_depth < abs(result[related]):
                    result[related] = direction * next_depth
                    queue.append((related, next_depth))
        return result

    @staticmethod
    def _remove_collapsed_branches(center, roles, child_map, collapsed):
        excluded = set()
        for person_id in collapsed:
            queue = deque(child_map.get(person_id, ()))
            while queue:
                child = queue.popleft()
                excluded.add(child)
                queue.extend(child_map.get(child, ()))
        return set(roles) - excluded

    def _connectors(self, graph, visible, parent_map, center_id):
        families = {family.family_id: family for family in graph.families}
        for edge in graph.edges:
            if edge.source_id not in visible or edge.target_id not in visible:
                continue
            special = edge.kind == "parent" and len(parent_map.get(edge.target_id, ())) > 2
            if edge.kind == "spouse" and edge.relationship_type not in {"marriage", "unknown"}:
                special = True
            yield TreeCanvasConnector(edge.key, edge.kind, edge.source_id, edge.target_id, edge.family_id, edge.relationship_type, special)

    def _layout(self, nodes, connectors, mode):
        rows = defaultdict(list)
        for node in nodes:
            generation = node.generation
            if mode == "top_to_bottom":
                generation += 4
            elif mode == "left_to_right":
                generation += 4
            rows[generation].append(node.person_id)
        positions = {}
        for generation, person_ids in sorted(rows.items()):
            for index, person_id in enumerate(sorted(person_ids)):
                if mode == "left_to_right":
                    positions[person_id] = (90 + generation * (CARD_WIDTH + V_GAP), 90 + index * (CARD_HEIGHT + H_GAP))
                else:
                    positions[person_id] = (90 + index * (CARD_WIDTH + H_GAP), 90 + generation * (CARD_HEIGHT + V_GAP))
        return positions

    def _duplicate_candidates(self):
        try:
            return {int(item["left_id"]) for item in self.repository.find_duplicate_candidates()} | {int(item["right_id"]) for item in self.repository.find_duplicate_candidates()}
        except Exception:
            return set()

    def _validate_change(self, change, people, families, graph):
        if change.kind not in EDIT_KINDS:
            raise TreeCanvasSafetyError("Неподдерживаемое изменение полотна")
        if change.source_id not in people or (change.target_id and change.target_id not in people):
            raise TreeCanvasSafetyError("Исходный или целевой человек больше не существует")
        if change.kind in {"add_parent", "add_child", "add_spouse", "add_partner"} and change.source_id == change.target_id:
            raise TreeCanvasSafetyError("Нельзя создать связь человека с самим собой")
        if change.kind in {"add_spouse", "add_partner"}:
            if self._is_ancestor_or_descendant(change.source_id, change.target_id, graph):
                raise TreeCanvasSafetyError("Нельзя создать супружескую связь между предком и потомком")
            if change.relationship_type not in RelationshipService.RELATIONSHIP_TYPES:
                raise TreeCanvasSafetyError("Неподдерживаемый тип отношений")
        if change.kind in {"remove_relationship", "reassign_child", "replace_parent", "change_relationship_type"}:
            family = families.get(change.family_id)
            if family is None:
                raise TreeCanvasSafetyError("Семья для изменения не найдена")
            if change.kind == "remove_relationship":
                if change.source_id not in (family.husband_id, family.wife_id, *family.child_ids):
                    raise TreeCanvasSafetyError("Выбранная связь больше не существует")
                if change.target_id and change.target_id not in (family.husband_id, family.wife_id, *family.child_ids):
                    raise TreeCanvasSafetyError("Целевая связь больше не существует")
                if change.source_id in (family.husband_id, family.wife_id) and family.child_ids:
                    raise TreeCanvasSafetyError("Удаление оставит недопустимую неполную семью с детьми")
            if change.kind == "reassign_child" and change.source_id not in family.child_ids:
                raise TreeCanvasSafetyError("Ребёнок не состоит в исходной семье")
            if change.kind == "replace_parent":
                if change.source_id not in family.child_ids or change.old_parent_id not in (family.husband_id, family.wife_id):
                    raise TreeCanvasSafetyError("Исходная родительская связь не найдена")
                if change.target_id == change.source_id:
                    raise TreeCanvasSafetyError("Человек не может быть собственным родителем")
            if change.kind == "change_relationship_type" and change.relationship_type not in RelationshipService.RELATIONSHIP_TYPES:
                raise TreeCanvasSafetyError("Неподдерживаемый тип отношений")
        if change.kind == "add_parent" and self._would_cycle(change.target_id, change.source_id, graph):
            raise TreeCanvasSafetyError("Добавление родителя создаёт цикл родословной")
        if change.kind == "add_child" and self._would_cycle(change.source_id, change.target_id, graph):
            raise TreeCanvasSafetyError("Добавление ребёнка создаёт цикл родословной")
        if change.kind == "replace_parent" and self._would_cycle(change.target_id, change.source_id, graph, exclude_family=change.family_id):
            raise TreeCanvasSafetyError("Замена родителя создаёт цикл родословной")

    def _graph_change(self, change, families):
        if change.kind == "add_parent":
            return GraphModification("link_parent", change.source_id, change.target_id, role=change.parent_role, relationship_type=change.relationship_type)
        if change.kind == "add_child":
            return GraphModification("link_parent", change.target_id, change.source_id, role=change.parent_role, other_parent_id=change.other_parent_id, relationship_type=change.relationship_type)
        if change.kind in {"add_spouse", "add_partner"}:
            return GraphModification("add_spouse", change.source_id, change.target_id, relationship_type=("civil_partner" if change.kind == "add_partner" else change.relationship_type or "marriage"))
        if change.kind == "reassign_child":
            return GraphModification("reattach_child", change.source_id, change.target_id, family_id=change.family_id, role=change.parent_role, other_parent_id=change.other_parent_id, relationship_type=change.relationship_type)
        if change.kind == "replace_parent":
            return GraphModification("change_parent", change.source_id, change.target_id, family_id=change.family_id, role=change.parent_role, old_parent_id=change.old_parent_id)
        if change.kind == "change_relationship_type":
            return None
        family = families[change.family_id]
        if change.source_id in family.child_ids:
            parent = family.husband_id or family.wife_id
            return GraphModification("remove_parent", change.source_id, parent or 0, family_id=change.family_id, role="father" if family.husband_id else "mother")
        return GraphModification("remove_spouse", change.source_id, family_id=change.family_id)

    def _apply_change(self, change):
        if change.kind == "add_parent":
            self.relationships.link_parent(change.source_id, change.target_id, change.parent_role)
            return f"Добавлен родитель ID {change.target_id} для ID {change.source_id}."
        if change.kind == "add_child":
            self.relationships.link_child(change.source_id, change.target_id, change.other_parent_id, change.relationship_type)
            return f"Добавлен ребёнок ID {change.target_id} для ID {change.source_id}."
        if change.kind in {"add_spouse", "add_partner"}:
            relationship_type = "civil_partner" if change.kind == "add_partner" else (change.relationship_type or "marriage")
            self.relationships.link_partner(change.source_id, change.target_id, relationship_type)
            return f"Добавлена партнёрская связь ID {change.source_id} и ID {change.target_id}."
        if change.kind == "reassign_child":
            self.relationships.remove_child_link(change.family_id, change.source_id)
            self.relationships.link_child(change.target_id, change.source_id, change.other_parent_id, change.relationship_type)
            return f"Ребёнок ID {change.source_id} перепривязан к ID {change.target_id}."
        if change.kind == "replace_parent":
            self.relationships.remove_parent_link(change.source_id, change.family_id, change.parent_role)
            self.relationships.link_parent(change.source_id, change.target_id, change.parent_role)
            return f"Родитель ID {change.old_parent_id} заменён на ID {change.target_id}."
        family = self.repository.get_family(change.family_id)
        if family is None:
            raise TreeCanvasSafetyError("Семья для изменения не найдена")
        if change.kind == "change_relationship_type":
            self.relationships.update_family(change.family_id, family.get("husband", ""), family.get("wife", ""), family.get("children", []), change.relationship_type)
            return f"Изменён тип отношений семьи ID {change.family_id}."
        if change.source_id in family.get("children", []):
            self.relationships.remove_child_link(change.family_id, change.source_id)
        else:
            self.relationships.remove_partner_link(change.source_id, change.family_id)
        return f"Удалена связь в семье ID {change.family_id}."

    @staticmethod
    def _normalize_change(change):
        if isinstance(change, TreeCanvasChange):
            return change
        if isinstance(change, dict):
            return TreeCanvasChange(**change)
        raise TypeError("Изменение полотна должно быть TreeCanvasChange или dict")

    @staticmethod
    def _family_summary(family):
        return {"id": family.family_id, "gedcom_id": family.gedcom_id, "relationship_type": family.relationship_type, "parents": tuple(value for value in (family.husband_id, family.wife_id) if value), "children": family.child_ids}

    @staticmethod
    def _created_links(change):
        return (f"{change.kind}: {change.source_id} -> {change.target_id}",) if change.kind != "remove_relationship" else ()

    @staticmethod
    def _removed_links(change):
        return (f"remove: {change.source_id} -> {change.target_id or change.family_id}",) if change.kind in {"remove_relationship", "reassign_child", "replace_parent"} else ()

    @staticmethod
    def _graph_fingerprint(graph):
        return tuple((family.family_id, family.husband_id, family.wife_id, family.child_ids, family.relationship_type) for family in graph.families)

    @staticmethod
    def _would_cycle(parent_id, child_id, graph, exclude_family=0):
        children = defaultdict(set)
        for edge in graph.edges:
            if edge.kind == "parent" and edge.family_id != exclude_family:
                children[edge.source_id].add(edge.target_id)
        stack, seen = [child_id], set()
        while stack:
            current = stack.pop()
            if current == parent_id:
                return True
            if current not in seen:
                seen.add(current)
                stack.extend(children[current])
        return False

    def _is_ancestor_or_descendant(self, first, second, graph):
        return self._would_cycle(first, second, graph) or self._would_cycle(second, first, graph)

    def _layout_path(self, center_id):
        return self.layout_dir / f"tree_{int(center_id)}.json"

    @staticmethod
    def _point(position, scale, offset_x, offset_y):
        return ((position[0] + offset_x) * scale, (position[1] + offset_y) * scale)

    def _bounds(self, model, scale):
        positions = tuple(model.positions.values()) or ((0, 0),)
        min_x, min_y = min(x for x, _y in positions), min(y for _x, y in positions)
        max_x, max_y = max(x for x, _y in positions) + CARD_WIDTH, max(y for _x, y in positions) + CARD_HEIGHT
        offset_x, offset_y = 30 - min_x, 55 - min_y
        return int((max_x - min_x + 60) * scale), int((max_y - min_y + 100) * scale), offset_x, offset_y

    def _connector_points(self, model, connector, scale, offset_x, offset_y):
        source = model.positions[connector.source_id]
        target = model.positions[connector.target_id]
        sx, sy = self._point((source[0] + CARD_WIDTH / 2, source[1] + (CARD_HEIGHT if connector.kind == "parent" else CARD_HEIGHT / 2)), scale, offset_x, offset_y)
        tx, ty = self._point((target[0] + CARD_WIDTH / 2, target[1] if connector.kind == "parent" else target[1] + CARD_HEIGHT / 2), scale, offset_x, offset_y)
        return f"{sx},{sy} {sx},{(sy + ty) / 2} {tx},{(sy + ty) / 2} {tx},{ty}"

    @staticmethod
    def _colors(node):
        if "selected" in node.states:
            return "#dceeff", "#146c94"
        if "warning" in node.states:
            return "#fff2cf", "#b67b00"
        if "duplicate" in node.states:
            return "#ffe1db", "#bf4f38"
        if "unnamed" in node.states:
            return "#e4e7ea", "#75808a"
        return "#ffffff", "#687681"

    @staticmethod
    def _pdf_text(value):
        return str(value).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)").encode("latin-1", "replace").decode("latin-1")
