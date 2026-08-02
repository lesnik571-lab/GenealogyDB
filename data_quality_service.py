from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

from repository.person_repository import PersonRepository
from text_utils import normalize_search_text


CATEGORY_DEFINITIONS = (
    ("unnamed_people", "Unnamed people"),
    ("people_without_family_links", "People without family links"),
    ("dangling_person_references", "Dangling person references"),
    ("dangling_family_references", "Dangling family references"),
    ("duplicate_gedcom_ids", "Duplicate GEDCOM IDs"),
    ("duplicate_family_gedcom_ids", "Duplicate family GEDCOM IDs"),
    ("possible_duplicate_people", "Possible duplicate people"),
    ("impossible_dates", "Impossible birth/death dates"),
    ("death_before_birth", "Death before birth"),
    ("parent_younger_than_child", "Parent younger than child"),
    ("parent_age_under_12", "Parent age under 12 at child birth"),
    ("parent_age_over_80", "Parent age over 80 at child birth"),
    ("duplicate_spouse_pairs", "Duplicate spouse pairs"),
    ("families_without_spouses", "Families without spouses"),
    ("families_without_children", "Families without children"),
)
CATEGORY_LABELS = dict(CATEGORY_DEFINITIONS)
CATEGORY_ORDER = {key: index for index, (key, _label) in enumerate(CATEGORY_DEFINITIONS)}
SEVERITY_ORDER = {"Critical": 0, "Warning": 1, "Information": 2}
GEDCOM_MONTHS = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
DATE_QUALIFIERS = {"ABT", "BEF", "AFT", "EST"}


@dataclass(frozen=True)
class DataQualityDate:
    """A parsed genealogy date suitable for quality comparisons."""
    raw: str
    year: int | None
    month: int | None
    day: int | None
    parseable: bool
    valid: bool


@dataclass(frozen=True)
class DataQualityIssue:
    """One actionable issue detected in genealogy data."""
    category: str
    issue_type: str
    severity: str
    entity_type: str
    database_id: int | None
    gedcom_id: str
    display_name: str
    explanation: str
    context_person_id: int | None = None


@dataclass(frozen=True)
class DataQualityReport:
    """Grouped data-quality findings and summary counts."""
    categories: Mapping[str, tuple[DataQualityIssue, ...]]
    issues: tuple[DataQualityIssue, ...]

    @property
    def counters(self) -> dict[str, int]:
        return {category: len(issues) for category, issues in self.categories.items()}


class DataQualityService:
    """Analyze genealogy data without modifying repository records."""

    def __init__(self, repository: PersonRepository) -> None:
        self.repository = repository

    def analyze(self) -> DataQualityReport:
        """Return deterministic findings for every supported category."""
        people = sorted(self.repository.list_people_full(), key=lambda item: int(item["id"]))
        families = sorted(self.repository.list_families_raw(), key=lambda item: int(item["id"]))
        family_children = sorted(
            self.repository.list_family_children_raw(),
            key=lambda item: (str(item.get("family_id") or ""), str(item.get("child_id") or "")),
        )
        people_by_id = {int(person["id"]): person for person in people}
        people_aliases = self._alias_index(people)
        family_aliases = self._alias_index(families)
        children_by_family = self._children_by_family(family_children, family_aliases)
        categories: dict[str, list[DataQualityIssue]] = {
            key: [] for key, _label in CATEGORY_DEFINITIONS
        }

        self._check_people(categories, people, families, family_children, people_aliases)
        self._check_dangling_references(
            categories, families, family_children, people_by_id, people_aliases, family_aliases
        )
        self._check_duplicate_ids(categories, people, families)
        self._check_possible_duplicate_people(categories, people)
        parsed_birth, _parsed_death = self._check_person_dates(categories, people)
        self._check_parent_ages(
            categories, families, children_by_family, people_by_id, people_aliases, parsed_birth
        )
        self._check_families(categories, families, children_by_family, people_aliases)

        frozen_categories = {
            key: tuple(sorted(items, key=self._issue_sort_key))
            for key, items in categories.items()
        }
        issues = tuple(
            issue
            for key, _label in CATEGORY_DEFINITIONS
            for issue in frozen_categories[key]
        )
        return DataQualityReport(categories=frozen_categories, issues=issues)

    @staticmethod
    def parse_date(raw_value: object) -> DataQualityDate:
        """Parse supported GEDCOM, year-only, dotted, and qualified dates safely."""
        raw = str(raw_value or "").strip()
        if not raw:
            return DataQualityDate(raw, None, None, None, False, True)
        parts = raw.upper().split()
        while parts and parts[0] in DATE_QUALIFIERS:
            parts.pop(0)
        text = " ".join(parts)
        year_match = re.fullmatch(r"(\d{4})", text)
        if year_match:
            return DataQualityService._validated_date(raw, int(year_match.group(1)), None, None)
        gedcom_match = re.fullmatch(r"(\d{1,2})\s+([A-Z]{3})\s+(\d{4})", text)
        if gedcom_match and gedcom_match.group(2) in GEDCOM_MONTHS:
            return DataQualityService._validated_date(
                raw,
                int(gedcom_match.group(3)),
                GEDCOM_MONTHS[gedcom_match.group(2)],
                int(gedcom_match.group(1)),
            )
        numeric_match = re.fullmatch(r"(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})", text)
        if numeric_match:
            return DataQualityService._validated_date(
                raw,
                int(numeric_match.group(3)),
                int(numeric_match.group(2)),
                int(numeric_match.group(1)),
            )
        return DataQualityDate(raw, None, None, None, False, False)

    def export_csv(self, report: DataQualityReport, destination: str | Path) -> Path:
        """Export all findings in deterministic order to UTF-8 CSV."""
        output = Path(destination)
        with output.open("w", encoding="utf-8", newline="") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow([
                "category", "issue_type", "severity", "entity_type", "database_id",
                "gedcom_id", "display_name", "explanation",
            ])
            for issue in report.issues:
                writer.writerow([
                    issue.category, issue.issue_type, issue.severity, issue.entity_type,
                    issue.database_id if issue.database_id is not None else "",
                    issue.gedcom_id, issue.display_name, issue.explanation,
                ])
        return output

    @staticmethod
    def _validated_date(raw: str, year: int, month: int | None, day: int | None) -> DataQualityDate:
        valid = 1 <= year <= 9999
        if valid and month is not None and day is not None:
            try:
                date(year, month, day)
            except ValueError:
                valid = False
        return DataQualityDate(raw, year, month, day, True, valid)

    @staticmethod
    def _alias_index(records: Iterable[Mapping[str, Any]]) -> dict[str, set[int]]:
        aliases: dict[str, set[int]] = {}
        for record in records:
            database_id = int(record["id"])
            for alias in (str(database_id), str(record.get("gedcom_id") or "").strip()):
                if alias:
                    aliases.setdefault(alias, set()).add(database_id)
        return aliases

    @staticmethod
    def _resolve_one(reference: object, aliases: Mapping[str, set[int]]) -> int | None:
        matches = aliases.get(str(reference or "").strip(), set())
        return min(matches) if matches else None

    @staticmethod
    def _children_by_family(
        family_children: Iterable[Mapping[str, Any]],
        family_aliases: Mapping[str, set[int]],
    ) -> dict[int, list[str]]:
        children: dict[int, list[str]] = {}
        for link in family_children:
            for family_id in family_aliases.get(str(link.get("family_id") or ""), set()):
                children.setdefault(family_id, []).append(str(link.get("child_id") or ""))
        return children

    def _check_people(
        self,
        categories: dict[str, list[DataQualityIssue]],
        people: list[dict[str, Any]],
        families: list[dict[str, Any]],
        family_children: list[dict[str, Any]],
        aliases: Mapping[str, set[int]],
    ) -> None:
        linked_ids: set[int] = set()
        for family in families:
            for reference in (family.get("husband_id"), family.get("wife_id")):
                person_id = self._resolve_one(reference, aliases)
                if person_id is not None:
                    linked_ids.add(person_id)
        for link in family_children:
            person_id = self._resolve_one(link.get("child_id"), aliases)
            if person_id is not None:
                linked_ids.add(person_id)
        for person in people:
            if not str(person.get("first_name") or "").strip() and not str(person.get("last_name") or "").strip():
                categories["unnamed_people"].append(self._person_issue(
                    "unnamed_people", "Warning", person, "Both first and last name are empty."
                ))
            if int(person["id"]) not in linked_ids:
                categories["people_without_family_links"].append(self._person_issue(
                    "people_without_family_links", "Information", person,
                    "Person is not referenced by any family or family-child link.",
                ))

    def _check_dangling_references(
        self,
        categories: dict[str, list[DataQualityIssue]],
        families: list[dict[str, Any]],
        family_children: list[dict[str, Any]],
        people_by_id: Mapping[int, dict[str, Any]],
        people_aliases: Mapping[str, set[int]],
        family_aliases: Mapping[str, set[int]],
    ) -> None:
        families_by_id = {int(family["id"]): family for family in families}
        for family in families:
            for field, label in (("husband_id", "husband"), ("wife_id", "wife")):
                reference = str(family.get(field) or "").strip()
                if reference and self._resolve_one(reference, people_aliases) is None:
                    categories["dangling_person_references"].append(self._family_issue(
                        "dangling_person_references", "Critical", family,
                        f"Family {label} reference '{reference}' does not resolve to a person.",
                    ))
        for link in family_children:
            family_reference = str(link.get("family_id") or "")
            child_reference = str(link.get("child_id") or "")
            family_ids = family_aliases.get(family_reference, set())
            if not family_ids:
                categories["dangling_family_references"].append(DataQualityIssue(
                    category="dangling_family_references",
                    issue_type=CATEGORY_LABELS["dangling_family_references"],
                    severity="Critical", entity_type="family", database_id=None,
                    gedcom_id=family_reference, display_name=f"Family {family_reference or '-'}",
                    explanation=f"Family-child link references missing family '{family_reference}'.",
                ))
            if child_reference and self._resolve_one(child_reference, people_aliases) is None:
                family = families_by_id[min(family_ids)] if family_ids else None
                if family:
                    categories["dangling_person_references"].append(self._family_issue(
                        "dangling_person_references", "Critical", family,
                        f"Child reference '{child_reference}' does not resolve to a person.",
                    ))

    def _check_duplicate_ids(
        self,
        categories: dict[str, list[DataQualityIssue]],
        people: list[dict[str, Any]],
        families: list[dict[str, Any]],
    ) -> None:
        for gedcom_id, records in self._duplicate_groups(people):
            first = records[0]
            ids = ", ".join(str(record["id"]) for record in records)
            categories["duplicate_gedcom_ids"].append(self._person_issue(
                "duplicate_gedcom_ids", "Critical", first,
                f"GEDCOM ID '{gedcom_id}' is used by people with database IDs: {ids}.",
            ))
        for gedcom_id, records in self._duplicate_groups(families):
            first = records[0]
            ids = ", ".join(str(record["id"]) for record in records)
            categories["duplicate_family_gedcom_ids"].append(self._family_issue(
                "duplicate_family_gedcom_ids", "Critical", first,
                f"Family GEDCOM ID '{gedcom_id}' is used by database IDs: {ids}.",
            ))

    @staticmethod
    def _duplicate_groups(records: Iterable[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
        groups: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            gedcom_id = str(record.get("gedcom_id") or "").strip()
            if gedcom_id:
                groups.setdefault(gedcom_id, []).append(record)
        return [(key, groups[key]) for key in sorted(groups) if len(groups[key]) > 1]

    def _check_possible_duplicate_people(
        self,
        categories: dict[str, list[DataQualityIssue]],
        people: list[dict[str, Any]],
    ) -> None:
        groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for person in people:
            key = (self._normalize(person.get("first_name")), self._normalize(person.get("last_name")))
            if all(key):
                groups.setdefault(key, []).append(person)
        for key in sorted(groups):
            records = groups[key]
            for index, left in enumerate(records):
                for right in records[index + 1:]:
                    left_year = self.parse_date(left.get("birth_date")).year
                    right_year = self.parse_date(right.get("birth_date")).year
                    if left_year and right_year and left_year != right_year:
                        continue
                    categories["possible_duplicate_people"].append(self._person_issue(
                        "possible_duplicate_people", "Warning", left,
                        f"Possible duplicate of database ID {right['id']} ({self._display_name(right)}).",
                    ))

    def _check_person_dates(
        self,
        categories: dict[str, list[DataQualityIssue]],
        people: list[dict[str, Any]],
    ) -> tuple[dict[int, DataQualityDate], dict[int, DataQualityDate]]:
        births: dict[int, DataQualityDate] = {}
        deaths: dict[int, DataQualityDate] = {}
        for person in people:
            person_id = int(person["id"])
            birth = self.parse_date(person.get("birth_date"))
            death = self.parse_date(person.get("death_date"))
            births[person_id] = birth
            deaths[person_id] = death
            for label, parsed in (("Birth", birth), ("Death", death)):
                if parsed.raw and (not parsed.parseable or not parsed.valid):
                    categories["impossible_dates"].append(self._person_issue(
                        "impossible_dates", "Critical", person,
                        f"{label} date '{parsed.raw}' is not a valid supported date.",
                    ))
            if self._date_key(death) is not None and self._date_key(birth) is not None:
                if self._date_key(death) < self._date_key(birth):
                    categories["death_before_birth"].append(self._person_issue(
                        "death_before_birth", "Critical", person,
                        f"Death date '{death.raw}' is before birth date '{birth.raw}'.",
                    ))
        return births, deaths

    def _check_parent_ages(
        self,
        categories: dict[str, list[DataQualityIssue]],
        families: list[dict[str, Any]],
        children_by_family: Mapping[int, list[str]],
        people_by_id: Mapping[int, dict[str, Any]],
        people_aliases: Mapping[str, set[int]],
        births: Mapping[int, DataQualityDate],
    ) -> None:
        for family in families:
            family_id = int(family["id"])
            parent_ids = {
                self._resolve_one(reference, people_aliases)
                for reference in (family.get("husband_id"), family.get("wife_id"))
            } - {None}
            child_ids = {
                self._resolve_one(reference, people_aliases)
                for reference in children_by_family.get(family_id, ())
            } - {None}
            for parent_id in sorted(parent_ids):
                for child_id in sorted(child_ids):
                    parent_year = births[parent_id].year if births[parent_id].valid else None
                    child_year = births[child_id].year if births[child_id].valid else None
                    if parent_year is None or child_year is None:
                        continue
                    age = child_year - parent_year
                    if age < 0:
                        category, severity = "parent_younger_than_child", "Critical"
                    elif age < 12:
                        category, severity = "parent_age_under_12", "Warning"
                    elif age > 80:
                        category, severity = "parent_age_over_80", "Warning"
                    else:
                        continue
                    categories[category].append(self._person_issue(
                        category, severity, people_by_id[parent_id],
                        f"Parent age at birth of {self._display_name(people_by_id[child_id])} "
                        f"(database ID {child_id}) is {age} years.",
                    ))

    def _check_families(
        self,
        categories: dict[str, list[DataQualityIssue]],
        families: list[dict[str, Any]],
        children_by_family: Mapping[int, list[str]],
        people_aliases: Mapping[str, set[int]],
    ) -> None:
        spouse_pairs: dict[tuple[int, int], list[dict[str, Any]]] = {}
        for family in families:
            husband_id = self._resolve_one(family.get("husband_id"), people_aliases)
            wife_id = self._resolve_one(family.get("wife_id"), people_aliases)
            if husband_id is not None and wife_id is not None:
                spouse_pairs.setdefault(tuple(sorted((husband_id, wife_id))), []).append(family)
            if husband_id is None and wife_id is None:
                categories["families_without_spouses"].append(self._family_issue(
                    "families_without_spouses", "Information", family,
                    "Family has no resolved spouse or partner references.",
                ))
            if not children_by_family.get(int(family["id"])):
                categories["families_without_children"].append(self._family_issue(
                    "families_without_children", "Information", family,
                    "Family has no child links.", context_person_id=husband_id or wife_id,
                ))
        for pair in sorted(spouse_pairs):
            records = spouse_pairs[pair]
            if len(records) > 1:
                ids = ", ".join(str(record["id"]) for record in records)
                categories["duplicate_spouse_pairs"].append(self._family_issue(
                    "duplicate_spouse_pairs", "Warning", records[0],
                    f"Spouse pair {pair[0]} and {pair[1]} occurs in families: {ids}.",
                    context_person_id=pair[0],
                ))

    def _person_issue(
        self, category: str, severity: str, person: Mapping[str, Any], explanation: str
    ) -> DataQualityIssue:
        person_id = int(person["id"])
        return DataQualityIssue(
            category, CATEGORY_LABELS[category], severity, "person", person_id,
            str(person.get("gedcom_id") or ""), self._display_name(person), explanation,
            context_person_id=person_id,
        )

    def _family_issue(
        self,
        category: str,
        severity: str,
        family: Mapping[str, Any],
        explanation: str,
        context_person_id: int | None = None,
    ) -> DataQualityIssue:
        return DataQualityIssue(
            category, CATEGORY_LABELS[category], severity, "family", int(family["id"]),
            str(family.get("gedcom_id") or ""),
            f"Family {family.get('gedcom_id') or family.get('id')}", explanation,
            context_person_id=context_person_id,
        )

    @staticmethod
    def _display_name(person: Mapping[str, Any]) -> str:
        return " ".join(
            value for value in (
                str(person.get("first_name") or "").strip(),
                str(person.get("last_name") or "").strip(),
            ) if value
        ) or "Без имени"

    @staticmethod
    def _normalize(value: object) -> str:
        return normalize_search_text(value)

    @staticmethod
    def _date_key(parsed: DataQualityDate) -> tuple[int, int, int] | None:
        if not parsed.valid or parsed.year is None:
            return None
        return parsed.year, parsed.month or 1, parsed.day or 1

    @staticmethod
    def _issue_sort_key(issue: DataQualityIssue) -> tuple[object, ...]:
        return (
            SEVERITY_ORDER[issue.severity],
            issue.database_id if issue.database_id is not None else -1,
            issue.gedcom_id,
            issue.explanation,
        )