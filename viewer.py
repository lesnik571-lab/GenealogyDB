import json
import re
import queue
import sqlite3
import sys
import threading
import time
import tkinter as tk
import unicodedata
import os
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Mapping

Image = None
ImageTk = None

from config import (
    APP_VERSION,
    BUILD_DATE,
    DATA_DIR,
    DB_NAME,
    LOG_DIR,
    EXPORT_DIR,
    PLUGIN_DIR,
    USER_MANUAL_PATH,
    prepare_user_environment,
)
from advanced_search_service import AdvancedSearchFilters, AdvancedSearchService
from data_quality_service import CATEGORY_DEFINITIONS, DataQualityService
from database import backup_database, initialize_database, restore_database
from family_tree_view_service import FamilyTreeModel, FamilyTreePerson, FamilyTreeViewService
from integrity_service import IntegrityCheckService
from kinship_service import KinshipAnalysis, KinshipService
from logging_service import (
    configure_logging,
    diagnostics_snapshot,
    export_diagnostics,
    get_logger,
    install_exception_logging,
)
from plugin_manager import PluginApp, PluginManager, ReadOnlyPluginData
from recovery_wizard_service import RecoveryRecord, RecoveryWizardService
from relationship_path_service import RelationshipPath, RelationshipPathService
from source_service import CITATION_FIELDS, SOURCE_FIELDS, TARGET_TYPES, SourceService
from timeline_service import FamilyTimelineService, SUPPORTED_EVENT_TYPES, TimelineFilters
from repository import PersonRepository
from repository.person_attachment_service import PersonAttachmentService
from repository.person_event_service import PersonEventService
from repository.person_life_map_service import PersonLifeMapService
from repository.person_timeline_service import PersonTimelineService
from repository.relationship_service import RelationshipService
from undo_manager import (
    AddPersonCommand,
    DeletePersonCommand,
    EditPersonCommand,
    RecoveryUpdateCommand,
    RelationshipEditCommand,
    UndoManager,
)


RECOVERY_FIELD_SPECS = (
    ("first_name", "Имя"),
    ("last_name", "Фамилия"),
    ("sex", "Пол"),
    ("birth_date", "Дата рождения"),
    ("birth_place", "Место рождения"),
    ("death_date", "Дата смерти"),
    ("death_place", "Место смерти"),
    ("occupation", "Занятие"),
)
RECOVERY_WINDOW_GEOMETRY = "1100x800"
RECOVERY_MATCH_WINDOW_GEOMETRY = "900x460"
BATCH_RECOVERY_WINDOW_GEOMETRY = "1420x780"
RECOVERY_EMPTY_FIELD_COLOR = "#fff3bf"
RECOVERY_FILLED_FIELD_COLOR = "white"
RECOVERY_LAYOUT_SAVE_DELAY_MS = 300
RECOVERY_MAX_SHORTCUT_CANDIDATES = 9
PERCENT_SCALE = 100
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600
FAMILY_TREE_MIN_ZOOM = 0.7
FAMILY_TREE_MAX_ZOOM = 2.0
FAMILY_TREE_ZOOM_STEP = 0.1
FAMILY_TREE_CARD_WIDTH = 190
FAMILY_TREE_CARD_HEIGHT = 150
FAMILY_TREE_BACKGROUND = "#f4f6f8"
FAMILY_TREE_MALE_BORDER = "#79b9e7"
FAMILY_TREE_FEMALE_BORDER = "#e8a1b7"
FAMILY_TREE_UNNAMED_BACKGROUND = "#fff3bf"
FAMILY_TREE_UNNAMED_BORDER = "#c63c3c"
FAMILY_TREE_CURRENT_BACKGROUND = "#dceeff"
UI_FONT = "TkDefaultFont"
UI_BUTTON_PAD_X = 10
UI_BUTTON_PAD_Y = 4
UI_OUTER_PADDING = 12
UI_CONTROL_GAP = 8
UI_ROW_GAP = 4


def _install_tk_fallback():
    try:
        probe_root = tk.Tk()
        probe_root.destroy()
    except Exception:
        class _FallbackWidget:
            def __init__(self, *args, **kwargs):
                self._values = {}
                self._text = ""
                self._children = []
                self._items = {}
                self._selection = ()

            def pack(self, *args, **kwargs):
                return None

            def grid(self, *args, **kwargs):
                return None

            def place(self, *args, **kwargs):
                return None

            def grid_rowconfigure(self, *args, **kwargs):
                return None

            def grid_columnconfigure(self, *args, **kwargs):
                return None

            def destroy(self):
                return None

            def winfo_children(self):
                return []

            def bind(self, *args, **kwargs):
                return None

            def config(self, *args, **kwargs):
                return self

            def cget(self, *args, **kwargs):
                return ""

            def update_idletasks(self):
                return None

            def withdraw(self):
                return None

            def title(self, *args, **kwargs):
                return None

            def geometry(self, *args, **kwargs):
                return None

            def transient(self, *args, **kwargs):
                return None

            def grab_set(self, *args, **kwargs):
                return None

            def focus_set(self):
                return None

            def protocol(self, *args, **kwargs):
                return None

            def mainloop(self, *args, **kwargs):
                return None

            def after(self, *args, **kwargs):
                return None

            def attributes(self, *args, **kwargs):
                return None

            def lift(self, *args, **kwargs):
                return None

        class _FallbackEntry(_FallbackWidget):
            def insert(self, index, text):
                self._text = f"{self._text}{text}"

            def get(self, *args):
                return self._text

            def delete(self, *args, **kwargs):
                self._text = ""

        class _FallbackLabel(_FallbackWidget):
            pass

        class _FallbackButton(_FallbackWidget):
            pass

        class _FallbackCheckbutton(_FallbackWidget):
            pass

        class _FallbackMenu(_FallbackWidget):
            def add_command(self, *args, **kwargs):
                return None

            def add_cascade(self, *args, **kwargs):
                return None

            def entryconfig(self, *args, **kwargs):
                return None

        class _FallbackFrame(_FallbackWidget):
            pass

        class _FallbackPanedWindow(_FallbackWidget):
            def add(self, child, **kwargs):
                self._children.append(child)

            def sash_coord(self, index):
                return (240, 240)

            def sash_place(self, index, x, y):
                return None

        class _FallbackLabelFrame(_FallbackWidget):
            pass

        class _FallbackCanvas(_FallbackWidget):
            def create_window(self, *args, **kwargs):
                return 1

            def create_rectangle(self, *args, **kwargs):
                return 5

            def create_line(self, *args, **kwargs):
                return 2

            def create_oval(self, *args, **kwargs):
                return 3

            def create_text(self, *args, **kwargs):
                return 4

            def tag_bind(self, *args, **kwargs):
                return None

            def delete(self, *args, **kwargs):
                return None

            def yview(self, *args, **kwargs):
                return None

            def bbox(self, *args, **kwargs):
                return (0, 0, 800, 1200)

            def itemconfigure(self, *args, **kwargs):
                return None

        class _FallbackText(_FallbackWidget):
            def insert(self, index, text):
                self._text = f"{self._text}{text}"

            def get(self, start=None, end=None):
                return self._text

            def delete(self, *args, **kwargs):
                self._text = ""

            def index(self, index):
                return "1.0"

            def tag_add(self, *args, **kwargs):
                return None

            def tag_configure(self, *args, **kwargs):
                return None

            def tag_bind(self, *args, **kwargs):
                return None

        class _FallbackStringVar(_FallbackWidget):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._value = kwargs.get("value", "")

            def get(self):
                return self._value

            def set(self, value):
                self._value = value

        class _FallbackBooleanVar(_FallbackStringVar):
            def get(self):
                return bool(self._value)

        class _FallbackToplevel(_FallbackWidget):
            pass

        class _FallbackListbox(_FallbackWidget):
            def insert(self, index, text):
                self._children.append(text)

            def delete(self, start, end=None):
                self._children = []

            def get(self, index):
                return self._children[index]

            def curselection(self):
                return ()

        class _FallbackTreeview(_FallbackWidget):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._rows = {}

            def heading(self, *args, **kwargs):
                return None

            def column(self, *args, **kwargs):
                return None

            def configure(self, *args, **kwargs):
                return self

            def insert(self, parent, index, values=None, **kwargs):
                item_id = str(len(self._rows) + 1)
                self._rows[item_id] = {"values": list(values or [])}
                self._children.append(item_id)
                return item_id

            def get_children(self):
                return list(self._children)

            def delete(self, item):
                self._children = [child for child in self._children if child != item]
                self._rows.pop(item, None)

            def item(self, item):
                return self._rows.get(item, {"values": []})

            def selection(self):
                return self._selection

            def tag_configure(self, *args, **kwargs):
                return None

            def identify_row(self, *args, **kwargs):
                return self._selection[0] if self._selection else ""

            def identify_column(self, *args, **kwargs):
                return ""

        class _FallbackScrollbar(_FallbackWidget):
            def set(self, *args, **kwargs):
                return None

        class _FallbackCombobox(_FallbackWidget):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._text = kwargs.get("textvariable", "")

            def current(self, *args, **kwargs):
                return None

        class _FallbackProgressbar(_FallbackWidget):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self._values["value"] = 0
                self._values["maximum"] = kwargs.get("maximum", 100)

            def __setitem__(self, key, value):
                self._values[key] = value

            def __getitem__(self, key):
                return self._values.get(key)

        class _FallbackNotebook(_FallbackWidget):
            def add(self, child, text=""):
                self._children.append((child, text))

        class _FallbackTk(_FallbackWidget):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)

        tk.Tk = _FallbackTk
        tk.Frame = _FallbackFrame
        tk.PanedWindow = _FallbackPanedWindow
        tk.LabelFrame = _FallbackLabelFrame
        tk.Label = _FallbackLabel
        tk.Entry = _FallbackEntry
        tk.Text = _FallbackText
        tk.Canvas = _FallbackCanvas
        tk.Button = _FallbackButton
        tk.Checkbutton = _FallbackCheckbutton
        tk.Menu = _FallbackMenu
        tk.StringVar = _FallbackStringVar
        tk.BooleanVar = _FallbackBooleanVar
        tk.Toplevel = _FallbackToplevel
        tk.Listbox = _FallbackListbox
        ttk.Treeview = _FallbackTreeview
        ttk.Scrollbar = _FallbackScrollbar
        ttk.Combobox = _FallbackCombobox
        ttk.Progressbar = _FallbackProgressbar
        ttk.Notebook = _FallbackNotebook


_install_tk_fallback()


class GenealogyViewer:
    """Coordinate GenealogyDB services and the Tkinter user interface."""

    def __init__(self, root):
        self.root = root
        self._configure_ui_defaults()
        self.repository = PersonRepository(DB_NAME)
        self.relationship_service = RelationshipService(self.repository)
        self.family_tree_view_service = FamilyTreeViewService(self.relationship_service)
        self.relationship_path_service = RelationshipPathService(self.repository)
        self.kinship_service = KinshipService(self.repository)
        self.event_service = PersonEventService(self.repository)
        self.timeline_service = PersonTimelineService(self.repository)
        self.family_timeline_service = FamilyTimelineService(self.repository)
        self.source_service = SourceService(self.repository)
        self.life_map_service = PersonLifeMapService(self.repository, timeline_service=self.timeline_service)
        self.current_person_id = None
        self.current_person_gedcom_id = None
        self._person_dialog = None
        self._person_card_body = None
        self._person_history = []
        self._person_history_index = -1
        self.attachment_service = PersonAttachmentService(self.repository, media_root=DATA_DIR / "media")
        self._card_media_records = []
        self._card_source_records = []
        self._card_photo_records = []
        self._card_document_records = []
        self._card_photo_index = 0
        self._timeline_entries = []
        self._timeline_source_map = {}
        self._life_map_data = {"markers": [], "route": []}
        self._life_map_canvas = None
        self._life_map_tree = None
        self._life_map_progress_label = None
        self._life_map_key_label = None
        self._life_map_current_person_id = None
        self._life_map_geocode_running = False
        self._life_map_geocode_queue = None
        self._life_map_geocode_cancel_event = None
        self._life_map_geocode_thread = None
        self._life_map_marker_lookup = {}
        self._card_photo_image = None
        self.integrity_service = IntegrityCheckService(self.repository, data_dir=DATA_DIR)
        self.data_quality_service = DataQualityService(self.repository)
        self.advanced_search_service = AdvancedSearchService(
            self.repository, DATA_DIR / "advanced_search_last.json"
        )
        self._advanced_search_vars = {}
        self._advanced_search_results = ()
        self._advanced_search_after_id = None
        self._edit_menu = None
        self.undo_manager = UndoManager(self._update_undo_menu)
        self.plugin_manager = PluginManager(
            PLUGIN_DIR, LOG_DIR / "plugins.log"
        )
        self._plugin_button_frame = None
        self._plugin_menu_bar = None
        self._plugin_menus = {}
        self._plugin_reports = {}
        self._plugin_exports = {}
        self.loaded_plugins = ()
        self.recovery_wizard_service = RecoveryWizardService(self.repository)
        self._recovery_window = None
        self._recovery_records = []
        self._recovery_index = -1
        self._recovery_total = 0
        self._recovery_ui_state = {}
        self._recovery_save_after_id = None
        self._batch_window = None
        self._batch_records = []
        self._batch_index = -1
        self._batch_total = 0
        self._batch_started_at = None
        self._batch_last_save = None
        self._batch_candidates = []
        self._family_tree_window = None
        self._family_tree_original_person_id = None
        self._family_tree_history = []
        self._family_tree_history_index = -1
        self._family_timeline_window = None
        self._family_timeline_tree = None
        self._family_timeline_entries = ()
        self._family_timeline_visible_entries = ()
        self._family_timeline_vars = {}
        self._family_timeline_person_ids = {}
        self._family_timeline_status = None
        self._family_timeline_event_control = None
        self._source_window = None
        self._source_tree = None
        self._source_citation_tree = None
        self._source_browser_tree = None
        self._source_usage_map = {}
        self._source_statistics_text = None
        self._relationship_inspector_window = None
        self._relationship_inspector_path = None
        self._kinship_window = None
        self._kinship_analysis = None
        self._kinship_path_tree = None
        self._kinship_canvas = None
        self._data_quality_window = None
        self._data_quality_report = None
        self._data_quality_category_tree = None
        self._data_quality_issue_tree = None
        self._data_quality_severity_var = None
        self._data_quality_issue_map = {}
        self._integrity_report_window = None
        self._integrity_report_body = None
        self._integrity_last_report = None
        self._integrity_scan_running = False
        self._integrity_scan_thread = None
        self._integrity_scan_queue = None
        self._integrity_scan_cancel_event = None
        self._integrity_scan_started_at = None
        self._integrity_progress_window = None
        self._integrity_progress_stage_label = None
        self._integrity_progress_count_label = None
        self._integrity_progress_bar = None
        self._integrity_progress_cancel_button = None
        self.root.title(f"GenealogyDB {APP_VERSION}")
        self.root.geometry("1000x700")
        self._create_widgets()
        plugin_app = PluginApp(
            ReadOnlyPluginData(self.repository),
            self._register_plugin_button,
            self._register_plugin_menu_item,
            self._register_plugin_report,
            self._register_plugin_export,
        )
        self.loaded_plugins = self.plugin_manager.load_plugins(plugin_app)
        self.search_people()

    def _configure_ui_defaults(self):
        """Apply shared typography and control sizing through Tk's option database."""
        option_add = getattr(self.root, "option_add", None)
        if not callable(option_add):
            return
        option_add("*Font", UI_FONT)
        option_add("*Button.padX", UI_BUTTON_PAD_X)
        option_add("*Button.padY", UI_BUTTON_PAD_Y)

    def _create_dialog(self, parent=None):
        """Create a consistently parented transient application dialog."""
        owner = parent or self.root
        dialog = tk.Toplevel(owner)
        dialog.transient(owner)
        return dialog

    def _close_person_card(self):
        if self._person_card_body is not None:
            try:
                self._person_card_body.destroy()
            except Exception:
                pass
            self._person_card_body = None
        if self._person_dialog is not None:
            dialog = self._person_dialog
            self._person_dialog = None
            try:
                dialog.destroy()
            except Exception:
                pass

    def _push_person_history(self, person_id):
        if self._person_history_index >= 0 and self._person_history[self._person_history_index] == person_id:
            return
        if self._person_history_index < len(self._person_history) - 1:
            self._person_history = self._person_history[: self._person_history_index + 1]
        self._person_history.append(person_id)
        self._person_history_index = len(self._person_history) - 1

    def _open_person_from_history(self, person_id):
        self.show_person(person_id, add_to_history=False)

    def _navigate_person_history(self, step):
        next_index = self._person_history_index + step
        if next_index < 0 or next_index >= len(self._person_history):
            return
        self._person_history_index = next_index
        self._open_person_from_history(self._person_history[next_index])

    def _build_relatives_section(self, parent, title, rows, row_index):
        section = tk.LabelFrame(parent, text=title)
        section.grid(row=row_index, column=0, sticky="ew", padx=0, pady=(0, 8))
        section.grid_columnconfigure(0, weight=1)
        if not rows:
            tk.Label(section, text="Нет данных").grid(row=0, column=0, sticky="w", padx=8, pady=6)
            return
        for index, (last_name, first_name, person_reference) in enumerate(rows):
            related_person_id = self.repository.resolve_person_reference(person_reference)
            if related_person_id is None:
                continue
            display_name = self.format_name(last_name, first_name) or "Без имени"
            tk.Button(
                section,
                text=display_name,
                anchor="w",
                command=lambda person_id=related_person_id: self.show_person(person_id),
            ).grid(row=index, column=0, sticky="ew", padx=8, pady=3)

    @staticmethod
    def _open_file_with_default_app(path_value):
        if not path_value:
            return False
        path = Path(path_value).expanduser()
        if not path.exists() or not path.is_file():
            return False
        if hasattr(os, "startfile"):
            os.startfile(str(path))
            return True
        webbrowser.open(path.as_uri())
        return True

    @staticmethod
    def _display_media_item(media_item):
        media_type_label = "Фото" if media_item.get("media_type") == "photo" else "Документ"
        title = media_item.get("title") or Path(media_item.get("file_path") or "").name
        return f"{media_type_label}: {title}"

    @staticmethod
    def _display_source_item(source):
        title = source.get("title") or "Без названия"
        archive = source.get("archive_reference") or ""
        return f"{title} | {archive}" if archive else title

    def _refresh_person_card(self):
        if self.current_person_id:
            self.show_person(self.current_person_id, add_to_history=False)

    def _current_person_reference(self):
        if self.current_person_gedcom_id:
            return self.current_person_gedcom_id
        if self.current_person_id is not None:
            return str(self.current_person_id)
        return ""

    def _resolve_person_id_for_view(self, person_reference):
        resolver = getattr(self.repository, "resolve_person_reference", None)
        if callable(resolver):
            return resolver(person_reference)
        reference_text = str(person_reference or "").strip()
        if not reference_text:
            return None
        if reference_text.isdigit():
            return int(reference_text)
        lookup = getattr(self.repository, "get_person_by_gedcom_id", None)
        if callable(lookup):
            row = lookup(reference_text)
            if row:
                return row[0]
        return None

    @staticmethod
    def _relationship_type_labels():
        return {
            "marriage": "Брак",
            "former_spouse": "Бывший супруг",
            "civil_partner": "Гражданский партнёр",
            "unknown": "Неизвестно",
        }

    def _relationship_type_label(self, relationship_type):
        return self._relationship_type_labels().get(relationship_type or "unknown", relationship_type or "unknown")

    def _person_picker_row_text(self, person):
        return person.get("display_name") or self.repository.format_person_label(person)

    def _relationship_person_text(self, record):
        person = (record or {}).get("person") or {}
        name = self.format_name(person.get("last_name"), person.get("first_name")) or "Без имени"
        birth_date = person.get("birth_date") or "?"
        reference = person.get("reference") or ""
        relationship_type = self._relationship_type_label(record.get("relationship_type"))
        suffix = f" | {relationship_type}" if record.get("link_type") == "partner" else ""
        if record.get("link_type") == "child":
            other_parent = record.get("other_parent") or {}
            if other_parent:
                other_name = self.format_name(other_parent.get("last_name"), other_parent.get("first_name")) or "Без имени"
                suffix += f" | другой родитель: {other_name}"
        return f"ID {person.get('id')} | {name} | р. {birth_date} | ref {reference}{suffix}"

    @staticmethod
    def _selected_dialog_record(listbox):
        selection = listbox.curselection()
        if not selection:
            return None
        records = getattr(listbox, "_records", [])
        index = selection[0]
        if index >= len(records):
            return None
        return records[index]

    def _choose_person(self, title, exclude_reference=None):
        dialog = self._create_dialog(self._person_dialog or self.root)
        dialog.title(title)
        dialog.geometry("720x420")
        dialog.grab_set()

        result = {"reference": None}

        tk.Label(dialog, text="Поиск по имени, фамилии, дате рождения, GEDCOM ID или DB ID").pack(anchor="w", padx=12, pady=(12, 6))
        search_frame = tk.Frame(dialog)
        search_frame.pack(fill="x", padx=12, pady=(0, 8))
        search_entry = tk.Entry(search_frame)
        search_entry.pack(side="left", fill="x", expand=True)
        listbox = tk.Listbox(dialog, height=14)
        listbox.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        def reload_people():
            query = search_entry.get().strip()
            rows = self.relationship_service.list_people(query=query, exclude_person_reference=exclude_reference)
            listbox.delete(0, tk.END)
            listbox._records = rows
            for person in rows:
                listbox.insert("end", self._person_picker_row_text(person))

        def choose_selected():
            record = self._selected_dialog_record(listbox)
            if not record:
                messagebox.showwarning("Выбор", "Сначала выберите человека.", parent=dialog)
                return
            result["reference"] = record.get("reference")
            dialog.destroy()

        tk.Button(search_frame, text="Найти", command=reload_people).pack(side="left", padx=(8, 0))
        tk.Button(search_frame, text="Выбрать", command=choose_selected).pack(side="left", padx=(8, 0))
        tk.Button(search_frame, text="Отмена", command=dialog.destroy).pack(side="left", padx=(8, 0))
        listbox.bind("<Double-1>", lambda _event: choose_selected())

        reload_people()
        wait_window = getattr(dialog, "wait_window", None)
        if callable(wait_window):
            wait_window(dialog)
        return result["reference"]

    def _prompt_relationship_type(self, title, default_value="unknown"):
        dialog = self._create_dialog(self._person_dialog or self.root)
        dialog.title(title)
        dialog.geometry("360x140")
        dialog.grab_set()

        labels = self._relationship_type_labels()
        values = list(labels.keys())
        result = {"value": None}
        var = tk.StringVar(value=default_value if default_value in values else "unknown")

        frame = tk.Frame(dialog)
        frame.pack(fill="both", expand=True, padx=12, pady=12)
        tk.Label(frame, text="Тип отношений").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Combobox(frame, textvariable=var, values=[labels[value] for value in values], state="readonly").grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=4)

        def save():
            selected_label = var.get().strip()
            for key, label in labels.items():
                if label == selected_label:
                    result["value"] = key
                    break
            if result["value"] is None and selected_label in values:
                result["value"] = selected_label
            dialog.destroy()

        tk.Button(frame, text="Сохранить", command=save).grid(row=1, column=1, sticky="e", pady=(12, 0))
        tk.Button(frame, text="Отмена", command=dialog.destroy).grid(row=1, column=0, sticky="w", pady=(12, 0))
        frame.grid_columnconfigure(1, weight=1)

        wait_window = getattr(dialog, "wait_window", None)
        if callable(wait_window):
            wait_window(dialog)
        return result["value"]

    def _collect_person_form_data(self, title):
        dialog = self._create_dialog(self._person_dialog or self.root)
        dialog.title(title)
        dialog.geometry("520x360")
        dialog.grab_set()

        result = {"person": None}
        fields = {}
        form = tk.Frame(dialog)
        form.pack(fill="both", expand=True, padx=12, pady=12)

        row_specs = [
            ("Имя", "first_name"),
            ("Фамилия", "last_name"),
            ("Пол", "sex"),
            ("Дата рождения", "birth_date"),
            ("Место рождения", "birth_place"),
            ("Примечание", "note"),
        ]
        for row_index, (label, key) in enumerate(row_specs):
            tk.Label(form, text=label).grid(row=row_index, column=0, sticky="w", pady=4)
            if key == "sex":
                var = tk.StringVar(value="")
                ttk.Combobox(form, textvariable=var, values=["", "M", "F"], state="readonly").grid(row=row_index, column=1, sticky="ew", padx=(8, 0), pady=4)
                fields[key] = var
            else:
                entry = tk.Entry(form)
                entry.grid(row=row_index, column=1, sticky="ew", padx=(8, 0), pady=4)
                fields[key] = entry

        def save():
            payload = {
                "gedcom_id": None,
                "first_name": fields["first_name"].get().strip(),
                "last_name": fields["last_name"].get().strip(),
                "sex": fields["sex"].get().strip(),
                "birth_date": fields["birth_date"].get().strip(),
                "birth_place": fields["birth_place"].get().strip(),
                "note": fields["note"].get().strip(),
            }
            if not payload["first_name"] or not payload["last_name"]:
                messagebox.showerror("Ошибка", "Имя и фамилия обязательны", parent=dialog)
                return
            result["person"] = payload
            dialog.destroy()

        tk.Button(form, text="Создать", command=save).grid(row=len(row_specs), column=1, sticky="e", pady=(12, 0))
        tk.Button(form, text="Отмена", command=dialog.destroy).grid(row=len(row_specs), column=0, sticky="w", pady=(12, 0))
        form.grid_columnconfigure(1, weight=1)

        wait_window = getattr(dialog, "wait_window", None)
        if callable(wait_window):
            wait_window(dialog)
        return result["person"]

    def _choose_other_parent(self, current_person_reference):
        state = self.relationship_service.get_relationship_editor_state(current_person_reference)
        partners = state.get("partners", [])
        if not partners:
            return ""

        dialog = self._create_dialog(self._person_dialog or self.root)
        dialog.title("Выберите второго родителя")
        dialog.geometry("620x320")
        dialog.grab_set()

        result = {"reference": ""}
        tk.Label(dialog, text="Можно оставить ребёнка с одним известным родителем.").pack(anchor="w", padx=12, pady=(12, 6))
        listbox = tk.Listbox(dialog, height=10)
        listbox.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        records = []
        listbox.insert("end", "Без второго родителя")
        records.append({"person_reference": ""})
        for partner in partners:
            listbox.insert("end", self._relationship_person_text(partner))
            records.append(partner)
        listbox._records = records

        def choose_selected():
            record = self._selected_dialog_record(listbox)
            if record is None:
                messagebox.showwarning("Выбор", "Сначала выберите вариант.", parent=dialog)
                return
            result["reference"] = record.get("person_reference") or ""
            dialog.destroy()

        controls = tk.Frame(dialog)
        controls.pack(fill="x", padx=12, pady=(0, 12))
        tk.Button(controls, text="Выбрать", command=choose_selected).pack(side="left")
        tk.Button(controls, text="Другой человек", command=lambda: result.update({"reference": self._choose_person("Выберите второго родителя", exclude_reference=current_person_reference) or ""}) or dialog.destroy()).pack(side="left", padx=(8, 0))
        tk.Button(controls, text="Отмена", command=dialog.destroy).pack(side="left", padx=(8, 0))

        wait_window = getattr(dialog, "wait_window", None)
        if callable(wait_window):
            wait_window(dialog)
        return result["reference"]

    def _reload_relationship_editor(self, person_reference, parent_listbox, partner_listbox, child_listbox):
        state = self.relationship_service.get_relationship_editor_state(person_reference)
        for listbox, key in [(parent_listbox, "parents"), (partner_listbox, "partners"), (child_listbox, "children")]:
            listbox.delete(0, tk.END)
            records = state.get(key, [])
            listbox._records = records
            if not records:
                listbox.insert("end", "Нет данных")
                continue
            for record in records:
                listbox.insert("end", self._relationship_person_text(record))

    def _apply_relationship_change(self, callback):
        try:
            self._get_undo_manager().execute(RelationshipEditCommand(self.repository, callback))
        except ValueError as error:
            messagebox.showerror("Ошибка", str(error), parent=self._person_dialog or self.root)
            return False
        self.refresh_views()
        self._refresh_person_card()
        return True

    def _clear_integrity_report_body(self):
        if self._integrity_report_body is None:
            return
        for child in self._integrity_report_body.winfo_children():
            child.destroy()

    @staticmethod
    def _filter_data_quality_issues(report, category="all", severity="All"):
        if report is None:
            return ()
        return tuple(
            issue for issue in report.issues
            if (category == "all" or issue.category == category)
            and (severity == "All" or issue.severity == severity)
        )

    def open_data_quality_center(self):
        if self._data_quality_window is not None:
            for method_name in ("deiconify", "lift", "focus_set"):
                method = getattr(self._data_quality_window, method_name, None)
                if callable(method):
                    method()
            self._refresh_data_quality_report()
            return

        dialog = self._create_dialog()
        dialog.title("Качество данных")
        dialog.geometry("1240x720")
        self._data_quality_window = dialog

        def close_window():
            self._data_quality_window = None
            self._data_quality_category_tree = None
            self._data_quality_issue_tree = None
            self._data_quality_issue_map = {}
            dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", close_window)
        toolbar = tk.Frame(dialog)
        toolbar.pack(fill="x", padx=12, pady=12)
        tk.Button(toolbar, text="Обновить", command=self._refresh_data_quality_report).pack(side="left")
        tk.Button(toolbar, text="Экспорт CSV", command=self._export_data_quality_csv).pack(side="left", padx=(8, 0))
        tk.Button(toolbar, text="Открыть выбранное", command=self._open_selected_data_quality_issue).pack(side="left", padx=(8, 0))
        tk.Label(toolbar, text="Важность:").pack(side="left", padx=(24, 6))
        self._data_quality_severity_var = tk.StringVar(value="All")
        severity_box = ttk.Combobox(
            toolbar,
            textvariable=self._data_quality_severity_var,
            values=("All", "Critical", "Warning", "Information"),
            state="readonly",
            width=14,
        )
        severity_box.pack(side="left")
        severity_box.bind("<<ComboboxSelected>>", lambda _event: self._render_data_quality_issues())
        tk.Button(toolbar, text="Закрыть", command=close_window).pack(side="right")

        body = ttk.Panedwindow(dialog, orient="horizontal")
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        category_frame = tk.Frame(body)
        issue_frame = tk.Frame(body)
        body.add(category_frame, weight=1)
        body.add(issue_frame, weight=4)

        category_tree = ttk.Treeview(category_frame, columns=("category", "count"), show="headings", selectmode="browse")
        category_tree.heading("category", text="Категория")
        category_tree.heading("count", text="Количество")
        category_tree.column("category", width=245, anchor="w")
        category_tree.column("count", width=80, anchor="e", stretch=False)
        category_scroll = ttk.Scrollbar(category_frame, orient="vertical", command=category_tree.yview)
        category_tree.configure(yscrollcommand=category_scroll.set)
        category_tree.pack(side="left", fill="both", expand=True)
        category_scroll.pack(side="right", fill="y")
        category_tree.bind("<<TreeviewSelect>>", lambda _event: self._render_data_quality_issues())
        self._data_quality_category_tree = category_tree

        columns = ("issue_type", "severity", "database_id", "gedcom_id", "display_name", "explanation")
        issue_tree = ttk.Treeview(issue_frame, columns=columns, show="headings", selectmode="browse")
        headings = {
            "issue_type": "Тип проблемы", "severity": "Важность", "database_id": "ID базы",
            "gedcom_id": "GEDCOM ID", "display_name": "Имя", "explanation": "Описание",
        }
        widths = {"issue_type": 190, "severity": 90, "database_id": 85, "gedcom_id": 100, "display_name": 170, "explanation": 390}
        for column in columns:
            issue_tree.heading(column, text=headings[column], command=lambda name=column: self._sort_data_quality_table(name))
            issue_tree.column(column, width=widths[column], anchor="w")
        issue_scroll = ttk.Scrollbar(issue_frame, orient="vertical", command=issue_tree.yview)
        issue_tree.configure(yscrollcommand=issue_scroll.set)
        issue_tree.pack(side="left", fill="both", expand=True)
        issue_scroll.pack(side="right", fill="y")
        issue_tree.bind("<Double-1>", lambda _event: self._open_selected_data_quality_issue())
        self._data_quality_issue_tree = issue_tree
        self._refresh_data_quality_report()

    def _selected_data_quality_category(self):
        tree = self._data_quality_category_tree
        if tree is None or not tree.selection():
            return "all"
        return tree.selection()[0]

    def _refresh_data_quality_report(self):
        self._data_quality_report = self.data_quality_service.analyze()
        tree = self._data_quality_category_tree
        if tree is None:
            return
        selected = self._selected_data_quality_category()
        for item_id in tree.get_children(""):
            tree.delete(item_id)
        tree.insert("", "end", iid="all", values=("Все проблемы", len(self._data_quality_report.issues)))
        for category, label in CATEGORY_DEFINITIONS:
            tree.insert("", "end", iid=category, values=(label, self._data_quality_report.counters[category]))
        available = tree.get_children("")
        tree.selection_set(selected if selected in available else "all")
        self._render_data_quality_issues()

    def _render_data_quality_issues(self):
        tree = self._data_quality_issue_tree
        if tree is None:
            return
        severity = self._data_quality_severity_var.get() if self._data_quality_severity_var is not None else "All"
        issues = self._filter_data_quality_issues(
            self._data_quality_report, self._selected_data_quality_category(), severity
        )
        for item_id in tree.get_children(""):
            tree.delete(item_id)
        self._data_quality_issue_map = {}
        for index, issue in enumerate(issues):
            item_id = f"issue-{index}"
            tree.insert("", "end", iid=item_id, values=(
                issue.issue_type, issue.severity,
                issue.database_id if issue.database_id is not None else "",
                issue.gedcom_id, issue.display_name, issue.explanation,
            ))
            self._data_quality_issue_map[item_id] = issue

    def _sort_data_quality_table(self, column, descending=False):
        tree = self._data_quality_issue_tree
        if tree is None:
            return

        def sort_value(item_id):
            value = tree.set(item_id, column)
            if column == "database_id":
                return int(value) if str(value).isdigit() else -1
            return str(value).casefold()

        items = sorted(tree.get_children(""), key=sort_value, reverse=descending)
        for position, item_id in enumerate(items):
            tree.move(item_id, "", position)
        tree.heading(column, command=lambda: self._sort_data_quality_table(column, not descending))

    def _open_selected_data_quality_issue(self):
        tree = self._data_quality_issue_tree
        if tree is None or not tree.selection():
            messagebox.showinfo("Качество данных", "Выберите проблему в таблице.")
            return
        issue = self._data_quality_issue_map.get(tree.selection()[0])
        if issue is None:
            return
        if issue.entity_type == "person" and issue.database_id is not None:
            self.show_person(issue.database_id)
        elif issue.context_person_id is not None:
            self.show_person(issue.context_person_id)
        else:
            self._show_data_quality_family_context(issue)

    def _show_data_quality_family_context(self, issue):
        family = next(
            (item for item in self.repository.list_families_raw() if item["id"] == issue.database_id),
            None,
        )
        dialog = self._create_dialog(self._data_quality_window or self.root)
        dialog.title("Сведения о семье")
        dialog.geometry("620x340")
        rows = [
            ("Проблема", issue.issue_type), ("Важность", issue.severity),
            ("ID базы", issue.database_id if issue.database_id is not None else "-"),
            ("GEDCOM ID", issue.gedcom_id or "-"), ("Описание", issue.explanation),
        ]
        if family:
            rows.extend([
                ("Супруг / партнер 1", family.get("husband_id") or "-"),
                ("Супруг / партнер 2", family.get("wife_id") or "-"),
                ("Тип отношений", family.get("relationship_type") or "unknown"),
            ])
        for row, (label, value) in enumerate(rows):
            tk.Label(dialog, text=f"{label}:", font=("TkDefaultFont", 9, "bold")).grid(row=row, column=0, sticky="nw", padx=12, pady=6)
            tk.Label(dialog, text=str(value), justify="left", wraplength=430).grid(row=row, column=1, sticky="nw", padx=(0, 12), pady=6)
        tk.Button(dialog, text="Закрыть", command=dialog.destroy).grid(row=len(rows), column=1, sticky="e", padx=12, pady=12)

    def _export_data_quality_csv(self):
        if self._data_quality_report is None:
            self._refresh_data_quality_report()
        destination = filedialog.asksaveasfilename(
            title="Сохранить отчет о качестве данных",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("Все файлы", "*.*")],
        )
        if not destination:
            return
        try:
            saved_path = self.data_quality_service.export_csv(self._data_quality_report, destination)
            messagebox.showinfo("Экспорт", f"Отчет сохранен: {saved_path}")
        except OSError as error:
            messagebox.showerror("Ошибка", str(error))

    def open_integrity_report(self):
        if self._integrity_report_window is not None:
            for method_name in ("deiconify", "lift", "focus_set"):
                method = getattr(self._integrity_report_window, method_name, None)
                if callable(method):
                    try:
                        method()
                    except Exception:
                        pass
            self._refresh_integrity_report()
            return

        dialog = self._create_dialog()
        dialog.title("Отчет проверки базы")
        dialog.geometry("980x700")
        self._integrity_report_window = dialog

        def close_window():
            self._integrity_report_body = None
            self._integrity_report_window = None
            dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", close_window)

        controls = tk.Frame(dialog)
        controls.pack(fill="x", padx=12, pady=(12, 8))
        tk.Button(controls, text="Обновить проверку", command=self._refresh_integrity_report).pack(side="left")
        tk.Button(controls, text="Экспорт CSV", command=self._export_integrity_report_csv).pack(side="left", padx=(8, 0))
        tk.Button(controls, text="Закрыть", command=close_window).pack(side="right")

        scroll_host = tk.Frame(dialog)
        scroll_host.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        canvas = tk.Canvas(scroll_host, highlightthickness=0)
        scrollbar = ttk.Scrollbar(scroll_host, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        body = tk.Frame(canvas)
        body_window = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(body_window, width=event.width))
        self._integrity_report_body = body

        self._refresh_integrity_report()

    def _refresh_integrity_report(self):
        if self._integrity_report_body is None or self._integrity_scan_running:
            return

        self._clear_integrity_report_body()
        tk.Label(self._integrity_report_body, text="Проверка выполняется...", justify="left").grid(row=0, column=0, sticky="w", padx=8, pady=6)

        self._integrity_scan_running = True
        self._integrity_scan_queue = queue.Queue()
        self._integrity_scan_cancel_event = threading.Event()
        self._integrity_scan_started_at = time.perf_counter()
        self._show_integrity_progress_window()

        db_path = getattr(self.repository, "db_name", DB_NAME)
        data_dir = DATA_DIR
        result_queue = self._integrity_scan_queue
        cancel_event = self._integrity_scan_cancel_event

        def worker():
            worker_repository = None
            try:
                worker_repository = PersonRepository(db_path)
                worker_service = IntegrityCheckService(worker_repository, data_dir=data_dir)

                def report_progress(stage, processed, total, percent):
                    result_queue.put(("progress", stage, processed, total, percent))

                result = worker_service.run_checks_with_progress(
                    progress_callback=report_progress,
                    cancel_event=cancel_event,
                )
                elapsed = time.perf_counter() - self._integrity_scan_started_at
                result_queue.put(("done", result, elapsed))
            except Exception as error:
                result_queue.put(("error", str(error)))
            finally:
                if worker_repository is not None:
                    worker_repository.close()

        self._integrity_scan_thread = threading.Thread(target=worker, daemon=True)
        self._integrity_scan_thread.start()
        self.root.after(50, self._poll_integrity_scan_queue)

    def _show_integrity_progress_window(self):
        if self._integrity_progress_window is not None:
            try:
                self._integrity_progress_window.destroy()
            except Exception:
                pass

        dialog = self._create_dialog()
        dialog.title("Проверка базы")
        dialog.geometry("460x180")

        container = tk.Frame(dialog)
        container.pack(fill="both", expand=True, padx=12, pady=12)

        self._integrity_progress_stage_label = tk.Label(container, text="Этап: подготовка")
        self._integrity_progress_stage_label.pack(anchor="w")

        self._integrity_progress_count_label = tk.Label(container, text="Обработано: 0 / ?")
        self._integrity_progress_count_label.pack(anchor="w", pady=(6, 8))

        self._integrity_progress_bar = ttk.Progressbar(container, orient="horizontal", mode="determinate", maximum=100)
        self._integrity_progress_bar.pack(fill="x", pady=(0, 10))
        self._integrity_progress_bar["value"] = 0

        self._integrity_progress_cancel_button = tk.Button(container, text="Отмена", command=self._cancel_integrity_scan)
        self._integrity_progress_cancel_button.pack(anchor="e")

        dialog.protocol("WM_DELETE_WINDOW", self._cancel_integrity_scan)
        self._integrity_progress_window = dialog

    def _cancel_integrity_scan(self):
        if not self._integrity_scan_running or self._integrity_scan_cancel_event is None:
            return
        self._integrity_scan_cancel_event.set()
        if self._integrity_progress_cancel_button is not None:
            self._integrity_progress_cancel_button.config(state="disabled", text="Остановка...")

    def _poll_integrity_scan_queue(self):
        if self._integrity_scan_queue is None:
            return

        keep_polling = self._integrity_scan_running
        while True:
            try:
                message = self._integrity_scan_queue.get_nowait()
            except queue.Empty:
                break

            kind = message[0]
            if kind == "progress":
                _kind, stage, processed, total, percent = message
                if self._integrity_progress_stage_label is not None:
                    self._integrity_progress_stage_label.config(text=f"Этап: {stage}")
                if self._integrity_progress_count_label is not None:
                    total_text = str(total) if total else "?"
                    self._integrity_progress_count_label.config(text=f"Обработано: {processed} / {total_text}")
                if self._integrity_progress_bar is not None:
                    self._integrity_progress_bar["value"] = percent
            elif kind == "done":
                _kind, result, elapsed = message
                self._integrity_last_report = result.get("report", {})
                self._integrity_scan_running = False
                keep_polling = False
                if self._integrity_progress_window is not None:
                    try:
                        self._integrity_progress_window.destroy()
                    except Exception:
                        pass
                    self._integrity_progress_window = None

                if self._integrity_report_body is not None:
                    self._clear_integrity_report_body()
                    self._render_integrity_report(self._integrity_last_report)

                if result.get("cancelled"):
                    self.status_label.config(text=f"Проверка отменена через {elapsed:.2f} c")
                    messagebox.showinfo("Проверка базы", "Сканирование отменено пользователем.")
                else:
                    self.status_label.config(text=f"Проверка завершена за {elapsed:.2f} c")
            elif kind == "error":
                _kind, error_text = message
                self._integrity_scan_running = False
                keep_polling = False
                if self._integrity_progress_window is not None:
                    try:
                        self._integrity_progress_window.destroy()
                    except Exception:
                        pass
                    self._integrity_progress_window = None
                messagebox.showerror("Ошибка проверки базы", error_text)

        if keep_polling:
            self.root.after(100, self._poll_integrity_scan_queue)

    def _export_integrity_report_csv(self):
        if self._integrity_scan_running:
            messagebox.showinfo("Проверка базы", "Дождитесь завершения текущего сканирования.")
            return
        if not self._integrity_last_report:
            messagebox.showinfo("Проверка базы", "Сначала выполните проверку базы.")
            return
        destination = filedialog.asksaveasfilename(
            title="Сохранить отчет",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("Все файлы", "*.*")],
        )
        if not destination:
            return
        try:
            saved_path = self.integrity_service.export_report_csv(self._integrity_last_report, destination)
            messagebox.showinfo("Экспорт", f"Отчет сохранен: {saved_path}")
        except OSError as error:
            messagebox.showerror("Ошибка", str(error))

    @staticmethod
    def _severity_text(severity):
        return severity or "Информация"

    def _render_integrity_report(self, report):
        sections = [
            ("Возможные дубликаты", "duplicates"),
            ("Проблемы дат", "date_problems"),
            ("Проблемы связей", "broken_relationships"),
            ("Пустые записи", "empty_people"),
        ]

        row_index = 0
        for section_title, section_key in sections:
            frame = tk.LabelFrame(self._integrity_report_body, text=section_title)
            frame.grid(row=row_index, column=0, sticky="ew", pady=(0, 10))
            frame.grid_columnconfigure(0, weight=1)

            items = report.get(section_key, [])
            if not items:
                tk.Label(frame, text="Нарушений не найдено.").grid(row=0, column=0, sticky="w", padx=8, pady=6)
            elif section_key == "duplicates":
                self._render_duplicate_items(frame, items)
            else:
                self._render_generic_integrity_items(frame, items)

            row_index += 1

        self._integrity_report_body.grid_columnconfigure(0, weight=1)

    def _render_duplicate_items(self, frame, items):
        for index, item in enumerate(items):
            row = tk.Frame(frame)
            row.grid(row=index, column=0, sticky="ew", padx=8, pady=6)
            row.grid_columnconfigure(0, weight=1)

            text = (
                f"[{self._severity_text(item.get('severity'))}] "
                f"{item.get('left_name', '')} ({item.get('left_birth', '')}) ID={item.get('left_person_id', '')} ↔ "
                f"{item.get('right_name', '')} ({item.get('right_birth', '')}) ID={item.get('right_person_id', '')}"
            )
            tk.Label(row, text=text, justify="left", wraplength=700).grid(row=0, column=0, sticky="w")

            actions = tk.Frame(row)
            actions.grid(row=0, column=1, sticky="e", padx=(8, 0))
            left_id = item.get("left_person_id")
            right_id = item.get("right_person_id")
            tk.Button(actions, text="Открыть 1", command=lambda person_id=left_id: self.show_person(person_id)).pack(side="left")
            tk.Button(actions, text="Открыть 2", command=lambda person_id=right_id: self.show_person(person_id)).pack(side="left", padx=(6, 0))
            tk.Button(
                actions,
                text="Не дубликаты",
                command=lambda l_id=left_id, r_id=right_id: self._exclude_duplicate_pair(l_id, r_id),
            ).pack(side="left", padx=(6, 0))

    def _exclude_duplicate_pair(self, left_person_id, right_person_id):
        self.integrity_service.mark_not_duplicate(left_person_id, right_person_id)
        self._refresh_integrity_report()

    def _render_generic_integrity_items(self, frame, items):
        for index, item in enumerate(items):
            row = tk.Frame(frame)
            row.grid(row=index, column=0, sticky="ew", padx=8, pady=6)
            row.grid_columnconfigure(0, weight=1)

            text = f"[{self._severity_text(item.get('severity'))}] {item.get('message', '')}"
            tk.Label(row, text=text, justify="left", wraplength=760).grid(row=0, column=0, sticky="w")

            person_ids = item.get("person_ids", [])
            if person_ids:
                actions = tk.Frame(row)
                actions.grid(row=0, column=1, sticky="e", padx=(8, 0))
                for person_id in person_ids[:3]:
                    tk.Button(actions, text=f"Открыть ID {person_id}", command=lambda pid=person_id: self.show_person(pid)).pack(side="left", padx=(6, 0))

    def _add_media_attachment(self, person_id, media_type):
        file_types = [("Изображения", "*.png *.jpg *.jpeg *.gif *.bmp")] if media_type == "photo" else [("Документы", "*.pdf *.doc *.docx *.txt *.jpg *.jpeg *.png")]
        file_path = filedialog.askopenfilename(title="Выберите файл", filetypes=file_types)
        if not file_path:
            return
        try:
            self.attachment_service.attach_media_file(person_id, media_type, file_path)
            self._refresh_person_card()
        except (OSError, ValueError) as error:
            messagebox.showerror("Ошибка", str(error))

    def _photo_preview_placeholder_text(self):
        return "Фотография отсутствует"

    def _load_photo_preview_image(self, file_path, max_width=320, max_height=220):
        global Image, ImageTk

        path = Path(file_path).expanduser()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError("Файл изображения не найден")

        suffix = path.suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".bmp", ".gif"}:
            raise ValueError("Неподдерживаемый формат изображения")

        if Image is None or ImageTk is None:
            try:
                from PIL import Image as pillow_image
                from PIL import ImageTk as pillow_image_tk
            except Exception:  # pragma: no cover - optional dependency
                pass
            else:
                Image = pillow_image
                ImageTk = pillow_image_tk

        if Image is not None and ImageTk is not None:
            image = Image.open(path)
            image.thumbnail((max_width, max_height), Image.Resampling.LANCZOS)
            return ImageTk.PhotoImage(image)

        image = tk.PhotoImage(file=str(path))
        width = max(1, int(image.width()))
        height = max(1, int(image.height()))
        ratio = min(max_width / width, max_height / height, 1.0)
        if ratio < 1.0:
            step = max(1, int(1 / ratio))
            image = image.subsample(step, step)
        return image

    def _set_photo_index(self, index):
        if not self._card_photo_records:
            self._card_photo_index = 0
            return
        self._card_photo_index = max(0, min(index, len(self._card_photo_records) - 1))

    def _current_photo(self):
        if not self._card_photo_records:
            return None
        if self._card_photo_index >= len(self._card_photo_records):
            self._card_photo_index = len(self._card_photo_records) - 1
        return self._card_photo_records[self._card_photo_index]

    def _open_current_photo_original(self):
        photo = self._current_photo()
        if not photo:
            return
        if not self._open_file_with_default_app(photo.get("file_path")):
            messagebox.showwarning("Фотографии", "Файл фотографии не найден или был перемещен.")

    def _render_photo_preview(self, preview_label, counter_label, title_label, description_label):
        photo = self._current_photo()
        self._card_photo_image = None

        if not photo:
            preview_label.config(image="", text=self._photo_preview_placeholder_text())
            counter_label.config(text="0 из 0")
            title_label.config(text="Название: нет")
            description_label.config(text="Описание: нет")
            return

        counter_label.config(text=f"{self._card_photo_index + 1} из {len(self._card_photo_records)}")
        title_label.config(text=f"Название: {photo.get('title') or 'без названия'}")
        description_label.config(text=f"Описание: {photo.get('description') or 'нет'}")

        try:
            preview = self._load_photo_preview_image(photo.get("file_path"), max_width=320, max_height=220)
            self._card_photo_image = preview
            preview_label.config(image=preview, text="")
        except FileNotFoundError:
            preview_label.config(image="", text="Файл фотографии не найден")
        except Exception:
            preview_label.config(image="", text="Не удалось открыть изображение")

    def _show_previous_photo(self, preview_label, counter_label, title_label, description_label):
        if not self._card_photo_records:
            return
        self._set_photo_index(self._card_photo_index - 1)
        self._render_photo_preview(preview_label, counter_label, title_label, description_label)

    def _show_next_photo(self, preview_label, counter_label, title_label, description_label):
        if not self._card_photo_records:
            return
        self._set_photo_index(self._card_photo_index + 1)
        self._render_photo_preview(preview_label, counter_label, title_label, description_label)

    def _mark_current_photo_primary(self):
        photo = self._current_photo()
        if not photo:
            messagebox.showwarning("Фотографии", "Сначала выберите фотографию.")
            return
        self.attachment_service.set_primary_photo(photo.get("id"))
        self._refresh_person_card()

    def _selected_document(self, documents_listbox):
        selection = documents_listbox.curselection()
        if not selection:
            return None
        index = selection[0]
        if index < 0 or index >= len(self._card_document_records):
            return None
        return self._card_document_records[index]

    def _open_selected_document(self, documents_listbox):
        media = self._selected_document(documents_listbox)
        if media is None:
            messagebox.showwarning("Выбор", "Сначала выберите документ.")
            return
        if not self._open_file_with_default_app(media.get("file_path")):
            messagebox.showwarning("Документы", "Файл документа не найден или был перемещен.")

    def _delete_selected_document(self, documents_listbox):
        media = self._selected_document(documents_listbox)
        if media is None:
            messagebox.showwarning("Выбор", "Сначала выберите документ.")
            return
        if not messagebox.askyesno("Удаление", "Удалить выбранный документ?"):
            return
        self.attachment_service.delete_media(media.get("id"))
        self._refresh_person_card()

    def _rename_selected_document(self, documents_listbox):
        media = self._selected_document(documents_listbox)
        if media is None:
            messagebox.showwarning("Выбор", "Сначала выберите документ.")
            return

        dialog = self._create_dialog(self._person_dialog or self.root)
        dialog.title("Переименовать документ")
        dialog.geometry("420x140")
        dialog.grab_set()

        form = tk.Frame(dialog)
        form.pack(fill="both", expand=True, padx=12, pady=12)
        tk.Label(form, text="Название:").grid(row=0, column=0, sticky="w", pady=4)
        title_entry = tk.Entry(form)
        title_entry.grid(row=0, column=1, sticky="ew", pady=4)
        title_entry.insert(0, media.get("title") or "")
        form.grid_columnconfigure(1, weight=1)

        def save_title():
            self.attachment_service.update_media_title(media.get("id"), title_entry.get().strip())
            dialog.destroy()
            self._refresh_person_card()

        controls = tk.Frame(dialog)
        controls.pack(fill="x", padx=12, pady=(0, 12))
        tk.Button(controls, text="Сохранить", command=save_title).pack(side="left")
        tk.Button(controls, text="Отмена", command=dialog.destroy).pack(side="left", padx=(8, 0))

    def _edit_selected_document_description(self, documents_listbox):
        media = self._selected_document(documents_listbox)
        if media is None:
            messagebox.showwarning("Выбор", "Сначала выберите документ.")
            return

        dialog = self._create_dialog(self._person_dialog or self.root)
        dialog.title("Изменить описание")
        dialog.geometry("520x220")
        dialog.grab_set()

        form = tk.Frame(dialog)
        form.pack(fill="both", expand=True, padx=12, pady=12)
        tk.Label(form, text="Описание:").pack(anchor="w")
        description_entry = tk.Text(form, height=6, wrap="word")
        description_entry.pack(fill="both", expand=True, pady=(6, 0))
        description_entry.insert("1.0", media.get("description") or "")

        def save_description():
            text = description_entry.get("1.0", "end").strip()
            self.attachment_service.update_media_description(media.get("id"), text)
            dialog.destroy()
            self._refresh_person_card()

        controls = tk.Frame(dialog)
        controls.pack(fill="x", padx=12, pady=(0, 12))
        tk.Button(controls, text="Сохранить", command=save_description).pack(side="left")
        tk.Button(controls, text="Отмена", command=dialog.destroy).pack(side="left", padx=(8, 0))

    @staticmethod
    def _document_row_text(media):
        title = media.get("title") or "без названия"
        file_type = Path(media.get("file_path") or "").suffix.lower().lstrip(".") or "unknown"
        description = media.get("description") or ""
        created_at = media.get("created_at") or ""
        return f"{title} | {file_type.upper()} | {description} | {created_at}"

    def _open_selected_source(self, source_listbox):
        selection = source_listbox.curselection()
        if not selection:
            messagebox.showwarning("Выбор", "Сначала выберите источник.")
            return
        source = self._card_source_records[selection[0]]
        source_url = (source.get("source_url") or "").strip()
        if not source_url:
            messagebox.showwarning("URL", "У выбранного источника нет URL.")
            return
        webbrowser.open(source_url)

    def _delete_selected_source(self, source_listbox):
        selection = source_listbox.curselection()
        if not selection:
            messagebox.showwarning("Выбор", "Сначала выберите источник.")
            return
        source = self._card_source_records[selection[0]]
        if not messagebox.askyesno("Удаление", "Удалить выбранный источник?"):
            return
        self.attachment_service.delete_source(source.get("id"))
        self._refresh_person_card()

    def _edit_source_dialog(self, person_id, source=None):
        dialog = self._create_dialog(self._person_dialog or self.root)
        dialog.title("Источник")
        dialog.geometry("520x300")
        dialog.grab_set()

        form = tk.Frame(dialog)
        form.pack(fill="both", expand=True, padx=12, pady=12)

        values = source or {}
        fields = {}
        for row, (label, key) in enumerate([
            ("Название", "title"),
            ("URL", "source_url"),
            ("Архивная ссылка", "archive_reference"),
            ("Примечание", "note"),
        ]):
            tk.Label(form, text=label).grid(row=row, column=0, sticky="w", pady=4)
            entry = tk.Entry(form)
            entry.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=4)
            entry.insert(0, values.get(key, "") or "")
            fields[key] = entry

        form.grid_columnconfigure(1, weight=1)

        def save_source():
            payload = {key: fields[key].get().strip() for key in fields}
            try:
                if source:
                    self.attachment_service.update_source(source.get("id"), **payload)
                else:
                    self.attachment_service.create_source(person_id, **payload)
                dialog.destroy()
                self._refresh_person_card()
            except ValueError as error:
                messagebox.showerror("Ошибка", str(error), parent=dialog)

        buttons = tk.Frame(dialog)
        buttons.pack(fill="x", padx=12, pady=(0, 12))
        tk.Button(buttons, text="Сохранить", command=save_source).pack(side="left")
        tk.Button(buttons, text="Отмена", command=dialog.destroy).pack(side="left", padx=(8, 0))

    def _add_source(self, person_id):
        self._edit_source_dialog(person_id, source=None)

    def _edit_selected_source(self, person_id, source_listbox):
        selection = source_listbox.curselection()
        if not selection:
            messagebox.showwarning("Выбор", "Сначала выберите источник.")
            return
        source = self._card_source_records[selection[0]]
        self._edit_source_dialog(person_id, source=source)

    def _populate_timeline_tree(self, tree):
        for item in tree.get_children():
            tree.delete(item)

        for entry in self._timeline_entries:
            tags = ("contradiction",) if entry.get("is_contradictory") else ()
            tree.insert(
                "",
                "end",
                values=(
                    entry.get("date_text", ""),
                    entry.get("place", ""),
                    entry.get("event_label", ""),
                    entry.get("description", ""),
                    entry.get("source_title", ""),
                    entry.get("event_id") or "",
                    entry.get("source_id") or "",
                    entry.get("event_type") or "",
                ),
                tags=tags,
            )

        try:
            tree.tag_configure("contradiction", foreground="red")
        except Exception:
            pass

    def _refresh_timeline(self, person_id, tree):
        self._timeline_entries = self.timeline_service.build_timeline(person_id)
        self._timeline_source_map = {
            source.get("id"): source
            for source in self.attachment_service.list_sources(person_id)
            if source.get("id") is not None
        }
        self._populate_timeline_tree(tree)

    def _export_timeline_csv(self):
        if not self._timeline_entries:
            messagebox.showinfo("Хронология", "Нет данных для экспорта.")
            return

        destination = filedialog.asksaveasfilename(
            title="Экспорт хронологии в CSV",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("Все файлы", "*.*")],
        )
        if not destination:
            return

        try:
            saved_path = self.timeline_service.export_timeline_csv(self._timeline_entries, destination)
            messagebox.showinfo("Экспорт", f"Хронология сохранена: {saved_path}")
        except OSError as error:
            messagebox.showerror("Ошибка", str(error))

    def _export_timeline_pdf(self):
        if not self._timeline_entries:
            messagebox.showinfo("Хронология", "Нет данных для экспорта.")
            return

        destination = filedialog.asksaveasfilename(
            title="Экспорт хронологии в PDF",
            defaultextension=".pdf",
            filetypes=[("PDF", "*.pdf"), ("Все файлы", "*.*")],
        )
        if not destination:
            return

        try:
            saved_path = self.timeline_service.export_timeline_pdf(self._timeline_entries, destination)
            messagebox.showinfo("Экспорт", f"Хронология сохранена: {saved_path}")
        except OSError as error:
            messagebox.showerror("Ошибка", str(error))

    def _open_timeline_source_record(self, person_id, source_id):
        if not source_id:
            return
        source = self._timeline_source_map.get(source_id)
        if source:
            self._edit_source_dialog(person_id, source=source)

    def _handle_timeline_single_click(self, event, person_id, tree):
        identify_row = getattr(tree, "identify_row", None)
        row_id = ""
        if callable(identify_row):
            try:
                row_id = identify_row(event.y)
            except Exception:
                row_id = ""

        if not row_id:
            selection = tree.selection()
            row_id = selection[0] if selection else ""
        if not row_id:
            return

        item = tree.item(row_id)
        values = item.get("values", [])
        if len(values) < 8:
            return

        column = ""
        identify_column = getattr(tree, "identify_column", None)
        if callable(identify_column):
            try:
                column = identify_column(event.x)
            except Exception:
                column = ""

        if column == "#5":
            return

        event_id = values[5]
        event_type = values[7]
        if event_id:
            try:
                self._manage_person_event(self._person_dialog or self.root, person_id, int(event_id), close_parent_on_save=False)
            except (TypeError, ValueError):
                return
            return

        if event_type in {"birth", "death", "occupation"}:
            self._show_person_editor(person_id)

    def _handle_timeline_double_click(self, event, person_id, tree):
        selection = tree.selection()
        if not selection:
            return

        item = tree.item(selection[0])
        values = item.get("values", [])
        if len(values) < 8:
            return

        source_id = values[6]

        column = ""
        identify_column = getattr(tree, "identify_column", None)
        if callable(identify_column):
            try:
                column = identify_column(event.x)
            except Exception:
                column = ""

        if column == "#5" and source_id:
            try:
                self._open_timeline_source_record(person_id, int(source_id))
            except (TypeError, ValueError):
                return

    def _build_timeline_tab(self, parent, person_id):
        controls = tk.Frame(parent)
        controls.pack(fill="x", padx=8, pady=(8, 6))
        tk.Button(controls, text="Обновить", command=lambda: self._refresh_timeline(person_id, timeline_tree)).pack(side="left")
        tk.Button(controls, text="Экспорт CSV", command=self._export_timeline_csv).pack(side="left", padx=(8, 0))
        tk.Button(controls, text="Экспорт PDF", command=self._export_timeline_pdf).pack(side="left", padx=(8, 0))

        table_frame = tk.Frame(parent)
        table_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        columns = ("date", "place", "type", "description", "source", "event_id", "source_id", "event_type")
        timeline_tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        timeline_tree.heading("date", text="Дата")
        timeline_tree.heading("place", text="Место")
        timeline_tree.heading("type", text="Тип события")
        timeline_tree.heading("description", text="Описание")
        timeline_tree.heading("source", text="Источник")
        timeline_tree.column("date", width=120)
        timeline_tree.column("place", width=170)
        timeline_tree.column("type", width=140)
        timeline_tree.column("description", width=260)
        timeline_tree.column("source", width=200)
        timeline_tree.column("event_id", width=0, stretch=False)
        timeline_tree.column("source_id", width=0, stretch=False)
        timeline_tree.column("event_type", width=0, stretch=False)
        timeline_tree.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=timeline_tree.yview)
        timeline_tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

        timeline_tree.bind("<ButtonRelease-1>", lambda event: self._handle_timeline_single_click(event, person_id, timeline_tree))
        timeline_tree.bind("<Double-1>", lambda event: self._handle_timeline_double_click(event, person_id, timeline_tree))

        hint = tk.Label(
            parent,
            text="Двойной клик по событию: редактирование. Двойной клик по колонке 'Источник': карточка источника.",
            justify="left",
        )
        hint.pack(anchor="w", padx=8, pady=(0, 8))

        self._refresh_timeline(person_id, timeline_tree)

    def _selected_life_map_marker(self):
        if self._life_map_tree is None:
            return None
        selection = self._life_map_tree.selection()
        if not selection:
            return None
        values = self._life_map_tree.item(selection[0]).get("values", [])
        if len(values) < 8:
            return None

        try:
            event_id = int(values[5]) if values[5] not in ("", None) else None
        except (TypeError, ValueError):
            event_id = None

        return {
            "date_text": values[0],
            "place": values[1],
            "event_label": values[2],
            "description": values[3],
            "status": values[4],
            "event_id": event_id,
            "event_type": values[6],
            "normalized_place": values[7],
        }

    def _open_life_map_event_details(self, marker):
        if not marker or self._life_map_current_person_id is None:
            return

        event_id = marker.get("event_id")
        if event_id:
            self._manage_person_event(self._person_dialog or self.root, self._life_map_current_person_id, event_id, close_parent_on_save=False)
            return

        if marker.get("event_type") in {"birth", "death", "occupation"}:
            self._show_person_editor(self._life_map_current_person_id)

    def _life_map_canvas_point(self, latitude, longitude, width=820, height=360):
        x = ((float(longitude) + 180.0) / 360.0) * width
        y = ((90.0 - float(latitude)) / 180.0) * height
        return x, y

    def _redraw_life_map_canvas(self):
        if self._life_map_canvas is None:
            return

        canvas = self._life_map_canvas
        canvas.delete("all")
        self._life_map_marker_lookup = {}

        width = 820
        height = 360
        get_width = getattr(canvas, "winfo_width", None)
        get_height = getattr(canvas, "winfo_height", None)
        if callable(get_width):
            try:
                current_width = int(get_width())
                if current_width > 50:
                    width = current_width
            except Exception:
                pass
        if callable(get_height):
            try:
                current_height = int(get_height())
                if current_height > 50:
                    height = current_height
            except Exception:
                pass

        canvas.create_rectangle(1, 1, width - 1, height - 1, outline="#9fb3c8")
        canvas.create_text(8, 8, anchor="nw", text="Карта жизни (offline)", fill="#445b74")

        route_points = []
        for marker in self._life_map_data.get("route", []):
            lat = marker.get("latitude")
            lng = marker.get("longitude")
            if lat is None or lng is None:
                continue
            route_points.append(self._life_map_canvas_point(lat, lng, width=width, height=height))

        if len(route_points) >= 2:
            for index in range(len(route_points) - 1):
                x1, y1 = route_points[index]
                x2, y2 = route_points[index + 1]
                canvas.create_line(x1, y1, x2, y2, fill="#4b84c2", width=2)

        clustered = {}
        for marker in self._life_map_data.get("markers", []):
            lat = marker.get("latitude")
            lng = marker.get("longitude")
            if lat is None or lng is None:
                continue
            key = (round(float(lat), 2), round(float(lng), 2))
            clustered[key] = clustered.get(key, 0) + 1
            offset = clustered[key] - 1
            x, y = self._life_map_canvas_point(lat, lng, width=width, height=height)
            x += offset * 4
            y += offset * 4

            status = marker.get("geocode_status")
            fill = "#2b7a2b" if status in {"ok", "manual"} else "#b05a2e"
            item_id = canvas.create_oval(x - 5, y - 5, x + 5, y + 5, fill=fill, outline="white")
            canvas.create_text(x + 8, y - 8, anchor="nw", text=marker.get("event_label", ""), fill="#1f2933")
            self._life_map_marker_lookup[item_id] = marker
            canvas.tag_bind(item_id, "<Button-1>", lambda _event, mark=marker: self._open_life_map_event_details(mark))

    def _render_life_map_tree(self):
        if self._life_map_tree is None:
            return

        tree = self._life_map_tree
        for item in tree.get_children():
            tree.delete(item)

        for marker in self._life_map_data.get("markers", []):
            status = marker.get("geocode_status", "missing")
            date_text = marker.get("date_text", "")
            status_text = {
                "ok": "ok",
                "manual": "вручную",
                "failed": "ошибка",
                "needs_key": "нет ключа",
                "missing": "нет координат",
            }.get(status, status)
            tree.insert(
                "",
                "end",
                values=(
                    date_text,
                    marker.get("place", ""),
                    marker.get("event_label", ""),
                    marker.get("description", ""),
                    status_text,
                    marker.get("event_id") or "",
                    marker.get("event_type") or "",
                    marker.get("normalized_place") or "",
                ),
            )

    def _refresh_life_map_data(self, person_id):
        self._life_map_current_person_id = person_id
        self._life_map_data = self.life_map_service.build_map_data(person_id)
        self._render_life_map_tree()
        self._redraw_life_map_canvas()

        if self._life_map_key_label is not None:
            if self._life_map_data.get("geocoding_enabled"):
                self._life_map_key_label.config(text="Геокодирование: ключ настроен")
            else:
                self._life_map_key_label.config(text="Геокодирование не настроено. Укажите ключ в переменной GENEALOGYDB_GEOCODING_API_KEY или введите координаты вручную.")

    def _open_life_map_external(self):
        marker = self._selected_life_map_marker()
        if marker is None:
            marker = next((item for item in self._life_map_data.get("markers", []) if item.get("latitude") is not None), None)
        if marker is None:
            messagebox.showwarning("Карта жизни", "Нет координат для открытия карты.")
            return

        normalized_place = marker.get("normalized_place")
        cache = self.repository.get_geocoding_cache(normalized_place) if normalized_place else None
        latitude = cache.get("latitude") if cache else None
        longitude = cache.get("longitude") if cache else None
        if latitude is None or longitude is None:
            messagebox.showwarning("Карта жизни", "Для выбранного места нет координат.")
            return

        webbrowser.open(f"https://www.openstreetmap.org/?mlat={latitude}&mlon={longitude}#map=11/{latitude}/{longitude}")

    def _export_life_map_kml(self):
        if not self._life_map_data.get("markers"):
            messagebox.showinfo("Карта жизни", "Нет данных для экспорта.")
            return

        destination = filedialog.asksaveasfilename(
            title="Экспорт карты жизни в KML",
            defaultextension=".kml",
            filetypes=[("KML", "*.kml"), ("Все файлы", "*.*")],
        )
        if not destination:
            return

        try:
            saved = self.life_map_service.export_kml(self._life_map_data, destination)
            messagebox.showinfo("Экспорт", f"KML сохранен: {saved}")
        except OSError as error:
            messagebox.showerror("Ошибка", str(error))

    def _edit_life_map_coordinates(self):
        marker = self._selected_life_map_marker()
        if marker is None:
            messagebox.showwarning("Карта жизни", "Сначала выберите запись в таблице.")
            return

        place = marker.get("place", "")
        if not place:
            messagebox.showwarning("Карта жизни", "Для выбранного события не указано место.")
            return

        dialog = self._create_dialog(self._person_dialog or self.root)
        dialog.title("Ручная коррекция координат")
        dialog.geometry("420x220")
        dialog.grab_set()

        form = tk.Frame(dialog)
        form.pack(fill="both", expand=True, padx=12, pady=12)

        tk.Label(form, text="Место:").grid(row=0, column=0, sticky="w", pady=4)
        place_entry = tk.Entry(form)
        place_entry.grid(row=0, column=1, sticky="ew", pady=4)
        place_entry.insert(0, place)

        tk.Label(form, text="Широта:").grid(row=1, column=0, sticky="w", pady=4)
        lat_entry = tk.Entry(form)
        lat_entry.grid(row=1, column=1, sticky="ew", pady=4)

        tk.Label(form, text="Долгота:").grid(row=2, column=0, sticky="w", pady=4)
        lng_entry = tk.Entry(form)
        lng_entry.grid(row=2, column=1, sticky="ew", pady=4)

        normalized_place = marker.get("normalized_place")
        cache = self.repository.get_geocoding_cache(normalized_place) if normalized_place else None
        if cache and cache.get("latitude") is not None and cache.get("longitude") is not None:
            lat_entry.insert(0, str(cache.get("latitude")))
            lng_entry.insert(0, str(cache.get("longitude")))

        form.grid_columnconfigure(1, weight=1)

        def save_manual():
            try:
                self.life_map_service.set_manual_coordinates(place_entry.get().strip(), lat_entry.get().strip(), lng_entry.get().strip())
            except ValueError as error:
                messagebox.showerror("Ошибка", str(error), parent=dialog)
                return
            dialog.destroy()
            if self._life_map_current_person_id is not None:
                self._refresh_life_map_data(self._life_map_current_person_id)

        controls = tk.Frame(dialog)
        controls.pack(fill="x", padx=12, pady=(0, 12))
        tk.Button(controls, text="Сохранить", command=save_manual).pack(side="left")
        tk.Button(controls, text="Отмена", command=dialog.destroy).pack(side="left", padx=(8, 0))

    def _start_life_map_geocoding(self):
        if self._life_map_geocode_running or self._life_map_current_person_id is None:
            return

        self._life_map_geocode_running = True
        self._life_map_geocode_queue = queue.Queue()
        self._life_map_geocode_cancel_event = threading.Event()
        if self._life_map_progress_label is not None:
            self._life_map_progress_label.config(text="Геокодирование: запуск...")

        db_path = getattr(self.repository, "db_name", DB_NAME)
        person_id = self._life_map_current_person_id
        result_queue = self._life_map_geocode_queue
        cancel_event = self._life_map_geocode_cancel_event

        def worker():
            worker_repository = None
            try:
                worker_repository = PersonRepository(db_path)
                worker_timeline_service = PersonTimelineService(worker_repository)
                worker_map_service = PersonLifeMapService(worker_repository, timeline_service=worker_timeline_service)

                def progress(stage, processed, total, percent):
                    result_queue.put(("progress", stage, processed, total, percent))

                summary = worker_map_service.update_missing_coordinates(person_id, progress_callback=progress, cancel_event=cancel_event)
                result_queue.put(("done", summary))
            except Exception as error:
                result_queue.put(("error", str(error)))
            finally:
                if worker_repository is not None:
                    worker_repository.close()

        self._life_map_geocode_thread = threading.Thread(target=worker, daemon=True)
        self._life_map_geocode_thread.start()
        self.root.after(100, self._poll_life_map_geocoding)

    def _poll_life_map_geocoding(self):
        if self._life_map_geocode_queue is None:
            return

        keep_polling = self._life_map_geocode_running
        while True:
            try:
                message = self._life_map_geocode_queue.get_nowait()
            except queue.Empty:
                break

            kind = message[0]
            if kind == "progress":
                _kind, stage, processed, total, percent = message
                if self._life_map_progress_label is not None:
                    total_text = str(total) if total else "?"
                    self._life_map_progress_label.config(text=f"{stage}: {processed}/{total_text} ({percent}%)")
            elif kind == "done":
                _kind, summary = message
                self._life_map_geocode_running = False
                keep_polling = False
                if self._life_map_progress_label is not None:
                    if summary.get("needs_key"):
                        self._life_map_progress_label.config(text="Геокодирование недоступно: не настроен ключ.")
                    else:
                        self._life_map_progress_label.config(
                            text=f"Обновление завершено: успешно {summary.get('updated', 0)}, ошибок {summary.get('failed', 0)}"
                        )
                if self._life_map_current_person_id is not None:
                    self._refresh_life_map_data(self._life_map_current_person_id)
            elif kind == "error":
                _kind, error_text = message
                self._life_map_geocode_running = False
                keep_polling = False
                if self._life_map_progress_label is not None:
                    self._life_map_progress_label.config(text="Ошибка геокодирования")
                messagebox.showerror("Карта жизни", error_text)

        if keep_polling:
            self.root.after(150, self._poll_life_map_geocoding)

    def _build_life_map_tab(self, parent, person_id):
        controls = tk.Frame(parent)
        controls.pack(fill="x", padx=8, pady=(8, 6))
        tk.Button(controls, text="Обновить координаты", command=self._start_life_map_geocoding).pack(side="left")
        tk.Button(controls, text="Открыть во внешней карте", command=self._open_life_map_external).pack(side="left", padx=(8, 0))
        tk.Button(controls, text="Экспорт KML", command=self._export_life_map_kml).pack(side="left", padx=(8, 0))
        tk.Button(controls, text="Исправить координаты", command=self._edit_life_map_coordinates).pack(side="left", padx=(8, 0))

        self._life_map_progress_label = tk.Label(parent, text="")
        self._life_map_progress_label.pack(anchor="w", padx=8, pady=(0, 4))

        self._life_map_key_label = tk.Label(parent, text="", justify="left", wraplength=860)
        self._life_map_key_label.pack(anchor="w", padx=8, pady=(0, 6))

        canvas_frame = tk.Frame(parent)
        canvas_frame.pack(fill="x", padx=8, pady=(0, 8))
        self._life_map_canvas = tk.Canvas(canvas_frame, height=360, highlightthickness=1, highlightbackground="#d1d9e0")
        self._life_map_canvas.pack(fill="x", expand=False)

        table_frame = tk.Frame(parent)
        table_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        columns = ("date", "place", "type", "description", "status", "event_id", "event_type", "normalized_place")
        tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        tree.heading("date", text="Дата")
        tree.heading("place", text="Место")
        tree.heading("type", text="Событие")
        tree.heading("description", text="Описание")
        tree.heading("status", text="Координаты")
        tree.column("date", width=120)
        tree.column("place", width=200)
        tree.column("type", width=140)
        tree.column("description", width=280)
        tree.column("status", width=120)
        tree.column("event_id", width=0, stretch=False)
        tree.column("event_type", width=0, stretch=False)
        tree.column("normalized_place", width=0, stretch=False)
        tree.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

        tree.bind("<Double-1>", lambda _event: self._open_life_map_event_details(self._selected_life_map_marker()))
        self._life_map_tree = tree

        self._refresh_life_map_data(person_id)

    def _recovery_ui_state_path(self) -> Path:
        return Path(getattr(self, "_recovery_ui_path", DATA_DIR / "recovery_wizard_ui.json"))

    def _load_recovery_ui_state(self) -> dict[str, Any]:
        try:
            state = json.loads(self._recovery_ui_state_path().read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            state = {}
        return state if isinstance(state, dict) else {}

    def _save_recovery_ui_state(self, capture_layout: bool = False) -> None:
        state = dict(getattr(self, "_recovery_ui_state", {}) or {})
        if 0 <= self._recovery_index < len(self._recovery_records):
            state["selected_person_id"] = self._recovery_records[self._recovery_index].person_id
        if capture_layout and self._recovery_window is not None:
            try:
                geometry = self._recovery_window.geometry()
                if geometry:
                    state["geometry"] = geometry
            except Exception:
                pass
            for attribute, key, axis, count in (
                ("_recovery_main_pane", "main_sashes", 0, 1),
                ("_recovery_content_pane", "content_sashes", 1, 2),
            ):
                pane = getattr(self, attribute, None)
                if pane is None:
                    continue
                try:
                    state[key] = [pane.sash_coord(index)[axis] for index in range(count)]
                except Exception:
                    pass
        self._recovery_ui_state = state
        try:
            path = self._recovery_ui_state_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _restore_recovery_splitters(self) -> None:
        state = self._recovery_ui_state
        try:
            for index, position in enumerate(state.get("main_sashes", [])):
                self._recovery_main_pane.sash_place(index, int(position), 0)
            for index, position in enumerate(state.get("content_sashes", [])):
                self._recovery_content_pane.sash_place(index, 0, int(position))
        except (AttributeError, TypeError, ValueError, tk.TclError):
            pass

    def _schedule_recovery_ui_save(self, _event: Any = None) -> None:
        if self._recovery_window is None:
            return
        if self._recovery_save_after_id is not None:
            try:
                self._recovery_window.after_cancel(self._recovery_save_after_id)
            except Exception:
                pass
        self._recovery_save_after_id = self._recovery_window.after(
            RECOVERY_LAYOUT_SAVE_DELAY_MS,
            lambda: self._save_recovery_ui_state(capture_layout=True),
        )

    def _update_recovery_progress(self) -> None:
        if not hasattr(self, "_recovery_progress"):
            return
        remaining = len(self._recovery_records)
        total = max(self._recovery_total, remaining)
        processed = max(0, total - remaining)
        completed = round((processed / total) * PERCENT_SCALE) if total else PERCENT_SCALE
        self._recovery_progress.configure(
            text=f"Processed: {processed} / {total}    Remaining: {remaining}    Completed: {completed}%"
        )

    def _highlight_recovery_empty_fields(self, _event: Any = None) -> None:
        for key, entry in getattr(self, "_recovery_entries", {}).items():
            entry.configure(
                background=(
                    RECOVERY_EMPTY_FIELD_COLOR
                    if not self._recovery_vars[key].get().strip()
                    else RECOVERY_FILLED_FIELD_COLOR
                )
            )
        if hasattr(self, "_recovery_note"):
            note_empty = not self._recovery_note.get("1.0", "end").strip()
            self._recovery_note.configure(
                background=RECOVERY_EMPTY_FIELD_COLOR if note_empty else RECOVERY_FILLED_FIELD_COLOR
            )

    def _focus_next_recovery_field(self, event: Any, index: int) -> str | None:
        if getattr(event, "state", 0) & 0x4:
            return None
        if self._recovery_focus_order:
            self._recovery_focus_order[index % len(self._recovery_focus_order)].focus_set()
        return "break"

    def _open_recovery_relative(self, _event: Any = None) -> None:
        selection = self._recovery_relatives.selection()
        if not selection:
            return
        person_id = self._recovery_relatives.set(selection[0], "person_id")
        if person_id:
            self.show_person(int(person_id))

    @staticmethod
    def _format_batch_duration(seconds: float) -> str:
        total_seconds = max(0, round(seconds or 0))
        hours, remainder = divmod(total_seconds, SECONDS_PER_HOUR)
        minutes, seconds = divmod(remainder, SECONDS_PER_MINUTE)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"

    def _update_batch_progress(self) -> None:
        if not hasattr(self, "_batch_progress"):
            return
        remaining = len(self._batch_records)
        total = max(self._batch_total, remaining)
        processed = max(0, total - remaining)
        percent = round((processed / total) * PERCENT_SCALE) if total else PERCENT_SCALE
        eta = "—"
        if processed and self._batch_started_at is not None:
            elapsed = max(0, time.perf_counter() - self._batch_started_at)
            eta = self._format_batch_duration((elapsed / processed) * remaining)
        self._batch_progress.configure(
            text=(
                f"Processed: {processed}    Remaining: {remaining}    "
                f"Percent: {percent}%    Estimated remaining time: {eta}"
            )
        )

    def _close_batch_recovery(self) -> None:
        if self._batch_window is not None:
            try:
                self._batch_window.destroy()
            except Exception:
                pass
        self._batch_window = None
        self._batch_records = []
        self._batch_index = -1
        self._batch_candidates = []

    @staticmethod
    def _form_data(variables: Mapping[str, Any], note: Any) -> dict[str, str]:
        data = {key: variable.get() for key, variable in variables.items()}
        data["note"] = note.get("1.0", "end").strip()
        return data

    @staticmethod
    def _load_record_form(record: RecoveryRecord, variables: Mapping[str, Any], note: Any) -> None:
        for key, variable in variables.items():
            variable.set(getattr(record, key, ""))
        note.delete("1.0", "end")
        note.insert("1.0", record.note)

    def _batch_form_data(self) -> dict[str, str]:
        return self._form_data(self._batch_vars, self._batch_note)

    def _recovery_form_data(self) -> dict[str, str]:
        return self._form_data(self._recovery_vars, self._recovery_note)

    def _clear_batch_candidates(self) -> None:
        for item_id in self._batch_candidate_tree.get_children():
            self._batch_candidate_tree.delete(item_id)
        self._batch_candidates = []

    def _calculate_batch_candidates(self) -> None:
        self._clear_batch_candidates()
        if self._batch_index < 0 or self._batch_index >= len(self._batch_records):
            return
        record = self._batch_records[self._batch_index]
        try:
            self._batch_candidates = self.recovery_wizard_service.find_matches(
                record.person_id,
                self._batch_form_data(),
            )
        except Exception as exc:
            messagebox.showerror("Пакетный режим", str(exc), parent=self._batch_window)
            return
        for position, candidate in enumerate(self._batch_candidates, start=1):
            birth = " — ".join(value for value in (candidate.birth_date, candidate.birth_place) if value) or "нет данных"
            self._batch_candidate_tree.insert(
                "",
                "end",
                values=(
                    position if position <= RECOVERY_MAX_SHORTCUT_CANDIDATES else "",
                    f"{candidate.confidence}%",
                    candidate.full_name,
                    birth,
                    candidate.gedcom_id or "-",
                    candidate.person_id,
                ),
            )

    def _load_batch_record(self, index: int) -> None:
        if index < 0 or index >= len(self._batch_records):
            return
        self._batch_index = index
        record = self._batch_records[index]
        self._batch_current.configure(
            text=f"ID {record.person_id} | GEDCOM {record.gedcom_id or 'нет'} | Карточка {index + 1} из {len(self._batch_records)}"
        )
        self._load_record_form(record, self._batch_vars, self._batch_note)
        self._batch_list.selection_clear(0, "end")
        self._batch_list.selection_set(index)
        self._batch_list.see(index)
        self._calculate_batch_candidates()
        self._update_batch_progress()

    def _on_batch_select(self, _event: Any = None) -> None:
        selection = self._batch_list.curselection()
        if selection:
            self._load_batch_record(int(selection[0]))

    def _navigate_batch_person(self, step: int) -> str:
        if not self._batch_records:
            return "break"
        if self._batch_index < 0:
            next_index = 0 if step > 0 else len(self._batch_records) - 1
        else:
            next_index = max(0, min(self._batch_index + step, len(self._batch_records) - 1))
        self._load_batch_record(next_index)
        return "break"

    @staticmethod
    def _focus_batch_widget(widget: Any) -> str:
        widget.focus_set()
        return "break"

    def _choose_batch_candidate(self, position: int) -> str:
        candidate_index = position - 1
        if candidate_index < 0 or candidate_index >= len(self._batch_candidates):
            return "break"
        candidate = self._batch_candidates[candidate_index]
        person = self.repository.get_person_record(candidate.person_id)
        if not person:
            return "break"
        for key, var in self._batch_vars.items():
            if not var.get().strip() and person.get(key):
                var.set(person[key])
        if not self._batch_note.get("1.0", "end").strip() and person.get("note"):
            self._batch_note.insert("1.0", person["note"])
        item_ids = self._batch_candidate_tree.get_children()
        if candidate_index < len(item_ids):
            self._batch_candidate_tree.selection_set(item_ids[candidate_index])
            self._batch_candidate_tree.see(item_ids[candidate_index])
        return "break"

    def _open_batch_candidate(self, _event: Any = None) -> None:
        selection = self._batch_candidate_tree.selection()
        if not selection:
            return
        person_id = self._batch_candidate_tree.set(selection[0], "person_id")
        if person_id:
            self.show_person(int(person_id))

    def _save_batch_person(self, advance: bool = False) -> str:
        if self._batch_index < 0 or self._batch_index >= len(self._batch_records):
            return "break"
        record = self._batch_records[self._batch_index]
        try:
            form_data = self._batch_form_data()
            self._get_undo_manager().execute(RecoveryUpdateCommand(
                self.repository,
                lambda: self.recovery_wizard_service.update_existing_person(record.person_id, form_data),
            ))
        except Exception as exc:
            messagebox.showerror("Пакетный режим", str(exc), parent=self._batch_window)
            return "break"

        saved_index = self._batch_index
        self._batch_last_save = {"person_id": record.person_id, "index": saved_index}
        self._batch_undo_button.configure(state="normal")
        del self._batch_records[saved_index]
        self._batch_list.delete(saved_index)
        self.refresh_views()
        self._update_batch_progress()

        if not self._batch_records:
            self._batch_index = -1
            self._batch_current.configure(text="Все пустые карточки обработаны.")
            self._clear_batch_candidates()
            return "break"
        if advance:
            self._load_batch_record(min(saved_index, len(self._batch_records) - 1))
        else:
            self._batch_index = -1
            self._batch_list.selection_clear(0, "end")
            self._batch_current.configure(text="Сохранено. Выберите следующую карточку стрелками Up/Down.")
            self._clear_batch_candidates()
        return "break"

    def _undo_batch_save(self) -> None:
        if not self._batch_last_save:
            return
        undo = self._batch_last_save
        undo_manager = self._get_undo_manager()
        if undo_manager.undo_name != "Recovery Wizard change":
            messagebox.showinfo("Пакетный режим", "Последнее изменение не относится к Мастеру восстановления.")
            return
        if not undo_manager.undo():
            return
        self._batch_records = self.recovery_wizard_service.list_incomplete_people()
        self._batch_last_save = None
        self._batch_undo_button.configure(state="disabled")
        self._batch_list.delete(0, "end")
        for record in self._batch_records:
            self._batch_list.insert("end", f"ID {record.person_id} | {record.gedcom_id or '-'}")
        restored_index = next(
            (index for index, record in enumerate(self._batch_records) if record.person_id == undo["person_id"]),
            min(undo["index"], max(0, len(self._batch_records) - 1)),
        )
        self.refresh_views()
        self._update_batch_progress()
        if self._batch_records:
            self._load_batch_record(restored_index)

    def _build_batch_form(self, parent: Any) -> None:
        self._batch_current = tk.Label(parent, text="", anchor="w")
        self._batch_current.grid(row=0, column=0, columnspan=2, sticky="ew", padx=8, pady=(8, 6))
        self._batch_vars = {}
        self._batch_focus_widgets = []
        for row, (key, label) in enumerate(RECOVERY_FIELD_SPECS, start=1):
            tk.Label(parent, text=label + ":").grid(row=row, column=0, sticky="e", padx=6, pady=3)
            variable = tk.StringVar()
            self._batch_vars[key] = variable
            entry = tk.Entry(parent, textvariable=variable)
            entry.grid(row=row, column=1, sticky="ew", padx=6, pady=3)
            self._batch_focus_widgets.append(entry)
        note_row = len(RECOVERY_FIELD_SPECS) + 1
        tk.Label(parent, text="Примечание:").grid(row=note_row, column=0, sticky="ne", padx=6, pady=3)
        self._batch_note = tk.Text(parent, height=8, wrap="word")
        self._batch_note.grid(row=note_row, column=1, sticky="nsew", padx=6, pady=3)
        self._batch_focus_widgets.append(self._batch_note)
        parent.grid_columnconfigure(1, weight=1)
        parent.grid_rowconfigure(note_row, weight=1)

    def _build_batch_candidate_table(self, parent: Any) -> None:
        columns = ("shortcut", "confidence", "name", "birth", "gedcom_id", "person_id")
        self._batch_candidate_tree = ttk.Treeview(parent, columns=columns, show="headings")
        for column, title, width in (
            ("shortcut", "№", 40), ("confidence", "Confidence", 90), ("name", "Name", 180),
            ("birth", "Birth", 180), ("gedcom_id", "GEDCOM ID", 90), ("person_id", "Database ID", 90),
        ):
            self._batch_candidate_tree.heading(column, text=title)
            self._batch_candidate_tree.column(column, width=width, anchor="center" if column != "name" else "w")
        self._batch_candidate_tree.pack(fill="both", expand=True, padx=6, pady=6)
        self._batch_candidate_tree.bind("<Double-1>", self._open_batch_candidate)

    def _bind_batch_shortcuts(self, window: Any) -> None:
        for index, widget in enumerate(self._batch_focus_widgets):
            next_widget = self._batch_focus_widgets[(index + 1) % len(self._batch_focus_widgets)]
            widget.bind("<Tab>", lambda _event, target=next_widget: self._focus_batch_widget(target))
            widget.bind("<Up>", lambda _event: self._navigate_batch_person(-1))
            widget.bind("<Down>", lambda _event: self._navigate_batch_person(1))
            widget.bind("<Control-s>", lambda _event: self._save_batch_person(False))
            widget.bind("<Control-Return>", lambda _event: self._save_batch_person(True))
        window.bind("<Up>", lambda _event: self._navigate_batch_person(-1))
        window.bind("<Down>", lambda _event: self._navigate_batch_person(1))
        window.bind("<Control-s>", lambda _event: self._save_batch_person(False))
        window.bind("<Control-Return>", lambda _event: self._save_batch_person(True))
        window.bind("<Escape>", lambda _event: self._close_batch_recovery() or "break")
        for position in range(1, RECOVERY_MAX_SHORTCUT_CANDIDATES + 1):
            window.bind(
                f"<Control-Key-{position}>",
                lambda _event, value=position: self._choose_batch_candidate(value),
            )

    def open_batch_recovery(self) -> None:
        """Open the keyboard-oriented batch editor for incomplete people."""
        if self._batch_window is not None:
            try:
                self._batch_window.lift()
                self._batch_window.focus_force()
                return
            except Exception:
                self._batch_window = None
        records = self.recovery_wizard_service.list_incomplete_people()
        if not records:
            messagebox.showinfo("Пакетный режим", "Пустых карточек не найдено.")
            return

        self._close_recovery_wizard()
        self._batch_records = records
        self._batch_index = 0
        self._batch_total = len(records)
        self._batch_started_at = time.perf_counter()
        self._batch_last_save = None
        self._batch_candidates = []

        win = self._create_dialog()
        self._batch_window = win
        win.title("Пакетный режим восстановления")
        win.geometry(BATCH_RECOVERY_WINDOW_GEOMETRY)
        win.protocol("WM_DELETE_WINDOW", self._close_batch_recovery)

        progress_frame = tk.LabelFrame(win, text="Прогресс")
        progress_frame.pack(fill="x", padx=10, pady=(10, 6))
        self._batch_progress = tk.Label(progress_frame, text="", anchor="w")
        self._batch_progress.pack(fill="x", padx=8, pady=6)

        panes = tk.PanedWindow(win, orient="horizontal", sashrelief="raised")
        panes.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        left = tk.LabelFrame(panes, text="Неполные карточки")
        panes.add(left, minsize=230)
        self._batch_list = tk.Listbox(left, width=30, exportselection=False)
        self._batch_list.pack(fill="both", expand=True, padx=6, pady=6)
        for record in records:
            self._batch_list.insert("end", f"ID {record.person_id} | {record.gedcom_id or '-'}")
        self._batch_list.bind("<<ListboxSelect>>", self._on_batch_select)

        center = tk.LabelFrame(panes, text="Текущий человек")
        panes.add(center, minsize=470)
        self._build_batch_form(center)

        right = tk.LabelFrame(panes, text="Кандидаты (Ctrl+1..9)")
        panes.add(right, minsize=520)
        self._build_batch_candidate_table(right)

        controls = tk.Frame(win)
        controls.pack(fill="x", padx=10, pady=(0, 10))
        tk.Button(controls, text="Сохранить", command=lambda: self._save_batch_person(False)).pack(side="left")
        tk.Button(controls, text="Сохранить и следующая", command=lambda: self._save_batch_person(True)).pack(side="left", padx=8)
        self._batch_undo_button = tk.Button(controls, text="Отменить последнее сохранение", command=self._undo_batch_save, state="disabled")
        self._batch_undo_button.pack(side="left")
        tk.Button(controls, text="Закрыть", command=self._close_batch_recovery).pack(side="right")

        self._bind_batch_shortcuts(win)

        self._load_batch_record(0)
        self._batch_focus_widgets[0].focus_set()

    def _build_recovery_form(self, parent: Any) -> None:
        self._recovery_vars = {}
        self._recovery_entries = {}
        for row, (key, label) in enumerate(RECOVERY_FIELD_SPECS):
            tk.Label(parent, text=label + ":", width=18, anchor="e").grid(
                row=row, column=0, sticky="e", padx=5, pady=3
            )
            variable = tk.StringVar()
            self._recovery_vars[key] = variable
            entry = tk.Entry(parent, textvariable=variable, width=58)
            entry.grid(row=row, column=1, sticky="ew", padx=5, pady=3)
            entry.bind("<KeyRelease>", self._highlight_recovery_empty_fields)
            entry.bind("<FocusOut>", self._highlight_recovery_empty_fields)
            self._recovery_entries[key] = entry
        note_row = len(RECOVERY_FIELD_SPECS)
        tk.Label(parent, text="Примечание:", width=18, anchor="ne").grid(
            row=note_row, column=0, sticky="ne", padx=5, pady=3
        )
        self._recovery_note = tk.Text(parent, height=6, wrap="word")
        self._recovery_note.grid(row=note_row, column=1, sticky="nsew", padx=5, pady=3)
        self._recovery_note.bind("<KeyRelease>", self._highlight_recovery_empty_fields)
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(note_row, weight=1)

    def _bind_recovery_shortcuts(self, window: Any) -> None:
        self._recovery_focus_order = list(self._recovery_entries.values()) + [self._recovery_note]
        for index, widget in enumerate(self._recovery_focus_order):
            widget.bind(
                "<Return>",
                lambda event, next_index=index + 1: self._focus_next_recovery_field(event, next_index),
            )
            widget.bind("<Control-s>", lambda _event: self._save_recovery_person() or "break")
            widget.bind("<Control-Return>", lambda _event: self._save_recovery_person() or "break")
        window.bind("<Control-s>", lambda _event: self._save_recovery_person() or "break")
        window.bind("<Control-Return>", lambda _event: self._save_recovery_person() or "break")
        window.bind("<Escape>", lambda _event: self._close_recovery_wizard() or "break")
        window.bind("<Configure>", self._schedule_recovery_ui_save)

    def open_recovery_wizard(self) -> None:
        """Open a guided editor for people whose name fields are empty."""
        if self._recovery_window is not None:
            try:
                self._recovery_window.lift()
                self._recovery_window.focus_force()
                return
            except Exception:
                self._recovery_window = None

        try:
            records = self.recovery_wizard_service.list_incomplete_people()
        except Exception as exc:
            messagebox.showerror("Мастер восстановления", f"Не удалось получить список карточек:\n{exc}")
            return

        if not records:
            messagebox.showinfo("Мастер восстановления", "Пустых карточек не найдено.")
            return

        self._recovery_records = records
        self._recovery_total = len(records)
        self._recovery_ui_state = self._load_recovery_ui_state()
        selected_person_id = self._recovery_ui_state.get("selected_person_id")
        self._recovery_index = next(
            (index for index, record in enumerate(records) if record.person_id == selected_person_id),
            0,
        )
        win = self._create_dialog()
        self._recovery_window = win
        win.title("Мастер восстановления данных")
        try:
            win.geometry(self._recovery_ui_state.get("geometry") or RECOVERY_WINDOW_GEOMETRY)
        except tk.TclError:
            win.geometry(RECOVERY_WINDOW_GEOMETRY)
        win.protocol("WM_DELETE_WINDOW", self._close_recovery_wizard)

        outer = tk.Frame(win)
        outer.pack(fill="both", expand=True, padx=10, pady=10)

        self._recovery_main_pane = tk.PanedWindow(outer, orient="horizontal", sashrelief="raised")
        self._recovery_main_pane.pack(fill="both", expand=True)
        self._recovery_main_pane.bind("<ButtonRelease-1>", self._schedule_recovery_ui_save)
        left = tk.Frame(self._recovery_main_pane)
        self._recovery_main_pane.add(left, minsize=190)
        tk.Label(left, text=f"Пустые карточки: {len(records)}").pack(anchor="w")
        self._recovery_list = tk.Listbox(left, width=30, height=34)
        self._recovery_list.pack(side="left", fill="y")
        list_scroll = ttk.Scrollbar(left, orient="vertical", command=self._recovery_list.yview)
        list_scroll.pack(side="right", fill="y")
        self._recovery_list.configure(yscrollcommand=list_scroll.set)
        for i, record in enumerate(records, start=1):
            ref = record.gedcom_id or f"ID {record.person_id}"
            self._recovery_list.insert("end", f"{i}. {ref} ({record.sex or '?'})")
        self._recovery_list.bind("<<ListboxSelect>>", self._on_recovery_select)

        right = tk.Frame(self._recovery_main_pane)
        self._recovery_main_pane.add(right, minsize=620)
        self._recovery_status = tk.Label(right, text="", anchor="w")
        self._recovery_status.pack(fill="x", pady=(0, 8))
        self._recovery_progress = tk.Label(right, text="", anchor="w")
        self._recovery_progress.pack(fill="x", pady=(0, 8))

        self._recovery_content_pane = tk.PanedWindow(right, orient="vertical", sashrelief="raised")
        self._recovery_content_pane.pack(fill="both", expand=True)
        self._recovery_content_pane.bind("<ButtonRelease-1>", self._schedule_recovery_ui_save)

        info = tk.LabelFrame(self._recovery_content_pane, text="Семейный контекст — двойной щелчок открывает карточку")
        self._recovery_content_pane.add(info, minsize=100)
        relative_columns = ("role", "name", "person_id")
        self._recovery_relatives = ttk.Treeview(info, columns=relative_columns, show="headings", height=6)
        self._recovery_relatives.heading("role", text="Связь")
        self._recovery_relatives.heading("name", text="Человек")
        self._recovery_relatives.heading("person_id", text="ID")
        self._recovery_relatives.column("role", width=140)
        self._recovery_relatives.column("name", width=420)
        self._recovery_relatives.column("person_id", width=80, anchor="center")
        self._recovery_relatives.pack(fill="both", expand=True, padx=6, pady=6)
        self._recovery_relatives.bind("<Double-1>", self._open_recovery_relative)

        self._recovery_events_frame = tk.LabelFrame(self._recovery_content_pane, text="События: 0")
        self._recovery_content_pane.add(self._recovery_events_frame, minsize=90)
        self._recovery_events = tk.Text(self._recovery_events_frame, height=7, wrap="word")
        self._recovery_events.pack(fill="both", expand=True, padx=6, pady=6)
        self._recovery_events.configure(state="disabled")

        form = tk.LabelFrame(self._recovery_content_pane, text="Заполните существующую карточку")
        self._recovery_content_pane.add(form, minsize=260)
        self._build_recovery_form(form)

        controls = tk.Frame(right)
        controls.pack(fill="x", pady=(10, 0))
        tk.Button(controls, text="Сохранить и следующая", command=self._save_recovery_person).pack(side="left")
        tk.Button(controls, text="Пропустить", command=self._skip_recovery_person).pack(side="left", padx=8)
        tk.Button(controls, text="Открыть карточку", command=self._open_recovery_person_card).pack(side="left")
        tk.Button(controls, text="Найти совпадения", command=self._find_recovery_matches).pack(side="left", padx=8)
        tk.Button(controls, text="Пакетный режим", command=self.open_batch_recovery).pack(side="left")
        tk.Button(controls, text="Закрыть", command=self._close_recovery_wizard).pack(side="right")

        self._bind_recovery_shortcuts(win)

        self._recovery_list.selection_set(self._recovery_index)
        self._recovery_list.see(self._recovery_index)
        self._load_recovery_record(self._recovery_index)
        win.after(0, self._restore_recovery_splitters)

    def _close_recovery_wizard(self) -> None:
        if self._recovery_window is not None:
            if self._recovery_save_after_id is not None:
                try:
                    self._recovery_window.after_cancel(self._recovery_save_after_id)
                except Exception:
                    pass
                self._recovery_save_after_id = None
            self._save_recovery_ui_state(capture_layout=True)
            try:
                self._recovery_window.destroy()
            except Exception:
                pass
        self._recovery_window = None
        self._recovery_records = []
        self._recovery_index = -1

    def _on_recovery_select(self, _event: Any = None) -> None:
        selection = self._recovery_list.curselection() if hasattr(self, "_recovery_list") else ()
        if selection:
            self._load_recovery_record(int(selection[0]))

    def _load_recovery_record(self, index: int) -> None:
        if index < 0 or index >= len(self._recovery_records):
            return
        self._recovery_index = index
        record = self._recovery_records[index]
        self._recovery_status.configure(text=f"Карточка {index + 1} из {len(self._recovery_records)} — ID {record.person_id}, GEDCOM {record.gedcom_id or 'нет'}")
        self._load_record_form(record, self._recovery_vars, self._recovery_note)
        for item_id in self._recovery_relatives.get_children():
            self._recovery_relatives.delete(item_id)
        relative_groups = (
            ("Родитель", record.parent_links),
            ("Супруг/партнёр", record.partner_links),
            ("Ребёнок", record.child_links),
        )
        for role, links in relative_groups:
            for name, person_id in links:
                self._recovery_relatives.insert("", "end", values=(role, name, person_id))
        if not any(links for _role, links in relative_groups):
            self._recovery_relatives.insert("", "end", values=("", "Нет данных", ""))
        self._recovery_events_frame.configure(text=f"События: {record.event_count}")
        self._recovery_events.configure(state="normal")
        self._recovery_events.delete("1.0", "end")
        self._recovery_events.insert(
            "1.0",
            "\n".join(record.event_descriptions) if record.event_descriptions else "Нет событий",
        )
        self._recovery_events.configure(state="disabled")
        self._highlight_recovery_empty_fields()
        self._update_recovery_progress()
        self._save_recovery_ui_state()

    def _save_recovery_person(self) -> None:
        if self._recovery_index < 0 or self._recovery_index >= len(self._recovery_records):
            return
        record = self._recovery_records[self._recovery_index]
        data = self._recovery_form_data()
        try:
            self._get_undo_manager().execute(RecoveryUpdateCommand(
                self.repository,
                lambda: self.recovery_wizard_service.update_existing_person(record.person_id, data),
            ))
        except Exception as exc:
            messagebox.showerror("Мастер восстановления", str(exc))
            return
        self.refresh_views()
        current = self._recovery_index
        del self._recovery_records[current]
        self._recovery_list.delete(current)
        if not self._recovery_records:
            self._update_recovery_progress()
            messagebox.showinfo("Мастер восстановления", "Все пустые карточки обработаны.")
            self._close_recovery_wizard()
            return
        next_index = min(current, len(self._recovery_records) - 1)
        self._recovery_list.selection_clear(0, "end")
        self._recovery_list.selection_set(next_index)
        self._load_recovery_record(next_index)

    def _skip_recovery_person(self) -> None:
        if not self._recovery_records:
            return
        next_index = (self._recovery_index + 1) % len(self._recovery_records)
        self._recovery_list.selection_clear(0, "end")
        self._recovery_list.selection_set(next_index)
        self._recovery_list.see(next_index)
        self._load_recovery_record(next_index)

    def _open_recovery_person_card(self) -> None:
        if self._recovery_index < 0 or self._recovery_index >= len(self._recovery_records):
            return
        self.show_person(self._recovery_records[self._recovery_index].person_id)

    def _find_recovery_matches(self) -> None:
        if self._recovery_index < 0 or self._recovery_index >= len(self._recovery_records):
            return
        record = self._recovery_records[self._recovery_index]
        criteria = self._recovery_form_data()
        try:
            candidates = self.recovery_wizard_service.find_matches(record.person_id, criteria)
        except Exception as exc:
            messagebox.showerror("Поиск совпадений", str(exc), parent=self._recovery_window)
            return
        if not candidates:
            messagebox.showinfo("Поиск совпадений", "Совпадения не найдены.", parent=self._recovery_window)
            return

        dialog = self._create_dialog(self._recovery_window)
        dialog.title("Найденные совпадения")
        dialog.geometry(RECOVERY_MATCH_WINDOW_GEOMETRY)

        tk.Label(dialog, text=f"Кандидаты для ID {record.person_id}, GEDCOM {record.gedcom_id or 'нет'}").pack(
            anchor="w", padx=12, pady=(12, 8)
        )
        table_frame = tk.Frame(dialog)
        table_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        columns = ("confidence", "full_name", "birth", "gedcom_id", "id")
        tree = ttk.Treeview(table_frame, columns=columns, show="headings")

        def sort_table(column: str, descending: bool = False) -> None:
            def sort_value(item_id: str) -> str | int:
                value = tree.set(item_id, column)
                if column == "confidence":
                    return int(value.rstrip("%") or 0)
                if column == "id":
                    return int(value or 0)
                return value.casefold()

            items = sorted(tree.get_children(""), key=sort_value, reverse=descending)
            for position, item_id in enumerate(items):
                tree.move(item_id, "", position)
            tree.heading(column, command=lambda: sort_table(column, not descending))

        headings = {
            "confidence": "Score",
            "full_name": "Name",
            "birth": "Birth",
            "gedcom_id": "GEDCOM ID",
            "id": "Database ID",
        }
        for column, title in headings.items():
            tree.heading(column, text=title, command=lambda selected=column: sort_table(selected))
        tree.column("confidence", width=100, anchor="center")
        tree.column("full_name", width=260)
        tree.column("birth", width=250)
        tree.column("gedcom_id", width=120, anchor="center")
        tree.column("id", width=100, anchor="center")
        tree.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

        for candidate in candidates:
            birth = " — ".join(value for value in (candidate.birth_date, candidate.birth_place) if value) or "нет данных"
            tree.insert(
                "",
                "end",
                values=(
                    f"{candidate.confidence}%",
                    candidate.full_name,
                    birth,
                    candidate.gedcom_id or "-",
                    candidate.person_id,
                ),
            )

        def open_candidate(_event: Any = None) -> None:
            selection = tree.selection()
            if not selection:
                return
            person_id = tree.set(selection[0], "id")
            self.show_person(person_id)

        tree.bind("<Double-1>", open_candidate)
        tk.Button(dialog, text="Закрыть", command=dialog.destroy).pack(anchor="e", padx=12, pady=(0, 12))

    def _create_widgets(self):
        self._plugin_menu_bar = tk.Menu(self.root)
        self.root.config(menu=self._plugin_menu_bar)
        self._edit_menu = tk.Menu(self._plugin_menu_bar, tearoff=False)
        self._plugin_menu_bar.add_cascade(label="Edit", menu=self._edit_menu)
        self._edit_menu.add_command(label="Undo", command=self._undo_command, state="disabled")
        self._edit_menu.add_command(label="Redo", command=self._redo_command, state="disabled")
        self.root.bind("<Control-z>", self._undo_command)
        self.root.bind("<Control-y>", self._redo_command)
        self._update_undo_menu()
        help_menu = tk.Menu(self._plugin_menu_bar, tearoff=False)
        self._plugin_menu_bar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="User Manual", command=self._show_user_manual)
        help_menu.add_command(label="Diagnostics", command=self._show_diagnostics)
        help_menu.add_command(label="About", command=self._show_about)

        search_frame = tk.LabelFrame(self.root, text="Расширенный поиск")
        search_frame.pack(fill="x", padx=10, pady=(10, 4))
        text_fields = (
            ("first_name", "Имя"), ("last_name", "Фамилия"),
            ("patronymic", "Отчество"), ("sex", "Пол"),
            ("birth_year_from", "Год рождения от"), ("birth_year_to", "до"),
            ("death_year_from", "Год смерти от"), ("death_year_to", "до"),
            ("birth_place", "Место рождения"), ("death_place", "Место смерти"),
            ("occupation", "Занятие"), ("note_contains", "Заметка содержит"),
            ("gedcom_id", "GEDCOM ID"), ("database_id", "ID базы"),
        )
        loaded_filters = self.advanced_search_service.load_last_search()
        for index, (key, label) in enumerate(text_fields):
            row, column = divmod(index, 4)
            cell = tk.Frame(search_frame)
            cell.grid(row=row, column=column, sticky="ew", padx=6, pady=3)
            tk.Label(cell, text=f"{label}:").pack(side="left")
            value = getattr(loaded_filters, key)
            variable = tk.StringVar(value="" if value is None else str(value))
            self._advanced_search_vars[key] = variable
            if key == "sex":
                control = ttk.Combobox(
                    cell, textvariable=variable, values=("", "M", "F"),
                    state="readonly", width=8,
                )
                control.bind("<<ComboboxSelected>>", self._schedule_advanced_search)
            else:
                control = tk.Entry(cell, textvariable=variable, width=18)
                control.bind("<KeyRelease>", self._schedule_advanced_search)
                control.bind("<Return>", lambda _event: self.search_people())
            control.pack(side="right", fill="x", expand=True, padx=(6, 0))
            search_frame.grid_columnconfigure(column, weight=1)

        flags = tk.Frame(search_frame)
        flags.grid(row=3, column=2, columnspan=2, sticky="w", padx=6, pady=3)
        for key, label in (
            ("has_parents", "Есть родители"), ("has_spouses", "Есть супруги"),
            ("has_children", "Есть дети"), ("has_events", "Есть события"),
            ("has_attachments", "Есть вложения"),
        ):
            variable = tk.BooleanVar(value=getattr(loaded_filters, key))
            self._advanced_search_vars[key] = variable
            tk.Checkbutton(
                flags, text=label, variable=variable, command=self._schedule_advanced_search
            ).pack(side="left", padx=(0, 8))

        search_actions = tk.Frame(search_frame)
        search_actions.grid(row=4, column=0, columnspan=4, sticky="ew", padx=6, pady=(4, 7))
        tk.Button(search_actions, text="Поиск", command=self.search_people).pack(side="left")
        tk.Button(search_actions, text="Сбросить", command=self._clear_advanced_search).pack(side="left", padx=(8, 0))
        tk.Button(search_actions, text="Экспорт CSV", command=self._export_advanced_search_csv).pack(side="left", padx=(8, 0))
        self.status_label = tk.Label(search_actions, text="Найдено: 0")
        self.status_label.pack(side="left", padx=16)

        top = tk.Frame(self.root)
        top.pack(fill="x", padx=10, pady=(4, 10))
        self._plugin_button_frame = top

        self.backup_button = tk.Button(top, text="Backup database", command=self.backup_database)
        self.backup_button.pack(side="left", padx=(10, 5))
        self.restore_button = tk.Button(top, text="Restore database", command=self.restore_database)
        self.restore_button.pack(side="left", padx=(0, 5))
        self.relationship_button = tk.Button(top, text="Edit relationships", command=self.open_relationship_editor)
        self.relationship_button.pack(side="left")
        self.family_tree_button = tk.Button(top, text="Семейное дерево", command=self.open_family_tree)
        self.family_tree_button.pack(side="left", padx=(10, 0))
        self.family_timeline_button = tk.Button(top, text="Хронология", command=self.open_family_timeline)
        self.family_timeline_button.pack(side="left", padx=(10, 0))
        self.source_manager_button = tk.Button(top, text="Источники", command=self.open_source_manager)
        self.source_manager_button.pack(side="left", padx=(10, 0))
        self.relationship_inspector_button = tk.Button(
            top,
            text="Связь между людьми",
            command=self.open_relationship_inspector,
        )
        self.relationship_inspector_button.pack(side="left", padx=(10, 0))
        self.kinship_button = tk.Button(top, text="Анализ родства", command=self.open_kinship_analyzer)
        self.kinship_button.pack(side="left", padx=(10, 0))
        self.integrity_button = tk.Button(top, text="Проверка базы", command=self.open_integrity_report)
        self.integrity_button.pack(side="left", padx=(10, 0))
        self.data_quality_button = tk.Button(top, text="Качество данных", command=self.open_data_quality_center)
        self.data_quality_button.pack(side="left", padx=(10, 0))
        self.recovery_button = tk.Button(top, text="Мастер восстановления", command=self.open_recovery_wizard)
        self.recovery_button.pack(side="left", padx=(10, 0))
        self.add_person_button = tk.Button(top, text="Add person", command=lambda: self._show_person_editor(None))
        self.add_person_button.pack(side="left", padx=(10, 5))
        self.edit_person_button = tk.Button(top, text="Edit person", command=self._edit_selected_person)
        self.edit_person_button.pack(side="left")
        self.delete_person_button = tk.Button(top, text="Delete person", command=self._delete_selected_person)
        self.delete_person_button.pack(side="left", padx=(10, 5))

        table_frame = tk.Frame(self.root)
        table_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.tree = ttk.Treeview(table_frame, columns=("id", "name", "birth", "death"), show="headings")
        self.tree.heading("id", text="ID")
        self.tree.heading("name", text="Имя")
        self.tree.heading("birth", text="Рождение")
        self.tree.heading("death", text="Смерть")
        self.tree.column("id", width=80)
        self.tree.column("name", width=350)
        self.tree.column("birth", width=120)
        self.tree.column("death", width=120)
        self.tree.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", self.open_person)

        self.family_tree_text = tk.Text(self.root, height=8, wrap="word")
        self.family_tree_text.pack(fill="x", padx=10, pady=(0, 10))
        self.family_tree_text.insert("end", "Выберите человека, чтобы увидеть семейное дерево.\n")
        self.family_tree_text.config(state="disabled")

    def _update_undo_menu(self):
        if getattr(self, "_edit_menu", None) is None:
            return
        undo_manager = self._get_undo_manager()
        undo_label = f"Undo {undo_manager.undo_name}" if undo_manager.can_undo else "Undo"
        redo_label = f"Redo {undo_manager.redo_name}" if undo_manager.can_redo else "Redo"
        self._edit_menu.entryconfig(0, label=undo_label, state="normal" if undo_manager.can_undo else "disabled")
        self._edit_menu.entryconfig(1, label=redo_label, state="normal" if undo_manager.can_redo else "disabled")

    def _get_undo_manager(self):
        manager = getattr(self, "undo_manager", None)
        if manager is None:
            manager = UndoManager(self._update_undo_menu)
            self.undo_manager = manager
        return manager

    def _undo_command(self, _event=None):
        if self._get_undo_manager().undo():
            self.refresh_views()
            self._refresh_person_card()
        return "break"

    def _redo_command(self, _event=None):
        if self._get_undo_manager().redo():
            self.refresh_views()
            self._refresh_person_card()
        return "break"

    def _run_plugin_action(self, context, callback):
        try:
            return callback()
        except Exception as error:
            self.plugin_manager.log_runtime_error(context, error)
            messagebox.showerror("Plugin error", f"{context}: {error}")
            return None

    def _register_plugin_button(self, label, command):
        wrapped = lambda: self._run_plugin_action(label, command)
        button = tk.Button(self._plugin_button_frame, text=label, command=wrapped)
        button.pack(side="left", padx=(10, 0))
        return button

    def _show_about(self):
        """Show release and runtime version information."""
        messagebox.showinfo(
            "About GenealogyDB",
            "\n".join((
                f"GenealogyDB {APP_VERSION}",
                f"Build date: {BUILD_DATE}",
                f"Python: {sys.version.split()[0]}",
                f"SQLite: {sqlite3.sqlite_version}",
            )),
            parent=self.root,
        )

    def _show_user_manual(self):
        """Open the bundled user manual in a read-only application dialog."""
        try:
            manual = USER_MANUAL_PATH.read_text(encoding="utf-8")
        except OSError as error:
            messagebox.showerror(
                "User Manual",
                f"Unable to open the user manual: {error}",
                parent=self.root,
            )
            return

        dialog = self._create_dialog()
        dialog.title("GenealogyDB User Manual")
        dialog.geometry("760x620")
        body = tk.Text(dialog, wrap="word")
        body.pack(fill="both", expand=True, padx=12, pady=(12, 8))
        body.insert("end", manual)
        body.config(state="disabled")
        tk.Button(dialog, text="Close", command=dialog.destroy).pack(
            anchor="e", padx=12, pady=(0, 12)
        )

    def _diagnostics_snapshot(self):
        service_names = {
            type(value).__name__
            for name, value in vars(self).items()
            if value is not None and name.endswith(("_service", "_manager"))
        }
        service_names.add(type(self.repository).__name__)
        return diagnostics_snapshot(
            plugins=getattr(self, "loaded_plugins", ()),
            services=service_names,
        )

    def _show_diagnostics(self):
        """Show runtime diagnostics and offer a privacy-safe ZIP export."""
        snapshot = self._diagnostics_snapshot()
        lines = (
            f"Application version: {snapshot['application_version']}",
            f"Database path: {snapshot['database_path']}",
            f"Log folder: {snapshot['log_folder']}",
            f"Python version: {snapshot['python_version']}",
            f"SQLite version: {snapshot['sqlite_version']}",
            "",
            "Plugins:",
            *(f"  {name}" for name in snapshot["plugins"]),
            "",
            "Loaded services:",
            *(f"  {name}" for name in snapshot["loaded_services"]),
        )
        dialog = self._create_dialog()
        dialog.title("GenealogyDB Diagnostics")
        dialog.geometry("760x560")
        body = tk.Text(dialog, wrap="word")
        body.pack(fill="both", expand=True, padx=12, pady=(12, 8))
        body.insert("end", "\n".join(lines))
        body.config(state="disabled")
        controls = tk.Frame(dialog)
        controls.pack(fill="x", padx=12, pady=(0, 12))
        tk.Button(
            controls,
            text="Export diagnostics.zip",
            command=lambda: self._export_diagnostics(snapshot, dialog),
        ).pack(side="left")
        tk.Button(controls, text="Close", command=dialog.destroy).pack(side="right")

    def _export_diagnostics(self, snapshot, parent=None):
        destination = filedialog.asksaveasfilename(
            parent=parent or self.root,
            title="Export diagnostics",
            initialdir=str(EXPORT_DIR),
            initialfile="diagnostics.zip",
            defaultextension=".zip",
            filetypes=[("ZIP archives", "*.zip")],
        )
        if not destination:
            return
        try:
            output = export_diagnostics(destination, snapshot)
        except Exception:
            get_logger("diagnostics").exception("Diagnostics export failed")
            messagebox.showerror("Diagnostics", "Diagnostics export failed.", parent=parent)
            return
        messagebox.showinfo("Diagnostics", f"Diagnostics exported to:\n{output}", parent=parent)

    def _register_plugin_menu_item(self, menu_name, label, command):
        menu = self._plugin_menus.get(menu_name)
        if menu is None:
            menu = tk.Menu(self._plugin_menu_bar, tearoff=False)
            self._plugin_menu_bar.add_cascade(label=menu_name, menu=menu)
            self._plugin_menus[menu_name] = menu
        wrapped = lambda: self._run_plugin_action(f"{menu_name} / {label}", command)
        menu.add_command(label=label, command=wrapped)
        return wrapped

    def _register_plugin_report(self, name, provider):
        self._plugin_reports[name] = provider
        command = lambda: self._open_plugin_report(name)
        self._register_plugin_menu_item("Reports", name, command)
        return command

    def _register_plugin_export(self, name, exporter):
        self._plugin_exports[name] = exporter
        command = lambda: self._run_plugin_export(name)
        self._register_plugin_menu_item("Exports", name, command)
        return command

    def _open_plugin_report(self, name):
        provider = self._plugin_reports[name]
        report = provider()
        dialog = self._create_dialog()
        dialog.title(name)
        dialog.geometry("620x460")
        text = tk.Text(dialog, wrap="word")
        text.pack(fill="both", expand=True, padx=12, pady=(12, 8))
        text.insert("end", self._format_plugin_report(report))
        text.config(state="disabled")
        tk.Button(dialog, text="Закрыть", command=dialog.destroy).pack(anchor="e", padx=12, pady=(0, 12))

    @staticmethod
    def _format_plugin_report(report):
        if isinstance(report, Mapping):
            return "\n".join(f"{key}: {value}" for key, value in report.items())
        if isinstance(report, (list, tuple)):
            return "\n".join(str(item) for item in report)
        return str(report)

    def _run_plugin_export(self, name):
        destination = filedialog.asksaveasfilename(
            title=f"Export {name}",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All files", "*.*")],
        )
        if destination:
            self._plugin_exports[name](Path(destination))

    def _clear_tree(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

    def _query_people(self, query):
        if hasattr(self.repository, "find_people"):
            try:
                return self.repository.find_people(query)
            except TypeError:
                pass

        if not query:
            return self.repository.list_people()

        return self.repository.list_people(surname=query, first_name=query, last_name=query)

    @staticmethod
    def _optional_search_integer(value, label):
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError as error:
            raise ValueError(f"{label}: введите целое число.") from error

    def _collect_advanced_search_filters(self):
        values = {key: variable.get() for key, variable in self._advanced_search_vars.items()}
        return AdvancedSearchFilters(
            first_name=str(values["first_name"]).strip(),
            last_name=str(values["last_name"]).strip(),
            patronymic=str(values["patronymic"]).strip(),
            sex=str(values["sex"]).strip(),
            birth_year_from=self._optional_search_integer(values["birth_year_from"], "Год рождения от"),
            birth_year_to=self._optional_search_integer(values["birth_year_to"], "Год рождения до"),
            death_year_from=self._optional_search_integer(values["death_year_from"], "Год смерти от"),
            death_year_to=self._optional_search_integer(values["death_year_to"], "Год смерти до"),
            birth_place=str(values["birth_place"]).strip(),
            death_place=str(values["death_place"]).strip(),
            occupation=str(values["occupation"]).strip(),
            note_contains=str(values["note_contains"]).strip(),
            gedcom_id=str(values["gedcom_id"]).strip(),
            database_id=self._optional_search_integer(values["database_id"], "ID базы"),
            has_parents=bool(values["has_parents"]),
            has_spouses=bool(values["has_spouses"]),
            has_children=bool(values["has_children"]),
            has_events=bool(values["has_events"]),
            has_attachments=bool(values["has_attachments"]),
        )

    def _schedule_advanced_search(self, _event=None):
        if self._advanced_search_after_id is not None:
            try:
                self.root.after_cancel(self._advanced_search_after_id)
            except (AttributeError, tk.TclError):
                pass
        self._advanced_search_after_id = self.root.after(250, self.search_people)

    def _clear_advanced_search(self):
        for key, variable in self._advanced_search_vars.items():
            variable.set(False if key.startswith("has_") else "")
        self.search_people()

    def _export_advanced_search_csv(self):
        if not self._advanced_search_results:
            messagebox.showinfo("Расширенный поиск", "Нет результатов для экспорта.")
            return
        destination = filedialog.asksaveasfilename(
            title="Экспорт результатов поиска",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("Все файлы", "*.*")],
        )
        if not destination:
            return
        try:
            saved_path = self.advanced_search_service.export_csv(
                self._advanced_search_results, destination
            )
            messagebox.showinfo("Экспорт", f"Результаты сохранены: {saved_path}")
        except OSError as error:
            messagebox.showerror("Ошибка", str(error))

    def search_people(self):
        if hasattr(self, "_advanced_search_vars") and self._advanced_search_vars:
            self._advanced_search_after_id = None
            self._clear_tree()
            self.status_label.config(text="Поиск...")
            self.root.update_idletasks()
            try:
                filters = self._collect_advanced_search_filters()
                rows = self.advanced_search_service.search(filters)
                self.advanced_search_service.save_last_search(filters)
            except ValueError as error:
                self._advanced_search_results = ()
                self.status_label.config(text=str(error))
                return
            self._advanced_search_results = rows
            for person in rows:
                self.tree.insert("", "end", values=(
                    person.database_id, person.display_name,
                    person.birth_date, person.death_date,
                ))
            self.status_label.config(text=f"Найдено: {len(rows)}")
            return

        query = self.search_entry.get().strip()
        self._clear_tree()
        self.status_label.config(text="Поиск..." if query else "Загрузка...")
        self.root.update_idletasks()
        rows = self._query_people(query)
        for row in rows:
            if not row:
                continue
            person_id = row[0]
            last_name = row[1]
            first_name = row[2]
            birth_date = row[3]
            death_date = row[4]
            full_name = f"{first_name} {last_name}".strip()
            self.tree.insert("", "end", values=(person_id, full_name, birth_date or "", death_date or ""))
        self.status_label.config(text=f"Показано: {len(rows)}" if rows else "Ничего не найдено")

    def open_person(self, _event=None):
        selected = self.tree.selection()
        if not selected:
            return
        person_id = self.tree.item(selected[0])["values"][0]
        person = self.repository.get_person(person_id)
        if not person:
            return
        self.current_person_id = person_id
        self.current_person_gedcom_id = person[0]
        self.show_person(person_id)

    def _selected_person_id(self):
        if not self.tree:
            return None
        selected = self.tree.selection()
        if not selected:
            return None
        return self.tree.item(selected[0])["values"][0]

    def open_relationship_inspector(self) -> None:
        """Select two people and show their shortest read-only relationship path."""
        source_reference = self._choose_person("Выберите первого человека")
        if not source_reference:
            return
        target_reference = self._choose_person(
            "Выберите второго человека",
            exclude_reference=source_reference,
        )
        if not target_reference:
            return
        try:
            path = self.relationship_path_service.find_shortest_path(
                source_reference,
                target_reference,
            )
        except Exception as exc:
            messagebox.showerror("Связь между людьми", str(exc))
            return
        if path is None:
            messagebox.showinfo("Связь между людьми", "Связь между выбранными людьми не найдена.")
            return
        self._show_relationship_inspector(path)

    def open_kinship_analyzer(self) -> None:
        """Select two people and open their read-only kinship analysis."""
        source_reference = self._choose_person("Выберите первого человека")
        if not source_reference:
            return
        target_reference = self._choose_person(
            "Выберите второго человека",
            exclude_reference=source_reference,
        )
        if not target_reference:
            return
        try:
            analysis = self.kinship_service.analyze(source_reference, target_reference)
        except ValueError as error:
            messagebox.showerror("Анализ родства", str(error), parent=self.root)
            return
        self._show_kinship_analysis(analysis)

    def _show_kinship_analysis(self, analysis: KinshipAnalysis) -> None:
        if self._kinship_window is not None:
            try:
                self._kinship_window.destroy()
            except Exception:
                pass
        window = self._create_dialog()
        self._kinship_window = window
        self._kinship_analysis = analysis
        window.title("Анализ родства")
        window.geometry("1180x760")
        window.minsize(860, 560)
        window.protocol("WM_DELETE_WINDOW", self._close_kinship_analyzer)

        summary = tk.LabelFrame(window, text="Результат")
        summary.pack(fill="x", padx=12, pady=(12, 6))
        nearest = ", ".join(item.person.full_name for item in analysis.nearest_common_ancestors) or "нет"
        common = ", ".join(item.person.full_name for item in analysis.common_ancestors) or "нет"
        generation = (
            f"{analysis.generation_distance[0]} / {analysis.generation_distance[1]}"
            if analysis.generation_distance else "-"
        )
        inbreeding = ", ".join(
            f"{person.full_name}: {coefficient:.6f}"
            for person, coefficient in analysis.inbreeding_coefficients
        ) or "не применимо"
        lines = (
            f"Люди: {analysis.source.full_name} / {analysis.target.full_name}",
            f"Кратчайший путь: {analysis.shortest_path.description if analysis.shortest_path else 'не найден'}",
            f"Кровное родство: {'да' if analysis.blood_relationship else 'нет'}    Степень: {analysis.relationship_degree if analysis.relationship_degree is not None else '-'}    Поколения: {generation}",
            f"Общие предки: {common}",
            f"Ближайшие общие предки: {nearest}",
            f"Коэффициент родства: {analysis.coefficient_of_relationship:.6f}    Коэффициент инбридинга: {inbreeding}",
        )
        tk.Label(summary, text="\n".join(lines), justify="left", anchor="w").pack(
            fill="x", padx=8, pady=8
        )

        graph_frame = tk.LabelFrame(window, text="Графическое дерево родства")
        graph_frame.pack(fill="x", padx=12, pady=6)
        canvas = tk.Canvas(graph_frame, height=170, background="white")
        canvas.pack(fill="x", expand=True, padx=8, pady=8)
        self._kinship_canvas = canvas
        self._draw_kinship_tree(analysis)

        paths_frame = tk.LabelFrame(window, text="Все альтернативные пути")
        paths_frame.pack(fill="both", expand=True, padx=12, pady=6)
        columns = ("number", "distance", "blood", "path")
        tree = ttk.Treeview(paths_frame, columns=columns, show="headings")
        for column, title, width in (
            ("number", "#", 45), ("distance", "Расстояние", 90),
            ("blood", "Кровный", 80), ("path", "Путь", 820),
        ):
            tree.heading(column, text=title)
            tree.column(column, width=width, anchor="w" if column == "path" else "center")
        tree.tag_configure("direct_blood", background="#d9f2df")
        tree.tag_configure("blood", background="#eef7f0")
        for index, path in enumerate(analysis.paths, start=1):
            tag = "direct_blood" if path.is_direct_blood else "blood" if path.is_blood else ""
            tree.insert("", "end", values=(index, path.distance, "да" if path.is_blood else "нет", path.description), tags=(tag,) if tag else ())
        tree.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(paths_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self._kinship_path_tree = tree

        controls = tk.Frame(window)
        controls.pack(fill="x", padx=12, pady=(0, 12))
        tk.Button(controls, text="Экспорт PDF", command=self._export_kinship_pdf).pack(side="left")
        tk.Button(controls, text="Экспорт HTML", command=self._export_kinship_html).pack(side="left", padx=(8, 0))
        tk.Button(controls, text="Экспорт CSV", command=self._export_kinship_csv).pack(side="left", padx=(8, 0))
        tk.Button(controls, text="Закрыть", command=self._close_kinship_analyzer).pack(side="right")

    def _draw_kinship_tree(self, analysis: KinshipAnalysis) -> None:
        canvas = self._kinship_canvas
        if canvas is None:
            return
        canvas.delete("all")
        path = next((item for item in analysis.paths if item.is_direct_blood), analysis.shortest_path)
        if path is None:
            canvas.create_text(20, 80, text="Путь не найден", anchor="w")
            return
        node_width = 150
        gap = 55
        y = 85
        for index, person in enumerate(path.people):
            x = 20 + index * (node_width + gap)
            if index:
                edge = path.edges[index - 1]
                canvas.create_line(x - gap, y, x, y, fill="#2f7d4a", width=3, arrow="last")
                canvas.create_text(x - gap / 2, y - 16, text=edge)
            canvas.create_rectangle(x, y - 32, x + node_width, y + 32, fill="#d9f2df", outline="#2f7d4a", width=2)
            canvas.create_text(x + node_width / 2, y, text=person.full_name, width=node_width - 12)
        canvas.configure(scrollregion=(0, 0, 40 + len(path.people) * (node_width + gap), 170))

    def _kinship_export_destination(self, extension, file_type):
        return filedialog.asksaveasfilename(
            parent=self._kinship_window,
            title=f"Экспорт анализа родства в {extension.upper()}",
            initialdir=str(EXPORT_DIR),
            initialfile=f"kinship_analysis.{extension}",
            defaultextension=f".{extension}",
            filetypes=[(file_type, f"*.{extension}")],
        )

    def _export_kinship_pdf(self) -> None:
        destination = self._kinship_export_destination("pdf", "PDF files")
        if destination and self._kinship_analysis:
            self.kinship_service.export_pdf(self._kinship_analysis, destination)

    def _export_kinship_html(self) -> None:
        destination = self._kinship_export_destination("html", "HTML files")
        if destination and self._kinship_analysis:
            self.kinship_service.export_html(self._kinship_analysis, destination)

    def _export_kinship_csv(self) -> None:
        destination = self._kinship_export_destination("csv", "CSV files")
        if destination and self._kinship_analysis:
            self.kinship_service.export_csv(self._kinship_analysis, destination)

    def _close_kinship_analyzer(self) -> None:
        if self._kinship_window is not None:
            try:
                self._kinship_window.destroy()
            except Exception:
                pass
        self._kinship_window = None
        self._kinship_analysis = None
        self._kinship_path_tree = None
        self._kinship_canvas = None

    def _show_relationship_inspector(self, path: RelationshipPath) -> None:
        if self._relationship_inspector_window is not None:
            try:
                self._relationship_inspector_window.destroy()
            except Exception:
                pass
        window = self._create_dialog()
        self._relationship_inspector_window = window
        self._relationship_inspector_path = path
        window.title("Связь между людьми")
        window.geometry("920x520")
        window.protocol("WM_DELETE_WINDOW", self._close_relationship_inspector)

        summary = tk.LabelFrame(window, text="Результат")
        summary.pack(fill="x", padx=12, pady=(12, 8))
        tk.Label(summary, text=f"Описание: {path.description}", anchor="w").pack(
            fill="x", padx=8, pady=(6, 2)
        )
        tk.Label(
            summary,
            text=f"Distance: {path.distance}    Generations: {path.generations}",
            anchor="w",
        ).pack(fill="x", padx=8, pady=(0, 6))

        table_frame = tk.Frame(window)
        table_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        columns = ("step", "relationship", "name", "database_id", "gedcom_id")
        tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        for column, title, width in (
            ("step", "Step", 55),
            ("relationship", "Relationship", 110),
            ("name", "Person", 350),
            ("database_id", "Database ID", 110),
            ("gedcom_id", "GEDCOM ID", 110),
        ):
            tree.heading(column, text=title)
            tree.column(column, width=width, anchor="w" if column == "name" else "center")
        tree.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        for row in self._relationship_path_rows(path):
            tree.insert("", "end", values=row)
        tree.bind(
            "<ButtonRelease-1>",
            lambda event: self._open_relationship_path_person(tree, event),
        )
        self._relationship_inspector_tree = tree

        controls = tk.Frame(window)
        controls.pack(fill="x", padx=12, pady=(0, 12))
        tk.Button(controls, text="Экспорт в текст", command=self._export_relationship_path).pack(
            side="left"
        )
        tk.Button(controls, text="Закрыть", command=self._close_relationship_inspector).pack(
            side="right"
        )

    @staticmethod
    def _relationship_path_rows(path: RelationshipPath) -> list[tuple[object, ...]]:
        rows = [
            (
                0,
                "start",
                path.people[0].full_name,
                path.people[0].database_id,
                path.people[0].gedcom_id or "-",
            )
        ]
        rows.extend(
            (
                index,
                step.relationship_type,
                step.target.full_name,
                step.target.database_id,
                step.target.gedcom_id or "-",
            )
            for index, step in enumerate(path.steps, start=1)
        )
        return rows

    def _open_relationship_path_person(self, tree: Any, event: Any) -> None:
        item_id = tree.identify_row(event.y)
        if not item_id:
            return
        person_id = tree.set(item_id, "database_id")
        if person_id:
            self.show_person(int(person_id))

    def _export_relationship_path(self) -> None:
        if self._relationship_inspector_path is None:
            return
        destination = filedialog.asksaveasfilename(
            parent=self._relationship_inspector_window,
            title="Экспорт связи",
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt"), ("All files", "*.*")],
        )
        if not destination:
            return
        self.relationship_path_service.export_path_text(
            self._relationship_inspector_path,
            destination,
        )
        messagebox.showinfo(
            "Связь между людьми",
            "Путь экспортирован.",
            parent=self._relationship_inspector_window,
        )

    def _close_relationship_inspector(self) -> None:
        if self._relationship_inspector_window is not None:
            try:
                self._relationship_inspector_window.destroy()
            except Exception:
                pass
        self._relationship_inspector_window = None
        self._relationship_inspector_path = None

    def open_family_tree(self) -> None:
        """Open the interactive read-only tree for the selected person."""
        person_id = self._selected_person_id()
        if person_id is None:
            messagebox.showwarning("Семейное дерево", "Сначала выберите человека.")
            return

        if self._family_tree_window is not None:
            try:
                self._family_tree_window.lift()
                self._family_tree_window.focus_force()
                self._start_family_tree_history(int(person_id))
                return
            except Exception:
                self._family_tree_window = None

        window = self._create_dialog()
        self._family_tree_window = window
        window.title("Семейное дерево")
        window.geometry("1100x720")
        window.minsize(760, 520)
        window.protocol("WM_DELETE_WINDOW", self._close_family_tree)

        toolbar = tk.Frame(window)
        toolbar.pack(fill="x", padx=12, pady=(12, 6))
        self._family_tree_back_button = tk.Button(toolbar, text="Назад", command=self._family_tree_back)
        self._family_tree_back_button.pack(side="left")
        self._family_tree_forward_button = tk.Button(
            toolbar,
            text="Вперёд",
            command=self._family_tree_forward,
        )
        self._family_tree_forward_button.pack(side="left", padx=(6, 0))
        tk.Button(
            toolbar,
            text="Вернуться к исходному человеку",
            command=self._family_tree_return_to_original,
        ).pack(side="left", padx=(12, 0))
        self._family_tree_zoom_label = tk.Label(toolbar, text="100%")
        self._family_tree_zoom_label.pack(side="left", padx=(16, 0))
        tk.Button(toolbar, text="Закрыть", command=self._close_family_tree).pack(side="right")

        canvas_frame = tk.Frame(window)
        canvas_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        self._family_tree_canvas = tk.Canvas(
            canvas_frame,
            background=FAMILY_TREE_BACKGROUND,
            highlightthickness=0,
        )
        horizontal_scroll = ttk.Scrollbar(
            canvas_frame,
            orient="horizontal",
            command=self._family_tree_canvas.xview,
        )
        vertical_scroll = ttk.Scrollbar(
            canvas_frame,
            orient="vertical",
            command=self._family_tree_canvas.yview,
        )
        self._family_tree_canvas.configure(
            xscrollcommand=horizontal_scroll.set,
            yscrollcommand=vertical_scroll.set,
        )
        self._family_tree_canvas.grid(row=0, column=0, sticky="nsew")
        vertical_scroll.grid(row=0, column=1, sticky="ns")
        horizontal_scroll.grid(row=1, column=0, sticky="ew")
        canvas_frame.grid_rowconfigure(0, weight=1)
        canvas_frame.grid_columnconfigure(0, weight=1)
        self._family_tree_canvas.bind("<MouseWheel>", self._zoom_family_tree)
        self._family_tree_canvas.bind("<ButtonPress-1>", self._start_family_tree_drag)
        self._family_tree_canvas.bind("<B1-Motion>", self._drag_family_tree)
        self._start_family_tree_history(int(person_id))

    def open_family_timeline(self) -> None:
        """Open the read-only chronological timeline for all people."""
        if self._family_timeline_window is not None:
            try:
                self._family_timeline_window.lift()
                self._family_timeline_window.focus_force()
                self._load_family_timeline()
                return
            except Exception:
                self._family_timeline_window = None

        window = self._create_dialog()
        self._family_timeline_window = window
        window.title("Хронология")
        window.geometry("1180x680")
        window.minsize(860, 480)
        window.protocol("WM_DELETE_WINDOW", self._close_family_timeline)

        filters = tk.LabelFrame(window, text="Фильтры")
        filters.pack(fill="x", padx=12, pady=(12, 8))
        specs = (
            ("year_from", "Год от", 10),
            ("year_to", "Год до", 10),
            ("surname", "Фамилия", 18),
            ("place", "Место", 20),
        )
        for column, (key, label, width) in enumerate(specs):
            tk.Label(filters, text=f"{label}:").grid(row=0, column=column * 2, padx=(8, 3), pady=8)
            variable = tk.StringVar(value="")
            self._family_timeline_vars[key] = variable
            entry = tk.Entry(filters, textvariable=variable, width=width)
            entry.grid(row=0, column=column * 2 + 1, padx=(0, 8), pady=8)
            entry.bind("<Return>", lambda _event: self._apply_family_timeline_filters())

        tk.Label(filters, text="Событие:").grid(row=0, column=8, padx=(8, 3), pady=8)
        event_type = tk.StringVar(value="")
        self._family_timeline_vars["event_type"] = event_type
        event_control = ttk.Combobox(
            filters,
            textvariable=event_type,
            values=("", *SUPPORTED_EVENT_TYPES),
            state="readonly",
            width=18,
        )
        event_control.grid(row=0, column=9, padx=(0, 8), pady=8)
        event_control.bind("<<ComboboxSelected>>", lambda _event: self._apply_family_timeline_filters())
        self._family_timeline_event_control = event_control
        tk.Button(filters, text="Применить", command=self._apply_family_timeline_filters).grid(
            row=0, column=10, padx=8, pady=8
        )

        table_frame = tk.Frame(window)
        table_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        columns = ("date", "year", "person", "event_type", "place", "age")
        tree = ttk.Treeview(table_frame, columns=columns, show="headings")
        for column, title, width in (
            ("date", "Дата", 150),
            ("year", "Год", 70),
            ("person", "Человек", 240),
            ("event_type", "Событие", 160),
            ("place", "Место", 300),
            ("age", "Возраст", 80),
        ):
            tree.heading(column, text=title)
            tree.column(column, width=width, anchor="w" if column in {"date", "person", "event_type", "place"} else "center")
        tree.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        tree.bind("<Double-1>", self._open_family_timeline_person)
        self._family_timeline_tree = tree

        controls = tk.Frame(window)
        controls.pack(fill="x", padx=12, pady=(0, 12))
        tk.Button(controls, text="Экспорт CSV", command=self._export_family_timeline_csv).pack(side="left")
        tk.Button(controls, text="Экспорт HTML", command=self._export_family_timeline_html).pack(side="left", padx=(8, 0))
        self._family_timeline_status = tk.Label(controls, text="")
        self._family_timeline_status.pack(side="left", padx=16)
        tk.Button(controls, text="Закрыть", command=self._close_family_timeline).pack(side="right")
        self._load_family_timeline()

    def open_source_manager(self) -> None:
        """Open source management and read-only usage browser tabs."""
        if self._source_window is not None:
            try:
                self._source_window.lift()
                self._source_window.focus_force()
                self._refresh_source_manager()
                return
            except Exception:
                self._source_window = None

        window = self._create_dialog()
        self._source_window = window
        window.title("Источники и цитаты")
        window.geometry("1120x700")
        window.minsize(850, 520)
        window.protocol("WM_DELETE_WINDOW", self._close_source_manager)
        notebook = ttk.Notebook(window)
        notebook.pack(fill="both", expand=True, padx=12, pady=12)
        manager_tab = tk.Frame(notebook)
        browser_tab = tk.Frame(notebook)
        notebook.add(manager_tab, text="Source Manager")
        notebook.add(browser_tab, text="Source Browser")
        self._build_source_manager_tab(manager_tab)
        self._build_source_browser_tab(browser_tab)
        self._refresh_source_manager()

    def _build_source_manager_tab(self, parent) -> None:
        source_frame = tk.LabelFrame(parent, text="Источники")
        source_frame.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        columns = ("id", "title", "author", "repository", "references")
        tree = ttk.Treeview(source_frame, columns=columns, show="headings", height=9)
        for column, title, width in (
            ("id", "ID", 55), ("title", "Название", 280), ("author", "Автор", 180),
            ("repository", "Репозиторий", 220), ("references", "Ссылок", 75),
        ):
            tree.heading(column, text=title)
            tree.column(column, width=width, anchor="w" if column not in {"id", "references"} else "center")
        tree.pack(fill="both", expand=True, padx=8, pady=8)
        tree.bind("<<TreeviewSelect>>", lambda _event: self._refresh_source_citations())
        tree.bind("<Double-1>", lambda _event: self._edit_selected_source())
        self._source_tree = tree
        source_controls = tk.Frame(source_frame)
        source_controls.pack(fill="x", padx=8, pady=(0, 8))
        tk.Button(source_controls, text="Добавить", command=lambda: self._edit_source_record()).pack(side="left")
        tk.Button(source_controls, text="Изменить", command=self._edit_selected_source).pack(side="left", padx=(8, 0))
        tk.Button(source_controls, text="Удалить", command=self._delete_selected_source).pack(side="left", padx=(8, 0))
        tk.Button(source_controls, text="Добавить цитату", command=self._add_source_citation).pack(side="left", padx=(16, 0))

        citation_frame = tk.LabelFrame(parent, text="Цитаты выбранного источника")
        citation_frame.pack(fill="both", expand=True, padx=8, pady=(4, 8))
        columns = ("id", "type", "target", "page", "quality")
        citations = ttk.Treeview(citation_frame, columns=columns, show="headings", height=7)
        for column, title, width in (
            ("id", "ID", 55), ("type", "Тип", 110), ("target", "Объект", 390),
            ("page", "Страница", 120), ("quality", "Качество", 130),
        ):
            citations.heading(column, text=title)
            citations.column(column, width=width, anchor="w" if column not in {"id"} else "center")
        citations.pack(fill="both", expand=True, padx=8, pady=8)
        citations.bind("<Double-1>", lambda _event: self._edit_selected_citation())
        self._source_citation_tree = citations
        citation_controls = tk.Frame(citation_frame)
        citation_controls.pack(fill="x", padx=8, pady=(0, 8))
        tk.Button(citation_controls, text="Изменить цитату", command=self._edit_selected_citation).pack(side="left")
        tk.Button(citation_controls, text="Удалить цитату", command=self._delete_selected_citation).pack(side="left", padx=(8, 0))

    def _build_source_browser_tab(self, parent) -> None:
        columns = ("source", "type", "target", "page", "quality")
        tree = ttk.Treeview(parent, columns=columns, show="headings", height=14)
        for column, title, width in (
            ("source", "Источник", 260), ("type", "Тип", 100), ("target", "Используется в", 400),
            ("page", "Страница", 100), ("quality", "Качество", 120),
        ):
            tree.heading(column, text=title)
            tree.column(column, width=width, anchor="w")
        tree.pack(fill="both", expand=True, padx=8, pady=8)
        tree.bind("<Double-1>", self._open_source_usage)
        self._source_browser_tree = tree
        controls = tk.Frame(parent)
        controls.pack(fill="x", padx=8, pady=(0, 8))
        tk.Button(controls, text="Экспорт CSV", command=self._export_sources_csv).pack(side="left")
        self._source_statistics_text = tk.Text(parent, height=11, wrap="word")
        self._source_statistics_text.pack(fill="x", padx=8, pady=(0, 8))

    def _refresh_source_manager(self) -> None:
        if self._source_tree is not None:
            for item in self._source_tree.get_children():
                self._source_tree.delete(item)
            for source in self.source_service.list_sources():
                self._source_tree.insert("", "end", values=(
                    source["id"], source["title"], source["author"],
                    source["repository"], source.get("citation_count", 0),
                ))
        self._refresh_source_citations()
        self._refresh_source_browser()

    def _selected_source_id(self):
        if self._source_tree is None or not self._source_tree.selection():
            return None
        return int(self._source_tree.item(self._source_tree.selection()[0])["values"][0])

    def _refresh_source_citations(self) -> None:
        if self._source_citation_tree is None:
            return
        for item in self._source_citation_tree.get_children():
            self._source_citation_tree.delete(item)
        source_id = self._selected_source_id()
        if source_id is None:
            return
        for citation in self.source_service.list_citations(source_id):
            try:
                target = self.source_service.resolve_target(citation["target_type"], citation["target_id"])
            except ValueError:
                target = {"target_label": "Недоступный объект"}
            self._source_citation_tree.insert("", "end", values=(
                citation["id"], citation["target_type"], target["target_label"],
                citation["page"], citation["quality"],
            ))

    def _edit_source_record(self, source_id=None) -> None:
        source = self.source_service.get_source(source_id) if source_id else {}
        dialog = self._create_dialog(self._source_window)
        dialog.title("Источник")
        fields = {}
        form = tk.Frame(dialog)
        form.pack(fill="both", expand=True, padx=12, pady=12)
        labels = {
            "title": "Название", "author": "Автор", "publication": "Публикация",
            "repository": "Репозиторий", "call_number": "Шифр", "url": "URL", "notes": "Примечания",
        }
        for row, field in enumerate(SOURCE_FIELDS):
            tk.Label(form, text=f"{labels[field]}:").grid(row=row, column=0, sticky="nw", pady=4)
            if field == "notes":
                control = tk.Text(form, height=5, width=55)
                control.insert("1.0", source.get(field, ""))
            else:
                control = tk.Entry(form, width=58)
                control.insert(0, source.get(field, ""))
            control.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=4)
            fields[field] = control

        def save():
            data = {
                field: control.get("1.0", "end").strip() if field == "notes" else control.get().strip()
                for field, control in fields.items()
            }
            try:
                if source_id:
                    self.source_service.update_source(source_id, data)
                else:
                    self.source_service.create_source(data)
            except ValueError as error:
                messagebox.showerror("Источник", str(error), parent=dialog)
                return
            dialog.destroy()
            self._refresh_source_manager()

        tk.Button(form, text="Сохранить", command=save).grid(row=len(SOURCE_FIELDS), column=1, sticky="e", pady=(10, 0))

    def _edit_selected_source(self) -> None:
        source_id = self._selected_source_id()
        if source_id is not None:
            self._edit_source_record(source_id)

    def _delete_selected_source(self) -> None:
        source_id = self._selected_source_id()
        if source_id is not None and messagebox.askyesno("Источник", "Удалить источник и все его цитаты?", parent=self._source_window):
            self.source_service.delete_source(source_id)
            self._refresh_source_manager()

    def _add_source_citation(self) -> None:
        source_id = self._selected_source_id()
        if source_id is None:
            messagebox.showwarning("Цитата", "Выберите источник.", parent=self._source_window)
            return
        self._edit_citation_record(source_id)

    def _selected_citation_id(self):
        if self._source_citation_tree is None or not self._source_citation_tree.selection():
            return None
        return int(self._source_citation_tree.item(self._source_citation_tree.selection()[0])["values"][0])

    def _edit_citation_record(self, source_id, citation_id=None) -> None:
        citation = next((item for item in self.source_service.list_citations(source_id) if item["id"] == citation_id), {})
        dialog = self._create_dialog(self._source_window)
        dialog.title("Цитата")
        form = tk.Frame(dialog)
        form.pack(fill="both", expand=True, padx=12, pady=12)
        values = {
            "target_type": tk.StringVar(value=citation.get("target_type", "person")),
            "target_id": tk.StringVar(value=citation.get("target_id", str(self.current_person_id or ""))),
            **{field: tk.StringVar(value=citation.get(field, "")) for field in CITATION_FIELDS},
        }
        labels = {"target_type": "Тип объекта", "target_id": "ID объекта", "page": "Страница", "quality": "Качество/достоверность", "transcription": "Транскрипция", "comment": "Комментарий"}
        for row, key in enumerate(("target_type", "target_id", *CITATION_FIELDS)):
            tk.Label(form, text=f"{labels[key]}:").grid(row=row, column=0, sticky="w", pady=4)
            if key == "target_type":
                control = ttk.Combobox(form, textvariable=values[key], values=TARGET_TYPES, state="readonly", width=38)
            else:
                control = tk.Entry(form, textvariable=values[key], width=42)
            control.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=4)

        def save():
            details = {field: values[field].get() for field in CITATION_FIELDS}
            try:
                if citation_id:
                    self.source_service.update_citation(citation_id, source_id, values["target_type"].get(), values["target_id"].get(), **details)
                else:
                    self.source_service.create_citation(source_id, values["target_type"].get(), values["target_id"].get(), **details)
            except (ValueError, TypeError) as error:
                messagebox.showerror("Цитата", str(error), parent=dialog)
                return
            dialog.destroy()
            self._refresh_source_manager()

        tk.Button(form, text="Сохранить", command=save).grid(row=6, column=1, sticky="e", pady=(10, 0))

    def _edit_selected_citation(self) -> None:
        source_id = self._selected_source_id()
        citation_id = self._selected_citation_id()
        if source_id is not None and citation_id is not None:
            self._edit_citation_record(source_id, citation_id)

    def _delete_selected_citation(self) -> None:
        citation_id = self._selected_citation_id()
        if citation_id is not None:
            self.source_service.delete_citation(citation_id)
            self._refresh_source_manager()

    def _refresh_source_browser(self) -> None:
        if self._source_browser_tree is None:
            return
        for item in self._source_browser_tree.get_children():
            self._source_browser_tree.delete(item)
        self._source_usage_map = {}
        for usage in self.source_service.browser_rows():
            item = self._source_browser_tree.insert("", "end", values=(
                usage["source_title"], usage["target_type"], usage["target_label"],
                usage["page"], usage["quality"],
            ))
            self._source_usage_map[item] = usage
        statistics = self.source_service.statistics()
        lines = [
            f"Источников: {statistics['source_count']}    Цитат: {statistics['citation_count']}",
            "Сиротские источники: " + (", ".join(item["title"] for item in statistics["orphan_sources"]) or "нет"),
            "Наиболее используемые: " + (", ".join(f"{title} ({count})" for title, count in statistics["most_referenced"][:10]) or "нет"),
            "По типу объекта: " + ", ".join(f"{key}: {value}" for key, value in statistics["by_target_type"].items()),
            "По репозиториям: " + (", ".join(f"{key}: {value}" for key, value in statistics["by_repository"].items()) or "нет"),
        ]
        if self._source_statistics_text is not None:
            self._source_statistics_text.config(state="normal")
            self._source_statistics_text.delete("1.0", "end")
            self._source_statistics_text.insert("end", "\n".join(lines))
            self._source_statistics_text.config(state="disabled")

    def _open_source_usage(self, _event=None) -> None:
        if self._source_browser_tree is None or not self._source_browser_tree.selection():
            return
        usage = self._source_usage_map.get(self._source_browser_tree.selection()[0])
        if not usage or usage.get("linked_person_id") is None:
            return
        person_id = usage["linked_person_id"]
        if usage["target_type"] == "event":
            self._manage_person_event(self._source_window, person_id, int(usage["target_id"]), close_parent_on_save=False)
        elif usage["target_type"] in {"family", "relationship"}:
            person = self.repository.get_person(person_id)
            self.current_person_id = person_id
            self.current_person_gedcom_id = person[0] if person else ""
            self.open_relationship_editor()
        else:
            self.show_person(person_id)

    def _export_sources_csv(self) -> None:
        destination = filedialog.asksaveasfilename(
            parent=self._source_window, title="Экспорт источников", initialdir=str(EXPORT_DIR),
            initialfile="sources.csv", defaultextension=".csv", filetypes=[("CSV files", "*.csv")],
        )
        if destination:
            self.source_service.export_csv(destination)

    def _close_source_manager(self) -> None:
        if self._source_window is not None:
            try:
                self._source_window.destroy()
            except Exception:
                pass
        self._source_window = None
        self._source_tree = None
        self._source_citation_tree = None
        self._source_browser_tree = None
        self._source_usage_map = {}
        self._source_statistics_text = None

    def _load_family_timeline(self) -> None:
        self._family_timeline_entries = self.family_timeline_service.build_timeline()
        if self._family_timeline_event_control is not None:
            event_types = sorted({
                entry.event_type for entry in self._family_timeline_entries
            }, key=str.casefold)
            self._family_timeline_event_control.config(values=("", *event_types))
        self._apply_family_timeline_filters()

    def _apply_family_timeline_filters(self) -> None:
        try:
            year_from = self._optional_timeline_year(self._family_timeline_vars["year_from"].get())
            year_to = self._optional_timeline_year(self._family_timeline_vars["year_to"].get())
            if year_from is not None and year_to is not None and year_from > year_to:
                raise ValueError("Начальный год не может быть больше конечного.")
        except ValueError as error:
            messagebox.showerror("Хронология", str(error), parent=self._family_timeline_window)
            return
        filters = TimelineFilters(
            year_from=year_from,
            year_to=year_to,
            event_type=self._family_timeline_vars["event_type"].get(),
            surname=self._family_timeline_vars["surname"].get(),
            place=self._family_timeline_vars["place"].get(),
        )
        self._family_timeline_visible_entries = self.family_timeline_service.filter_timeline(
            self._family_timeline_entries, filters
        )
        self._render_family_timeline()

    def _render_family_timeline(self) -> None:
        tree = self._family_timeline_tree
        if tree is None:
            return
        for item_id in tree.get_children():
            tree.delete(item_id)
        self._family_timeline_person_ids = {}
        for entry in self._family_timeline_visible_entries:
            item_id = tree.insert("", "end", values=(
                entry.date,
                "" if entry.normalized_year is None else entry.normalized_year,
                entry.person,
                entry.event_label,
                entry.place,
                "" if entry.age is None else entry.age,
            ))
            self._family_timeline_person_ids[item_id] = entry.person_id
        if self._family_timeline_status is not None:
            self._family_timeline_status.config(
                text=f"Показано: {len(self._family_timeline_visible_entries)}"
            )

    @staticmethod
    def _optional_timeline_year(value) -> int | None:
        text = str(value or "").strip()
        if not text:
            return None
        if not text.isdigit() or len(text) > 4:
            raise ValueError("Год должен быть целым числом от 1 до 9999.")
        year = int(text)
        if year < 1 or year > 9999:
            raise ValueError("Год должен быть целым числом от 1 до 9999.")
        return year

    def _open_family_timeline_person(self, _event=None) -> None:
        if self._family_timeline_tree is None:
            return
        selection = self._family_timeline_tree.selection()
        if not selection:
            return
        person_id = self._family_timeline_person_ids.get(selection[0])
        if person_id is not None:
            self.show_person(person_id)

    def _export_family_timeline_csv(self) -> None:
        destination = filedialog.asksaveasfilename(
            parent=self._family_timeline_window,
            title="Экспорт хронологии в CSV",
            initialdir=str(EXPORT_DIR),
            initialfile="family_timeline.csv",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv")],
        )
        if destination:
            self.family_timeline_service.export_csv(self._family_timeline_visible_entries, destination)

    def _export_family_timeline_html(self) -> None:
        destination = filedialog.asksaveasfilename(
            parent=self._family_timeline_window,
            title="Экспорт хронологии в HTML",
            initialdir=str(EXPORT_DIR),
            initialfile="family_timeline.html",
            defaultextension=".html",
            filetypes=[("HTML files", "*.html")],
        )
        if destination:
            self.family_timeline_service.export_html(self._family_timeline_visible_entries, destination)

    def _close_family_timeline(self) -> None:
        if self._family_timeline_window is not None:
            try:
                self._family_timeline_window.destroy()
            except Exception:
                pass
        self._family_timeline_window = None
        self._family_timeline_tree = None
        self._family_timeline_person_ids = {}
        self._family_timeline_event_control = None

    def _start_family_tree_history(self, person_id: int) -> None:
        self._family_tree_original_person_id = person_id
        self._family_tree_history = [person_id]
        self._family_tree_history_index = 0
        self._load_family_tree_person(person_id, add_to_history=False)

    def _load_family_tree_person(self, person_id: int, add_to_history: bool = True) -> None:
        try:
            model = self.family_tree_view_service.build_tree(person_id)
        except Exception as exc:
            messagebox.showerror("Семейное дерево", str(exc), parent=self._family_tree_window)
            return
        if add_to_history:
            if self._family_tree_history_index < len(self._family_tree_history) - 1:
                self._family_tree_history = self._family_tree_history[: self._family_tree_history_index + 1]
            if not self._family_tree_history or self._family_tree_history[-1] != person_id:
                self._family_tree_history.append(person_id)
            self._family_tree_history_index = len(self._family_tree_history) - 1
        self._render_family_tree(model)
        self._update_family_tree_navigation()

    def _render_family_tree(self, model: FamilyTreeModel) -> None:
        self._family_tree_model = model
        self._family_tree_presentation = self.family_tree_view_service.build_card_presentation(model)
        self._family_tree_zoom = 1.0
        self._family_tree_selected_person_id = model.center.database_id
        self._draw_family_tree_canvas()
        self._family_tree_canvas.after_idle(self._center_family_tree_current)

    def _draw_family_tree_canvas(self) -> None:
        canvas = self._family_tree_canvas
        canvas.delete("all")
        scale = self._family_tree_zoom
        card_width = FAMILY_TREE_CARD_WIDTH * scale
        card_height = FAMILY_TREE_CARD_HEIGHT * scale
        gap = 44 * scale
        model = self._family_tree_model
        max_count = max(len(model.parents), len(model.children), len(model.partners) + 1, 4)
        canvas_width = max(1100 * scale, max_count * (card_width + gap) + 240 * scale)
        canvas_height = 780 * scale
        center_x = canvas_width / 2

        parent_positions = self._family_tree_row_positions(
            model.parents, center_x, 70 * scale, card_width, card_height, gap
        )
        child_positions = self._family_tree_row_positions(
            model.children, center_x, 560 * scale, card_width, card_height, gap
        )
        center_position = (center_x - card_width / 2, 305 * scale, card_width, card_height)
        partner_positions = self._family_tree_partner_positions(
            model.partners, center_position, card_width, card_height, gap
        )
        positions = {
            **parent_positions,
            model.center.database_id: center_position,
            **partner_positions,
            **child_positions,
        }

        self._draw_family_tree_connectors(
            model,
            positions,
            parent_positions,
            partner_positions,
            child_positions,
        )
        self._family_tree_card_items = {}
        for person in (*model.parents, model.center, *model.partners, *model.children):
            self._draw_family_tree_card(person, positions[person.database_id])

        self._family_tree_current_center = (
            center_position[0] + card_width / 2,
            center_position[1] + card_height / 2,
        )
        canvas.configure(scrollregion=(0, 0, canvas_width, canvas_height))
        self._family_tree_zoom_label.configure(text=f"{round(scale * 100)}%")

    @staticmethod
    def _family_tree_row_positions(
        people: tuple[FamilyTreePerson, ...],
        center_x: float,
        top: float,
        width: float,
        height: float,
        gap: float,
    ) -> dict[int, tuple[float, float, float, float]]:
        total_width = len(people) * width + max(0, len(people) - 1) * gap
        left = center_x - total_width / 2
        return {
            person.database_id: (left + index * (width + gap), top, width, height)
            for index, person in enumerate(people)
        }

    @staticmethod
    def _family_tree_partner_positions(
        people: tuple[FamilyTreePerson, ...],
        center_position: tuple[float, float, float, float],
        width: float,
        height: float,
        gap: float,
    ) -> dict[int, tuple[float, float, float, float]]:
        center_left, top, _, _ = center_position
        positions = {}
        for index, person in enumerate(people):
            distance = (index // 2 + 1) * (width + gap)
            left = center_left + distance if index % 2 == 0 else center_left - distance
            positions[person.database_id] = (left, top, width, height)
        return positions

    def _draw_family_tree_connectors(
        self,
        model: FamilyTreeModel,
        positions: Mapping[int, tuple[float, float, float, float]],
        parents: Mapping[int, tuple[float, float, float, float]],
        partners: Mapping[int, tuple[float, float, float, float]],
        children: Mapping[int, tuple[float, float, float, float]],
    ) -> None:
        canvas = self._family_tree_canvas
        center_left, center_top, center_width, center_height = positions[model.center.database_id]
        center_x = center_left + center_width / 2
        for left, top, width, height in parents.values():
            parent_x = left + width / 2
            middle_y = (top + height + center_top) / 2
            canvas.create_line(
                parent_x, top + height, parent_x, middle_y, center_x, middle_y,
                center_x, center_top, fill="#75808a", width=2, arrow="last",
            )
        for left, top, width, height in partners.values():
            partner_x = left + (0 if left > center_left else width)
            current_x = center_left + (center_width if left > center_left else 0)
            y = top + height / 2
            canvas.create_line(current_x, y, partner_x, y, fill="#75808a", width=2, arrow="both")
        for left, top, width, _height in children.values():
            child_x = left + width / 2
            middle_y = (center_top + center_height + top) / 2
            canvas.create_line(
                center_x, center_top + center_height, center_x, middle_y, child_x,
                middle_y, child_x, top, fill="#75808a", width=2, arrow="last",
            )

    def _draw_family_tree_card(
        self,
        person: FamilyTreePerson,
        position: tuple[float, float, float, float],
    ) -> None:
        canvas = self._family_tree_canvas
        left, top, width, height = position
        metadata = self._family_tree_presentation[person.database_id]
        border = self._family_tree_card_border(person, metadata["sex"])
        background = self._family_tree_card_background(person)
        tag = f"family-tree-person-{person.database_id}"
        rectangle = canvas.create_rectangle(
            left, top, left + width, top + height, fill=background, outline=border,
            width=4 if person.database_id == self._family_tree_selected_person_id else 2,
            tags=(tag, "family-tree-card"),
        )
        scale = self._family_tree_zoom
        text_left = left + 10 * scale
        lines = (
            (metadata["relationship"], 9, "bold"),
            (person.full_name, 11, "bold"),
            (f"Рождение: {person.birth_date or '-'}", 9, "normal"),
            (f"Смерть: {person.death_date or '-'}", 9, "normal"),
            (f"Database ID: {person.database_id}", 9, "normal"),
            (f"GEDCOM ID: {person.gedcom_id or '-'}", 9, "normal"),
        )
        line_y = top + 12 * scale
        for text, size, weight in lines:
            canvas.create_text(
                text_left, line_y, text=text, anchor="nw", fill="#20252a",
                font=("Segoe UI", max(7, round(size * scale)), weight), tags=(tag,),
            )
            line_y += (23 if size == 11 else 20) * scale
        self._family_tree_card_items[person.database_id] = (rectangle, border)
        canvas.tag_bind(tag, "<Button-1>", lambda _event, value=person.database_id: self._select_family_tree_card(value))
        canvas.tag_bind(tag, "<Double-1>", lambda _event, value=person.database_id: self._load_family_tree_person(value))

    @staticmethod
    def _family_tree_card_border(person: FamilyTreePerson, sex: str) -> str:
        if person.is_unnamed:
            return FAMILY_TREE_UNNAMED_BORDER
        if sex == "M":
            return FAMILY_TREE_MALE_BORDER
        if sex == "F":
            return FAMILY_TREE_FEMALE_BORDER
        return "#7a8793"

    def _family_tree_card_background(self, person: FamilyTreePerson) -> str:
        if person.is_unnamed:
            return FAMILY_TREE_UNNAMED_BACKGROUND
        if person.database_id == self._family_tree_model.center.database_id:
            return FAMILY_TREE_CURRENT_BACKGROUND
        return "white"

    def _select_family_tree_card(self, person_id: int) -> None:
        self._family_tree_selected_person_id = person_id
        for card_id, (item_id, border) in self._family_tree_card_items.items():
            self._family_tree_canvas.itemconfigure(
                item_id,
                outline=border,
                width=4 if card_id == person_id else 2,
            )

    def _zoom_family_tree(self, event: Any) -> str:
        direction = 1 if getattr(event, "delta", 0) > 0 else -1
        self._family_tree_zoom = self._clamp_family_tree_zoom(
            self._family_tree_zoom + direction * FAMILY_TREE_ZOOM_STEP
        )
        self._draw_family_tree_canvas()
        self._family_tree_canvas.after_idle(self._center_family_tree_current)
        return "break"

    @staticmethod
    def _clamp_family_tree_zoom(value: float) -> float:
        return max(FAMILY_TREE_MIN_ZOOM, min(FAMILY_TREE_MAX_ZOOM, round(value, 2)))

    def _start_family_tree_drag(self, event: Any) -> None:
        self._family_tree_canvas.scan_mark(event.x, event.y)

    def _drag_family_tree(self, event: Any) -> None:
        self._family_tree_canvas.scan_dragto(event.x, event.y, gain=1)

    def _center_family_tree_current(self) -> None:
        canvas = self._family_tree_canvas
        canvas.update_idletasks()
        scroll_region = canvas.cget("scrollregion").split()
        if len(scroll_region) != 4:
            return
        _, _, region_width, region_height = (float(value) for value in scroll_region)
        center_x, center_y = self._family_tree_current_center
        viewport_width = max(1, canvas.winfo_width())
        viewport_height = max(1, canvas.winfo_height())
        canvas.xview_moveto(max(0.0, min(1.0, (center_x - viewport_width / 2) / region_width)))
        canvas.yview_moveto(max(0.0, min(1.0, (center_y - viewport_height / 2) / region_height)))

    def _family_tree_back(self) -> None:
        if self._family_tree_history_index <= 0:
            return
        self._family_tree_history_index -= 1
        self._load_family_tree_person(
            self._family_tree_history[self._family_tree_history_index],
            add_to_history=False,
        )

    def _family_tree_forward(self) -> None:
        if self._family_tree_history_index >= len(self._family_tree_history) - 1:
            return
        self._family_tree_history_index += 1
        self._load_family_tree_person(
            self._family_tree_history[self._family_tree_history_index],
            add_to_history=False,
        )

    def _family_tree_return_to_original(self) -> None:
        if self._family_tree_original_person_id is not None:
            self._load_family_tree_person(self._family_tree_original_person_id)

    def _update_family_tree_navigation(self) -> None:
        self._family_tree_back_button.configure(
            state="normal" if self._family_tree_history_index > 0 else "disabled"
        )
        self._family_tree_forward_button.configure(
            state=(
                "normal"
                if self._family_tree_history_index < len(self._family_tree_history) - 1
                else "disabled"
            )
        )

    def _close_family_tree(self) -> None:
        if self._family_tree_window is not None:
            try:
                self._family_tree_window.destroy()
            except Exception:
                pass
        self._family_tree_window = None
        self._family_tree_original_person_id = None
        self._family_tree_history = []
        self._family_tree_history_index = -1

    def _edit_selected_person(self):
        person_id = self._selected_person_id()
        if person_id is None:
            messagebox.showwarning("Выбор", "Сначала выберите человека.")
            return
        self._show_person_editor(person_id)

    def _delete_selected_person(self):
        person_id = self._selected_person_id()
        if person_id is None:
            messagebox.showwarning("Выбор", "Сначала выберите человека.")
            return
        self._delete_person(person_id)

    def show_person(self, person_id, add_to_history=True):
        if not hasattr(self, "attachment_service"):
            self.attachment_service = PersonAttachmentService(self.repository, media_root=DATA_DIR / "media")
        if not hasattr(self, "timeline_service"):
            self.timeline_service = PersonTimelineService(self.repository)
        if not hasattr(self, "life_map_service"):
            self.life_map_service = PersonLifeMapService(self.repository, timeline_service=self.timeline_service)
        if not hasattr(self, "_card_media_records"):
            self._card_media_records = []
        if not hasattr(self, "_card_source_records"):
            self._card_source_records = []
        if not hasattr(self, "_timeline_entries"):
            self._timeline_entries = []
            self._timeline_source_map = {}
        if not hasattr(self, "_person_history"):
            self._person_history = []
            self._person_history_index = -1

        person = self.repository.get_person(person_id)
        if not person:
            messagebox.showerror("Ошибка", "Человек не найден.")
            return
        self.current_person_id = person_id
        self.current_person_gedcom_id = person[0]
        if add_to_history:
            self._push_person_history(person_id)

        dialog = self._person_dialog
        if dialog is None:
            dialog = self._create_dialog()
            dialog.geometry("860x620")
            self._person_dialog = dialog
            dialog.protocol("WM_DELETE_WINDOW", self._close_person_card)
        else:
            for method_name in ("deiconify", "lift", "focus_set"):
                method = getattr(dialog, method_name, None)
                if callable(method):
                    try:
                        method()
                    except Exception:
                        pass

        dialog.title("Person Card")
        dialog.grab_set()

        if self._person_card_body is not None:
            try:
                self._person_card_body.destroy()
            except Exception:
                pass
            self._person_card_body = None

        body = tk.Frame(dialog)
        body.pack(fill="both", expand=True, padx=16, pady=16)
        self._person_card_body = body

        nav = tk.Frame(body)
        nav.pack(fill="x", pady=(0, 8))
        tk.Button(nav, text="Назад", command=lambda: self._navigate_person_history(-1)).pack(side="left")
        tk.Button(nav, text="Вперёд", command=lambda: self._navigate_person_history(1)).pack(side="left", padx=(8, 0))
        tk.Button(nav, text="События", command=lambda: self._show_person_events(person_id)).pack(side="left", padx=(12, 0))
        tk.Button(nav, text="Редактировать семью", command=self.open_relationship_editor).pack(side="left", padx=(8, 0))
        tk.Button(nav, text="Закрыть", command=self._close_person_card).pack(side="right")

        details_tab = None
        timeline_tab = None
        life_map_tab = None
        notebook = None
        try:
            notebook = ttk.Notebook(body)
            notebook.pack(fill="both", expand=True)
            details_tab = tk.Frame(notebook)
            timeline_tab = tk.Frame(notebook)
            life_map_tab = tk.Frame(notebook)
            notebook.add(details_tab, text="Карточка")
            notebook.add(timeline_tab, text="Хронология")
            notebook.add(life_map_tab, text="Карта жизни")
        except Exception:
            details_tab = tk.Frame(body)
            details_tab.pack(fill="both", expand=True)

        scroll_host = tk.Frame(details_tab)
        scroll_host.pack(fill="both", expand=True)
        canvas = tk.Canvas(scroll_host, highlightthickness=0)
        v_scroll = ttk.Scrollbar(scroll_host, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=v_scroll.set)
        canvas.pack(side="left", fill="both", expand=True)
        v_scroll.pack(side="right", fill="y")

        content = tk.Frame(canvas)
        content_window = canvas.create_window((0, 0), window=content, anchor="nw")

        content.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(content_window, width=event.width))

        name = self.format_name(person[1], person[2]) or "(без имени)"
        tk.Label(content, text=name, font=("default", 16, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 10))

        def _value(text):
            return text if text else "нет данных"

        details = tk.LabelFrame(content, text="Основные данные")
        details.grid(row=1, column=0, sticky="ew", pady=(0, 10))

        detail_rows = [
            ("Пол", _value(person[3])),
            ("Дата рождения", _value(person[4])),
            ("Место рождения", _value(person[5])),
            ("Дата смерти", _value(person[6])),
            ("Место смерти", _value(person[7])),
            ("Занятие", _value(person[8])),
            ("Примечания", _value(person[9])),
            ("Полное имя", name),
        ]

        for index, (label, value) in enumerate(detail_rows):
            row_index = index // 2
            col_offset = (index % 2) * 2
            tk.Label(details, text=f"{label}:").grid(row=row_index, column=col_offset, sticky="w", padx=(8, 6), pady=4)
            tk.Label(details, text=value).grid(row=row_index, column=col_offset + 1, sticky="w", padx=(0, 12), pady=4)

        details.grid_columnconfigure(1, weight=1)
        details.grid_columnconfigure(3, weight=1)

        person_gedcom_id = person[0] or str(person_id)
        family_groups = [
            ("Биологический отец", self.repository.get_biological_fathers(person_gedcom_id) or []),
            ("Биологическая мать", self.repository.get_biological_mothers(person_gedcom_id) or []),
            ("Приемные родители", self.repository.get_adoptive_parents(person_gedcom_id) or []),
            ("Супруги", self.repository.get_spouses(person_gedcom_id) or []),
            ("Дети", self.repository.get_children(person_gedcom_id) or []),
            ("Родные братья и сестры", self.repository.get_full_siblings(person_gedcom_id) or []),
            ("Единокровные братья и сестры", self.repository.get_half_siblings_paternal(person_gedcom_id) or []),
            ("Единоутробные братья и сестры", self.repository.get_half_siblings_maternal(person_gedcom_id) or []),
            ("Дедушки и бабушки", self.repository.get_grandparents(person_gedcom_id) or []),
            ("Внуки", self.repository.get_grandchildren(person_gedcom_id) or []),
            ("Дяди и тети", self.repository.get_uncles_aunts(person_gedcom_id) or []),
            ("Племянники и племянницы", self.repository.get_nephews_nieces(person_gedcom_id) or []),
            ("Двоюродные братья и сестры", self.repository.get_first_cousins(person_gedcom_id) or []),
        ]

        section_row = 2
        for label, rows in family_groups:
            self._build_relatives_section(content, label, rows, section_row)
            section_row += 1

        events_section = tk.LabelFrame(content, text="События")
        events_section.grid(row=section_row, column=0, sticky="nsew", padx=0, pady=(0, 8))
        events_container = tk.Frame(events_section)
        events_container.pack(fill="both", expand=True, padx=8, pady=8)
        events_list = tk.Listbox(events_container, height=8)
        events_scroll = ttk.Scrollbar(events_container, orient="vertical", command=events_list.yview)
        events_list.configure(yscrollcommand=events_scroll.set)
        events_list.pack(side="left", fill="both", expand=True)
        events_scroll.pack(side="right", fill="y")

        events = self.event_service.list_events(person_id)
        if not events:
            events_list.insert("end", "Нет данных")
        else:
            for event in events:
                event_type = event.get("event_type", "custom")
                event_date = event.get("date") or ""
                event_place = event.get("place") or ""
                event_description = event.get("description") or ""
                events_list.insert("end", f"{event_type}: {event_date} | {event_place} | {event_description}")

        section_row += 1
        media_section = tk.LabelFrame(content, text="Фотографии и документы")
        media_section.grid(row=section_row, column=0, sticky="ew", padx=0, pady=(0, 8))
        media_section.grid_columnconfigure(0, weight=1)

        photo_preview_frame = tk.Frame(media_section, bd=1, relief="solid")
        photo_preview_frame.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 6))
        photo_preview_frame.grid_columnconfigure(0, weight=1)

        preview_label = tk.Label(photo_preview_frame, text=self._photo_preview_placeholder_text(), width=44, height=14, bg="#f2f4f7")
        preview_label.grid(row=0, column=0, columnspan=3, sticky="nsew", padx=8, pady=(8, 6))
        preview_label.bind("<Double-1>", lambda _event: self._open_current_photo_original())

        counter_label = tk.Label(photo_preview_frame, text="0 из 0")
        counter_label.grid(row=1, column=1, sticky="n")
        tk.Button(
            photo_preview_frame,
            text="Предыдущая",
            command=lambda: self._show_previous_photo(preview_label, counter_label, photo_title_label, photo_description_label),
        ).grid(row=1, column=0, sticky="w", padx=8, pady=(0, 6))
        tk.Button(
            photo_preview_frame,
            text="Следующая",
            command=lambda: self._show_next_photo(preview_label, counter_label, photo_title_label, photo_description_label),
        ).grid(row=1, column=2, sticky="e", padx=8, pady=(0, 6))

        photo_title_label = tk.Label(photo_preview_frame, text="Название: нет", anchor="w", justify="left")
        photo_title_label.grid(row=2, column=0, columnspan=3, sticky="ew", padx=8)
        photo_description_label = tk.Label(photo_preview_frame, text="Описание: нет", anchor="w", justify="left", wraplength=760)
        photo_description_label.grid(row=3, column=0, columnspan=3, sticky="ew", padx=8, pady=(0, 8))

        photo_controls = tk.Frame(media_section)
        photo_controls.grid(row=1, column=0, sticky="w", padx=8, pady=(0, 6))
        tk.Button(photo_controls, text="Добавить фото", command=lambda: self._add_media_attachment(person_id, "photo")).pack(side="left")
        tk.Button(photo_controls, text="Сделать портретом", command=self._mark_current_photo_primary).pack(side="left", padx=(8, 0))

        documents_label = tk.Label(media_section, text="Документы")
        documents_label.grid(row=2, column=0, sticky="w", padx=8)

        documents_frame = tk.Frame(media_section)
        documents_frame.grid(row=3, column=0, sticky="nsew", padx=8, pady=(4, 6))
        documents_frame.grid_columnconfigure(0, weight=1)
        documents_frame.grid_rowconfigure(0, weight=1)

        documents_listbox = tk.Listbox(documents_frame, height=6)
        documents_listbox.grid(row=0, column=0, sticky="nsew")
        documents_scroll = ttk.Scrollbar(documents_frame, orient="vertical", command=documents_listbox.yview)
        documents_listbox.configure(yscrollcommand=documents_scroll.set)
        documents_scroll.grid(row=0, column=1, sticky="ns")
        documents_listbox.bind("<Double-1>", lambda _event: self._open_selected_document(documents_listbox))

        document_controls = tk.Frame(media_section)
        document_controls.grid(row=4, column=0, sticky="w", padx=8, pady=(0, 8))
        tk.Button(document_controls, text="Добавить документ", command=lambda: self._add_media_attachment(person_id, "document")).pack(side="left")
        tk.Button(document_controls, text="Открыть", command=lambda: self._open_selected_document(documents_listbox)).pack(side="left", padx=(8, 0))
        tk.Button(document_controls, text="Переименовать", command=lambda: self._rename_selected_document(documents_listbox)).pack(side="left", padx=(8, 0))
        tk.Button(document_controls, text="Изменить описание", command=lambda: self._edit_selected_document_description(documents_listbox)).pack(side="left", padx=(8, 0))
        tk.Button(document_controls, text="Удалить", command=lambda: self._delete_selected_document(documents_listbox)).pack(side="left", padx=(8, 0))

        grouped_media = self.attachment_service.list_media_grouped(person_id)
        self._card_photo_records = grouped_media.get("photos", [])
        self._card_document_records = grouped_media.get("documents", [])
        self._card_media_records = self._card_photo_records + self._card_document_records
        self._set_photo_index(0)
        self._render_photo_preview(preview_label, counter_label, photo_title_label, photo_description_label)

        for document in self._card_document_records:
            documents_listbox.insert("end", self._document_row_text(document))

        section_row += 1
        sources_section = tk.LabelFrame(content, text="Источники")
        sources_section.grid(row=section_row, column=0, sticky="ew", padx=0, pady=(0, 8))
        sources_section.grid_columnconfigure(0, weight=1)

        sources_listbox = tk.Listbox(sources_section, height=6)
        sources_listbox.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 8))

        source_controls = tk.Frame(sources_section)
        source_controls.grid(row=1, column=0, sticky="w", padx=8, pady=(0, 8))
        tk.Button(source_controls, text="Добавить", command=lambda: self._add_source(person_id)).pack(side="left")
        tk.Button(source_controls, text="Изменить", command=lambda: self._edit_selected_source(person_id, sources_listbox)).pack(side="left", padx=(8, 0))
        tk.Button(source_controls, text="Удалить", command=lambda: self._delete_selected_source(sources_listbox)).pack(side="left", padx=(8, 0))
        tk.Button(source_controls, text="Открыть URL", command=lambda: self._open_selected_source(sources_listbox)).pack(side="left", padx=(8, 0))

        self._card_source_records = self.attachment_service.list_sources(person_id)
        for source in self._card_source_records:
            sources_listbox.insert("end", self._display_source_item(source))

        content.grid_columnconfigure(0, weight=1)
        content.grid_rowconfigure(section_row, weight=1)

        if timeline_tab is not None:
            self._build_timeline_tab(timeline_tab, person_id)
        if life_map_tab is not None:
            self._build_life_map_tab(life_map_tab, person_id)

    def _show_person_events(self, person_id):
        dialog = self._create_dialog()
        dialog.title("События")
        dialog.geometry("640x420")
        dialog.grab_set()

        tk.Label(dialog, text="События человека").pack(anchor="w", padx=12, pady=(12, 6))
        listbox = tk.Listbox(dialog, height=10)
        listbox.pack(fill="both", expand=True, padx=12, pady=(0, 8))

        for event in self.event_service.list_events(person_id):
            event_type = event.get("event_type", "custom")
            date = event.get("date") or ""
            place = event.get("place") or ""
            description = event.get("description") or ""
            listbox.insert("end", f"{event_type}: {date} | {place} | {description}")

        controls = tk.Frame(dialog)
        controls.pack(fill="x", padx=12, pady=(0, 12))
        tk.Button(controls, text="Добавить", command=lambda: self._manage_person_event(dialog, person_id, None)).pack(side="left")
        tk.Button(controls, text="Изменить", command=lambda: self._edit_person_event(dialog, person_id, listbox)).pack(side="left", padx=(8, 0))
        tk.Button(controls, text="Удалить", command=lambda: self._delete_person_event(dialog, person_id, listbox)).pack(side="left", padx=(8, 0))

    def _manage_person_event(self, dialog, person_id, event_id, close_parent_on_save=True):
        event_window = self._create_dialog(dialog)
        event_window.title("Событие")
        event_window.geometry("480x320")
        event_window.grab_set()

        fields = {}
        form = tk.Frame(event_window)
        form.pack(fill="both", expand=True, padx=12, pady=12)

        event_types = [
            "birth",
            "baptism",
            "residence",
            "education",
            "occupation",
            "military_service",
            "marriage",
            "divorce",
            "immigration",
            "emigration",
            "census",
            "awards",
            "death",
            "burial",
            "custom",
        ]
        default_type = "custom"
        if event_id:
            event = self.repository.get_person_event(event_id)
            if event:
                default_type = event.get("event_type", "custom")

        tk.Label(form, text="Тип").grid(row=0, column=0, sticky="w", pady=4)
        event_type_var = tk.StringVar(value=default_type)
        combobox = ttk.Combobox(form, textvariable=event_type_var, values=event_types, state="readonly")
        combobox.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=4)
        fields["event_type"] = event_type_var

        for label, key, default in [("Дата", "date", ""), ("Место", "place", ""), ("Описание", "description", "")]:
            tk.Label(form, text=label).grid(row=len(fields), column=0, sticky="w", pady=4)
            entry = tk.Entry(form)
            if event_id:
                event = self.repository.get_person_event(event_id)
                if event:
                    entry.insert(0, event.get(key, ""))
            entry.grid(row=len(fields), column=1, sticky="ew", padx=(8, 0), pady=4)
            fields[key] = entry

        def save():
            try:
                if event_id:
                    self.event_service.update_event(
                        event_id,
                        event_type=event_type_var.get().strip(),
                        date=fields["date"].get().strip(),
                        place=fields["place"].get().strip(),
                        description=fields["description"].get().strip(),
                    )
                else:
                    self.event_service.create_event(
                        person_id,
                        event_type=event_type_var.get().strip(),
                        date=fields["date"].get().strip(),
                        place=fields["place"].get().strip(),
                        description=fields["description"].get().strip(),
                    )
                event_window.destroy()
                if close_parent_on_save:
                    dialog.destroy()
                self.show_person(person_id)
            except ValueError as error:
                messagebox.showerror("Ошибка", str(error), parent=event_window)

        tk.Button(form, text="Сохранить", command=save).grid(row=4, column=1, sticky="e", pady=(12, 0))

    def _edit_person_event(self, dialog, person_id, listbox):
        selection = listbox.curselection()
        if not selection:
            messagebox.showwarning("Выбор", "Сначала выберите событие.")
            return
        events = self.event_service.list_events(person_id)
        if not events:
            return
        event_id = events[selection[0]]["id"]
        self._manage_person_event(dialog, person_id, event_id)

    def _delete_person_event(self, dialog, person_id, listbox):
        selection = listbox.curselection()
        if not selection:
            messagebox.showwarning("Выбор", "Сначала выберите событие.")
            return
        events = self.event_service.list_events(person_id)
        if not events:
            return
        event_id = events[selection[0]]["id"]
        if messagebox.askyesno("Удаление", "Удалить выбранное событие?"):
            self.event_service.delete_event(event_id)
            dialog.destroy()
            self.show_person(person_id)

    def build_family_tree_nodes(self, person_reference):
        person_id = self._resolve_person_id_for_view(person_reference)
        if person_id is None:
            return []
        person = self.repository.get_person(person_id)
        if not person:
            return []
        _, last_name, first_name, *_ = person
        nodes = [
            {
                "id": person_reference,
                "name": self.format_name(last_name, first_name) or str(person_reference),
                "role": "center",
                "x": 0,
                "y": 0,
            }
        ]
        for index, (p_last, p_first, p_gedcom) in enumerate(self.repository.get_parents(person_reference)):
            nodes.append(
                {
                    "id": p_gedcom,
                    "name": self.format_name(p_last, p_first) or p_gedcom,
                    "role": "parent",
                    "x": -220,
                    "y": -120 + index * 70,
                }
            )
        for index, (s_last, s_first, s_gedcom) in enumerate(self.repository.get_spouses(person_reference)):
            nodes.append(
                {
                    "id": s_gedcom,
                    "name": self.format_name(s_last, s_first) or s_gedcom,
                    "role": "spouse",
                    "x": 220,
                    "y": -120 + index * 70,
                }
            )
        for index, (c_last, c_first, c_gedcom) in enumerate(self.repository.get_children(person_reference)):
            nodes.append(
                {
                    "id": c_gedcom,
                    "name": self.format_name(c_last, c_first) or c_gedcom,
                    "role": "child",
                    "x": -120 + index * 120,
                    "y": 140,
                }
            )
        return nodes

    def insert_people(self, text, rows, empty_text, show_gedcom_id=True, clickable_hint=False):
        if not rows:
            text.insert("end", f"  {empty_text}\n")
            return
        for last_name, first_name, gedcom_id in rows:
            name = self.format_name(last_name, first_name) or "(без имени)"
            visible_id = f" [{gedcom_id}]" if show_gedcom_id else ""
            hint = " (open card)" if clickable_hint else ""
            display_text = f"  {name}{visible_id}{hint}\n"
            text.insert("end", display_text)
            tag_name = f"person:{gedcom_id}"
            start_index = text.index("end-1c linestart")
            end_index = text.index("end-1c")
            text.tag_add(tag_name, start_index, end_index)
            text.tag_configure(tag_name, foreground="blue", underline=True)
            text.tag_bind(tag_name, "<Button-1>", lambda _event, gid=gedcom_id: self.open_related_person(gid))

    def open_related_person(self, person_reference):
        person_id = self._resolve_person_id_for_view(person_reference)
        if person_id is None:
            messagebox.showerror("Ошибка", "Человек не найден.")
            return
        person = self.repository.get_person(person_id)
        self.current_person_id = person_id
        self.current_person_gedcom_id = person[0] if person else ""
        self.show_person(person_id)

    def _show_person_editor(self, person_id=None):
        dialog = self._create_dialog()
        dialog.title("Редактор человека")
        dialog.geometry("640x520")
        dialog.grab_set()

        person = None
        if person_id is not None:
            person = self.repository.get_person(person_id)

        form = tk.Frame(dialog)
        form.pack(fill="both", expand=True, padx=16, pady=16)

        fields = {}
        labels = [
            ("Имя", "first_name"),
            ("Фамилия", "last_name"),
            ("Пол", "sex"),
            ("Дата рождения", "birth_date"),
            ("Место рождения", "birth_place"),
            ("Дата смерти", "death_date"),
            ("Место смерти", "death_place"),
            ("Занятие", "occupation"),
            ("Примечание", "note"),
        ]

        person_values = {}
        if person:
            person_values = {
                "first_name": person[2] or "",
                "last_name": person[1] or "",
                "sex": person[3] or "",
                "birth_date": person[4] or "",
                "birth_place": person[5] or "",
                "death_date": person[6] or "",
                "death_place": person[7] or "",
                "occupation": person[8] or "",
                "note": person[9] or "",
            }

        row_index = 0
        for label, key in labels:
            tk.Label(form, text=label).grid(row=row_index, column=0, sticky="w", pady=4)
            if key == "sex":
                var = tk.StringVar(value=person_values.get("sex", ""))
                combobox = ttk.Combobox(form, textvariable=var, values=["M", "F", ""], state="readonly")
                combobox.grid(row=row_index, column=1, sticky="ew", padx=(8, 0), pady=4)
                fields[key] = var
            else:
                entry = tk.Entry(form)
                entry.grid(row=row_index, column=1, sticky="ew", padx=(8, 0), pady=4)
                fields[key] = entry
            row_index += 1

        for key, value in person_values.items():
            if key in fields:
                widget = fields[key]
                if isinstance(widget, tk.StringVar):
                    widget.set(value)
                else:
                    widget.delete(0, tk.END)
                    widget.insert(0, value)

        buttons = tk.Frame(dialog)
        buttons.pack(fill="x", padx=16, pady=(0, 16))

        def save():
            data = {
                "first_name": fields["first_name"].get().strip() if isinstance(fields["first_name"], tk.Entry) else fields["first_name"].get().strip(),
                "last_name": fields["last_name"].get().strip() if isinstance(fields["last_name"], tk.Entry) else fields["last_name"].get().strip(),
                "sex": fields["sex"].get().strip() if isinstance(fields["sex"], tk.Entry) else fields["sex"].get().strip(),
                "birth_date": fields["birth_date"].get().strip() if isinstance(fields["birth_date"], tk.Entry) else fields["birth_date"].get().strip(),
                "birth_place": fields["birth_place"].get().strip() if isinstance(fields["birth_place"], tk.Entry) else fields["birth_place"].get().strip(),
                "death_date": fields["death_date"].get().strip() if isinstance(fields["death_date"], tk.Entry) else fields["death_date"].get().strip(),
                "death_place": fields["death_place"].get().strip() if isinstance(fields["death_place"], tk.Entry) else fields["death_place"].get().strip(),
                "occupation": fields["occupation"].get().strip() if isinstance(fields["occupation"], tk.Entry) else fields["occupation"].get().strip(),
                "note": fields["note"].get().strip() if isinstance(fields["note"], tk.Entry) else fields["note"].get().strip(),
            }
            try:
                self._save_person(person_id, data)
            except ValueError as error:
                messagebox.showerror("Ошибка", str(error), parent=dialog)
                return
            dialog.destroy()
            self.search_people()

        tk.Button(buttons, text="Сохранить", command=save).pack(side="left")
        tk.Button(buttons, text="Отмена", command=dialog.destroy).pack(side="left", padx=(8, 0))

    def _save_person(self, person_id, data):
        first_name = (data or {}).get("first_name", "") or ""
        last_name = (data or {}).get("last_name", "") or ""
        if not first_name.strip() or not last_name.strip():
            raise ValueError("Имя и фамилия обязательны")

        payload = {
            "gedcom_id": "",
            "first_name": first_name.strip(),
            "last_name": last_name.strip(),
            "sex": (data or {}).get("sex", "") or "",
            "birth_date": (data or {}).get("birth_date", "") or "",
            "birth_place": (data or {}).get("birth_place", "") or "",
            "death_date": (data or {}).get("death_date", "") or "",
            "death_place": (data or {}).get("death_place", "") or "",
            "occupation": (data or {}).get("occupation", "") or "",
            "note": (data or {}).get("note", "") or "",
        }
        if person_id is None:
            person_id = self._get_undo_manager().execute(AddPersonCommand(self.repository, payload))
        else:
            self._get_undo_manager().execute(EditPersonCommand(self.repository, person_id, payload))
        self.current_person_id = person_id
        return person_id

    def _delete_person(self, person_id):
        if person_id is None:
            return False
        if messagebox.askyesno("Удаление", "Удалить выбранного человека?"):
            deleted = self._get_undo_manager().execute(DeletePersonCommand(self.repository, person_id))
            self.current_person_id = None
            self.search_people()
            return deleted
        return False

    def open_relationship_editor(self):
        if self.current_person_id is None:
            messagebox.showwarning("Связи", "Сначала выберите человека из списка.")
            return

        if not hasattr(self, "relationship_service"):
            self.relationship_service = RelationshipService(self.repository)

        person_reference = self._current_person_reference()
        state = self.relationship_service.get_relationship_editor_state(person_reference)

        dialog = self._create_dialog()
        dialog.title("Редактор отношений")
        dialog.geometry("880x620")
        dialog.grab_set()

        title_name = self.format_name(state["person"].get("last_name"), state["person"].get("first_name")) or "Без имени"
        tk.Label(dialog, text=f"Отношения для {title_name}").pack(anchor="w", padx=12, pady=12)

        body = tk.Frame(dialog)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(2, weight=1)

        parent_section = tk.LabelFrame(body, text="Родители")
        parent_section.grid(row=0, column=0, sticky="nsew", padx=(0, 6), pady=(0, 8))
        parent_listbox = tk.Listbox(parent_section, height=8)
        parent_listbox.pack(fill="both", expand=True, padx=8, pady=(8, 6))
        parent_controls = tk.Frame(parent_section)
        parent_controls.pack(fill="x", padx=8, pady=(0, 8))
        tk.Button(parent_controls, text="Добавить отца", command=lambda: self._relationship_add_parent(dialog, person_reference, parent_listbox, partner_listbox, child_listbox, "father", False)).pack(side="left")
        tk.Button(parent_controls, text="Создать отца", command=lambda: self._relationship_add_parent(dialog, person_reference, parent_listbox, partner_listbox, child_listbox, "father", True)).pack(side="left", padx=(8, 0))
        tk.Button(parent_controls, text="Добавить мать", command=lambda: self._relationship_add_parent(dialog, person_reference, parent_listbox, partner_listbox, child_listbox, "mother", False)).pack(side="left", padx=(8, 0))
        tk.Button(parent_controls, text="Создать мать", command=lambda: self._relationship_add_parent(dialog, person_reference, parent_listbox, partner_listbox, child_listbox, "mother", True)).pack(side="left", padx=(8, 0))
        tk.Button(parent_controls, text="Удалить связь", command=lambda: self._relationship_remove_parent(dialog, person_reference, parent_listbox, partner_listbox, child_listbox)).pack(side="left", padx=(8, 0))

        partner_section = tk.LabelFrame(body, text="Супруги и партнёры")
        partner_section.grid(row=0, column=1, sticky="nsew", padx=(6, 0), pady=(0, 8))
        partner_listbox = tk.Listbox(partner_section, height=8)
        partner_listbox.pack(fill="both", expand=True, padx=8, pady=(8, 6))
        partner_controls = tk.Frame(partner_section)
        partner_controls.pack(fill="x", padx=8, pady=(0, 8))
        tk.Button(partner_controls, text="Добавить связь", command=lambda: self._relationship_add_partner(dialog, person_reference, parent_listbox, partner_listbox, child_listbox, False)).pack(side="left")
        tk.Button(partner_controls, text="Создать связь", command=lambda: self._relationship_add_partner(dialog, person_reference, parent_listbox, partner_listbox, child_listbox, True)).pack(side="left", padx=(8, 0))
        tk.Button(partner_controls, text="Удалить связь", command=lambda: self._relationship_remove_partner(dialog, person_reference, parent_listbox, partner_listbox, child_listbox)).pack(side="left", padx=(8, 0))

        child_section = tk.LabelFrame(body, text="Дети")
        child_section.grid(row=1, column=0, columnspan=2, sticky="nsew", pady=(0, 8))
        child_listbox = tk.Listbox(child_section, height=10)
        child_listbox.pack(fill="both", expand=True, padx=8, pady=(8, 6))
        child_controls = tk.Frame(child_section)
        child_controls.pack(fill="x", padx=8, pady=(0, 8))
        tk.Button(child_controls, text="Добавить ребёнка", command=lambda: self._relationship_add_child(dialog, person_reference, parent_listbox, partner_listbox, child_listbox, False)).pack(side="left")
        tk.Button(child_controls, text="Создать ребёнка", command=lambda: self._relationship_add_child(dialog, person_reference, parent_listbox, partner_listbox, child_listbox, True)).pack(side="left", padx=(8, 0))
        tk.Button(child_controls, text="Удалить связь", command=lambda: self._relationship_remove_child(dialog, person_reference, parent_listbox, partner_listbox, child_listbox)).pack(side="left", padx=(8, 0))

        footer = tk.Frame(body)
        footer.grid(row=2, column=0, columnspan=2, sticky="ew")
        tk.Button(footer, text="Закрыть", command=dialog.destroy).pack(side="right")

        self._reload_relationship_editor(person_reference, parent_listbox, partner_listbox, child_listbox)

    def _relationship_add_parent(self, dialog, person_reference, parent_listbox, partner_listbox, child_listbox, parent_role, create_new):
        selected_reference = None
        if create_new:
            person_data = self._collect_person_form_data("Создать родителя")
            if not person_data:
                return
            changed = self._apply_relationship_change(lambda: self.relationship_service.create_parent_and_link(person_reference, parent_role, person_data))
        else:
            selected_reference = self._choose_person("Выберите родителя", exclude_reference=person_reference)
            if not selected_reference:
                return
            changed = self._apply_relationship_change(lambda: self.relationship_service.link_parent(person_reference, selected_reference, parent_role))
        if changed:
            self._reload_relationship_editor(person_reference, parent_listbox, partner_listbox, child_listbox)

    def _relationship_remove_parent(self, dialog, person_reference, parent_listbox, partner_listbox, child_listbox):
        record = self._selected_dialog_record(parent_listbox)
        if not record or not record.get("person"):
            messagebox.showwarning("Выбор", "Сначала выберите связь родителя.", parent=dialog)
            return
        if not messagebox.askyesno("Подтверждение", "Удалить связь с выбранным родителем?", parent=dialog):
            return
        parent_role = record.get("link_type")
        changed = self._apply_relationship_change(lambda: self.relationship_service.remove_parent_link(person_reference, record["family_id"], parent_role))
        if changed:
            self._reload_relationship_editor(person_reference, parent_listbox, partner_listbox, child_listbox)

    def _relationship_add_partner(self, dialog, person_reference, parent_listbox, partner_listbox, child_listbox, create_new):
        relationship_type = self._prompt_relationship_type("Тип отношений", default_value="unknown")
        if not relationship_type:
            return
        if create_new:
            person_data = self._collect_person_form_data("Создать супруга или партнёра")
            if not person_data:
                return
            changed = self._apply_relationship_change(lambda: self.relationship_service.create_partner_and_link(person_reference, person_data, relationship_type=relationship_type))
        else:
            selected_reference = self._choose_person("Выберите супруга или партнёра", exclude_reference=person_reference)
            if not selected_reference:
                return
            changed = self._apply_relationship_change(lambda: self.relationship_service.link_partner(person_reference, selected_reference, relationship_type=relationship_type))
        if changed:
            self._reload_relationship_editor(person_reference, parent_listbox, partner_listbox, child_listbox)

    def _relationship_remove_partner(self, dialog, person_reference, parent_listbox, partner_listbox, child_listbox):
        record = self._selected_dialog_record(partner_listbox)
        if not record or not record.get("person"):
            messagebox.showwarning("Выбор", "Сначала выберите связь супруга или партнёра.", parent=dialog)
            return
        if not messagebox.askyesno("Подтверждение", "Удалить связь с выбранным супругом или партнёром?", parent=dialog):
            return
        changed = self._apply_relationship_change(lambda: self.relationship_service.remove_partner_link(person_reference, record["family_id"]))
        if changed:
            self._reload_relationship_editor(person_reference, parent_listbox, partner_listbox, child_listbox)

    def _relationship_add_child(self, dialog, person_reference, parent_listbox, partner_listbox, child_listbox, create_new):
        other_parent_reference = self._choose_other_parent(person_reference)
        if create_new:
            person_data = self._collect_person_form_data("Создать ребёнка")
            if not person_data:
                return
            changed = self._apply_relationship_change(lambda: self.relationship_service.create_child_and_link(person_reference, person_data, other_parent_reference=other_parent_reference, relationship_type="unknown"))
        else:
            child_reference = self._choose_person("Выберите ребёнка", exclude_reference=person_reference)
            if not child_reference:
                return
            changed = self._apply_relationship_change(lambda: self.relationship_service.link_child(person_reference, child_reference, other_parent_reference=other_parent_reference, relationship_type="unknown"))
        if changed:
            self._reload_relationship_editor(person_reference, parent_listbox, partner_listbox, child_listbox)

    def _relationship_remove_child(self, dialog, person_reference, parent_listbox, partner_listbox, child_listbox):
        record = self._selected_dialog_record(child_listbox)
        if not record or not record.get("person"):
            messagebox.showwarning("Выбор", "Сначала выберите связь ребёнка.", parent=dialog)
            return
        if not messagebox.askyesno("Подтверждение", "Удалить связь с выбранным ребёнком? Человек удалён не будет.", parent=dialog):
            return
        changed = self._apply_relationship_change(lambda: self.relationship_service.remove_child_link(record["family_id"], record["person_reference"]))
        if changed:
            self._reload_relationship_editor(person_reference, parent_listbox, partner_listbox, child_listbox)

    def backup_database(self):
        try:
            backup_path = filedialog.asksaveasfilename(
                title="Выберите файл резервной копии",
                defaultextension=".db",
                initialfile=Path(DB_NAME).stem + "-backup.db",
                filetypes=[("SQLite databases", "*.db *.sqlite *.sqlite3"), ("All files", "*.*")],
            )
            if not backup_path:
                return
            destination = backup_database(DB_NAME, backup_path)
            messagebox.showinfo("Резервная копия", f"База сохранена в:\n{destination}")
        except (FileNotFoundError, ValueError, OSError) as error:
            messagebox.showerror("Ошибка резервного копирования", str(error))

    def restore_database(self):
        try:
            restore_path = filedialog.askopenfilename(
                title="Выберите файл для восстановления",
                filetypes=[("SQLite databases", "*.db *.sqlite *.sqlite3"), ("All files", "*.*")],
            )
            if not restore_path:
                return
            if not messagebox.askyesno(
                "Подтверждение восстановления",
                "Восстановить базу из выбранного файла? Это заменит текущую базу и создаст резервную копию текущего состояния.",
            ):
                return
            restore_database(restore_path, DB_NAME)
            self.refresh_views()
            messagebox.showinfo("Восстановлено", "База данных восстановлена и список обновлён.")
        except (FileNotFoundError, ValueError, OSError) as error:
            messagebox.showerror("Ошибка восстановления", str(error))

    def refresh_views(self):
        self.search_people()
        self._refresh_family_tree()

    def _refresh_family_tree(self):
        if not hasattr(self, "family_tree_text"):
            return
        self.family_tree_text.config(state="normal")
        self.family_tree_text.delete("1.0", "end")
        if not self.current_person_gedcom_id:
            self.family_tree_text.insert("end", "Выберите человека, чтобы увидеть семейное дерево.\n")
        else:
            nodes = self.build_family_tree_nodes(self.current_person_gedcom_id)
            if not nodes:
                self.family_tree_text.insert("end", "Данные о семейном дереве отсутствуют.\n")
            else:
                for node in nodes:
                    self.family_tree_text.insert(
                        "end",
                        f"{node['role']}: {node['name']}\n",
                    )
        self.family_tree_text.config(state="disabled")

    @staticmethod
    def format_name(last_name, first_name):
        return f"{last_name or ''} {first_name or ''}".strip()

    @staticmethod
    def _normalize_name(value):
        value = unicodedata.normalize("NFKC", value or "").casefold().replace("ё", "е")
        return " ".join(re.findall(r"[a-zа-я0-9]+", value))

    def close(self):
        self.repository.close()


def main():
    """Launch the genealogy viewer application."""
    prepare_user_environment()
    configure_logging()
    get_logger("startup").info("Application startup version=%s", APP_VERSION)
    initialize_database()
    if "--smoke-test" in sys.argv:
        return
    root = tk.Tk()
    install_exception_logging(root)
    app = GenealogyViewer(root)

    def close_application():
        app.close()
        get_logger("startup").info("Application closed")
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close_application)
    root.mainloop()


if __name__ == "__main__":
    main()
