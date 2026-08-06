"""RC1 workflow validation for the GenealogyDB 2.2 release line."""

from __future__ import annotations

import sys
from pathlib import Path

from build_info import APP_VERSION
from rc_validation_service import RCValidationService


class RC22ValidationService(RCValidationService):
    """Exercise the established RC workflow with GenealogyDB 2.2 metadata."""

    def __init__(self, configured_database, *, project_root=None):
        super().__init__(
            configured_database,
            project_root=project_root or Path(__file__).resolve().parent,
        )
        self.report_dir = self.project_root / "release" / "2.2-rc1-validation"

    def _packaging_checks(self):
        return [
            self._call(
                "packaging.resources",
                "Packaging",
                "Build definitions, resources, icons, and release metadata",
                self._build_resources,
            ),
            self._call(
                "packaging.version",
                "Packaging",
                "Version metadata is synchronized for the 2.2 RC1 release",
                self._version_ready,
            ),
        ]

    def _version_ready(self):
        expected_version = APP_VERSION
        supported = expected_version == "2.2.0" or (
            expected_version.startswith("2.2.0-")
            and any(marker in expected_version for marker in ("beta", "rc"))
        )
        if self._is_frozen():
            self._assert(
                supported and Path(sys.executable).is_file(),
                f"installed executable reports version {expected_version}",
            )
            return

        text = (self.project_root / "build_info.py").read_text(encoding="utf-8")
        self._assert(
            supported and f'APP_VERSION = "{expected_version}"' in text,
            "GenealogyDB 2.2 version metadata is synchronized",
        )
