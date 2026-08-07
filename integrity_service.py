from __future__ import annotations

import csv
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from config import DATA_DIR
from person_duplicate_service import PersonDuplicateService
from repository.person_repository import PersonRepository


MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}

APPROX_MARKERS = {"ABT", "ABOUT", "BEF", "AFT", "BET", "FROM", "TO", "CAL", "EST"}

TRANSLIT = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "i",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


@dataclass
class ParsedDate:
    """A normalized date and its parsing precision."""
    raw: str
    year: int | None
    month: int | None
    day: int | None
    precision: str
    approximate: bool
    parseable: bool
    valid_calendar: bool

    def to_date(self):
        if self.year is None:
            return None
        if self.precision == "full" and self.valid_calendar and self.month and self.day:
            return date(self.year, self.month, self.day)
        return None


class ScanCancelled(Exception):
    """Signal that an integrity scan was cancelled by the caller."""
    pass


class IntegrityCheckService:
    """Scan genealogy records for structural and semantic problems."""
    def __init__(self, repository: PersonRepository, data_dir: Path | None = None):
        self.repository = repository
        self.data_dir = Path(data_dir) if data_dir else DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.exclusions_path = self.data_dir / "integrity_duplicate_exclusions.json"
        self.database_scope = str(getattr(repository, "db_name", "") or "").strip()

    def run_checks(self):
        result = self.run_checks_with_progress(progress_callback=None, cancel_event=None)
        return result["report"]

    def run_checks_with_progress(self, progress_callback=None, cancel_event=None):
        stage_weights = {
            "Загрузка данных": 5,
            "Поиск дубликатов": 40,
            "Проверка дат": 25,
            "Проверка связей": 20,
            "Проверка пустых записей": 10,
        }
        total_weight = sum(stage_weights.values())
        completed_weight = 0

        report = {
            "duplicates": [],
            "date_problems": [],
            "broken_relationships": [],
            "empty_people": [],
        }

        def stage_progress(stage_name, processed, total):
            if not progress_callback:
                return
            fraction = (processed / total) if total else 0.0
            stage_weight = stage_weights.get(stage_name, 0)
            percent = int(((completed_weight + (stage_weight * fraction)) / total_weight) * 100)
            progress_callback(stage_name, processed, total, max(0, min(100, percent)))

        try:
            stage_progress("Загрузка данных", 0, 4)
            self._raise_if_cancelled(cancel_event)
            people = self.repository.list_people_for_integrity()
            stage_progress("Загрузка данных", 1, 4)
            self._raise_if_cancelled(cancel_event)
            families = self.repository.list_families_raw()
            stage_progress("Загрузка данных", 2, 4)
            self._raise_if_cancelled(cancel_event)
            family_children = self.repository.list_family_children_raw()
            stage_progress("Загрузка данных", 3, 4)
            self._raise_if_cancelled(cancel_event)
            events = self.repository.list_person_events_for_integrity()
            stage_progress("Загрузка данных", 4, 4)

            completed_weight += stage_weights["Загрузка данных"]

            people_by_id = {person["id"]: person for person in people}
            people_by_gedcom = {person["gedcom_id"]: person for person in people if person.get("gedcom_id")}

            report["duplicates"] = self._find_possible_duplicates(
                people,
                progress_callback=lambda processed, total: stage_progress("Поиск дубликатов", processed, total),
                cancel_event=cancel_event,
            )
            completed_weight += stage_weights["Поиск дубликатов"]

            report["date_problems"] = self._find_date_problems(
                people_by_id,
                people_by_gedcom,
                families,
                family_children,
                events,
                progress_callback=lambda processed, total: stage_progress("Проверка дат", processed, total),
                cancel_event=cancel_event,
            )
            completed_weight += stage_weights["Проверка дат"]

            report["broken_relationships"] = self._find_broken_relationships(
                people_by_gedcom,
                families,
                family_children,
                progress_callback=lambda processed, total: stage_progress("Проверка связей", processed, total),
                cancel_event=cancel_event,
            )
            completed_weight += stage_weights["Проверка связей"]

            report["empty_people"] = self._find_empty_people(
                people_by_id,
                people_by_gedcom,
                families,
                family_children,
                events,
                progress_callback=lambda processed, total: stage_progress("Проверка пустых записей", processed, total),
                cancel_event=cancel_event,
            )
            completed_weight += stage_weights["Проверка пустых записей"]

            if progress_callback:
                progress_callback("Завершено", 1, 1, 100)
            return {"report": report, "cancelled": False}
        except ScanCancelled:
            if progress_callback:
                progress_callback("Отменено", 1, 1, min(99, int((completed_weight / total_weight) * 100)))
            return {"report": report, "cancelled": True}

    def mark_not_duplicate(self, left_person_id, right_person_id):
        pair_key = self._pair_key(left_person_id, right_person_id)
        exclusions = self._load_exclusions()
        if pair_key not in exclusions:
            exclusions.append(pair_key)
            self._save_exclusions(exclusions)

    def list_not_duplicate_pairs(self):
        pairs = []
        for pair_key in self._load_exclusions():
            try:
                left_raw, right_raw = pair_key.split(":", 1)
                left_id = int(left_raw)
                right_id = int(right_raw)
            except ValueError:
                continue
            pairs.append((min(left_id, right_id), max(left_id, right_id)))
        return tuple(sorted(set(pairs)))

    def unmark_not_duplicate(self, left_person_id, right_person_id):
        pair_key = self._pair_key(left_person_id, right_person_id)
        exclusions = self._load_exclusions()
        remaining = [item for item in exclusions if item != pair_key]
        if len(remaining) == len(exclusions):
            return False
        self._save_exclusions(remaining)
        return True

    def export_report_csv(self, report, destination_path):
        destination = Path(destination_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8", newline="") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow([
                "section",
                "severity",
                "message",
                "person_ids",
                "left_person_id",
                "right_person_id",
                "left_name",
                "right_name",
                "left_birth",
                "right_birth",
                "match_score",
                "match_reasons",
            ])
            for section_name, items in report.items():
                for item in items:
                    writer.writerow([
                        section_name,
                        item.get("severity", ""),
                        item.get("message", ""),
                        "|".join(str(person_id) for person_id in item.get("person_ids", [])),
                        item.get("left_person_id", ""),
                        item.get("right_person_id", ""),
                        item.get("left_name", ""),
                        item.get("right_name", ""),
                        item.get("left_birth", ""),
                        item.get("right_birth", ""),
                        item.get("match_score", ""),
                        "|".join(str(reason) for reason in item.get("match_reasons", [])),
                    ])
        return destination

    def _find_possible_duplicates(self, people, progress_callback=None, cancel_event=None):
        exclusions = set(self._load_exclusions())
        findings = []

        prepared = []
        grouped_candidates = {}
        for person in people:
            self._raise_if_cancelled(cancel_event)
            first_norm = self._normalize_name(person.get("first_name", ""))
            last_norm = self._normalize_name(person.get("last_name", ""))
            if not first_norm or not last_norm:
                continue

            record = {
                "id": person["id"],
                "first_norm": first_norm,
                "last_norm": last_norm,
                "birth_date": person.get("birth_date", ""),
                "birth_place_norm": self._normalize_text(person.get("birth_place", "")),
                "raw": person,
            }
            prepared.append(record)
            bucket_key = (last_norm, first_norm[:3])
            grouped_candidates.setdefault(bucket_key, []).append(record)

        total_pairs = 0
        for bucket in grouped_candidates.values():
            size = len(bucket)
            if size > 1:
                total_pairs += (size * (size - 1)) // 2

        processed_pairs = 0
        if progress_callback:
            progress_callback(0, total_pairs)

        for bucket in grouped_candidates.values():
            if len(bucket) < 2:
                continue
            bucket.sort(key=lambda item: item["id"])
            for index, left in enumerate(bucket):
                self._raise_if_cancelled(cancel_event)
                for right in bucket[index + 1:]:
                    processed_pairs += 1
                    if progress_callback and (processed_pairs % 500 == 0 or processed_pairs == total_pairs):
                        progress_callback(processed_pairs, total_pairs)

                    if left["id"] == right["id"]:
                        continue

                    pair_key = self._pair_key(left["id"], right["id"])
                    if pair_key in exclusions:
                        continue

                    if left["first_norm"] != right["first_norm"] or left["last_norm"] != right["last_norm"]:
                        continue

                    if not self._compatible_birth_dates(left["birth_date"], right["birth_date"]):
                        continue

                    left_place = left["birth_place_norm"]
                    right_place = right["birth_place_norm"]
                    if left_place and right_place and left_place != right_place:
                        continue

                    assessment = PersonDuplicateService._compare(
                        left["raw"], right["raw"]
                    )
                    if assessment is None or assessment.score < 80:
                        continue
                    findings.append(
                        {
                            "severity": "Предупреждение",
                            "message": "Возможный дубликат человека.",
                            "person_ids": [left["id"], right["id"]],
                            "left_person_id": left["id"],
                            "right_person_id": right["id"],
                            "left_name": self._full_name(left["raw"]),
                            "right_name": self._full_name(right["raw"]),
                            "left_birth": left["raw"].get("birth_date", ""),
                            "right_birth": right["raw"].get("birth_date", ""),
                            "match_score": assessment.score if assessment else 0,
                            "match_reasons": list(assessment.reasons) if assessment else [],
                        }
                    )

        if progress_callback:
            progress_callback(total_pairs, total_pairs)

        findings.sort(
            key=lambda item: (
                -item.get("match_score", 0),
                item.get("left_person_id", 0),
                item.get("right_person_id", 0),
            )
        )
        return findings

    def _find_date_problems(self, people_by_id, people_by_gedcom, families, family_children, events, progress_callback=None, cancel_event=None):
        findings = []

        parsed_birth = {}
        parsed_death = {}
        family_children_by_family = {}
        for link in family_children:
            family_children_by_family.setdefault(link.get("family_id", ""), []).append(link.get("child_id", ""))

        parent_child_checks = 0
        for family in families:
            parent_count = int(bool(family.get("husband_id", ""))) + int(bool(family.get("wife_id", "")))
            parent_child_checks += parent_count * len(family_children_by_family.get(family.get("gedcom_id", ""), []))

        total_steps = len(people_by_id) + len(events) + parent_child_checks
        processed_steps = 0

        if progress_callback:
            progress_callback(0, total_steps)

        for person_id, person in people_by_id.items():
            self._raise_if_cancelled(cancel_event)
            birth = self._parse_date(person.get("birth_date", ""))
            death = self._parse_date(person.get("death_date", ""))
            parsed_birth[person_id] = birth
            parsed_death[person_id] = death

            findings.extend(self._date_parse_findings(person_id, "Дата рождения", birth))
            findings.extend(self._date_parse_findings(person_id, "Дата смерти", death))

            contradiction = self._compare_dates(death, birth)
            if contradiction is not None and contradiction < 0:
                findings.append(
                    self._issue("Ошибка", "Дата смерти раньше даты рождения.", [person_id])
                )

            processed_steps += 1
            if progress_callback and (processed_steps % 500 == 0 or processed_steps == total_steps):
                progress_callback(processed_steps, total_steps)

        for family in families:
            self._raise_if_cancelled(cancel_event)
            husband_gid = family.get("husband_id", "")
            wife_gid = family.get("wife_id", "")
            child_gids = family_children_by_family.get(family.get("gedcom_id", ""), [])

            parent_gids = [gid for gid in [husband_gid, wife_gid] if gid and gid in people_by_gedcom]
            for child_gid in child_gids:
                child_person = people_by_gedcom.get(child_gid)
                if not child_person:
                    continue
                child_id = child_person["id"]
                child_birth = parsed_birth.get(child_id)
                if child_birth is None:
                    continue

                for parent_gid in parent_gids:
                    self._raise_if_cancelled(cancel_event)
                    parent_person = people_by_gedcom.get(parent_gid)
                    if not parent_person:
                        processed_steps += 1
                        continue
                    parent_id = parent_person["id"]
                    parent_birth = parsed_birth.get(parent_id)
                    if parent_birth is None:
                        processed_steps += 1
                        continue

                    cmp_parent_child = self._compare_dates(child_birth, parent_birth)
                    if cmp_parent_child is not None and cmp_parent_child < 0:
                        findings.append(
                            self._issue("Ошибка", "Ребенок родился раньше родителя.", [parent_id, child_id])
                        )

                    if parent_birth.year is not None and child_birth.year is not None:
                        age = child_birth.year - parent_birth.year
                        if age < 12:
                            findings.append(
                                self._issue("Ошибка", "Возраст родителя меньше 12 лет на момент рождения ребенка.", [parent_id, child_id])
                            )
                        elif age > 80:
                            findings.append(
                                self._issue("Предупреждение", "Возраст родителя больше 80 лет на момент рождения ребенка.", [parent_id, child_id])
                            )

                    processed_steps += 1
                    if progress_callback and (processed_steps % 500 == 0 or processed_steps == total_steps):
                        progress_callback(processed_steps, total_steps)

        for event in events:
            self._raise_if_cancelled(cancel_event)
            person_id = event.get("person_id")
            if person_id not in people_by_id:
                processed_steps += 1
                continue
            event_date = self._parse_date(event.get("date", ""))
            findings.extend(self._date_parse_findings(person_id, "Дата события", event_date))

            birth = parsed_birth.get(person_id)
            death = parsed_death.get(person_id)
            if event.get("event_type") == "marriage":
                cmp_marriage_birth = self._compare_dates(event_date, birth)
                if cmp_marriage_birth is not None and cmp_marriage_birth < 0:
                    findings.append(
                        self._issue("Ошибка", "Дата брака раньше даты рождения.", [person_id])
                    )

            cmp_event_death = self._compare_dates(event_date, death)
            if cmp_event_death is not None and cmp_event_death > 0:
                findings.append(
                    self._issue("Ошибка", "Событие датировано после смерти человека.", [person_id])
                )

            processed_steps += 1
            if progress_callback and (processed_steps % 500 == 0 or processed_steps == total_steps):
                progress_callback(processed_steps, total_steps)

        if progress_callback:
            progress_callback(total_steps, total_steps)

        return findings

    def _find_broken_relationships(self, people_by_gedcom, families, family_children, progress_callback=None, cancel_event=None):
        findings = []
        family_by_gedcom = {family.get("gedcom_id", ""): family for family in families if family.get("gedcom_id")}

        total_steps = len(family_children) + len(families)
        processed_steps = 0
        if progress_callback:
            progress_callback(0, total_steps)

        children_by_family = {}
        seen_links = set()
        duplicate_links = set()
        for link in family_children:
            self._raise_if_cancelled(cancel_event)
            family_id = link.get("family_id", "")
            child_id = link.get("child_id", "")
            children_by_family.setdefault(family_id, []).append(child_id)
            pair = (family_id, child_id)
            if pair in seen_links:
                duplicate_links.add(pair)
            seen_links.add(pair)

            if family_id and family_id not in family_by_gedcom:
                findings.append(self._issue("Ошибка", "Запись ребенка ссылается на несуществующую семью.", []))

            if child_id and child_id not in people_by_gedcom:
                findings.append(self._issue("Ошибка", "Связь ребенка указывает на несуществующего человека.", []))

            processed_steps += 1
            if progress_callback and (processed_steps % 500 == 0 or processed_steps == total_steps):
                progress_callback(processed_steps, total_steps)

        for family in families:
            self._raise_if_cancelled(cancel_event)
            family_id = family.get("gedcom_id", "")
            husband_id = family.get("husband_id", "")
            wife_id = family.get("wife_id", "")
            children = children_by_family.get(family_id, [])

            if husband_id and husband_id not in people_by_gedcom:
                findings.append(self._issue("Ошибка", "Семья ссылается на несуществующего мужа.", []))
            if wife_id and wife_id not in people_by_gedcom:
                findings.append(self._issue("Ошибка", "Семья ссылается на несуществующую жену.", []))

            if not husband_id and not wife_id:
                findings.append(self._issue("Предупреждение", "Семья не содержит супругов.", []))
            if not children:
                findings.append(self._issue("Предупреждение", "Семья не содержит детей.", []))

            if husband_id and wife_id and husband_id == wife_id:
                person = people_by_gedcom.get(husband_id)
                findings.append(self._issue("Ошибка", "Человек связан сам с собой как супруг.", [person["id"]] if person else []))

            for child_gid in children:
                if child_gid and child_gid in {husband_id, wife_id}:
                    person = people_by_gedcom.get(child_gid)
                    findings.append(self._issue("Ошибка", "Человек связан сам с собой как родитель/ребенок.", [person["id"]] if person else []))

            processed_steps += 1
            if progress_callback and (processed_steps % 500 == 0 or processed_steps == total_steps):
                progress_callback(processed_steps, total_steps)

        for family_id, child_id in sorted(duplicate_links):
            child_person = people_by_gedcom.get(child_id)
            findings.append(self._issue("Предупреждение", "Обнаружена дублирующая связь ребенка в семье.", [child_person["id"]] if child_person else []))

        findings.extend(self._detect_circular_ancestry(people_by_gedcom, families, family_children, cancel_event=cancel_event))

        if progress_callback:
            progress_callback(total_steps, total_steps)

        return findings

    def _detect_circular_ancestry(self, people_by_gedcom, families, family_children, cancel_event=None):
        family_by_gedcom = {family.get("gedcom_id", ""): family for family in families if family.get("gedcom_id")}
        parent_map = {}
        for link in family_children:
            family = family_by_gedcom.get(link.get("family_id", ""))
            child_gid = link.get("child_id", "")
            if not family or not child_gid:
                continue
            parent_ids = [family.get("husband_id", ""), family.get("wife_id", "")]
            valid_parent_ids = [gid for gid in parent_ids if gid and gid in people_by_gedcom]
            if valid_parent_ids:
                parent_map.setdefault(child_gid, set()).update(valid_parent_ids)

        findings = []
        if not parent_map:
            return findings

        colors = {}
        reported_cycles = set()
        max_steps = max(10000, len(parent_map) * 40)
        step_count = 0
        deadline = time.monotonic() + 5.0

        for root_gid in parent_map.keys():
            self._raise_if_cancelled(cancel_event)
            if colors.get(root_gid) == 2:
                continue

            stack = [(root_gid, iter(parent_map.get(root_gid, set())))]
            path = [root_gid]
            path_index = {root_gid: 0}
            colors[root_gid] = 1

            while stack:
                self._raise_if_cancelled(cancel_event)
                step_count += 1
                if step_count >= max_steps or time.monotonic() >= deadline:
                    return findings

                node_gid, parent_iter = stack[-1]
                try:
                    parent_gid = next(parent_iter)
                except StopIteration:
                    stack.pop()
                    colors[node_gid] = 2
                    if path and path[-1] == node_gid:
                        path.pop()
                    path_index.pop(node_gid, None)
                    continue

                parent_state = colors.get(parent_gid, 0)
                if parent_state == 0:
                    colors[parent_gid] = 1
                    stack.append((parent_gid, iter(parent_map.get(parent_gid, set()))))
                    path_index[parent_gid] = len(path)
                    path.append(parent_gid)
                    continue

                cycle_start = path_index.get(parent_gid)
                if parent_state == 1 and cycle_start is not None:
                    cycle_path = path[cycle_start:]
                    cycle_ids = sorted(
                        {
                            people_by_gedcom[gid]["id"]
                            for gid in cycle_path
                            if gid in people_by_gedcom
                        }
                    )
                    cycle_key = tuple(cycle_ids)
                    if cycle_ids and cycle_key not in reported_cycles:
                        findings.append(self._issue("Ошибка", "Обнаружен циклический предок (замкнутая родословная).", cycle_ids))
                        reported_cycles.add(cycle_key)

        return findings

    def _find_empty_people(self, people_by_id, people_by_gedcom, families, family_children, events, progress_callback=None, cancel_event=None):
        findings = []

        event_people_ids = {event.get("person_id") for event in events if event.get("person_id") in people_by_id}
        valid_family_ids = {family.get("gedcom_id", "") for family in families if family.get("gedcom_id")}

        total_steps = len(families) + len(family_children) + len(people_by_id)
        processed_steps = 0
        if progress_callback:
            progress_callback(0, total_steps)

        linked_gedcom_ids = set()
        for family in families:
            self._raise_if_cancelled(cancel_event)
            husband_id = family.get("husband_id", "")
            wife_id = family.get("wife_id", "")
            if husband_id and husband_id in people_by_gedcom:
                linked_gedcom_ids.add(husband_id)
            if wife_id and wife_id in people_by_gedcom:
                linked_gedcom_ids.add(wife_id)

            processed_steps += 1
            if progress_callback and (processed_steps % 500 == 0 or processed_steps == total_steps):
                progress_callback(processed_steps, total_steps)

        for link in family_children:
            self._raise_if_cancelled(cancel_event)
            family_id = link.get("family_id", "")
            child_id = link.get("child_id", "")
            if family_id in valid_family_ids and child_id in people_by_gedcom:
                linked_gedcom_ids.add(child_id)

            processed_steps += 1
            if progress_callback and (processed_steps % 500 == 0 or processed_steps == total_steps):
                progress_callback(processed_steps, total_steps)

        for person_id, person in people_by_id.items():
            self._raise_if_cancelled(cancel_event)
            first_name = (person.get("first_name") or "").strip()
            last_name = (person.get("last_name") or "").strip()
            if first_name or last_name:
                processed_steps += 1
                continue

            has_dates = bool((person.get("birth_date") or "").strip() or (person.get("death_date") or "").strip())
            if has_dates:
                continue

            person_gid = person.get("gedcom_id", "")
            has_links = bool(person_gid and person_gid in linked_gedcom_ids)
            has_events = person_id in event_people_ids
            if not has_links and not has_events:
                findings.append(self._issue("Информация", "Пустая запись человека без дат, событий и семейных связей.", [person_id]))

            processed_steps += 1
            if progress_callback and (processed_steps % 500 == 0 or processed_steps == total_steps):
                progress_callback(processed_steps, total_steps)

        if progress_callback:
            progress_callback(total_steps, total_steps)

        return findings

    @staticmethod
    def _raise_if_cancelled(cancel_event):
        if cancel_event is not None and cancel_event.is_set():
            raise ScanCancelled()

    def _date_parse_findings(self, person_id, label, parsed):
        findings = []
        raw_value = (parsed.raw or "").strip()
        if not raw_value:
            return findings

        if not parsed.parseable:
            findings.append(self._issue("Предупреждение", f"{label}: не удалось распознать дату '{raw_value}'.", [person_id]))
            return findings

        if parsed.precision == "full" and not parsed.valid_calendar:
            findings.append(self._issue("Ошибка", f"{label}: невозможная календарная дата '{raw_value}'.", [person_id]))

        return findings

    @staticmethod
    def _issue(severity, message, person_ids):
        unique_ids = sorted({person_id for person_id in (person_ids or []) if person_id is not None})
        return {
            "severity": severity,
            "message": message,
            "person_ids": unique_ids,
        }

    @staticmethod
    def _pair_key(left_person_id, right_person_id):
        left = int(left_person_id)
        right = int(right_person_id)
        return f"{min(left, right)}:{max(left, right)}"

    def _load_exclusions(self):
        data = self._read_exclusions_payload()
        if isinstance(data, dict) and isinstance(data.get("databases"), dict):
            values = data["databases"].get(self.database_scope, [])
            return [str(item) for item in values] if isinstance(values, list) else []

        if isinstance(data, list):
            exclusions = [str(item) for item in data]
            try:
                self._save_exclusions(exclusions)
            except OSError:
                pass
            return exclusions
        return []

    def _read_exclusions_payload(self):
        if not self.exclusions_path.exists():
            return None
        try:
            return json.loads(self.exclusions_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def _save_exclusions(self, exclusions):
        current_payload = self._read_exclusions_payload()
        databases = {}
        if isinstance(current_payload, dict) and isinstance(current_payload.get("databases"), dict):
            databases = {
                str(scope): [str(item) for item in values]
                for scope, values in current_payload["databases"].items()
                if isinstance(values, list)
            }
        elif isinstance(current_payload, list):
            databases[self.database_scope] = [str(item) for item in current_payload]

        databases[self.database_scope] = sorted({str(item) for item in exclusions})
        payload = {
            "version": 2,
            "databases": databases,
        }
        self.exclusions_path.parent.mkdir(parents=True, exist_ok=True)
        self.exclusions_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _normalize_text(value):
        text = unicodedata.normalize("NFKD", (value or "").lower())
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        text = text.replace("ё", "е")
        return re.sub(r"\s+", " ", re.sub(r"[^a-zа-я0-9\s]", "", text)).strip()

    def _normalize_name(self, value):
        text = self._normalize_text(value)
        translated = "".join(TRANSLIT.get(ch, ch) for ch in text)
        return re.sub(r"[^a-z0-9]", "", translated)

    def _compatible_birth_dates(self, left_raw, right_raw):
        left = self._parse_date(left_raw)
        right = self._parse_date(right_raw)

        if not left.raw.strip() or not right.raw.strip():
            return True
        if not left.parseable or not right.parseable:
            return False
        if left.year is None or right.year is None:
            return True
        if left.year == right.year:
            return True
        return abs(left.year - right.year) <= 1

    @staticmethod
    def _full_name(person):
        return f"{person.get('last_name', '')} {person.get('first_name', '')}".strip() or "(без имени)"

    def _parse_date(self, raw):
        value = (raw or "").strip()
        if not value:
            return ParsedDate(raw=value, year=None, month=None, day=None, precision="none", approximate=False, parseable=True, valid_calendar=True)

        upper = value.upper()
        approximate = any(marker in upper for marker in APPROX_MARKERS)

        full_match = re.search(r"\b(\d{1,2})\s+([A-Z]{3})\s+(\d{4})\b", upper)
        if full_match:
            day = int(full_match.group(1))
            month = MONTHS.get(full_match.group(2), 0)
            year = int(full_match.group(3))
            valid_calendar = True
            try:
                date(year, month, day)
            except ValueError:
                valid_calendar = False
            return ParsedDate(
                raw=value,
                year=year,
                month=month if month else None,
                day=day,
                precision="full",
                approximate=approximate,
                parseable=True,
                valid_calendar=valid_calendar,
            )

        year_match = re.search(r"\b(\d{4})\b", upper)
        if year_match:
            year = int(year_match.group(1))
            return ParsedDate(
                raw=value,
                year=year,
                month=None,
                day=None,
                precision="year",
                approximate=approximate,
                parseable=True,
                valid_calendar=True,
            )

        # Non-empty date string without recognizable year is suspicious.
        return ParsedDate(
            raw=value,
            year=None,
            month=None,
            day=None,
            precision="unknown",
            approximate=approximate,
            parseable=False,
            valid_calendar=False,
        )

    @staticmethod
    def _compare_dates(left, right):
        if not left or not right:
            return None
        if left.year is None or right.year is None:
            return None

        left_exact = left.to_date()
        right_exact = right.to_date()
        if left_exact and right_exact:
            if left_exact < right_exact:
                return -1
            if left_exact > right_exact:
                return 1
            return 0

        if left.year < right.year:
            return -1
        if left.year > right.year:
            return 1
        return 0
