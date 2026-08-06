"""Release validation for the GenealogyDB 2.2 beta candidate."""

from __future__ import annotations

import ast
import html
import json
import re
from dataclasses import asdict

from beta21_validation_service import (
    BLOCKED,
    PASS,
    WARNING,
    Beta21ValidationService,
)
from home_person_service import HomePersonService
from person_favorites_service import PersonFavoritesService
from recent_people_service import RecentPeopleService


CANDIDATE_VERSION = "2.2.0-beta2"
DEVELOPMENT_VERSION = "2.2.0-beta2-dev"


class Beta22ValidationService(Beta21ValidationService):
    """Validate 2.2 navigation persistence plus the inherited release workflows."""

    def __init__(self, configured_database, *, project_root=None):
        super().__init__(configured_database, project_root=project_root)
        self.report_dir = self.project_root / "release" / "2.2-beta-validation"

    def _temporary_checks(self, root):
        checks = super()._temporary_checks(root)
        checks.append(self._navigation_persistence_check(root / "navigation"))
        return checks

    def _navigation_persistence_check(self, root):
        root.mkdir(parents=True, exist_ok=True)
        favorite_path = root / "favorites.json"
        recent_path = root / "recent.json"
        home_path = root / "home.json"
        first_scope, second_scope = "first.db", "second.db"

        first_favorites = PersonFavoritesService(
            favorite_path, database_scope=first_scope
        )
        second_favorites = PersonFavoritesService(
            favorite_path, database_scope=second_scope
        )
        first_recent = RecentPeopleService(recent_path, database_scope=first_scope)
        second_recent = RecentPeopleService(recent_path, database_scope=second_scope)
        first_home = HomePersonService(home_path, database_scope=first_scope)
        second_home = HomePersonService(home_path, database_scope=second_scope)

        first_favorites.add(7)
        second_favorites.add(9)
        first_recent.record(7)
        first_recent.record(11)
        second_recent.record(9)
        first_home.set_id(7)
        second_home.set_id(9)

        persisted = (
            PersonFavoritesService(favorite_path, database_scope=first_scope).list_ids()
            == (7,)
            and PersonFavoritesService(
                favorite_path, database_scope=second_scope
            ).list_ids()
            == (9,)
            and RecentPeopleService(recent_path, database_scope=first_scope).list_ids()
            == (11, 7)
            and RecentPeopleService(recent_path, database_scope=second_scope).list_ids()
            == (9,)
            and HomePersonService(home_path, database_scope=first_scope).get_id() == 7
            and HomePersonService(home_path, database_scope=second_scope).get_id() == 9
        )
        leftovers = tuple(root.rglob("*.tmp"))
        return self._result(
            "navigation.persistence",
            "Quick navigation",
            persisted and not leftovers,
            "favorites, recent people and home person persist independently per database",
            "Navigation sidecars were not isolated, persistent, or atomically cleaned up",
        )

    def _viewer_check(self):
        legacy = super()._viewer_check()
        source = self.viewer_path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        methods = {
            node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
        }
        required_methods = {
            "open_favorites",
            "open_recent_people",
            "open_home_person",
            "clear_home_person",
            "_refresh_person_navigation_views",
        }
        required_labels = {
            "Избранные люди",
            "Недавние люди",
            "Открыть главного человека",
            "Снять главного человека",
        }
        valid = (
            legacy.status == PASS
            and required_methods <= methods
            and all(label in source for label in required_labels)
        )
        return self._result(
            "viewer.navigation-2.2",
            "Viewer",
            valid,
            "2.2 quick-navigation commands and legacy 2.1 viewer contracts are present",
            "Missing 2.2 navigation command or legacy viewer contract",
        )

    def _packaging_check(self):
        required = (
            "build_info.py",
            "build_release.ps1",
            "GenealogyDB.spec",
            "installer/GenealogyDB.iss",
            "README.md",
            "USER_MANUAL.md",
            "CHANGELOG.md",
            "LICENSE",
        )
        missing = tuple(
            path for path in required if not (self.project_root / path).is_file()
        )
        if missing:
            return self._result(
                "packaging.metadata-2.2",
                "Packaging and documentation",
                False,
                f"candidate target: {CANDIDATE_VERSION}",
                f"Missing: {', '.join(missing)}",
            )

        build_info = (self.project_root / "build_info.py").read_text(encoding="utf-8")
        match = re.search(r'APP_VERSION\s*=\s*"([^"]+)"', build_info)
        current_version = match.group(1) if match else ""
        synchronized_files = ("build_release.ps1",)
        synchronized = bool(current_version) and all(
            current_version
            in (self.project_root / relative_path).read_text(encoding="utf-8")
            for relative_path in synchronized_files
        )
        supported = bool(
            re.fullmatch(
                r"2\.2\.0(?:-(?:beta\d+|rc\d+)(?:-dev)?)?",
                current_version,
            )
        )
        return self._result(
            "packaging.metadata-2.2",
            "Packaging and documentation",
            supported and synchronized,
            f"version metadata synchronized at {current_version}; candidate target: {CANDIDATE_VERSION}",
            "2.2 version metadata is missing or inconsistent",
        )

    @staticmethod
    def recommendation(checks):
        if any(check.status == BLOCKED for check in checks):
            return "NOT READY FOR 2.2.0-BETA2"
        if any(check.status == WARNING for check in checks):
            return "READY FOR 2.2.0-BETA2 WITH WARNINGS"
        return "READY FOR 2.2.0-BETA2"

    def export_all(self, report):
        self.report_dir.mkdir(parents=True, exist_ok=True)
        markdown = self._markdown(report)
        payload = json.dumps(
            self._payload(report), ensure_ascii=False, indent=2, sort_keys=True
        ) + "\n"
        values = (
            payload,
            markdown,
            '<!doctype html><meta charset="utf-8"><pre>'
            + html.escape(markdown)
            + "</pre>",
        )
        paths = tuple(
            self.report_dir / f"beta22-validation.{suffix}"
            for suffix in ("json", "md", "html")
        )
        for path, value in zip(paths, values):
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_text(value, encoding="utf-8")
            temporary.replace(path)
        return paths

    @staticmethod
    def _payload(report):
        return {
            "candidate_version": CANDIDATE_VERSION,
            "recommendation": report.recommendation,
            "configured_checksum_before": report.configured_checksum_before,
            "configured_checksum_after": report.configured_checksum_after,
            "checks": [asdict(check) for check in report.checks],
        }

    @staticmethod
    def _markdown(report):
        lines = [
            "# GenealogyDB 2.2 Beta Validation",
            "",
            f"Candidate: **{CANDIDATE_VERSION}**",
            f"Recommendation: **{report.recommendation}**",
            "",
            "| Section | Status | Evidence | Reason |",
            "| --- | --- | --- | --- |",
        ]
        lines.extend(
            f"| {check.section} | {check.status} | {check.evidence} | {check.reason} |"
            for check in report.checks
        )
        return "\n".join(lines) + "\n"
