import inspect
from pathlib import Path

from beta_stabilization_service import BetaStabilizationService, ReadinessCheck
from build_info import APP_VERSION
from database import initialize_database
from repository.person_repository import PersonRepository
from viewer import GenealogyViewer


def build_service(tmp_path):
    database_path = tmp_path / "genealogy.db"
    initialize_database(database_path)
    repository = PersonRepository(database_path)
    root = tmp_path / "project"
    root.mkdir()
    for relative, content in {
        "schema.sql": "schema",
        "USER_MANUAL.md": f"GenealogyDB {APP_VERSION}",
        "CHANGELOG.md": f"## [{APP_VERSION}]",
        "GenealogyDB.spec": "name='GenealogyDB'",
        "build_info.py": f'APP_VERSION = "{APP_VERSION}"',
        "build_release.ps1": "param()",
        "release_center_service.py": "from build_info import APP_VERSION\n",
        "installer/GenealogyDB.iss": f'#define MyAppVersion "{APP_VERSION}"',
        "resources/default_config.json": "{}",
        "plugins/statistics.py": "def register(app):\n    return None\n",
        "assets/app_icon.svg": "<svg/>",
        "viewer.py": "class GenealogyViewer:\n    def __init__(self):\n        self.example_service = object()\n    def command(self):\n        return self.example_service\n",
        "app.py": "",
        "performance_service.py": "class BoundedLRUCache: pass\n",
    }.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    service = BetaStabilizationService(
        repository,
        project_root=root,
        data_dir=tmp_path / "data",
        log_dir=tmp_path / "logs",
        plugin_dir=root / "plugins",
        config_path=tmp_path / "config.json",
        viewer_path=root / "viewer.py",
    )
    return repository, service, root


def test_readiness_classification_and_blocker_handling():
    assert BetaStabilizationService.classify(()) == "READY"
    assert BetaStabilizationService.classify((ReadinessCheck("test", "warning", "", ""),)) == "READY WITH WARNINGS"
    assert BetaStabilizationService.classify((ReadinessCheck("test", "blocker", "", ""),)) == "NOT READY"


def test_version_and_missing_resource_detection(tmp_path):
    repository, service, root = build_service(tmp_path)
    try:
        check = service._version_check()
        assert check.status == "passed", check.detail
        (root / "installer/GenealogyDB.iss").write_text('#define MyAppVersion "9.9.9"', encoding="utf-8")
        assert service._version_check().status == "warning"
        (root / "schema.sql").unlink()
        assert service._resources_check().status == "blocker"
    finally:
        repository.close()


def test_corrupted_sidecar_recovers_as_warning_without_database_modification(tmp_path):
    repository, service, _root = build_service(tmp_path)
    try:
        before = repository.capture_command_state()
        broken = service.data_dir / "intelligence" / "suggestions.json"
        broken.parent.mkdir(parents=True)
        broken.write_text("{broken", encoding="utf-8")
        check = service._sidecars_check()
        assert check.status == "warning"
        assert "intelligence/suggestions.json" in check.detail
        assert repository.capture_command_state() == before
    finally:
        repository.close()


def test_missing_sidecar_directories_are_recreated(tmp_path):
    repository, service, _root = build_service(tmp_path)
    try:
        assert not (service.data_dir / "source_analysis").exists()
        assert service._directories_check().status == "passed"
        assert (service.data_dir / "source_analysis").is_dir()
    finally:
        repository.close()


def test_viewer_static_checks_find_duplicates_missing_services_and_direct_sql(tmp_path):
    source = tmp_path / "viewer.py"
    source.write_text(
        "import tkinter as tk\n"
        "class GenealogyViewer:\n"
        "    def __init__(self):\n"
        "        self.available_service = object()\n"
        "    def build(self):\n"
        "        menu = tk.Menu()\n"
        "        menu.add_command(label='Duplicate', command=self.present)\n"
        "        menu.add_command(label='Duplicate', command=self.present)\n"
        "        tk.Button(text='Open')\n"
        "        tk.Button(text='Open')\n"
        "        self.repository.conn.execute('SELECT 1')\n"
        "        return self.missing_service\n"
        "    def present(self):\n"
        "        return None\n",
        encoding="utf-8",
    )
    checks = {item.name: item for item in BetaStabilizationService.viewer_checks(source)}
    assert checks["Duplicate Viewer buttons"].status == "warning"
    assert checks["Duplicate menu commands"].status == "warning"
    assert checks["Unavailable services"].status == "blocker"
    assert checks["Direct SQL in viewer.py"].status == "blocker"


def test_reports_are_atomic_deterministic_and_database_is_unchanged(tmp_path):
    repository, service, _root = build_service(tmp_path)
    try:
        before = repository.capture_command_state()
        first = service.analyze()
        second = service.analyze()
        assert [(item.name, item.status, item.detail) for item in first.checks] == [(item.name, item.status, item.detail) for item in second.checks]
        for report_format in ("markdown", "html", "json"):
            path = service.export(first, report_format)
            assert path.exists() and path.stat().st_size > 0
        assert repository.capture_command_state() == before
    finally:
        repository.close()


def test_beta_readiness_menu_and_real_viewer_static_scan_are_headless():
    source = inspect.getsource(GenealogyViewer._create_widgets)
    assert 'label="Готовность Beta"' in source
    checks = {
        item.name: item
        for item in BetaStabilizationService.viewer_checks(Path(__file__).parents[1] / "viewer.py")
    }
    assert checks["Direct SQL in viewer.py"].status == "passed"
    assert checks["Tkinter windows during import"].status == "passed"
