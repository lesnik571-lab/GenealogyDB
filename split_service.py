"""Plan, validate, export, and atomically split one person into two."""

from __future__ import annotations

import csv
import json
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from audit_service import AuditService
from config import DATA_DIR
from database import backup_database
from logging_service import get_logger
from repository.person_repository import PersonRepository
from undo_manager import RepositoryDeltaCommand, TableDelta


SPLIT_FIELDS = (
    "first_name", "last_name", "sex", "birth_date", "birth_place",
    "death_date", "death_place", "occupation", "note",
)
COLLECTION_KEYS = ("events", "sources", "citations", "attachments")


@dataclass(frozen=True)
class SplitRelationship:
    key: str
    category: str
    family_id: int
    relationship_type: str
    before: str
    after: str
    selected: bool
    implicit_effects: tuple[str, ...] = ()


@dataclass(frozen=True)
class SplitPlan:
    source: dict[str, Any]
    source_preview: dict[str, Any]
    new_person_preview: dict[str, Any]
    collections: dict[str, tuple[dict[str, Any], ...]]
    relationships: tuple[SplitRelationship, ...]
    selection: dict[str, tuple[Any, ...]]
    warnings: tuple[str, ...]
    blockers: tuple[str, ...]

    @property
    def can_execute(self) -> bool:
        return not self.blockers


@dataclass(frozen=True)
class SplitExecutionResult:
    source_id: int
    new_person_id: int
    backup_path: Path
    delta: dict[str, TableDelta]


class SplitSafetyError(ValueError):
    """Raised when a split plan would violate relationship safety."""


class SplitService:
    """Build read-only split plans and apply one plan as a reversible transaction."""

    def __init__(self, repository: PersonRepository, backup_dir=None) -> None:
        self.repository = repository
        self.backup_dir = Path(backup_dir) if backup_dir is not None else DATA_DIR / "backups"
        self.logger = get_logger("split")

    def plan_split(
        self,
        source_id: int,
        selection: Mapping[str, Any] | None = None,
        *,
        source_values: Mapping[str, Any] | None = None,
        new_values: Mapping[str, Any] | None = None,
    ) -> SplitPlan:
        source_id = int(source_id)
        source = self.repository.get_person_record(source_id)
        if source is None:
            raise ValueError("Человек не найден")
        normalized = self._normalize_selection(selection or {})
        source_preview, new_preview = self._scalar_previews(
            source, normalized["fields"], source_values or {}, new_values or {}
        )
        collections = self._collections(source_id, normalized)
        relationships = self._relationships(source_id, normalized["relationships"])
        blockers = list(self._selection_blockers(normalized, collections, relationships))
        blockers.extend(self._relationship_blockers(source_id, relationships))
        if not source_preview["first_name"] or not source_preview["last_name"]:
            blockers.append("У исходной карточки должны остаться имя и фамилия.")
        if not new_preview["first_name"] or not new_preview["last_name"]:
            blockers.append("Для новой карточки обязательны имя и фамилия.")
        warnings = []
        if any(item.implicit_effects for item in relationships if item.selected):
            warnings.append("Некоторые связи имеют показанные связанные изменения семей.")
        if not any(normalized[key] for key in normalized):
            warnings.append("Не выбраны данные для переноса; будет создана только новая карточка.")
        return SplitPlan(
            source=self._display_person(source),
            source_preview=source_preview,
            new_person_preview=new_preview,
            collections=collections,
            relationships=relationships,
            selection=normalized,
            warnings=tuple(warnings),
            blockers=tuple(dict.fromkeys(blockers)),
        )

    def execute(self, plan: SplitPlan, progress_callback=None) -> SplitExecutionResult:
        if plan.blockers:
            raise SplitSafetyError("; ".join(plan.blockers))
        self._assert_plan_current(plan)
        source_id = int(plan.source["id"])
        backup_path = backup_database(self.repository.db_name, self.backup_dir)
        before_state = self.repository.capture_command_state()
        self.logger.info("Split started: source_id=%s backup=%s", source_id, backup_path)
        try:
            with self.repository.transaction():
                if progress_callback:
                    progress_callback("Создание второй карточки", 1, 5)
                new_person_id = self.repository.create_person({
                    "gedcom_id": None,
                    **{field: plan.new_person_preview[field] for field in SPLIT_FIELDS},
                })
                new_reference = str(new_person_id)
                self.repository.update_person_fields(
                    source_id,
                    {field: plan.source_preview[field] for field in SPLIT_FIELDS},
                )
                if progress_callback:
                    progress_callback("Перенос событий и материалов", 2, 5)
                self._move_collections(plan, source_id, new_person_id)
                if progress_callback:
                    progress_callback("Перенос связей", 3, 5)
                self._move_relationships(plan, source_id, new_reference)
                if progress_callback:
                    progress_callback("Проверка безопасности", 4, 5)
                self._validate_post_split(source_id, new_person_id)
                if progress_callback:
                    progress_callback("Завершение", 5, 5)
        except Exception:
            self.logger.exception("Split rolled back: source_id=%s", source_id)
            raise
        after_state = self.repository.capture_command_state()
        delta = RepositoryDeltaCommand._build_delta(before_state, after_state)
        AuditService.for_database(self.repository.db_name).record_delta(
            "split",
            delta,
            database_id=(source_id, new_person_id),
            gedcom_id=plan.source.get("gedcom_id", ""),
            description=f"Карточка ID {source_id} разделена; создана карточка ID {new_person_id}.",
            service="split_service",
        )
        self.logger.info("Split completed: source_id=%s new_person_id=%s", source_id, new_person_id)
        return SplitExecutionResult(source_id, new_person_id, backup_path, delta)

    def export_json(self, plan: SplitPlan, destination_path) -> Path:
        destination = Path(destination_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            json.dumps(asdict(plan), ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return destination

    def export_csv(self, plan: SplitPlan, destination_path) -> Path:
        destination = Path(destination_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(("section", "record", "field", "before", "source_after", "new_after", "selected"))
            for field in SPLIT_FIELDS:
                writer.writerow((
                    "field", plan.source["id"], field, plan.source.get(field, ""),
                    plan.source_preview[field], plan.new_person_preview[field],
                    field in plan.selection["fields"],
                ))
            for collection, records in plan.collections.items():
                for record in records:
                    writer.writerow((
                        collection, record.get("id", ""), "", json.dumps(record, ensure_ascii=False, default=str),
                        "", "new person" if record["selected"] else "source person", record["selected"],
                    ))
            for relation in plan.relationships:
                writer.writerow((
                    "relationship", relation.key, relation.category, relation.before,
                    "", relation.after, relation.selected,
                ))
            for blocker in plan.blockers:
                writer.writerow(("blocker", "", "", blocker, "", "", False))
        return destination

    def _normalize_selection(self, selection):
        aliases = {
            "event_ids": "events", "source_ids": "sources",
            "citation_ids": "citations", "attachment_ids": "attachments",
            "relationship_keys": "relationships", "move_fields": "fields",
        }
        values = {key: selection.get(key, selection.get(next((alias for alias, target in aliases.items() if target == key), ""), ())) for key in ("fields", *COLLECTION_KEYS, "relationships")}
        fields = tuple(dict.fromkeys(str(field) for field in values["fields"]))
        invalid_fields = set(fields) - set(SPLIT_FIELDS)
        if invalid_fields:
            raise ValueError(f"Неизвестные поля разделения: {', '.join(sorted(invalid_fields))}")
        return {
            "fields": fields,
            "events": tuple(dict.fromkeys(int(value) for value in values["events"])),
            "sources": tuple(dict.fromkeys(int(value) for value in values["sources"])),
            "citations": tuple(dict.fromkeys(int(value) for value in values["citations"])),
            "attachments": tuple(dict.fromkeys(int(value) for value in values["attachments"])),
            "relationships": tuple(dict.fromkeys(str(value) for value in values["relationships"])),
        }

    def _scalar_previews(self, source, move_fields, source_values, new_values):
        source_preview = {}
        new_preview = {}
        for field in SPLIT_FIELDS:
            value = str(source.get(field) or "")
            source_preview[field] = "" if field in move_fields else value
            new_preview[field] = value if field in move_fields else ""
            if field in source_values:
                source_preview[field] = str(source_values[field] or "")
            if field in new_values:
                new_preview[field] = str(new_values[field] or "")
        return source_preview, new_preview

    def _collections(self, source_id, selection):
        person_citations = [
            record for record in self.repository.list_citation_records()
            if record["target_type"] == "person"
            and record["target_id"] in self._person_references(source_id)
        ]
        raw = {
            "events": self.repository.list_person_events(source_id),
            "sources": self.repository.list_person_sources(source_id),
            "citations": person_citations,
            "attachments": self.repository.list_person_media(source_id),
        }
        return {
            key: tuple({**record, "selected": int(record["id"]) in selection[key]} for record in records)
            for key, records in raw.items()
        }

    def _relationships(self, source_id, selected_keys):
        selected = set(selected_keys)
        source_refs = self._person_references(source_id)
        families = self.repository.list_families_raw()
        children = self.repository.list_family_children_raw()
        children_by_family = defaultdict(list)
        for child in children:
            children_by_family[str(child["family_id"])].append(str(child["child_id"]))
        relationships = []
        for child in children:
            if str(child["child_id"]) not in source_refs:
                continue
            family = self._family_for_reference(child["family_id"], families)
            if not family:
                continue
            key = f"parent:{family['id']}:{child['family_id']}"
            relationships.append(SplitRelationship(
                key, "parents", int(family["id"]), family["relationship_type"],
                f"{child['family_id']} -> {child['child_id']}",
                f"{child['family_id']} -> новая карточка", key in selected,
            ))
        for family in families:
            family_keys = {str(family["id"]), str(family.get("gedcom_id") or "")} - {""}
            family_children = sorted({child for key in family_keys for child in children_by_family.get(key, ())})
            for column in ("husband_id", "wife_id"):
                if str(family.get(column) or "") not in source_refs:
                    continue
                category = "partners" if family["relationship_type"] == "civil_partner" else "spouses"
                key = f"adult:{family['id']}:{column}"
                effects = tuple(
                    f"Родительская связь с ребёнком {child} перейдёт новой карточке."
                    for child in family_children
                )
                relationships.append(SplitRelationship(
                    key, category, int(family["id"]), family["relationship_type"],
                    self._family_description(family),
                    self._family_description({**family, column: "новая карточка"}),
                    key in selected, effects,
                ))
                for child in family_children:
                    child_key = f"child:{family['id']}:{child}"
                    relationships.append(SplitRelationship(
                        child_key, "children", int(family["id"]), family["relationship_type"],
                        f"Ребёнок {child} в семье {family['id']} исходной карточки",
                        f"Ребёнок {child} в копии семьи с новой карточкой",
                        child_key in selected,
                        ("Будет создана копия семьи с тем же вторым родителем и типом связи.",),
                    ))
        return tuple(relationships)

    def _selection_blockers(self, selection, collections, relationships):
        for key in COLLECTION_KEYS:
            available = {int(record["id"]) for record in collections[key]}
            missing = set(selection[key]) - available
            if missing:
                yield f"Выбраны недоступные записи {key}: {', '.join(map(str, sorted(missing)))}."
        available_relationships = {item.key for item in relationships}
        missing_relationships = set(selection["relationships"]) - available_relationships
        if missing_relationships:
            yield "Выбраны недоступные родственные связи."
        selected_adult_families = {
            item.family_id for item in relationships if item.selected and item.key.startswith("adult:")
        }
        for item in relationships:
            if item.selected and item.key.startswith("child:") and item.family_id in selected_adult_families:
                yield "Нельзя одновременно переносить всю семейную роль и отдельного ребёнка из той же семьи."

    def _relationship_blockers(self, source_id, relationships):
        selected = [item for item in relationships if item.selected]
        graph, changed_children, self_relationship = self._simulate_pedigree(source_id, selected)
        blockers = []
        if self._has_cycle(graph):
            blockers.append("Разделение создаст цикл в родословной.")
        if self_relationship:
            blockers.append("Разделение создаст связь человека с самим собой как супруга/партнёра.")
        for child, parents in changed_children.items():
            if not parents:
                blockers.append(f"Ребёнок {child} останется без родителя.")
            if child in parents:
                blockers.append("Разделение создаст связь человека с самим собой как родителя или ребёнка.")
        return tuple(blockers)

    def _simulate_pedigree(self, source_id, selected):
        new_node = -1
        families = {int(item["id"]): dict(item) for item in self.repository.list_families_raw()}
        children = self.repository.list_family_children_raw()
        selected_adults = defaultdict(set)
        selected_parent_families = set()
        selected_children = defaultdict(set)
        for item in selected:
            parts = item.key.split(":", 2)
            if parts[0] == "adult":
                selected_adults[int(parts[1])].add(parts[2])
            elif parts[0] == "parent":
                selected_parent_families.add(int(parts[1]))
            elif parts[0] == "child":
                selected_children[int(parts[1])].add(parts[2])
        graph = defaultdict(set)
        changed = defaultdict(set)
        self_relationship = False
        for family_id, family in families.items():
            family_keys = {str(family_id), str(family.get("gedcom_id") or "")} - {""}
            family_children = [str(row["child_id"]) for row in children if str(row["family_id"]) in family_keys]
            adults = []
            for column in ("husband_id", "wife_id"):
                person_id = self._resolve_optional(family.get(column))
                if column in selected_adults.get(family_id, set()):
                    person_id = new_node
                if person_id is not None:
                    adults.append(person_id)
            if len(adults) == 2 and adults[0] == adults[1]:
                self_relationship = True
            for child_ref in family_children:
                child_id = self._resolve_optional(child_ref)
                if child_id is None:
                    continue
                if family_id in selected_parent_families and child_id == source_id:
                    child_id = new_node
                    changed[child_id]
                if child_ref in selected_children.get(family_id, set()):
                    clone_adults = [new_node if adult == source_id else adult for adult in adults]
                    changed[child_id]
                    if len(clone_adults) == 2 and clone_adults[0] == clone_adults[1]:
                        self_relationship = True
                    for adult in clone_adults:
                        graph[adult].add(child_id)
                        changed[child_id].add(adult)
                    continue
                for adult in adults:
                    graph[adult].add(child_id)
                    if family_id in selected_adults or family_id in selected_parent_families:
                        changed[child_id].add(adult)
        return graph, changed, self_relationship

    def _move_collections(self, plan, source_id, new_person_id):
        table_map = {
            "events": "person_events", "sources": "person_sources", "attachments": "person_media",
        }
        for key, table in table_map.items():
            selected = plan.selection[key]
            for record_id in selected:
                self.repository.conn.execute(
                    f"UPDATE {table} SET person_id = ? WHERE id = ? AND person_id = ?",
                    (new_person_id, record_id, source_id),
                )
        source_refs = self._person_references(source_id)
        for citation_id in plan.selection["citations"]:
            placeholders = ", ".join("?" for _ in source_refs)
            self.repository.conn.execute(
                f"UPDATE citations SET target_id = ? WHERE id = ? AND target_type = 'person' AND target_id IN ({placeholders})",
                (str(new_person_id), citation_id, *source_refs),
            )

    def _move_relationships(self, plan, source_id, new_reference):
        selected = [item for item in plan.relationships if item.selected]
        selected_adults = {item.family_id: item for item in selected if item.key.startswith("adult:")}
        for item in selected:
            parts = item.key.split(":", 2)
            if parts[0] == "adult":
                column = parts[2]
                self.repository.conn.execute(
                    f"UPDATE families SET {column} = ? WHERE id = ?",
                    (new_reference, item.family_id),
                )
            elif parts[0] == "parent":
                family = self._family_by_id(item.family_id)
                for source_ref in self._person_references(source_id):
                    for family_ref in self._family_references(family):
                        self.repository.conn.execute(
                            "UPDATE family_children SET child_id = ? WHERE family_id = ? AND child_id = ?",
                            (new_reference, family_ref, source_ref),
                        )
        child_moves = defaultdict(list)
        for item in selected:
            if item.key.startswith("child:") and item.family_id not in selected_adults:
                child_moves[item.family_id].append(item.key.split(":", 2)[2])
        for family_id, child_refs in child_moves.items():
            self._clone_family_for_children(family_id, child_refs, source_id, new_reference)

    def _clone_family_for_children(self, family_id, child_refs, source_id, new_reference):
        family = self._family_by_id(family_id)
        clone_reference = f"SPLIT-{uuid.uuid4().hex}"
        source_refs = self._person_references(source_id)
        husband = new_reference if str(family.get("husband_id") or "") in source_refs else family.get("husband_id") or ""
        wife = new_reference if str(family.get("wife_id") or "") in source_refs else family.get("wife_id") or ""
        self.repository.conn.execute(
            "INSERT INTO families (gedcom_id, husband_id, wife_id, relationship_type) VALUES (?, ?, ?, ?)",
            (clone_reference, husband, wife, family["relationship_type"]),
        )
        for child_ref in child_refs:
            for family_ref in self._family_references(family):
                self.repository.conn.execute(
                    "DELETE FROM family_children WHERE family_id = ? AND child_id = ?",
                    (family_ref, child_ref),
                )
            self.repository.conn.execute(
                "INSERT INTO family_children (family_id, child_id) VALUES (?, ?)",
                (clone_reference, child_ref),
            )

    def _validate_post_split(self, source_id, new_person_id):
        if self.repository.get_person_record(source_id) is None or self.repository.get_person_record(new_person_id) is None:
            raise RuntimeError("Одна из карточек разделения отсутствует")
        graph, children_with_parents = self._current_pedigree_graph()
        if self._has_cycle(graph):
            raise SplitSafetyError("Разделение создало цикл в родословной")
        if any(child in parents for child, parents in children_with_parents.items()):
            raise SplitSafetyError("Разделение создало связь человека с самим собой")
        for family in self.repository.list_families_raw():
            husband = self._resolve_optional(family.get("husband_id"))
            wife = self._resolve_optional(family.get("wife_id"))
            if husband is not None and husband == wife:
                raise SplitSafetyError("Разделение создало связь человека с самим собой как супруга/партнёра")
        if self.repository.conn.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("После разделения обнаружены повреждённые ссылки")

    def _assert_plan_current(self, plan):
        source = self.repository.get_person_record(plan.source["id"])
        if source is None:
            raise RuntimeError("Данные изменились после предварительного просмотра")
        if self._display_person(source) != plan.source:
            raise RuntimeError("Данные изменились после предварительного просмотра")
        current = self.plan_split(
            int(plan.source["id"]), plan.selection,
            source_values=plan.source_preview, new_values=plan.new_person_preview,
        )
        current_records = {
            key: tuple((record["id"], record["selected"]) for record in records)
            for key, records in current.collections.items()
        }
        planned_records = {
            key: tuple((record["id"], record["selected"]) for record in records)
            for key, records in plan.collections.items()
        }
        if current_records != planned_records or current.relationships != plan.relationships:
            raise RuntimeError("Данные изменились после предварительного просмотра")

    def _current_pedigree_graph(self):
        graph = defaultdict(set)
        parents = defaultdict(set)
        families = self.repository.list_families_raw()
        children = self.repository.list_family_children_raw()
        for family in families:
            family_refs = self._family_references(family)
            family_children = [row for row in children if str(row["family_id"]) in family_refs]
            adults = [self._resolve_optional(family.get(column)) for column in ("husband_id", "wife_id")]
            for row in family_children:
                child = self._resolve_optional(row["child_id"])
                if child is None:
                    continue
                for adult in adults:
                    if adult is not None:
                        graph[adult].add(child)
                        parents[child].add(adult)
        return graph, parents

    def _person_references(self, person_id):
        person = self.repository.get_person_record(person_id)
        references = {str(person_id)}
        if person and person.get("gedcom_id"):
            references.add(str(person["gedcom_id"]))
        return references

    @staticmethod
    def _family_references(family):
        return {str(family["id"]), str(family.get("gedcom_id") or "")} - {""}

    @staticmethod
    def _family_for_reference(reference, families):
        reference = str(reference)
        return next(
            (family for family in families if reference in {str(family["id"]), str(family.get("gedcom_id") or "")}),
            None,
        )

    def _family_by_id(self, family_id):
        return next(family for family in self.repository.list_families_raw() if int(family["id"]) == int(family_id))

    @staticmethod
    def _family_description(family):
        return f"{family.get('husband_id', '')} + {family.get('wife_id', '')} [{family.get('relationship_type', 'unknown')}]"

    def _resolve_optional(self, reference):
        if not str(reference or "").strip():
            return None
        person_id = self.repository.resolve_person_reference(reference)
        return int(person_id) if person_id is not None else None

    @staticmethod
    def _display_person(person):
        return {key: person.get(key, "") for key in ("id", "gedcom_id", *SPLIT_FIELDS)}

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

        nodes = set(graph) | {child for values in graph.values() for child in values}
        return any(visit(node) for node in nodes)
