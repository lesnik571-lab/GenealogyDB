from pathlib import Path

from plugin_manager import PluginApp, PluginManager, ReadOnlyPluginData


class FakeRepository:
    def list_people_full(self):
        return [{"id": 1}, {"id": 2}]

    def list_families_raw(self):
        return [{"id": 1}]

    def list_family_children_raw(self):
        return [{"family_id": "F1", "child_id": "I2"}]

    def list_person_events_for_integrity(self):
        return [{"person_id": 1}, {"person_id": 2}]

    def list_person_media(self, person_id):
        return [{"person_id": person_id}] if person_id == 1 else []


class FakeHost:
    def __init__(self):
        self.buttons = []
        self.menus = []
        self.reports = {}
        self.exports = {}

    def add_button(self, label, command):
        self.buttons.append((label, command))

    def add_menu(self, menu_name, label, command):
        self.menus.append((menu_name, label, command))

    def add_report(self, name, provider):
        self.reports[name] = provider
        return lambda: provider()

    def add_export(self, name, exporter):
        self.exports[name] = exporter
        return lambda: None


def build_app():
    host = FakeHost()
    app = PluginApp(
        ReadOnlyPluginData(FakeRepository()), host.add_button, host.add_menu,
        host.add_report, host.add_export,
    )
    return app, host


def test_manager_loads_valid_plugin_and_continues_after_crash(tmp_path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "a_valid.py").write_text(
        "plugin_name = 'Valid'\nplugin_version = '1.2'\n"
        "def register(app):\n    app.add_viewer_button('Valid', lambda: None)\n",
        encoding="utf-8",
    )
    (plugin_dir / "b_crash.py").write_text(
        "plugin_name = 'Crash'\nplugin_version = '1.0'\n"
        "def register(app):\n    raise RuntimeError('broken plugin')\n",
        encoding="utf-8",
    )
    app, host = build_app()
    manager = PluginManager(plugin_dir, tmp_path / "plugin.log")

    loaded = manager.load_plugins(app)

    assert [plugin.name for plugin in loaded] == ["Valid"]
    assert [button[0] for button in host.buttons] == ["Valid"]
    log = (tmp_path / "plugin.log").read_text(encoding="utf-8")
    assert "Loaded Valid 1.2" in log
    assert "RuntimeError: broken plugin" in log


def test_manager_rejects_repository_imports_and_direct_sql_calls(tmp_path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / "repository_access.py").write_text(
        "import repository\nplugin_name='Unsafe'\nplugin_version='1'\n"
        "def register(app):\n    pass\n",
        encoding="utf-8",
    )
    (plugin_dir / "sql_access.py").write_text(
        "plugin_name='SQL'\nplugin_version='1'\n"
        "def register(app):\n    app.connection.execute('SELECT 1')\n",
        encoding="utf-8",
    )
    app, _host = build_app()

    loaded = PluginManager(plugin_dir, tmp_path / "plugin.log").load_plugins(app)

    assert loaded == ()
    log = (tmp_path / "plugin.log").read_text(encoding="utf-8")
    assert log.count("forbidden") == 2


def test_read_only_data_returns_immutable_copies_and_statistics():
    data = ReadOnlyPluginData(FakeRepository())

    statistics = data.statistics()

    assert dict(statistics) == {
        "People": 2, "Families": 1, "Events": 2, "Attachments": 1,
    }
    try:
        data.people()[0]["id"] = 9
    except TypeError:
        pass
    else:
        raise AssertionError("Plugin data must be immutable")


def test_example_statistics_plugin_registers_button_menu_and_report(tmp_path):
    app, host = build_app()
    source = Path("plugins/statistics.py")
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    (plugin_dir / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    loaded = PluginManager(plugin_dir, tmp_path / "plugin.log").load_plugins(app)

    assert [plugin.name for plugin in loaded] == ["Statistics"]
    assert [button[0] for button in host.buttons] == ["Statistics"]
    assert [(menu, label) for menu, label, _command in host.menus] == [("Plugins", "Statistics")]
    assert dict(host.reports["Statistics"]()) == {
        "People": 2, "Families": 1, "Events": 2, "Attachments": 1,
    }
    destination = tmp_path / "statistics.csv"
    host.exports["Statistics CSV"](destination)
    assert destination.read_text(encoding="utf-8").splitlines() == [
        "metric,count", "People,2", "Families,1", "Events,2", "Attachments,1",
    ]