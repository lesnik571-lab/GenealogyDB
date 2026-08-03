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
from graph_editor_service import GraphEditorService, GraphEdge, GraphFamily, GraphModel
from repository.person_repository import PersonRepository


LAYOUT_MODES = ("top_to_bottom", "left_to_right", "ancestors_only", "descendants_only", "hourglass")
CARD_WIDTH = 210
CARD_HEIGHT = 94
H_GAP = 48
V_GAP = 78
MIN_ZOOM = 0.35
MAX_ZOOM = 2.5


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
