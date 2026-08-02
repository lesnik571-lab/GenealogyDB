from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from repository.person_repository import PersonRepository


@dataclass
class CandidateScore:
    person_id: int
    person_reference: str
    score: int
    reasons: list[str]


class RelationshipPlaceholderRepairService:
    """Diagnose and repair references to empty placeholder people.

    The service intentionally uses conservative matching and prefers leaving
    uncertain placeholders untouched rather than guessing.
    """

    def __init__(self, repository: PersonRepository):
        self.repository = repository
        self._profile_cache: dict[int, dict[str, Any]] = {}
        self._people_cache: list[dict[str, Any]] = []

    def diagnose_placeholders(self) -> list[dict[str, Any]]:
        referenced_tokens = self._collect_referenced_person_tokens()
        if not referenced_tokens:
            return []

        placeholders: list[dict[str, Any]] = []
        people = self._people_cache or self.repository.list_people_full()
        for person in people:
            if not self._is_empty_name_person(person):
                continue
            refs = self._person_references(person)
            if not (refs & referenced_tokens):
                continue

            relation_profile = self._profile_cache.get(person["id"]) or self._relation_profile(refs)
            placeholders.append(
                {
                    "person": person,
                    "references": sorted(refs),
                    "family_links": relation_profile["family_links"],
                    "parents": sorted(relation_profile["parents"]),
                    "spouses": sorted(relation_profile["spouses"]),
                    "children": sorted(relation_profile["children"]),
                    "events": self._list_person_events_safe(person["id"]),
                    "sources": self._list_person_sources_safe(person["id"]),
                }
            )
        placeholders.sort(key=lambda item: item["person"]["id"])
        return placeholders

    def build_repair_plan(self) -> dict[str, Any]:
        self._prime_caches()
        placeholders = self.diagnose_placeholders()
        plan_entries: list[dict[str, Any]] = []
        unresolved: list[dict[str, Any]] = []
        replacement_map: dict[str, str] = {}

        for placeholder in placeholders:
            decision = self._choose_replacement(placeholder)
            if not decision["replacement"]:
                unresolved.append(
                    {
                        "placeholder_id": placeholder["person"]["id"],
                        "placeholder_reference": self._preferred_reference(placeholder["person"]),
                        "reason": decision["reason"],
                        "candidates": decision["candidates"],
                    }
                )
                continue

            replacement = decision["replacement"]
            old_refs = set(placeholder["references"])
            old_refs.discard(replacement["reference"])
            if not old_refs:
                unresolved.append(
                    {
                        "placeholder_id": placeholder["person"]["id"],
                        "placeholder_reference": self._preferred_reference(placeholder["person"]),
                        "reason": "already points to chosen reference",
                        "candidates": decision["candidates"],
                    }
                )
                continue

            family_changes, child_changes = self._collect_reference_changes(old_refs, replacement["reference"])
            if not family_changes and not child_changes:
                unresolved.append(
                    {
                        "placeholder_id": placeholder["person"]["id"],
                        "placeholder_reference": self._preferred_reference(placeholder["person"]),
                        "reason": "no relationship rows require update",
                        "candidates": decision["candidates"],
                    }
                )
                continue

            would_conflict = self._would_create_spouse_self_conflict(family_changes)
            if would_conflict:
                unresolved.append(
                    {
                        "placeholder_id": placeholder["person"]["id"],
                        "placeholder_reference": self._preferred_reference(placeholder["person"]),
                        "reason": "candidate would make spouse self-link",
                        "candidates": decision["candidates"],
                    }
                )
                continue

            for old_ref in old_refs:
                replacement_map[old_ref] = replacement["reference"]

            plan_entries.append(
                {
                    "placeholder": placeholder,
                    "replacement": replacement,
                    "candidate_reasons": decision["candidates"],
                    "family_updates": family_changes,
                    "child_updates": child_changes,
                }
            )

        changes = self._materialize_change_log(plan_entries)
        return {
            "diagnosis": placeholders,
            "repairs": plan_entries,
            "unresolved": unresolved,
            "changes": changes,
            "replacement_map": replacement_map,
        }

    def apply_repair_plan(self, plan: dict[str, Any], fail_after_changes: int | None = None) -> dict[str, Any]:
        changes = list(plan.get("changes", []))
        applied_count = 0

        with self.repository.transaction():
            for change in changes:
                table = change["table"]
                if table == "families":
                    self.repository.cur.execute(
                        f"UPDATE families SET {change['column']} = ? WHERE id = ?",
                        (change["new_value"], change["row_id"]),
                    )
                elif table == "family_children":
                    if change["action"] == "update":
                        self.repository.cur.execute(
                            "UPDATE family_children SET child_id = ? WHERE rowid = ?",
                            (change["new_value"], change["row_id"]),
                        )
                    elif change["action"] == "delete_duplicate":
                        self.repository.cur.execute(
                            "DELETE FROM family_children WHERE rowid = ?",
                            (change["row_id"],),
                        )
                applied_count += 1
                if fail_after_changes is not None and applied_count >= fail_after_changes:
                    raise RuntimeError("forced failure to verify rollback")

        return {
            "applied_changes": applied_count,
            "changes": changes,
            "repaired_placeholders": [
                {
                    "placeholder_id": entry["placeholder"]["person"]["id"],
                    "replacement_id": entry["replacement"]["id"],
                    "replacement_reference": entry["replacement"]["reference"],
                }
                for entry in plan.get("repairs", [])
            ],
        }

    def _collect_referenced_person_tokens(self) -> set[str]:
        referenced: set[str] = set()
        for family in self.repository.list_families_raw():
            for token in (family.get("husband_id", ""), family.get("wife_id", "")):
                token = str(token or "").strip()
                if token:
                    referenced.add(token)
        for item in self.repository.list_family_children_raw():
            token = str(item.get("child_id", "")).strip()
            if token:
                referenced.add(token)
        return referenced

    def _person_references(self, person: dict[str, Any]) -> set[str]:
        refs = {str(person["id"])}
        gedcom_id = str(person.get("gedcom_id") or "").strip()
        if gedcom_id:
            refs.add(gedcom_id)
        return {value for value in refs if value}

    def _prime_caches(self):
        self._people_cache = self.repository.list_people_full()
        self._profile_cache = self._build_profiles_from_current_data(self._people_cache)

    def _build_profiles_from_current_data(self, people: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
        ref_to_person_ids: dict[str, set[int]] = {}
        for person in people:
            for ref in self._person_references(person):
                ref_to_person_ids.setdefault(ref, set()).add(int(person["id"]))

        family_rows = self.repository.list_families_raw()
        child_rows = self.repository.list_family_children_raw()

        children_by_family: dict[str, list[str]] = {}
        for row in child_rows:
            family_ref = str(row.get("family_id") or "").strip()
            child_ref = str(row.get("child_id") or "").strip()
            if not family_ref or not child_ref:
                continue
            children_by_family.setdefault(family_ref, []).append(child_ref)

        profile_by_person: dict[int, dict[str, Any]] = {
            int(person["id"]): {
                "parents": set(),
                "spouses": set(),
                "children": set(),
                "family_links": [],
            }
            for person in people
        }

        def profile_targets(ref_value: str) -> list[int]:
            return sorted(ref_to_person_ids.get(ref_value, set()))

        for family in family_rows:
            family_id = family.get("id")
            family_ref = str(family.get("gedcom_id") or family_id or "").strip()
            husband = str(family.get("husband_id") or "").strip()
            wife = str(family.get("wife_id") or "").strip()
            family_children = [value for value in children_by_family.get(family_ref, []) if value]

            participant_refs = set([value for value in (husband, wife, *family_children) if value])
            for participant in participant_refs:
                person_ids = profile_targets(participant)
                for person_id in person_ids:
                    profile = profile_by_person.get(person_id)
                    if not profile:
                        continue

                    is_husband = husband and participant == husband
                    is_wife = wife and participant == wife
                    is_child = participant in family_children

                    if is_husband and wife:
                        profile["spouses"].add(wife)
                    if is_wife and husband:
                        profile["spouses"].add(husband)
                    if is_husband or is_wife:
                        for child_ref in family_children:
                            if child_ref != participant:
                                profile["children"].add(child_ref)
                    if is_child:
                        if husband and husband != participant:
                            profile["parents"].add(husband)
                        if wife and wife != participant:
                            profile["parents"].add(wife)

                    profile["family_links"].append(
                        {
                            "family_id": family_id,
                            "family_gedcom_id": family.get("gedcom_id") or "",
                            "husband_id": husband,
                            "wife_id": wife,
                            "children": sorted(family_children),
                            "relationship_type": family.get("relationship_type") or "unknown",
                            "roles": {
                                "is_husband": bool(is_husband),
                                "is_wife": bool(is_wife),
                                "is_child": bool(is_child),
                            },
                        }
                    )

        for profile in profile_by_person.values():
            profile["family_links"].sort(key=lambda row: (row["family_id"] or 0, row["family_gedcom_id"]))

        return profile_by_person

    def _preferred_reference(self, person: dict[str, Any]) -> str:
        gedcom_id = str(person.get("gedcom_id") or "").strip()
        if gedcom_id:
            return gedcom_id
        return str(person["id"])

    def _relation_profile(self, person_refs: set[str]) -> dict[str, Any]:
        families = self.repository.list_families_raw()
        children_raw = self.repository.list_family_children_raw()
        children_by_family: dict[str, list[str]] = {}
        for row in children_raw:
            family_ref = str(row.get("family_id") or "").strip()
            child_ref = str(row.get("child_id") or "").strip()
            if not family_ref or not child_ref:
                continue
            children_by_family.setdefault(family_ref, []).append(child_ref)

        parents: set[str] = set()
        spouses: set[str] = set()
        children: set[str] = set()
        family_links: list[dict[str, Any]] = []

        for family in families:
            family_ref = str(family.get("gedcom_id") or family.get("id") or "").strip()
            husband = str(family.get("husband_id") or "").strip()
            wife = str(family.get("wife_id") or "").strip()
            family_children = [value for value in children_by_family.get(family_ref, []) if value]

            is_husband = husband in person_refs if husband else False
            is_wife = wife in person_refs if wife else False
            is_child = any(child_ref in person_refs for child_ref in family_children)

            if not (is_husband or is_wife or is_child):
                continue

            if is_husband and wife:
                spouses.add(wife)
            if is_wife and husband:
                spouses.add(husband)
            if is_husband or is_wife:
                for child_ref in family_children:
                    if child_ref not in person_refs:
                        children.add(child_ref)
            if is_child:
                if husband and husband not in person_refs:
                    parents.add(husband)
                if wife and wife not in person_refs:
                    parents.add(wife)

            family_links.append(
                {
                    "family_id": family.get("id"),
                    "family_gedcom_id": family.get("gedcom_id") or "",
                    "husband_id": husband,
                    "wife_id": wife,
                    "children": sorted(family_children),
                    "relationship_type": family.get("relationship_type") or "unknown",
                    "roles": {
                        "is_husband": is_husband,
                        "is_wife": is_wife,
                        "is_child": is_child,
                    },
                }
            )

        family_links.sort(key=lambda row: (row["family_id"] or 0, row["family_gedcom_id"]))
        return {
            "parents": parents,
            "spouses": spouses,
            "children": children,
            "family_links": family_links,
        }

    def _is_empty_name_person(self, person: dict[str, Any]) -> bool:
        first_name = str(person.get("first_name") or "").strip()
        last_name = str(person.get("last_name") or "").strip()
        return not first_name and not last_name

    def _list_person_events_safe(self, person_id: int) -> list[dict[str, Any]]:
        if not self._table_exists("person_events"):
            return []
        return self.repository.list_person_events(person_id)

    def _list_person_sources_safe(self, person_id: int) -> list[dict[str, Any]]:
        if not self._table_exists("person_sources"):
            return []
        return self.repository.list_person_sources(person_id)

    def _table_exists(self, table_name: str) -> bool:
        row = self.repository.cur.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?",
            (table_name,),
        ).fetchone()
        return bool(row)

    def _choose_replacement(self, placeholder: dict[str, Any]) -> dict[str, Any]:
        person = placeholder["person"]
        candidates: list[CandidateScore] = []

        people = self._people_cache or self.repository.list_people_full()
        for candidate in people:
            if candidate["id"] == person["id"]:
                continue
            if self._is_empty_name_person(candidate):
                continue

            profile = self._profile_cache.get(candidate["id"]) or self._relation_profile(self._person_references(candidate))
            score, reasons = self._score_candidate(placeholder, candidate, profile)
            if score <= 0:
                continue
            candidates.append(
                CandidateScore(
                    person_id=candidate["id"],
                    person_reference=self._preferred_reference(candidate),
                    score=score,
                    reasons=reasons,
                )
            )

        if not candidates:
            return {"replacement": None, "reason": "no candidates", "candidates": []}

        candidates.sort(key=lambda row: (-row.score, row.person_id))
        best = candidates[0]
        if best.score < 6:
            return {
                "replacement": None,
                "reason": "no high-confidence candidate",
                "candidates": [self._candidate_to_dict(item) for item in candidates[:5]],
            }

        if len(candidates) > 1 and (best.score - candidates[1].score) < 2:
            return {
                "replacement": None,
                "reason": "ambiguous high-confidence candidates",
                "candidates": [self._candidate_to_dict(item) for item in candidates[:5]],
            }

        chosen = self.repository.get_person_record(best.person_id)
        if not chosen:
            return {"replacement": None, "reason": "candidate record not found", "candidates": []}

        return {
            "replacement": {
                "id": chosen["id"],
                "reference": chosen["reference"],
                "first_name": chosen.get("first_name", ""),
                "last_name": chosen.get("last_name", ""),
            },
            "reason": "high-confidence",
            "candidates": [self._candidate_to_dict(item) for item in candidates[:5]],
        }

    def _score_candidate(
        self,
        placeholder: dict[str, Any],
        candidate: dict[str, Any],
        candidate_rel: dict[str, Any],
    ) -> tuple[int, list[str]]:
        source = placeholder["person"]
        score = 0
        reasons: list[str] = []

        source_sex = str(source.get("sex") or "").strip()
        target_sex = str(candidate.get("sex") or "").strip()
        if source_sex and target_sex:
            if source_sex != target_sex:
                return 0, []
            score += 1
            reasons.append("same sex")

        source_birth = str(source.get("birth_date") or "").strip()
        target_birth = str(candidate.get("birth_date") or "").strip()
        if source_birth and target_birth:
            if source_birth != target_birth:
                return 0, []
            score += 2
            reasons.append("same birth date")

        source_death = str(source.get("death_date") or "").strip()
        target_death = str(candidate.get("death_date") or "").strip()
        if source_death and target_death:
            if source_death != target_death:
                return 0, []
            score += 2
            reasons.append("same death date")

        parent_score = self._relation_set_score(placeholder["parents"], candidate_rel["parents"], "parents")
        spouse_score = self._relation_set_score(placeholder["spouses"], candidate_rel["spouses"], "spouses")
        child_score = self._relation_set_score(placeholder["children"], candidate_rel["children"], "children")

        for part_score, part_reasons in (parent_score, spouse_score, child_score):
            score += part_score
            reasons.extend(part_reasons)

        # Do not allow surname-only matching: name contributes nothing to score.
        return score, reasons

    def _relation_set_score(self, left_values: list[str], right_values: set[str], label: str) -> tuple[int, list[str]]:
        left = {str(value).strip() for value in left_values if str(value).strip()}
        right = {str(value).strip() for value in right_values if str(value).strip()}
        if not left or not right:
            return 0, []
        if left == right:
            return 4, [f"same {label}"]
        if left.issubset(right) or right.issubset(left):
            return 2, [f"overlapping {label}"]
        overlap = left & right
        if overlap:
            return 1, [f"partial {label} overlap"]
        return 0, []

    def _candidate_to_dict(self, candidate: CandidateScore) -> dict[str, Any]:
        person = self.repository.get_person_record(candidate.person_id)
        return {
            "person_id": candidate.person_id,
            "reference": candidate.person_reference,
            "score": candidate.score,
            "reasons": list(candidate.reasons),
            "first_name": person.get("first_name", "") if person else "",
            "last_name": person.get("last_name", "") if person else "",
        }

    def _collect_reference_changes(self, old_refs: set[str], new_ref: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        old_refs = {str(value).strip() for value in old_refs if str(value).strip()}
        if not old_refs:
            return [], []

        placeholders = ",".join("?" for _ in old_refs)
        params = tuple(sorted(old_refs))

        family_changes: list[dict[str, Any]] = []
        family_rows = self.repository.cur.execute(
            f"SELECT id, husband_id, wife_id FROM families WHERE husband_id IN ({placeholders}) OR wife_id IN ({placeholders})",
            params + params,
        ).fetchall()
        for family_id, husband_id, wife_id in family_rows:
            husband_id = str(husband_id or "")
            wife_id = str(wife_id or "")
            if husband_id in old_refs and husband_id != new_ref:
                family_changes.append(
                    {
                        "table": "families",
                        "action": "update",
                        "row_id": family_id,
                        "column": "husband_id",
                        "old_value": husband_id,
                        "new_value": new_ref,
                    }
                )
            if wife_id in old_refs and wife_id != new_ref:
                family_changes.append(
                    {
                        "table": "families",
                        "action": "update",
                        "row_id": family_id,
                        "column": "wife_id",
                        "old_value": wife_id,
                        "new_value": new_ref,
                    }
                )

        child_changes: list[dict[str, Any]] = []
        child_rows = self.repository.cur.execute(
            f"SELECT rowid, family_id, child_id FROM family_children WHERE child_id IN ({placeholders})",
            params,
        ).fetchall()
        for rowid, family_id, child_id in child_rows:
            family_id = str(family_id or "")
            child_id = str(child_id or "")
            if child_id == new_ref:
                continue
            duplicate = self.repository.cur.execute(
                "SELECT rowid FROM family_children WHERE family_id = ? AND child_id = ? AND rowid != ?",
                (family_id, new_ref, rowid),
            ).fetchone()
            if duplicate:
                child_changes.append(
                    {
                        "table": "family_children",
                        "action": "delete_duplicate",
                        "row_id": rowid,
                        "family_id": family_id,
                        "old_value": child_id,
                        "new_value": new_ref,
                    }
                )
            else:
                child_changes.append(
                    {
                        "table": "family_children",
                        "action": "update",
                        "row_id": rowid,
                        "family_id": family_id,
                        "old_value": child_id,
                        "new_value": new_ref,
                    }
                )

        return family_changes, child_changes

    def _would_create_spouse_self_conflict(self, family_changes: list[dict[str, Any]]) -> bool:
        if not family_changes:
            return False
        by_family: dict[int, dict[str, str]] = {}
        for change in family_changes:
            family_id = int(change["row_id"])
            state = by_family.get(family_id)
            if not state:
                row = self.repository.cur.execute(
                    "SELECT husband_id, wife_id FROM families WHERE id = ?",
                    (family_id,),
                ).fetchone()
                if not row:
                    continue
                state = {"husband_id": str(row[0] or ""), "wife_id": str(row[1] or "")}
                by_family[family_id] = state
            state[change["column"]] = str(change["new_value"] or "")

        for state in by_family.values():
            husband_id = state.get("husband_id", "")
            wife_id = state.get("wife_id", "")
            if husband_id and wife_id and husband_id == wife_id:
                return True
        return False

    def _materialize_change_log(self, plan_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        for entry in plan_entries:
            placeholder = entry["placeholder"]["person"]
            replacement = entry["replacement"]
            for item in entry["family_updates"]:
                changes.append(
                    {
                        **item,
                        "placeholder_id": placeholder["id"],
                        "placeholder_reference": self._preferred_reference(placeholder),
                        "replacement_id": replacement["id"],
                        "replacement_reference": replacement["reference"],
                    }
                )
            for item in entry["child_updates"]:
                changes.append(
                    {
                        **item,
                        "placeholder_id": placeholder["id"],
                        "placeholder_reference": self._preferred_reference(placeholder),
                        "replacement_id": replacement["id"],
                        "replacement_reference": replacement["reference"],
                    }
                )
        changes.sort(key=lambda row: (row["table"], int(row["row_id"])))
        return changes