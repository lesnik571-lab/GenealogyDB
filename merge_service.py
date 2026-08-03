"""Plan, validate, export, and atomically execute duplicate-person merges."""

from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from config import DATA_DIR
from audit_service import AuditService
from database import backup_database
from logging_service import get_logger
from repository.person_repository import PersonRepository
from undo_manager import RepositoryDeltaCommand, TableDelta


SCALAR_FIELDS = (
    "first_name", "last_name", "sex", "birth_date", "birth_place",
    "death_date", "death_place", "occupation", "note",
)


@dataclass(frozen=True)
class ScalarResolution:
    field: str
    primary_value: str
    duplicate_value: str
    choice: str
    result_value: str
    conflicting: bool


@dataclass(frozen=True)
class RelationshipChange:
    family_id: int
    relationship_type: str
    before: str
    after: str
    action: str = "rewire"


@dataclass(frozen=True)
class MergePlan:
    primary: dict[str, Any]
    duplicate: dict[str, Any]
    scalar_resolutions: tuple[ScalarResolution, ...]
    primary_collections: dict[str, tuple[dict[str, Any], ...]]
    duplicate_collections: dict[str, tuple[dict[str, Any], ...]]
    relationship_changes: tuple[RelationshipChange, ...]
    warnings: tuple[str, ...]
    blockers: tuple[str, ...]

    @property
    def can_execute(self) -> bool:
        return not self.blockers


@dataclass(frozen=True)
class MergeExecutionResult:
    primary_id: int
    absorbed_id: int
    absorbed_gedcom_id: str
    backup_path: Path
    delta: dict[str, TableDelta]


class MergeSafetyError(ValueError):
    """Raised when a merge plan violates relationship safety rules."""


class MergeService:
    """Create exact dry-run plans and apply them as one reversible transaction."""

    def __init__(self, repository: PersonRepository, backup_dir=None) -> None:
        self.repository = repository
        self.backup_dir = Path(backup_dir) if backup_dir is not None else DATA_DIR / "backups"
        self.logger = get_logger("merge")

    def plan_merge(
        self,
        primary_id: int,
        duplicate_id: int,
        resolutions: Mapping[str, Any] | None = None,
    ) -> MergePlan:
        primary_id = int(primary_id)
        duplicate_id = int(duplicate_id)
        if primary_id == duplicate_id:
            return self._blocked_identity_plan(primary_id)
        primary = self.repository.get_person_record(primary_id)
        duplicate = self.repository.get_person_record(duplicate_id)
        if primary is None or duplicate is None:
            raise ValueError("Человек не найден")
        blockers = list(self._relationship_blockers(primary_id, duplicate_id))
        scalar_resolutions = tuple(
            self._resolve_scalar(field, primary, duplicate, resolutions or {})
            for field in SCALAR_FIELDS
        )
        primary_collections = self._collections(primary_id)
        duplicate_collections = self._collections(duplicate_id)
        relationship_changes = self._relationship_changes(primary_id, duplicate_id)
        warnings = []
        if any(item.conflicting for item in scalar_resolutions):
            warnings.append("Есть конфликтующие поля; проверьте выбранные значения.")
        return MergePlan(
            primary=self._display_person(primary),
            duplicate=self._display_person(duplicate),
            scalar_resolutions=scalar_resolutions,
            primary_collections=primary_collections,
            duplicate_collections=duplicate_collections,
            relationship_changes=relationship_changes,
            warnings=tuple(warnings),
            blockers=tuple(blockers),
        )

    def execute(self, plan: MergePlan, progress_callback=None) -> MergeExecutionResult:
        if plan.blockers:
            raise MergeSafetyError("; ".join(plan.blockers))
        primary_id = int(plan.primary["id"])
        duplicate_id = int(plan.duplicate["id"])
        current = self.plan_merge(
            primary_id,
            duplicate_id,
            {item.field: (item.choice, item.result_value) for item in plan.scalar_resolutions},
        )
        if current.blockers:
            raise MergeSafetyError("; ".join(current.blockers))
        self._assert_plan_current(plan)
        backup_path = backup_database(self.repository.db_name, self.backup_dir)
        before_state = self.repository.capture_command_state()
        self.logger.info(
            "Merge started: primary_id=%s primary_gedcom=%s absorbed_id=%s absorbed_gedcom=%s backup=%s",
            primary_id, plan.primary.get("gedcom_id", ""), duplicate_id,
            plan.duplicate.get("gedcom_id", ""), backup_path,
        )
        try:
            with self.repository.transaction():
                if progress_callback:
                    progress_callback("Объединение полей", 1, 5)
                self._apply_scalars(plan)
                if progress_callback:
                    progress_callback("Объединение коллекций", 2, 5)
                self._merge_owned_collections(primary_id, duplicate_id)
                if progress_callback:
                    progress_callback("Перенос связей", 3, 5)
                self._rewire_relationships(plan)
                if progress_callback:
                    progress_callback("Удаление дубля", 4, 5)
                self.repository.conn.execute("DELETE FROM people WHERE id = ?", (duplicate_id,))
                self._validate_post_merge(primary_id, duplicate_id, plan.duplicate.get("gedcom_id", ""))
                if progress_callback:
                    progress_callback("Проверка ссылок", 5, 5)
        except Exception:
            self.logger.exception(
                "Merge rolled back: primary_id=%s absorbed_id=%s absorbed_gedcom=%s",
                primary_id, duplicate_id, plan.duplicate.get("gedcom_id", ""),
            )
            raise
        after_state = self.repository.capture_command_state()
        delta = RepositoryDeltaCommand._build_delta(before_state, after_state)
        AuditService.for_database(self.repository.db_name).record_delta(
            "merge",
            delta,
            database_id=primary_id,
            gedcom_id=plan.primary.get("gedcom_id", ""),
            description=(
                f"Карточка ID {duplicate_id} ({plan.duplicate.get('gedcom_id', '')}) "
                f"объединена с ID {primary_id}."
            ),
            service="merge_service",
        )
        self.logger.info(
            "Merge completed: primary_id=%s absorbed_id=%s absorbed_gedcom=%s",
            primary_id, duplicate_id, plan.duplicate.get("gedcom_id", ""),
        )
        return MergeExecutionResult(
            primary_id=primary_id,
            absorbed_id=duplicate_id,
            absorbed_gedcom_id=str(plan.duplicate.get("gedcom_id", "")),
            backup_path=backup_path,
            delta=delta,
        )

    def export_json(self, plan: MergePlan, destination_path) -> Path:
        destination = Path(destination_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(asdict(plan), ensure_ascii=False, indent=2), encoding="utf-8")
        return destination

    def export_csv(self, plan: MergePlan, destination_path) -> Path:
        destination = Path(destination_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8-sig", newline="") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(("section", "record", "field", "before", "after", "status"))
            for item in plan.scalar_resolutions:
                writer.writerow(("scalar", plan.primary["id"], item.field, item.primary_value, item.result_value, item.choice))
            for item in plan.relationship_changes:
                writer.writerow(("relationship", item.family_id, item.relationship_type, item.before, item.after, item.action))
            for warning in plan.warnings:
                writer.writerow(("warning", "", "", "", "", warning))
            for blocker in plan.blockers:
                writer.writerow(("blocker", "", "", "", "", blocker))
        return destination

    def _blocked_identity_plan(self, person_id):
        person = self.repository.get_person_record(person_id)
        if person is None:
            raise ValueError("Человек не найден")
        display = self._display_person(person)
        return MergePlan(display, display, (), {}, {}, (), (), ("Нельзя объединить человека с самим собой.",))

    @staticmethod
    def _display_person(person):
        return {key: person.get(key, "") for key in ("id", "gedcom_id", *SCALAR_FIELDS)}

    def _resolve_scalar(self, field, primary, duplicate, resolutions):
        primary_value = str(primary.get(field) or "")
        duplicate_value = str(duplicate.get(field) or "")
        conflicting = bool(primary_value and duplicate_value and primary_value != duplicate_value)
        configured = resolutions.get(field)
        if configured is None:
            if field == "note" and primary_value and duplicate_value and primary_value != duplicate_value:
                choice, result = "manual", self._combine_notes(primary_value, duplicate_value)
            elif primary_value:
                choice, result = "primary", primary_value
            else:
                choice, result = "duplicate", duplicate_value
        elif isinstance(configured, (tuple, list)):
            choice = str(configured[0])
            manual = str(configured[1]) if len(configured) > 1 else ""
            result = self._resolution_value(choice, primary_value, duplicate_value, manual)
        else:
            choice = str(configured)
            result = self._resolution_value(choice, primary_value, duplicate_value, "")
        return ScalarResolution(field, primary_value, duplicate_value, choice, result, conflicting)

    @staticmethod
    def _resolution_value(choice, primary, duplicate, manual):
        if choice == "primary":
            return primary
        if choice == "duplicate":
            return duplicate
        if choice == "manual":
            return manual
        raise ValueError(f"Неизвестный выбор значения: {choice}")

    @staticmethod
    def _combine_notes(primary, duplicate):
        paragraphs = []
        seen = set()
        for value in (primary, duplicate):
            for paragraph in re.split(r"\n\s*\n", value.strip()):
                key = re.sub(r"\s+", " ", paragraph).casefold()
                if key and key not in seen:
                    seen.add(key)
                    paragraphs.append(paragraph.strip())
        return "\n\n".join(paragraphs)

    def _collections(self, person_id):
        citations = []
        for citation in self.repository.list_citation_records():
            if citation["target_type"] != "person" or citation["target_id"] != str(person_id):
                continue
            citations.append({
                **citation,
                "source": self.repository.get_source_record(citation["source_id"]),
            })
        return {
            "events": tuple(self.repository.list_person_events(person_id)),
            "sources": tuple(self.repository.list_person_sources(person_id)),
            "citations": tuple(citations),
            "attachments": tuple(self.repository.list_person_media(person_id)),
            "parents": tuple(self._relative_records(self.repository.get_parents(person_id))),
            "spouses": tuple(self._relative_records(self.repository.get_spouses(person_id))),
            "children": tuple(self._relative_records(self.repository.get_children(person_id))),
        }

    @staticmethod
    def _relative_records(rows):
        return ({"last_name": row[0] or "", "first_name": row[1] or "", "reference": row[2]} for row in rows)

    def _relationship_blockers(self, primary_id, duplicate_id):
        parents, children = self._pedigree_graph()
        if self._reachable(primary_id, duplicate_id, children) or self._reachable(duplicate_id, primary_id, children):
            yield "Нельзя объединять прямого предка с потомком."
        proposed_children = defaultdict(set)
        for parent_id, child_ids in children.items():
            mapped_parent = primary_id if parent_id == duplicate_id else parent_id
            for child_id in child_ids:
                mapped_child = primary_id if child_id == duplicate_id else child_id
                if mapped_parent == mapped_child:
                    yield "Объединение создаст связь человека с самим собой как родителя или ребёнка."
                proposed_children[mapped_parent].add(mapped_child)
        if self._has_cycle(proposed_children):
            yield "Объединение создаст цикл в родословной."
        for family in self.repository.list_families_raw():
            husband = self._resolve_optional(family["husband_id"])
            wife = self._resolve_optional(family["wife_id"])
            husband = primary_id if husband == duplicate_id else husband
            wife = primary_id if wife == duplicate_id else wife
            if husband is not None and husband == wife:
                yield "Объединение создаст связь человека с самим собой как супруга/партнёра."

    def _pedigree_graph(self):
        children_by_family = defaultdict(set)
        for link in self.repository.list_family_children_raw():
            child_id = self._resolve_optional(link["child_id"])
            if child_id is not None:
                children_by_family[str(link["family_id"])].add(child_id)
        parents = defaultdict(set)
        children = defaultdict(set)
        for family in self.repository.list_families_raw():
            family_keys = {str(family["id"]), str(family["gedcom_id"])}
            family_children = set().union(*(children_by_family[key] for key in family_keys))
            for reference in (family["husband_id"], family["wife_id"]):
                parent_id = self._resolve_optional(reference)
                if parent_id is None:
                    continue
                for child_id in family_children:
                    parents[child_id].add(parent_id)
                    children[parent_id].add(child_id)
        return parents, children

    @staticmethod
    def _reachable(start, target, graph):
        pending = [start]
        visited = set()
        while pending:
            current = pending.pop()
            if current == target and current != start:
                return True
            if current in visited:
                continue
            visited.add(current)
            pending.extend(graph.get(current, ()))
        return False

    @staticmethod
    def _has_cycle(graph):
        visiting = set()
        visited = set()
        def visit(node):
            if node in visiting:
                return True
            if node in visited:
                return False
            visiting.add(node)
            if any(visit(child) for child in graph.get(node, ())):
                return True
            visiting.remove(node)
            visited.add(node)
            return False
        return any(visit(node) for node in set(graph) | {child for values in graph.values() for child in values})

    def _relationship_changes(self, primary_id, duplicate_id):
        changes = []
        for family in self.repository.list_families_raw():
            before = self._family_description(family)
            husband = self._resolve_optional(family["husband_id"])
            wife = self._resolve_optional(family["wife_id"])
            mapped = dict(family)
            if husband == duplicate_id:
                mapped["husband_id"] = self._primary_reference(primary_id)
            if wife == duplicate_id:
                mapped["wife_id"] = self._primary_reference(primary_id)
            after = self._family_description(mapped)
            if before != after:
                changes.append(RelationshipChange(int(family["id"]), family["relationship_type"], before, after))
        for link in self.repository.list_family_children_raw():
            if self._resolve_optional(link["child_id"]) == duplicate_id:
                changes.append(RelationshipChange(0, "parent-child", str(link), f"{link['family_id']} -> {self._primary_reference(primary_id)}"))
        simulated = self._simulated_family_signatures(primary_id, duplicate_id)
        seen = {}
        for family_id, signature in simulated:
            if signature in seen:
                changes.append(RelationshipChange(
                    family_id,
                    signature[2],
                    f"Семья {family_id}",
                    f"Семья {seen[signature]}",
                    "deduplicate",
                ))
            else:
                seen[signature] = family_id
        return tuple(changes)

    def _simulated_family_signatures(self, primary_id, duplicate_id):
        primary_reference = self._primary_reference(primary_id)
        children_by_family = defaultdict(set)
        for link in self.repository.list_family_children_raw():
            child = primary_reference if self._resolve_optional(link["child_id"]) == duplicate_id else str(link["child_id"])
            children_by_family[str(link["family_id"])].add(child)
        result = []
        for family in self.repository.list_families_raw():
            keys = {str(family["id"]), str(family["gedcom_id"])}
            husband = primary_reference if self._resolve_optional(family["husband_id"]) == duplicate_id else str(family["husband_id"])
            wife = primary_reference if self._resolve_optional(family["wife_id"]) == duplicate_id else str(family["wife_id"])
            children = tuple(sorted(set().union(*(children_by_family[key] for key in keys))))
            result.append((int(family["id"]), (husband, wife, str(family["relationship_type"]), children)))
        return result

    @staticmethod
    def _family_description(family):
        return f"{family.get('husband_id', '')} + {family.get('wife_id', '')} [{family.get('relationship_type', 'unknown')}]"

    def _primary_reference(self, primary_id):
        person = self.repository.get_person_record(primary_id)
        return str(person.get("gedcom_id") or primary_id)

    def _resolve_optional(self, reference):
        if not str(reference or "").strip():
            return None
        person_id = self.repository.resolve_person_reference(reference)
        return int(person_id) if person_id is not None else None

    def _assert_plan_current(self, plan):
        for expected in (plan.primary, plan.duplicate):
            current = self.repository.get_person_record(expected["id"])
            if current is None:
                raise RuntimeError("Данные изменились после предварительного просмотра")
            for field in ("gedcom_id", *SCALAR_FIELDS):
                if str(current.get(field) or "") != str(expected.get(field) or ""):
                    raise RuntimeError("Данные изменились после предварительного просмотра")

    def _apply_scalars(self, plan):
        changes = {item.field: item.result_value for item in plan.scalar_resolutions}
        self.repository.update_person_fields(int(plan.primary["id"]), changes)

    def _merge_owned_collections(self, primary_id, duplicate_id):
        event_map = self._merge_owned_table(
            "person_events", primary_id, duplicate_id,
            ("event_type", "event_date", "event_place", "description"),
        )
        for duplicate_event_id, primary_event_id in event_map.items():
            self.repository.conn.execute(
                "UPDATE citations SET target_id = ? WHERE target_type = 'event' AND target_id = ?",
                (str(primary_event_id), str(duplicate_event_id)),
            )
        self._merge_owned_table(
            "person_sources", primary_id, duplicate_id,
            ("title", "source_url", "archive_reference", "note"),
        )
        self._merge_owned_table(
            "person_media", primary_id, duplicate_id,
            ("media_type", "title", "file_path", "description"),
        )
        self.repository.conn.execute(
            "UPDATE citations SET target_id = ? WHERE target_type = 'person' AND target_id IN (?, ?)",
            (str(primary_id), str(duplicate_id), str(self.repository.get_person_record(duplicate_id).get("gedcom_id") or "")),
        )
        self._deduplicate_citations()

    def _merge_owned_table(self, table, primary_id, duplicate_id, columns):
        column_sql = ", ".join(columns)
        primary_rows = self.repository.conn.execute(
            f"SELECT id, {column_sql} FROM {table} WHERE person_id = ? ORDER BY id", (primary_id,)
        ).fetchall()
        duplicate_rows = self.repository.conn.execute(
            f"SELECT id, {column_sql} FROM {table} WHERE person_id = ? ORDER BY id", (duplicate_id,)
        ).fetchall()
        signatures = {tuple(self._normalized(value) for value in row[1:]): row[0] for row in primary_rows}
        merged_ids = {}
        for row in duplicate_rows:
            signature = tuple(self._normalized(value) for value in row[1:])
            if signature in signatures:
                merged_ids[row[0]] = signatures[signature]
                self.repository.conn.execute(f"DELETE FROM {table} WHERE id = ?", (row[0],))
            else:
                self.repository.conn.execute(f"UPDATE {table} SET person_id = ? WHERE id = ?", (primary_id, row[0]))
                signatures[signature] = row[0]
        return merged_ids

    def _rewire_relationships(self, plan):
        primary_id = int(plan.primary["id"])
        duplicate_id = int(plan.duplicate["id"])
        primary_reference = str(plan.primary.get("gedcom_id") or primary_id)
        duplicate_references = {str(duplicate_id), str(plan.duplicate.get("gedcom_id") or "")}
        duplicate_references.discard("")
        for reference in duplicate_references:
            for column in ("husband_id", "wife_id"):
                self.repository.conn.execute(
                    f"UPDATE families SET {column} = ? WHERE {column} = ?",
                    (primary_reference, reference),
                )
            self.repository.conn.execute(
                "UPDATE family_children SET child_id = ? WHERE child_id = ?",
                (primary_reference, reference),
            )
        self._deduplicate_family_children()
        self._deduplicate_families()

    def _deduplicate_family_children(self):
        rows = self.repository.conn.execute(
            "SELECT rowid, family_id, child_id FROM family_children ORDER BY rowid"
        ).fetchall()
        seen = set()
        for rowid, family_id, child_id in rows:
            signature = (family_id, child_id)
            if signature in seen:
                self.repository.conn.execute("DELETE FROM family_children WHERE rowid = ?", (rowid,))
            else:
                seen.add(signature)

    def _deduplicate_families(self):
        families = self.repository.list_families_raw()
        child_rows = self.repository.list_family_children_raw()
        children_by_family = defaultdict(set)
        for child in child_rows:
            children_by_family[str(child["family_id"])].add(str(child["child_id"]))
        seen = {}
        for family in families:
            keys = {str(family["id"]), str(family["gedcom_id"])}
            children = tuple(sorted(set().union(*(children_by_family[key] for key in keys))))
            signature = (
                str(family["husband_id"]), str(family["wife_id"]),
                str(family["relationship_type"]), children,
            )
            if signature not in seen:
                seen[signature] = family
                continue
            keep = seen[signature]
            duplicate_aliases = {str(family["id"]), str(family["gedcom_id"])} - {""}
            for alias in duplicate_aliases:
                self.repository.conn.execute(
                    "UPDATE citations SET target_id = ? WHERE target_type IN ('family', 'relationship') AND target_id = ?",
                    (str(keep["id"]), alias),
                )
                self.repository.conn.execute("DELETE FROM family_children WHERE family_id = ?", (alias,))
            self.repository.conn.execute("DELETE FROM families WHERE id = ?", (family["id"],))
        self._deduplicate_citations()

    def _deduplicate_citations(self):
        rows = self.repository.conn.execute(
            "SELECT id, source_id, target_type, target_id, page, quality, transcription, comment FROM citations ORDER BY id"
        ).fetchall()
        seen = set()
        for row in rows:
            signature = tuple(self._normalized(value) for value in row[1:])
            if signature in seen:
                self.repository.conn.execute("DELETE FROM citations WHERE id = ?", (row[0],))
            else:
                seen.add(signature)

    def _validate_post_merge(self, primary_id, duplicate_id, duplicate_gedcom_id):
        if self.repository.get_person_record(duplicate_id) is not None:
            raise RuntimeError("Поглощённая карточка не удалена")
        references = {str(duplicate_id), str(duplicate_gedcom_id or "")} - {""}
        for reference in references:
            family_count = self.repository.conn.execute(
                "SELECT COUNT(*) FROM families WHERE husband_id = ? OR wife_id = ?", (reference, reference)
            ).fetchone()[0]
            child_count = self.repository.conn.execute(
                "SELECT COUNT(*) FROM family_children WHERE child_id = ?", (reference,)
            ).fetchone()[0]
            citation_count = self.repository.conn.execute(
                "SELECT COUNT(*) FROM citations WHERE target_type = 'person' AND target_id = ?", (reference,)
            ).fetchone()[0]
            if family_count or child_count or citation_count:
                raise RuntimeError("После объединения остались ссылки на поглощённую карточку")
        foreign_key_errors = self.repository.conn.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_errors:
            raise RuntimeError("После объединения обнаружены повреждённые ссылки")
        _parents, children = self._pedigree_graph()
        if any(parent == child for parent, child_ids in children.items() for child in child_ids):
            raise MergeSafetyError("После объединения создана связь человека с самим собой")
        if self._has_cycle(children):
            raise MergeSafetyError("После объединения создан цикл в родословной")
        for family in self.repository.list_families_raw():
            husband = self._resolve_optional(family["husband_id"])
            wife = self._resolve_optional(family["wife_id"])
            if husband is not None and husband == wife:
                raise MergeSafetyError("После объединения создана связь человека с самим собой как супруга")

    @staticmethod
    def _normalized(value):
        return re.sub(r"\s+", " ", str(value or "").strip()).casefold()