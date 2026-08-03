"""Read-only interactive genealogy canvas models, layouts, persistence, and exports."""

from __future__ import annotations

import json
import re
import struct
import zlib
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
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


LAYOUT_MODES = (
    "top_to_bottom", "bottom_to_top", "left_to_right", "right_to_left",
    "ancestors_only", "descendants_only", "hourglass", "fan", "compact_family_groups",
)
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


@dataclass(frozen=True)
class TreeLayoutOptions:
    layout_type: str = "hourglass"
    horizontal_spacing: float = H_GAP
    vertical_spacing: float = V_GAP
    card_width: float = CARD_WIDTH
    card_height: float = CARD_HEIGHT
    compact: bool = False
    line_routing: str = "orthogonal"


@dataclass(frozen=True)
class TreeLayoutPreview:
    positions: dict[int, tuple[float, float]]
    moved_node_count: int
    overlap_count: int
    edge_crossing_count: int
    pinned_nodes: frozenset[int]
    options: TreeLayoutOptions


@dataclass(frozen=True)
class TreeLayoutResult:
    path: Path
    before_payload: dict
    after_payload: dict
    preview: TreeLayoutPreview


class TreeCanvasLayoutCommand:
    """Undo/redo one layout-file update without touching genealogy tables."""

    name = "Автораскладка дерева"

    def __init__(self, result: TreeLayoutResult) -> None:
        self.result = result

    @property
    def has_effect(self):
        return self.result.before_payload != self.result.after_payload

    def undo(self):
        if self.result.before_payload:
            TreeCanvasService._write_layout_payload(self.result.path, self.result.before_payload)
        elif self.result.path.exists():
            self.result.path.unlink()

    def redo(self):
        TreeCanvasService._write_layout_payload(self.result.path, self.result.after_payload)


class TreeAutoLayoutEngine:
    """Pure, deterministic, generation-aware geometry for visible canvas nodes."""

    def layout(
        self,
        nodes: Iterable[TreeCanvasNode],
        connectors: Iterable[TreeCanvasConnector],
        options: TreeLayoutOptions | None = None,
        *,
        pinned_positions=None,
        previous_positions=None,
        progress_callback=None,
        cancel_callback=None,
    ) -> dict[int, tuple[float, float]]:
        options = options or TreeLayoutOptions()
        if options.layout_type not in LAYOUT_MODES:
            raise ValueError("Неизвестный режим автораскладки")
        node_list = tuple(sorted(nodes, key=lambda node: (node.generation, node.person_id)))
        connector_list = tuple(connectors)
        pinned = {int(key): (float(value[0]), float(value[1])) for key, value in (pinned_positions or {}).items()}
        node_ids = {node.person_id for node in node_list}
        pinned = {key: value for key, value in pinned.items() if key in node_ids}
        layers = defaultdict(list)
        for node in node_list:
            layers[node.generation].append(node.person_id)
        spouse_map, parent_map = defaultdict(set), defaultdict(set)
        for connector in connector_list:
            if connector.kind == "spouse":
                spouse_map[connector.source_id].add(connector.target_id)
                spouse_map[connector.target_id].add(connector.source_id)
            elif connector.kind == "parent":
                parent_map[connector.target_id].add(connector.source_id)
        sibling_groups = defaultdict(list)
        for person_id, parents in parent_map.items():
            sibling_groups[tuple(sorted(parents))].append(person_id)
        sibling_index = {
            person_id: (tuple(sorted(parents)), index)
            for parents, people in sibling_groups.items()
            for index, person_id in enumerate(sorted(people))
        }
        gap_x = max(8.0, float(options.horizontal_spacing) * (0.7 if options.compact else 1.0))
        gap_y = max(8.0, float(options.vertical_spacing) * (0.7 if options.compact else 1.0))
        width, height = max(40.0, float(options.card_width)), max(30.0, float(options.card_height))
        positions = dict(pinned)
        occupied = [(person_id, x, y, x + width, y + height) for person_id, (x, y) in pinned.items()]
        previous_positions = previous_positions or {}
        generations = sorted(layers)
        for layer_index, generation in enumerate(generations):
            if cancel_callback:
                cancel_callback()
            ordered = self._order_layer(layers[generation], spouse_map, parent_map, positions, previous_positions)
            cursor = 90.0
            y = 90.0 + layer_index * (height + gap_y)
            for person_id in ordered:
                if person_id in positions:
                    continue
                preferred = cursor
                parent_key, index = sibling_index.get(person_id, ((), 0))
                parent_centers = [positions[parent][0] + width / 2 for parent in parent_key if parent in positions]
                siblings = sibling_groups.get(parent_key, ())
                if parent_centers and siblings:
                    group_width = len(siblings) * width + max(0, len(siblings) - 1) * gap_x
                    preferred = sum(parent_centers) / len(parent_centers) - group_width / 2 + index * (width + gap_x)
                x = self._next_available(max(cursor, preferred), y, width, height, gap_x, occupied)
                positions[person_id] = (x, y)
                occupied.append((person_id, x, y, x + width, y + height))
                cursor = x + width + gap_x
            if progress_callback:
                progress_callback("Автораскладка дерева", layer_index + 1, len(generations))
        if not pinned:
            positions = self._orient(positions, options, width, height)
        positions.update(pinned)
        return {person_id: (round(x, 2), round(y, 2)) for person_id, (x, y) in positions.items()}

    @staticmethod
    def _order_layer(person_ids, spouse_map, parent_map, positions, previous_positions):
        remaining, groups = set(person_ids), []
        while remaining:
            start = min(remaining)
            group, queue = set(), deque([start])
            while queue:
                person_id = queue.popleft()
                if person_id in group or person_id not in remaining:
                    continue
                group.add(person_id)
                queue.extend(spouse for spouse in spouse_map[person_id] if spouse in remaining)
            remaining -= group
            groups.append(tuple(sorted(group)))
        def group_key(group):
            parent_centers = [positions[parent][0] for person in group for parent in parent_map[person] if parent in positions]
            prior = [previous_positions[person][0] for person in group if person in previous_positions]
            return (sum(parent_centers) / len(parent_centers) if parent_centers else sum(prior) / len(prior) if prior else min(group), min(group))
        return tuple(person_id for group in sorted(groups, key=group_key) for person_id in group)

    @staticmethod
    def _next_available(cursor, y, width, height, gap, occupied):
        x = cursor
        while True:
            conflicts = [item for item in occupied if x < item[3] and x + width > item[1] and y < item[4] and y + height > item[2]]
            if not conflicts:
                return x
            x = max(item[3] for item in conflicts) + gap

    @staticmethod
    def _orient(positions, options, width, height):
        if not positions or options.layout_type in {"top_to_bottom", "ancestors_only", "descendants_only", "hourglass", "compact_family_groups"}:
            return positions
        minimum_x, maximum_x = min(x for x, _y in positions.values()), max(x for x, _y in positions.values())
        minimum_y, maximum_y = min(y for _x, y in positions.values()), max(y for _x, y in positions.values())
        gap_x = max(8.0, float(options.horizontal_spacing) * (0.7 if options.compact else 1.0))
        gap_y = max(8.0, float(options.vertical_spacing) * (0.7 if options.compact else 1.0))
        if options.layout_type == "bottom_to_top":
            return {person_id: (x, maximum_y - (y - minimum_y)) for person_id, (x, y) in positions.items()}
        if options.layout_type == "left_to_right":
            return {
                person_id: (
                    90 + (y - minimum_y) * (width + gap_x) / (height + gap_y),
                    90 + (x - minimum_x) * (height + gap_y) / (width + gap_x),
                ) for person_id, (x, y) in positions.items()
            }
        if options.layout_type == "right_to_left":
            return {
                person_id: (
                    90 + (maximum_y - y) * (width + gap_x) / (height + gap_y),
                    90 + (x - minimum_x) * (height + gap_y) / (width + gap_x),
                ) for person_id, (x, y) in positions.items()
            }
        if options.layout_type == "fan":
            ordered = sorted(positions)
            return {
                person_id: (90 + index * (width + 24), 90 + abs(index - len(ordered) / 2) * (height * 0.45))
                for index, person_id in enumerate(ordered)
            }
        return positions


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

    def preview_auto_layout(
        self,
        model: TreeCanvasModel,
        *,
        positions=None,
        pinned_nodes=(),
        options: TreeLayoutOptions | None = None,
        progress_callback=None,
        cancel_callback=None,
    ) -> TreeLayoutPreview:
        options = options or TreeLayoutOptions(layout_type=model.mode)
        current = {int(key): (float(value[0]), float(value[1])) for key, value in (positions or model.positions).items()}
        pinned_ids = frozenset(int(value) for value in pinned_nodes)
        pinned = {person_id: current[person_id] for person_id in pinned_ids if person_id in current}
        calculated = TreeAutoLayoutEngine().layout(
            model.nodes, model.connectors, options, pinned_positions=pinned,
            previous_positions=current, progress_callback=progress_callback, cancel_callback=cancel_callback,
        )
        return TreeLayoutPreview(
            calculated,
            sum(1 for person_id, point in calculated.items() if current.get(person_id) != point),
            self._overlap_count(calculated, options.card_width, options.card_height),
            self._edge_crossing_count(model.connectors, calculated, options.card_width, options.card_height),
            pinned_ids, options,
        )

    def apply_auto_layout(self, model: TreeCanvasModel, preview: TreeLayoutPreview, *, name="default", scale=1.0) -> TreeLayoutResult:
        path = self._named_layout_path(model.center_id, name)
        before = self._read_layout_payload(path)
        created = before.get("metadata", {}).get("created_time") or self._timestamp()
        payload = self._layout_payload(
            preview.positions, model.center_id, name, preview.options, model.ancestor_depth,
            model.descendant_depth, scale, preview.pinned_nodes, created,
        )
        self._write_layout_payload(path, payload)
        return TreeLayoutResult(path, before, payload, preview)

    def save_named_layout(self, name, model: TreeCanvasModel, positions, *, pinned_nodes=(), options=None, scale=1.0) -> Path:
        options = options or TreeLayoutOptions(layout_type=model.mode)
        path = self._named_layout_path(model.center_id, name)
        before = self._read_layout_payload(path)
        created = before.get("metadata", {}).get("created_time") or self._timestamp()
        self._write_layout_payload(path, self._layout_payload(
            positions, model.center_id, name, options, model.ancestor_depth,
            model.descendant_depth, scale, pinned_nodes, created,
        ))
        return path

    def list_named_layouts(self, center_id):
        prefix = f"tree_{int(center_id)}"
        records = []
        for path in sorted(self.layout_dir.glob(f"{prefix}*.json")):
            payload = self._read_layout_payload(path)
            metadata = payload.get("metadata", {})
            records.append({"path": path, "name": metadata.get("name", "default"), **metadata})
        return records

    def load_named_layout(self, center_id, name="default", visible_ids=None):
        payload = self._read_layout_payload(self._named_layout_path(center_id, name))
        positions = self._payload_positions(payload, visible_ids)
        return positions, frozenset(int(value) for value in payload.get("metadata", {}).get("pinned_nodes", ())), payload.get("metadata", {})

    def delete_named_layout(self, center_id, name):
        path = self._named_layout_path(center_id, name)
        if path.exists():
            path.unlink()

    def rename_named_layout(self, center_id, old_name, new_name):
        old_path, new_path = self._named_layout_path(center_id, old_name), self._named_layout_path(center_id, new_name)
        payload = self._read_layout_payload(old_path)
        if not payload:
            raise ValueError("Раскладка не найдена")
        payload.setdefault("metadata", {})["name"] = self._layout_name(new_name)
        payload["metadata"]["modified_time"] = self._timestamp()
        self._write_layout_payload(new_path, payload)
        if old_path != new_path and old_path.exists():
            old_path.unlink()
        return new_path

    def duplicate_named_layout(self, center_id, source_name, target_name):
        source = self._read_layout_payload(self._named_layout_path(center_id, source_name))
        if not source:
            raise ValueError("Раскладка не найдена")
        source.setdefault("metadata", {})["name"] = self._layout_name(target_name)
        source["metadata"]["created_time"] = self._timestamp()
        source["metadata"]["modified_time"] = self._timestamp()
        target = self._named_layout_path(center_id, target_name)
        self._write_layout_payload(target, source)
        return target

    def export_layout_configuration(self, center_id, name, destination_path):
        payload = self._read_layout_payload(self._named_layout_path(center_id, name))
        if not payload:
            raise ValueError("Раскладка не найдена")
        destination = Path(destination_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return destination

    def import_layout_configuration(self, destination_name, source_path, *, center_id=None):
        payload = json.loads(Path(source_path).expanduser().read_text(encoding="utf-8"))
        metadata = payload.setdefault("metadata", {})
        resolved_center = int(center_id if center_id is not None else metadata.get("centered_person"))
        metadata["name"] = self._layout_name(destination_name)
        metadata["centered_person"] = resolved_center
        metadata["modified_time"] = self._timestamp()
        destination = self._named_layout_path(resolved_center, destination_name)
        self._write_layout_payload(destination, payload)
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
        positions = TreeAutoLayoutEngine().layout(nodes, connectors, TreeLayoutOptions(layout_type=mode), cancel_callback=cancel_callback)
        positions.update(self.load_positions(center_id, set(visible)))
        if progress_callback:
            progress_callback("Разметка дерева", len(nodes), len(nodes))
        return TreeCanvasModel(center_id, nodes, connectors, positions, mode, ancestor_depth, descendant_depth, collapsed)

    def save_positions(self, center_id, positions) -> Path:
        path = self._layout_path(center_id)
        existing = self._read_layout_payload(path)
        metadata = existing.get("metadata", {})
        payload = self._layout_payload(
            positions, center_id, metadata.get("name", "default"),
            TreeLayoutOptions(layout_type=metadata.get("layout_type", "hourglass")),
            metadata.get("ancestor_depth", 3), metadata.get("descendant_depth", 3),
            metadata.get("scale", 1.0), metadata.get("pinned_nodes", ()),
            metadata.get("created_time") or self._timestamp(),
        )
        self._write_layout_payload(path, payload)
        return path

    def load_positions(self, center_id, visible_ids=None):
        try:
            payload = self._read_layout_payload(self._layout_path(center_id))
        except (OSError, ValueError):
            return {}
        return self._payload_positions(payload, visible_ids)

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

    def _named_layout_path(self, center_id, name="default"):
        name = self._layout_name(name)
        return self._layout_path(center_id) if name == "default" else self.layout_dir / f"tree_{int(center_id)}_{name}.json"

    @staticmethod
    def _layout_name(name):
        normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", str(name or "default").strip()).strip("_")
        if not normalized:
            raise ValueError("Укажите имя раскладки")
        return normalized[:80]

    @staticmethod
    def _timestamp():
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _read_layout_payload(path):
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        if "positions" not in payload:
            return {"positions": payload, "metadata": {"name": "default", "pinned_nodes": []}}
        return payload

    @staticmethod
    def _write_layout_payload(path, payload):
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(destination)

    @staticmethod
    def _payload_positions(payload, visible_ids=None):
        allowed = set(visible_ids) if visible_ids is not None else None
        values = payload.get("positions", payload)
        return {
            int(person_id): (float(value[0]), float(value[1]))
            for person_id, value in values.items()
            if isinstance(value, list) and len(value) == 2 and (allowed is None or int(person_id) in allowed)
        }

    def _layout_payload(self, positions, center_id, name, options, ancestor_depth, descendant_depth, scale, pinned_nodes, created_time):
        now = self._timestamp()
        return {
            "metadata": {
                "name": self._layout_name(name), "centered_person": int(center_id),
                "layout_type": options.layout_type, "ancestor_depth": int(ancestor_depth),
                "descendant_depth": int(descendant_depth), "scale": float(scale),
                "created_time": created_time, "modified_time": now,
                "pinned_nodes": sorted(int(value) for value in pinned_nodes),
                "horizontal_spacing": options.horizontal_spacing, "vertical_spacing": options.vertical_spacing,
                "card_width": options.card_width, "card_height": options.card_height,
                "compact": options.compact, "line_routing": options.line_routing,
            },
            "positions": {
                str(int(person_id)): [round(float(x), 2), round(float(y), 2)]
                for person_id, (x, y) in positions.items()
            },
        }

    @staticmethod
    def _overlap_count(positions, width, height):
        rectangles = [(person_id, x, y, x + width, y + height) for person_id, (x, y) in positions.items()]
        return sum(
            1 for index, left in enumerate(rectangles) for right in rectangles[index + 1:]
            if left[1] < right[3] and left[3] > right[1] and left[2] < right[4] and left[4] > right[2]
        )

    @staticmethod
    def _edge_crossing_count(connectors, positions, width, height):
        segments = []
        for connector in connectors:
            if connector.source_id not in positions or connector.target_id not in positions:
                continue
            source, target = positions[connector.source_id], positions[connector.target_id]
            segments.append((connector.source_id, connector.target_id, source[0] + width / 2, source[1] + height / 2, target[0] + width / 2, target[1] + height / 2))
        crossings = 0
        for index, first in enumerate(segments):
            for second in segments[index + 1:]:
                if {first[0], first[1]} & {second[0], second[1]}:
                    continue
                if TreeCanvasService._segments_cross(first[2:], second[2:]):
                    crossings += 1
        return crossings

    @staticmethod
    def _segments_cross(first, second):
        ax, ay, bx, by = first
        cx, cy, dx, dy = second
        def orient(px, py, qx, qy, rx, ry):
            return (qx - px) * (ry - py) - (qy - py) * (rx - px)
        first_a, first_b = orient(ax, ay, bx, by, cx, cy), orient(ax, ay, bx, by, dx, dy)
        second_a, second_b = orient(cx, cy, dx, dy, ax, ay), orient(cx, cy, dx, dy, bx, by)
        return (first_a > 0) != (first_b > 0) and (second_a > 0) != (second_b > 0)

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
