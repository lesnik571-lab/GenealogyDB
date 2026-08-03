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
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
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
from audit_service import AuditService
from batch_operations_service import (
    BatchOperation,
    BatchOperationsService,
    OPERATION_LABELS,
)
from data_quality_service import CATEGORY_DEFINITIONS, DataQualityService
from validation_center_service import ValidationCenterService, ValidationFixCommand
from database import backup_database, initialize_database, restore_database
from evidence_service import (
    CONFIDENCE_LEVELS,
    PROOF_STATUSES,
    EvidenceAppliedCommand,
    EvidenceOperation,
    EvidenceService,
)
from gedcom_repair_service import GedcomRepairCommand, GedcomRepairService
from geo_map_studio_service import GeoMapFilters, GeoMapStudioService, SCOPES as GEO_MAP_SCOPES
from family_tree_view_service import FamilyTreeModel, FamilyTreePerson, FamilyTreeViewService
from graph_editor_service import GraphEditorService, GraphModification
from integrity_service import IntegrityCheckService
from kinship_service import KinshipAnalysis, KinshipService
from logging_service import (
    configure_logging,
    diagnostics_snapshot,
    export_diagnostics,
    get_logger,
    install_exception_logging,
)
from merge_service import MergeService
from plugin_manager import PluginApp, PluginManager, ReadOnlyPluginData
from recovery_wizard_service import RecoveryRecord, RecoveryWizardService
from research_workspace_service import HYPOTHESIS_STATES, TASK_PRIORITIES, TASK_STATUSES, ResearchWorkspaceService
from relationship_path_service import RelationshipPath, RelationshipPathService
from source_service import CITATION_FIELDS, SOURCE_FIELDS, TARGET_TYPES, SourceService
from split_service import SPLIT_FIELDS, SplitService
from task_manager import TaskManager
from timeline_service import FamilyTimelineService, SUPPORTED_EVENT_TYPES, TimelineFilters
from timeline_studio_service import SCOPES, TimelineStudioFilters, TimelineStudioService
from workspace_integration_service import MODULES, WorkspaceContext, WorkspaceIntegrationService
from tree_canvas_service import (
    CARD_HEIGHT,
    CARD_WIDTH,
    MAX_ZOOM as TREE_CANVAS_MAX_ZOOM,
    MIN_ZOOM as TREE_CANVAS_MIN_ZOOM,
    TreeCanvasChange,
    TreeCanvasLayoutCommand,
    TreeCanvasNavigation,
    TreeCanvasPrintOptions,
    TreeCanvasSafetyError,
    TreeCanvasService,
    TreeLayoutOptions,
)
from repository import PersonRepository
from repository.person_attachment_service import PersonAttachmentService
from repository.person_event_service import PersonEventService
from life_map_service import PersonLifeMapService
from repository.person_timeline_service import PersonTimelineService
from repository.relationship_service import RelationshipService
from undo_manager import (
    AddPersonCommand,
    AppliedDeltaCommand,
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
        self.task_manager = TaskManager(root)
        self.relationship_service = RelationshipService(self.repository)
        self.family_tree_view_service = FamilyTreeViewService(self.relationship_service)
        self.relationship_path_service = RelationshipPathService(self.repository)
        self.kinship_service = KinshipService(self.repository)
        self.event_service = PersonEventService(self.repository)
        self.timeline_service = PersonTimelineService(self.repository)
        self.family_timeline_service = FamilyTimelineService(self.repository)
        self.timeline_studio_service = TimelineStudioService(self.repository)
        self.geo_map_studio_service = GeoMapStudioService(self.repository)
        self.research_workspace_service = ResearchWorkspaceService(self.repository)
        self.workspace_integration_service = WorkspaceIntegrationService()
        self._error_dialog_active = False
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
        self._life_map_tree_markers = {}
        self._life_map_detail_label = None
        self._life_map_window = None
        self._card_photo_image = None
        self.integrity_service = IntegrityCheckService(self.repository, data_dir=DATA_DIR)
        self.data_quality_service = DataQualityService(self.repository)
        self.validation_center_service = ValidationCenterService(self.repository)
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
        self._timeline_studio_window = None
        self._timeline_studio_model = None
        self._timeline_studio_events = ()
        self._timeline_studio_event_map = {}
        self._timeline_studio_lane_tree = None
        self._timeline_studio_event_tree = None
        self._timeline_studio_canvas = None
        self._timeline_studio_vars = {}
        self._timeline_studio_history = []
        self._timeline_studio_history_index = -1
        self._timeline_studio_status = None
        self._geo_map_window = None
        self._geo_map_model = None
        self._geo_map_markers = ()
        self._geo_map_marker_map = {}
        self._geo_map_tree = None
        self._geo_map_canvas = None
        self._geo_map_vars = {}
        self._geo_map_status = None
        self._geo_map_playing = False
        self._research_window = None
        self._research_workspace = None
        self._research_project_id = None
        self._research_project_tree = None
        self._research_hypothesis_tree = None
        self._research_task_tree = None
        self._research_details = None
        self._source_window = None
        self._source_tree = None
        self._source_citation_tree = None
        self._source_browser_tree = None
        self._source_usage_map = {}
        self._source_statistics_text = None
        self._evidence_window = None
        self._evidence_model = None
        self._evidence_source_tree = None
        self._evidence_citation_tree = None
        self._evidence_usage_tree = None
        self._evidence_details_text = None
        self._evidence_diagnostics_text = None
        self._evidence_read_only_var = None
        self._evidence_mutation_buttons = []
        self._gedcom_repair_window = None
        self._gedcom_repair_preview = None
        self._gedcom_repair_source_path = None
        self._gedcom_repair_issue_tree = None
        self._gedcom_repair_status = None
        self._gedcom_repair_diagnostics_var = None
        self._gedcom_repair_apply_button = None
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
        self._validation_center_window = None
        self._validation_report = None
        self._validation_category_tree = None
        self._validation_issue_tree = None
        self._validation_issue_map = {}
        self._validation_detail_text = None
        self._validation_filter_vars = {}
        self._batch_operations_window = None
        self._batch_operations_people_tree = None
        self._batch_operations_preview_tree = None
        self._batch_operations_preview = None
        self._batch_operations_execute_button = None
        self._batch_operations_vars = {}
        self._merge_window = None
        self._merge_plan = None
        self._merge_scalar_vars = {}
        self._merge_scalar_result_entries = {}
        self._split_window = None
        self._split_plan = None
        self._split_source_vars = {}
        self._split_new_vars = {}
        self._split_move_vars = {}
        self._split_collection_lists = {}
        self._split_relationship_tree = None
        self._split_execute_button = None
        self._graph_editor_window = None
        self._graph_editor_canvas = None
        self._graph_editor_model = None
        self._graph_editor_positions = {}
        self._graph_editor_zoom = 1.0
        self._graph_editor_selected_person_id = None
        self._graph_editor_selected_edge = None
        self._graph_editor_card_drag = None
        self._graph_editor_link_line = None
        self._graph_editor_mode_var = None
        self._graph_editor_role_var = None
        self._graph_editor_status = None
        self._graph_preview_window = None
        self._graph_preview = None
        self._tree_canvas_window = None
        self._tree_canvas = None
        self._tree_canvas_model = None
        self._tree_canvas_navigation = None
        self._tree_canvas_positions = {}
        self._tree_canvas_zoom = 1.0
        self._tree_canvas_drag = None
        self._tree_canvas_pan = None
        self._tree_canvas_collapsed_ids = set()
        self._tree_canvas_mode_var = None
        self._tree_canvas_ancestor_var = None
        self._tree_canvas_descendant_var = None
        self._tree_canvas_status = None
        self._tree_canvas_pinned_nodes = set()
        self._tree_canvas_selected_card_id = None
        self._tree_canvas_layout_name_var = None
        self._tree_canvas_layout_type_var = None
        self._tree_canvas_horizontal_spacing_var = None
        self._tree_canvas_vertical_spacing_var = None
        self._tree_canvas_card_width_var = None
        self._tree_canvas_card_height_var = None
        self._tree_canvas_compact_var = None
        self._tree_canvas_routing_var = None
        self._tree_canvas_print_window = None
        self._tree_canvas_print_preview = None
        self._tree_canvas_print_vars = {}
        self.audit_service = AuditService.for_database(self.repository.db_name)
        self._audit_window = None
        self._audit_records = []
        self._audit_record_map = {}
        self._audit_filter_vars = {}
        self._audit_tree = None
        self._audit_before_text = None
        self._audit_after_text = None
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
        self._register_workspace_modules()
        self._restore_workspace_ui_state()
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

    def _submit_repository_task(
        self,
        name,
        operation,
        on_success,
        *,
        on_error=None,
        cancellable=False,
    ):
        manager = getattr(self, "task_manager", None)
        if manager is None:
            try:
                return on_success(operation(self.repository, None))
            except Exception as error:
                if on_error:
                    return on_error(error)
                raise
        db_path = getattr(self.repository, "db_name", DB_NAME)

        def worker(context):
            worker_repository = PersonRepository(db_path)
            try:
                return operation(worker_repository, context)
            finally:
                worker_repository.close()

        return manager.submit(
            name,
            worker,
            on_success=on_success,
            on_error=on_error or (lambda error: self._show_unified_error(name, error)),
            cancellable=cancellable,
        )
        option_add("*Button.padY", UI_BUTTON_PAD_Y)

    def _show_unified_error(self, context, error):
        """Log detailed failures and keep the UI alive behind one concise dialog."""
        get_logger("viewer").exception("%s failed", context, exc_info=error)
        if self._error_dialog_active:
            return
        self._error_dialog_active = True
        try:
            messagebox.showerror("Ошибка", f"Не удалось выполнить действие: {context}.", parent=getattr(self, "root", None))
        finally:
            self._error_dialog_active = False

    def _register_workspace_modules(self):
        service = self.workspace_integration_service
        for module in MODULES:
            service.register_module(module, lambda context, origin, module=module: self._apply_workspace_context(module, context, origin))

    def _apply_workspace_context(self, module, context: WorkspaceContext, _origin):
        """Highlight matching records in open modules without opening any window."""
        person_id = context.selected_person_id
        if person_id is None:
            self._update_workspace_status()
            return
        if module == "tree" and getattr(self, "_tree_canvas_window", None) is not None:
            self.highlight_tree_canvas_person(person_id)
        elif module == "map" and getattr(self, "_geo_map_window", None) is not None:
            self.highlight_geo_map_person(person_id)
        elif module == "timeline" and getattr(self, "_timeline_studio_window", None) is not None:
            variables = getattr(self, "_timeline_studio_vars", {})
            if "people" in variables:
                variables["people"].set(str(person_id))
        elif module == "main" and getattr(self, "_person_dialog", None) is not None and person_id != self.current_person_id:
            self.show_person(person_id, add_to_history=False)
        self._update_workspace_status()

    def _set_workspace_person(self, person_id, origin="main"):
        if self.workspace_integration_service.select_person(person_id, origin):
            self._update_workspace_status()

    def _workspace_back(self, _event=None):
        context = self.workspace_integration_service.navigate_back()
        if context is not None:
            self._apply_workspace_context("main", context, "navigation")
        return "break"

    def _workspace_forward(self, _event=None):
        context = self.workspace_integration_service.navigate_forward()
        if context is not None:
            self._apply_workspace_context("main", context, "navigation")
        return "break"

    def _workspace_open_main(self, _event=None):
        if self.current_person_id is not None:
            self.show_person(self.current_person_id)
        return "break"

    def _workspace_open(self, opener, _event=None):
        opener()
        return "break"

    def _workspace_open_modules(self):
        windows = {
            "main": getattr(self, "_person_dialog", None), "tree": getattr(self, "_tree_canvas_window", None),
            "timeline": getattr(self, "_timeline_studio_window", None), "map": getattr(self, "_geo_map_window", None),
            "evidence": getattr(self, "_evidence_window", None), "validation": getattr(self, "_validation_center_window", None),
            "research": getattr(self, "_research_window", None), "audit": getattr(self, "_audit_window", None),
        }
        return tuple(name for name, window in windows.items() if window is not None)

    def _workspace_ui_state(self):
        geometry = ""
        try:
            geometry = self.root.geometry()
        except Exception:
            pass
        return {"geometry": geometry, "filters": {key: value.get() for key, value in self._advanced_search_vars.items()}}

    def _save_workspace_ui_state(self):
        self.workspace_integration_service.save_ui_state(self._workspace_ui_state())

    def _restore_workspace_ui_state(self):
        state = self.workspace_integration_service.load_ui_state()
        if state.get("geometry"):
            try:
                self.root.geometry(state["geometry"])
            except Exception:
                pass
        for key, value in state.get("filters", {}).items():
            if key in self._advanced_search_vars:
                self._advanced_search_vars[key].set(value)
        bind = getattr(self.root, "bind", None)
        if callable(bind):
            bind("<Configure>", lambda _event: self._save_workspace_ui_state(), add="+")

    def _update_workspace_status(self):
        label = getattr(self, "workspace_status_label", None)
        if label is None:
            return
        context = self.workspace_integration_service.context
        running = len(getattr(getattr(self, "task_manager", None), "_tasks", {}))
        unresolved = len([issue for issue in getattr(getattr(self, "_validation_report", None), "issues", ()) if not issue.resolved])
        label.config(text=f"Человек: {context.selected_person_id or '-'} | Семья: {context.selected_family_id or '-'} | Модуль: {context.active_module} | Задач: {running} | Проверка: {unresolved} | БД: {Path(self.repository.db_name).name} | {APP_VERSION}")

    def open_integration_diagnostics(self):
        running = len(getattr(getattr(self, "task_manager", None), "_tasks", {}))
        availability = {name: hasattr(self, f"open_{name}_studio") or name in {"main", "tree", "evidence", "validation", "research", "audit"} for name in MODULES}
        data = self.workspace_integration_service.diagnostics(running_tasks=running, service_availability=availability, open_modules=self._workspace_open_modules())
        dialog = self._create_dialog(); dialog.title("Диагностика интеграции"); dialog.geometry("760x520")
        body = tk.Text(dialog, wrap="word"); body.pack(fill="both", expand=True, padx=12, pady=12); body.insert("1.0", json.dumps(data, ensure_ascii=False, indent=2)); body.config(state="disabled")
        tk.Button(dialog, text="Закрыть", command=dialog.destroy).pack(anchor="e", padx=12, pady=(0, 12))

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
            command = RelationshipEditCommand(self.repository, callback)
            self._get_undo_manager().execute(command)
            person = self.repository.get_person_record(self.current_person_id) if self.current_person_id else None
            self._record_audit_command(
                "relationship_change",
                command,
                database_id=self.current_person_id or "",
                gedcom_id=person["gedcom_id"] if person else "",
                description="Изменены родственные связи.",
                service="relationship_service",
            )
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

    def open_split_wizard(self):
        source_id = self.current_person_id
        if source_id is None:
            messagebox.showwarning("Разделить человека", "Сначала выберите человека из списка.")
            return
        source = self.repository.get_person_record(source_id)
        if source is None:
            messagebox.showerror("Разделить человека", "Человек не найден.")
            return
        new_values = {
            "first_name": source.get("first_name") or "",
            "last_name": source.get("last_name") or "",
        }
        return self._submit_repository_task(
            "Подготовка разделения человека",
            lambda repository, _context: SplitService(repository).plan_split(
                source_id, {}, new_values=new_values
            ),
            self._show_split_wizard,
            on_error=lambda error: messagebox.showerror(
                "Разделить человека", str(error), parent=self.root
            ),
        )

    def _show_split_wizard(self, plan):
        if self._split_window is not None:
            try:
                self._split_window.destroy()
            except Exception:
                pass
        self._split_plan = plan
        self._split_source_vars = {}
        self._split_new_vars = {}
        self._split_move_vars = {}
        self._split_collection_lists = {}
        window = self._create_dialog()
        self._split_window = window
        window.title("Разделить человека")
        window.geometry("1280x840")
        window.minsize(980, 660)
        window.protocol("WM_DELETE_WINDOW", self._close_split_wizard)

        source_name = " ".join(
            value for value in (plan.source.get("first_name", ""), plan.source.get("last_name", "")) if value
        )
        tk.Label(
            window,
            text=f"Исходная карточка: {source_name or 'Без имени'} | ID {plan.source['id']} | GEDCOM {plan.source.get('gedcom_id') or '-'}",
            anchor="w",
        ).pack(fill="x", padx=12, pady=(12, 8))

        notebook = ttk.Notebook(window)
        notebook.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        fields_tab = tk.Frame(notebook)
        collections_tab = tk.Frame(notebook)
        relationships_tab = tk.Frame(notebook)
        notebook.add(fields_tab, text="Поля")
        notebook.add(collections_tab, text="События и материалы")
        notebook.add(relationships_tab, text="Родственные связи")
        self._build_split_fields_tab(fields_tab, plan)
        self._build_split_collections_tab(collections_tab, plan)
        self._build_split_relationships_tab(relationships_tab, plan)

        messages = [*(f"Предупреждение: {item}" for item in plan.warnings)]
        messages.extend(f"Блокировка: {item}" for item in plan.blockers)
        tk.Label(
            window,
            text="\n".join(messages) or "Dry-run готов: база данных не изменена.",
            justify="left",
            anchor="w",
            foreground="#9b1c1c" if plan.blockers else "#245c36",
        ).pack(fill="x", padx=12, pady=(0, 8))

        controls = tk.Frame(window)
        controls.pack(fill="x", padx=12, pady=(0, 12))
        tk.Button(controls, text="Обновить dry-run", command=self._preview_split).pack(side="left")
        tk.Button(controls, text="Экспорт CSV", command=self._export_split_csv).pack(side="left", padx=(8, 0))
        tk.Button(controls, text="Экспорт JSON", command=self._export_split_json).pack(side="left", padx=(8, 0))
        self._split_execute_button = tk.Button(
            controls,
            text="Выполнить разделение",
            command=self._execute_split_plan,
            state="normal" if plan.can_execute else "disabled",
        )
        self._split_execute_button.pack(side="left", padx=(16, 0))
        tk.Button(controls, text="Закрыть", command=self._close_split_wizard).pack(side="right")

    def _build_split_fields_tab(self, parent, plan):
        labels = {
            "first_name": "Имя", "last_name": "Фамилия", "sex": "Пол",
            "birth_date": "Дата рождения", "birth_place": "Место рождения",
            "death_date": "Дата смерти", "death_place": "Место смерти",
            "occupation": "Занятие", "note": "Заметки",
        }
        for column, heading in enumerate(("Перенести", "Поле", "Останется", "Новая карточка")):
            tk.Label(parent, text=heading).grid(row=0, column=column, sticky="w", padx=6, pady=6)
        selected_fields = set(plan.selection["fields"])
        for row, field in enumerate(SPLIT_FIELDS, start=1):
            move_var = tk.BooleanVar(value=field in selected_fields)
            source_var = tk.StringVar(value=plan.source_preview[field])
            new_var = tk.StringVar(value=plan.new_person_preview[field])
            tk.Checkbutton(
                parent, variable=move_var, command=self._invalidate_split_preview
            ).grid(row=row, column=0, padx=6, pady=4)
            tk.Label(parent, text=labels.get(field, field)).grid(row=row, column=1, sticky="w", padx=6, pady=4)
            source_entry = tk.Entry(parent, textvariable=source_var)
            new_entry = tk.Entry(parent, textvariable=new_var)
            source_entry.grid(row=row, column=2, sticky="ew", padx=6, pady=4)
            new_entry.grid(row=row, column=3, sticky="ew", padx=6, pady=4)
            source_entry.bind("<KeyRelease>", self._invalidate_split_preview)
            new_entry.bind("<KeyRelease>", self._invalidate_split_preview)
            self._split_move_vars[field] = move_var
            self._split_source_vars[field] = source_var
            self._split_new_vars[field] = new_var
        parent.grid_columnconfigure(2, weight=1)
        parent.grid_columnconfigure(3, weight=1)

    def _build_split_collections_tab(self, parent, plan):
        labels = {
            "events": "События", "sources": "Источники",
            "citations": "Цитаты", "attachments": "Вложения",
        }
        for column, (key, records) in enumerate(plan.collections.items()):
            frame = tk.LabelFrame(parent, text=labels.get(key, key))
            frame.grid(row=0, column=column, sticky="nsew", padx=6, pady=6)
            listbox = tk.Listbox(frame, selectmode="extended", exportselection=False)
            listbox.pack(fill="both", expand=True)
            listbox._records = list(records)
            for index, record in enumerate(records):
                summary = json.dumps(
                    {field: value for field, value in record.items() if field != "selected"},
                    ensure_ascii=False, default=str,
                )
                listbox.insert("end", summary)
                if record["selected"]:
                    listbox.selection_set(index)
            listbox.bind("<<ListboxSelect>>", self._invalidate_split_preview)
            self._split_collection_lists[key] = listbox
            parent.grid_columnconfigure(column, weight=1)
        parent.grid_rowconfigure(0, weight=1)

    def _build_split_relationships_tab(self, parent, plan):
        columns = ("category", "family", "type", "before", "after", "effects")
        tree = ttk.Treeview(parent, columns=columns, show="headings", selectmode="extended")
        self._split_relationship_tree = tree
        for column, heading, width in (
            ("category", "Категория", 100), ("family", "Семья", 70),
            ("type", "Тип", 110), ("before", "До", 260),
            ("after", "После", 260), ("effects", "Связанные изменения", 360),
        ):
            tree.heading(column, text=heading)
            tree.column(column, width=width, anchor="w")
        for relationship in plan.relationships:
            tree.insert("", "end", iid=relationship.key, values=(
                relationship.category, relationship.family_id,
                relationship.relationship_type, relationship.before,
                relationship.after, " ".join(relationship.implicit_effects),
            ))
            if relationship.selected:
                tree.selection_add(relationship.key)
        if not plan.relationships:
            tree.insert("", "end", iid="none", values=("", "", "", "Связей нет", "", ""))
        tree.bind("<<TreeviewSelect>>", self._invalidate_split_preview)
        tree.pack(fill="both", expand=True)

    def _split_selection(self):
        selection = {
            "fields": tuple(field for field, variable in self._split_move_vars.items() if variable.get()),
            "relationships": tuple(
                item for item in self._split_relationship_tree.selection() if item != "none"
            ),
        }
        for key, listbox in self._split_collection_lists.items():
            selection[key] = tuple(
                int(listbox._records[index]["id"]) for index in listbox.curselection()
            )
        return selection

    def _preview_split(self):
        plan = self._split_plan
        if plan is None:
            return
        selection = self._split_selection()
        source_values = {field: variable.get() for field, variable in self._split_source_vars.items()}
        new_values = {field: variable.get() for field, variable in self._split_new_vars.items()}
        return self._submit_repository_task(
            "Dry-run разделения человека",
            lambda repository, _context: SplitService(repository).plan_split(
                plan.source["id"], selection,
                source_values=source_values, new_values=new_values,
            ),
            self._show_split_wizard,
            on_error=lambda error: messagebox.showerror(
                "Разделить человека", str(error), parent=self._split_window
            ),
        )

    def _invalidate_split_preview(self, _event=None):
        if self._split_execute_button is not None:
            self._split_execute_button.config(state="disabled")

    def _execute_split_plan(self):
        plan = self._split_plan
        if plan is None or not plan.can_execute:
            return
        if not messagebox.askyesno(
            "Подтверждение разделения",
            "Будет создана вторая карточка и перенесены выбранные данные. Продолжить?",
            parent=self._split_window,
        ):
            return

        def execute(repository, context):
            return SplitService(repository).execute(
                plan,
                progress_callback=lambda stage, completed, total: context.report(
                    stage, completed, total
                ),
            )

        return self._submit_repository_task(
            "Разделение человека",
            execute,
            self._complete_split,
            on_error=lambda error: messagebox.showerror(
                "Разделить человека", str(error), parent=self._split_window
            ),
        )

    def _complete_split(self, result):
        self._get_undo_manager().record_applied(
            AppliedDeltaCommand("Разделение человека", self.repository, result.delta, result)
        )
        self._close_split_wizard()
        self.refresh_views()
        self.show_person(result.new_person_id)
        messagebox.showinfo(
            "Разделить человека",
            f"Создана карточка ID {result.new_person_id}.\nРезервная копия: {result.backup_path}",
            parent=self.root,
        )

    def _export_split_csv(self):
        self._export_split_preview("csv")

    def _export_split_json(self):
        self._export_split_preview("json")

    def _export_split_preview(self, extension):
        plan = self._split_plan
        if plan is None:
            return
        destination = filedialog.asksaveasfilename(
            parent=self._split_window,
            title=f"Экспорт dry-run в {extension.upper()}",
            defaultextension=f".{extension}",
            filetypes=[(extension.upper(), f"*.{extension}")],
        )
        if not destination:
            return
        service = SplitService(self.repository)
        if extension == "csv":
            service.export_csv(plan, destination)
        else:
            service.export_json(plan, destination)

    def _close_split_wizard(self):
        if self._split_window is not None:
            try:
                self._split_window.destroy()
            except Exception:
                pass
        self._split_window = None
        self._split_plan = None
        self._split_collection_lists = {}
        self._split_relationship_tree = None
        self._split_execute_button = None

    def open_merge_wizard(self):
        primary_reference = self._choose_person("Выберите основного человека")
        if not primary_reference:
            return
        duplicate_reference = self._choose_person(
            "Выберите дублирующую карточку",
            exclude_reference=primary_reference,
        )
        if not duplicate_reference:
            return
        primary_id = self.repository.resolve_person_reference(primary_reference)
        duplicate_id = self.repository.resolve_person_reference(duplicate_reference)
        if primary_id is None or duplicate_id is None:
            messagebox.showerror("Объединить людей", "Человек не найден.", parent=self.root)
            return
        return self._submit_repository_task(
            "Подготовка объединения людей",
            lambda repository, _context: MergeService(repository).plan_merge(
                primary_id, duplicate_id
            ),
            self._show_merge_wizard,
            on_error=lambda error: messagebox.showerror(
                "Объединить людей", str(error), parent=self.root
            ),
        )

    def _show_merge_wizard(self, plan):
        if self._merge_window is not None:
            try:
                self._merge_window.destroy()
            except Exception:
                pass
        self._merge_plan = plan
        self._merge_scalar_vars = {}
        self._merge_scalar_result_entries = {}
        window = self._create_dialog()
        self._merge_window = window
        window.title("Объединить людей")
        window.geometry("1280x820")
        window.minsize(980, 640)
        window.protocol("WM_DELETE_WINDOW", self._close_merge_wizard)

        header = tk.LabelFrame(window, text="Карточки")
        header.pack(fill="x", padx=12, pady=(12, 8))
        primary_name = " ".join(
            value for value in (plan.primary.get("first_name", ""), plan.primary.get("last_name", ""))
            if value
        ) or "Без имени"
        duplicate_name = " ".join(
            value for value in (plan.duplicate.get("first_name", ""), plan.duplicate.get("last_name", ""))
            if value
        ) or "Без имени"
        tk.Label(
            header,
            text=(
                f"Основной: {primary_name} | ID {plan.primary['id']} | GEDCOM {plan.primary.get('gedcom_id') or '-'}\n"
                f"Поглощаемый: {duplicate_name} | ID {plan.duplicate['id']} | GEDCOM {plan.duplicate.get('gedcom_id') or '-'}"
            ),
            justify="left",
            anchor="w",
        ).pack(fill="x", padx=8, pady=8)

        notebook = ttk.Notebook(window)
        notebook.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        scalar_tab = tk.Frame(notebook)
        collections_tab = tk.Frame(notebook)
        relationships_tab = tk.Frame(notebook)
        notebook.add(scalar_tab, text="Поля")
        notebook.add(collections_tab, text="События и материалы")
        notebook.add(relationships_tab, text="Связи и безопасность")
        self._build_merge_scalar_tab(scalar_tab, plan)
        self._build_merge_collections_tab(collections_tab, plan)
        self._build_merge_relationships_tab(relationships_tab, plan)

        status_lines = [*(f"Предупреждение: {item}" for item in plan.warnings)]
        status_lines.extend(f"Блокировка: {item}" for item in plan.blockers)
        status = tk.Label(
            window,
            text="\n".join(status_lines) or "Dry-run готов: запись в базу не выполнялась.",
            justify="left",
            anchor="w",
            foreground="#9b1c1c" if plan.blockers else "#245c36",
        )
        status.pack(fill="x", padx=12, pady=(0, 8))

        controls = tk.Frame(window)
        controls.pack(fill="x", padx=12, pady=(0, 12))
        tk.Button(controls, text="Обновить dry-run", command=self._refresh_merge_plan).pack(side="left")
        tk.Button(controls, text="Экспорт CSV", command=self._export_merge_csv).pack(side="left", padx=(8, 0))
        tk.Button(controls, text="Экспорт JSON", command=self._export_merge_json).pack(side="left", padx=(8, 0))
        tk.Button(
            controls,
            text="Выполнить объединение",
            command=self._execute_merge_plan,
            state="normal" if plan.can_execute else "disabled",
        ).pack(side="left", padx=(16, 0))
        tk.Button(controls, text="Закрыть", command=self._close_merge_wizard).pack(side="right")

    def _build_merge_scalar_tab(self, parent, plan):
        labels = {
            "first_name": "Имя", "last_name": "Фамилия", "sex": "Пол",
            "birth_date": "Дата рождения", "birth_place": "Место рождения",
            "death_date": "Дата смерти", "death_place": "Место смерти",
            "occupation": "Занятие", "note": "Заметки",
        }
        headings = ("Поле", "Основной", "Дубликат", "Выбор", "Результат / вручную")
        for column, heading in enumerate(headings):
            tk.Label(parent, text=heading).grid(row=0, column=column, sticky="w", padx=6, pady=6)
        choice_labels = {
            "primary": "Оставить основной",
            "duplicate": "Использовать дубликат",
            "manual": "Ввести вручную",
        }
        for row, item in enumerate(plan.scalar_resolutions, start=1):
            tk.Label(parent, text=labels.get(item.field, item.field)).grid(row=row, column=0, sticky="nw", padx=6, pady=4)
            tk.Label(parent, text=item.primary_value, wraplength=220, justify="left").grid(row=row, column=1, sticky="nw", padx=6, pady=4)
            tk.Label(parent, text=item.duplicate_value, wraplength=220, justify="left").grid(row=row, column=2, sticky="nw", padx=6, pady=4)
            choice_var = tk.StringVar(value=choice_labels[item.choice])
            choice = ttk.Combobox(
                parent,
                textvariable=choice_var,
                values=tuple(choice_labels.values()),
                state="readonly",
                width=22,
            )
            choice.grid(row=row, column=3, sticky="ew", padx=6, pady=4)
            result_var = tk.StringVar(value=item.result_value)
            entry = tk.Entry(parent, textvariable=result_var)
            entry.grid(row=row, column=4, sticky="ew", padx=6, pady=4)
            self._merge_scalar_vars[item.field] = (choice_var, result_var)
            self._merge_scalar_result_entries[item.field] = entry
        parent.grid_columnconfigure(1, weight=1)
        parent.grid_columnconfigure(2, weight=1)
        parent.grid_columnconfigure(4, weight=1)

    def _build_merge_collections_tab(self, parent, plan):
        columns = ("side", "collection", "record")
        tree = ttk.Treeview(parent, columns=columns, show="headings")
        for column, heading, width in (
            ("side", "Карточка", 100),
            ("collection", "Раздел", 130),
            ("record", "Полная запись", 850),
        ):
            tree.heading(column, text=heading)
            tree.column(column, width=width, anchor="w")
        for side, collections in (
            ("Основной", plan.primary_collections),
            ("Дубликат", plan.duplicate_collections),
        ):
            for collection, records in collections.items():
                if not records:
                    tree.insert("", "end", values=(side, collection, "Нет данных"))
                    continue
                for record in records:
                    tree.insert(
                        "",
                        "end",
                        values=(side, collection, json.dumps(record, ensure_ascii=False, default=str)),
                    )
        tree.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

    def _build_merge_relationships_tab(self, parent, plan):
        columns = ("family", "type", "before", "after", "action")
        tree = ttk.Treeview(parent, columns=columns, show="headings")
        for column, heading, width in (
            ("family", "Семья", 70), ("type", "Тип", 130),
            ("before", "До", 330), ("after", "После", 330),
            ("action", "Действие", 110),
        ):
            tree.heading(column, text=heading)
            tree.column(column, width=width, anchor="w")
        for change in plan.relationship_changes:
            tree.insert("", "end", values=(
                change.family_id, change.relationship_type, change.before,
                change.after, change.action,
            ))
        if not plan.relationship_changes:
            tree.insert("", "end", values=("", "", "Изменений связей нет", "", ""))
        tree.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(parent, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")

    def _merge_resolutions(self):
        choices = {
            "Оставить основной": "primary",
            "Использовать дубликат": "duplicate",
            "Ввести вручную": "manual",
        }
        return {
            field: (choices[choice_var.get()], result_var.get())
            for field, (choice_var, result_var) in self._merge_scalar_vars.items()
        }

    def _refresh_merge_plan(self):
        plan = self._merge_plan
        if plan is None:
            return
        resolutions = self._merge_resolutions()
        return self._submit_repository_task(
            "Dry-run объединения людей",
            lambda repository, _context: MergeService(repository).plan_merge(
                plan.primary["id"], plan.duplicate["id"], resolutions
            ),
            self._show_merge_wizard,
            on_error=lambda error: messagebox.showerror(
                "Объединить людей", str(error), parent=self._merge_window
            ),
        )

    def _execute_merge_plan(self):
        plan = self._merge_plan
        if plan is None or not plan.can_execute:
            return
        if not messagebox.askyesno(
            "Подтверждение объединения",
            "Дублирующая карточка будет поглощена. Продолжить?",
            parent=self._merge_window,
        ):
            return

        def execute(repository, context):
            service = MergeService(repository)
            return service.execute(
                plan,
                progress_callback=lambda stage, completed, total: context.report(
                    stage, completed, total
                ),
            )

        return self._submit_repository_task(
            "Объединение людей",
            execute,
            self._complete_merge,
            on_error=lambda error: messagebox.showerror(
                "Объединить людей", str(error), parent=self._merge_window
            ),
        )

    def _complete_merge(self, result):
        self._get_undo_manager().record_applied(
            AppliedDeltaCommand("Объединение людей", self.repository, result.delta, result)
        )
        primary_id = result.primary_id
        self._close_merge_wizard()
        self.refresh_views()
        self.show_person(primary_id)
        messagebox.showinfo(
            "Объединить людей",
            f"Карточка ID {result.absorbed_id} объединена с ID {primary_id}.\nРезервная копия: {result.backup_path}",
            parent=self.root,
        )

    def _export_merge_csv(self):
        self._export_merge_preview("csv")

    def _export_merge_json(self):
        self._export_merge_preview("json")

    def _export_merge_preview(self, extension):
        plan = self._merge_plan
        if plan is None:
            return
        destination = filedialog.asksaveasfilename(
            parent=self._merge_window,
            title=f"Экспорт dry-run в {extension.upper()}",
            defaultextension=f".{extension}",
            filetypes=[(extension.upper(), f"*.{extension}")],
        )
        if not destination:
            return
        service = MergeService(self.repository)
        saved = service.export_csv(plan, destination) if extension == "csv" else service.export_json(plan, destination)
        messagebox.showinfo("Объединить людей", f"Предварительный просмотр сохранён: {saved}", parent=self._merge_window)

    def _close_merge_wizard(self):
        if self._merge_window is not None:
            try:
                self._merge_window.destroy()
            except Exception:
                pass
        self._merge_window = None
        self._merge_plan = None
        self._merge_scalar_vars = {}
        self._merge_scalar_result_entries = {}

    def open_batch_operations(self):
        if self._batch_operations_window is not None:
            try:
                self._batch_operations_window.lift()
                self._batch_operations_window.focus_force()
                return
            except Exception:
                self._batch_operations_window = None

        window = self._create_dialog()
        self._batch_operations_window = window
        window.title("Пакетные операции")
        window.geometry("1180x760")
        window.minsize(900, 600)
        window.protocol("WM_DELETE_WINDOW", self._close_batch_operations)

        body = tk.PanedWindow(window, orient="horizontal", sashrelief="raised")
        body.pack(fill="both", expand=True, padx=12, pady=12)
        people_frame = tk.LabelFrame(body, text="Выбранные люди")
        body.add(people_frame, minsize=300)
        people_tree = ttk.Treeview(
            people_frame,
            columns=("id", "name", "birth"),
            show="headings",
            selectmode="extended",
        )
        for column, label, width in (
            ("id", "ID", 60),
            ("name", "Человек", 200),
            ("birth", "Рождение", 100),
        ):
            people_tree.heading(column, text=label)
            people_tree.column(column, width=width, anchor="w")
        for person in self.repository.list_people_full():
            person_id = int(person["id"])
            name = " ".join(
                value for value in (person.get("first_name", ""), person.get("last_name", ""))
                if value
            ) or "Без имени"
            people_tree.insert(
                "",
                "end",
                iid=f"batch-person-{person_id}",
                values=(person_id, name, person.get("birth_date", "")),
            )
        people_tree.pack(side="left", fill="both", expand=True)
        people_scroll = ttk.Scrollbar(people_frame, orient="vertical", command=people_tree.yview)
        people_tree.configure(yscrollcommand=people_scroll.set)
        people_scroll.pack(side="right", fill="y")
        people_tree.bind("<<TreeviewSelect>>", self._invalidate_batch_preview)
        self._batch_operations_people_tree = people_tree

        right = tk.Frame(body)
        body.add(right, minsize=580)
        settings = tk.LabelFrame(right, text="Операция")
        settings.pack(fill="x", pady=(0, 8))
        self._batch_operations_vars = {
            "operation": tk.StringVar(value=next(iter(OPERATION_LABELS.values()))),
            "value": tk.StringVar(value=""),
            "replacement": tk.StringVar(value=""),
            "event_type": tk.StringVar(value="custom"),
            "event_date": tk.StringVar(value=""),
            "event_place": tk.StringVar(value=""),
            "event_notes": tk.StringVar(value=""),
        }
        specs = (
            ("operation", "Действие", tuple(OPERATION_LABELS.values())),
            ("value", "Значение / найти", None),
            ("replacement", "Заменить на", None),
            ("event_type", "Тип события", SUPPORTED_EVENT_TYPES),
            ("event_date", "Дата события", None),
            ("event_place", "Место события", None),
            ("event_notes", "Заметки события", None),
        )
        for row, (key, label, values) in enumerate(specs):
            tk.Label(settings, text=f"{label}:").grid(row=row, column=0, sticky="w", padx=8, pady=3)
            if values is None:
                control = tk.Entry(settings, textvariable=self._batch_operations_vars[key])
                control.bind("<KeyRelease>", self._invalidate_batch_preview)
            else:
                control = ttk.Combobox(
                    settings,
                    textvariable=self._batch_operations_vars[key],
                    values=values,
                    state="readonly",
                )
                control.bind("<<ComboboxSelected>>", self._invalidate_batch_preview)
            control.grid(row=row, column=1, sticky="ew", padx=8, pady=3)
        settings.grid_columnconfigure(1, weight=1)

        preview_frame = tk.LabelFrame(right, text="Предварительный просмотр")
        preview_frame.pack(fill="both", expand=True)
        columns = ("person", "record", "field", "before", "after")
        preview_tree = ttk.Treeview(preview_frame, columns=columns, show="headings")
        for column, label, width in (
            ("person", "Человек", 150),
            ("record", "Запись", 80),
            ("field", "Поле", 100),
            ("before", "До", 190),
            ("after", "После", 190),
        ):
            preview_tree.heading(column, text=label)
            preview_tree.column(column, width=width, anchor="w")
        preview_tree.pack(side="left", fill="both", expand=True)
        preview_scroll = ttk.Scrollbar(preview_frame, orient="vertical", command=preview_tree.yview)
        preview_tree.configure(yscrollcommand=preview_scroll.set)
        preview_scroll.pack(side="right", fill="y")
        self._batch_operations_preview_tree = preview_tree

        controls = tk.Frame(window)
        controls.pack(fill="x", padx=12, pady=(0, 12))
        tk.Button(controls, text="Предварительный просмотр", command=self._preview_batch_operations).pack(side="left")
        execute_button = tk.Button(
            controls,
            text="Выполнить",
            command=self._execute_batch_operations,
            state="disabled",
        )
        execute_button.pack(side="left", padx=(8, 0))
        tk.Button(controls, text="Закрыть", command=self._close_batch_operations).pack(side="right")
        self._batch_operations_execute_button = execute_button

        selected_person_id = self._selected_person_id()
        if selected_person_id is not None:
            item_id = f"batch-person-{selected_person_id}"
            try:
                people_tree.selection_set(item_id)
                people_tree.see(item_id)
            except Exception:
                pass

    def _selected_batch_person_ids(self):
        tree = self._batch_operations_people_tree
        if tree is None:
            return ()
        person_ids = []
        for item_id in tree.selection():
            values = tree.item(item_id).get("values", ())
            if values:
                person_ids.append(int(values[0]))
        return tuple(person_ids)

    def _collect_batch_operation(self):
        labels_to_kinds = {label: kind for kind, label in OPERATION_LABELS.items()}
        values = self._batch_operations_vars
        kind = labels_to_kinds.get(values["operation"].get())
        if not kind:
            raise ValueError("Выберите пакетную операцию")
        operation = BatchOperation(
            kind=kind,
            value=values["value"].get(),
            replacement=values["replacement"].get(),
            event_type=values["event_type"].get(),
            event_date=values["event_date"].get(),
            event_place=values["event_place"].get(),
            event_notes=values["event_notes"].get(),
        )
        if kind == "replace_text" and not operation.value:
            raise ValueError("Укажите текст для замены")
        return operation

    def _invalidate_batch_preview(self, _event=None):
        self._batch_operations_preview = None
        if self._batch_operations_execute_button is not None:
            self._batch_operations_execute_button.config(state="disabled")

    def _preview_batch_operations(self):
        try:
            person_ids = self._selected_batch_person_ids()
            operation = self._collect_batch_operation()
        except ValueError as error:
            messagebox.showerror("Пакетные операции", str(error), parent=self._batch_operations_window)
            return
        return self._submit_repository_task(
            "Предварительный просмотр пакетной операции",
            lambda repository, _context: BatchOperationsService(repository).preview(
                person_ids, operation
            ),
            self._show_batch_preview,
            on_error=lambda error: messagebox.showerror(
                "Пакетные операции", str(error), parent=self._batch_operations_window
            ),
        )

    def _show_batch_preview(self, preview):
        self._batch_operations_preview = preview
        tree = self._batch_operations_preview_tree
        if tree is not None:
            for item_id in tree.get_children():
                tree.delete(item_id)
            for change in preview.changes:
                record = change.record_type
                if change.record_id is not None:
                    record = f"{record} {change.record_id}"
                tree.insert(
                    "",
                    "end",
                    values=(
                        change.person_name,
                        record,
                        change.field,
                        change.before,
                        change.after,
                    ),
                )
        if self._batch_operations_execute_button is not None:
            self._batch_operations_execute_button.config(
                state="normal" if preview.changes else "disabled"
            )

    def _execute_batch_operations(self):
        preview = self._batch_operations_preview
        if preview is None or not preview.changes:
            messagebox.showinfo(
                "Пакетные операции",
                "Сначала выполните предварительный просмотр.",
                parent=self._batch_operations_window,
            )
            return

        def execute(repository, context):
            return BatchOperationsService(repository).execute(
                preview,
                progress_callback=lambda stage, completed, total: context.report(
                    stage, completed, total
                ),
            )

        return self._submit_repository_task(
            "Пакетные операции",
            execute,
            self._complete_batch_operations,
            on_error=lambda error: messagebox.showerror(
                "Пакетные операции", str(error), parent=self._batch_operations_window
            ),
        )

    def _complete_batch_operations(self, result):
        self._get_undo_manager().record_applied(
            AppliedDeltaCommand("Пакетные операции", self.repository, result.delta, result)
        )
        self._invalidate_batch_preview()
        self.refresh_views()
        messagebox.showinfo(
            "Пакетные операции",
            f"Изменено записей: {result.changed_records}; полей: {result.changed_fields}.",
            parent=self._batch_operations_window,
        )

    def _close_batch_operations(self):
        if self._batch_operations_window is not None:
            try:
                self._batch_operations_window.destroy()
            except Exception:
                pass
        self._batch_operations_window = None
        self._batch_operations_people_tree = None
        self._batch_operations_preview_tree = None
        self._batch_operations_preview = None
        self._batch_operations_execute_button = None
        self._batch_operations_vars = {}

    def open_audit_history(self):
        if self._audit_window is not None:
            try:
                self._audit_window.lift()
                self._audit_window.focus_force()
                return
            except Exception:
                self._audit_window = None
        window = self._create_dialog()
        self._audit_window = window
        window.title("История изменений")
        window.geometry("1280x820")
        window.minsize(960, 620)
        window.protocol("WM_DELETE_WINDOW", self._close_audit_history)

        filters = tk.LabelFrame(window, text="Фильтры")
        filters.pack(fill="x", padx=12, pady=(12, 8))
        options = self._get_audit_service().filter_options()
        definitions = (
            ("person", "Человек (ID/GEDCOM)", ()),
            ("operation", "Операция", ("", *options["operations"])),
            ("date_from", "Дата от", ()),
            ("date_to", "Дата до", ()),
            ("service", "Сервис", ("", *options["services"])),
            ("batch_id", "Batch ID", ("", *options["batch_ids"])),
        )
        self._audit_filter_vars = {}
        for column, (key, label, values) in enumerate(definitions):
            cell = tk.Frame(filters)
            cell.grid(row=0, column=column, sticky="ew", padx=6, pady=6)
            tk.Label(cell, text=label).pack(anchor="w")
            variable = tk.StringVar(value="")
            self._audit_filter_vars[key] = variable
            if values:
                control = ttk.Combobox(cell, textvariable=variable, values=values, state="readonly")
            else:
                control = tk.Entry(cell, textvariable=variable)
            control.pack(fill="x")
            filters.grid_columnconfigure(column, weight=1)
        actions = tk.Frame(filters)
        actions.grid(row=1, column=0, columnspan=6, sticky="ew", padx=6, pady=(0, 6))
        tk.Button(actions, text="Применить", command=self._load_audit_history).pack(side="left")
        tk.Button(actions, text="Сбросить", command=self._reset_audit_filters).pack(side="left", padx=(8, 0))
        tk.Button(actions, text="Экспорт CSV", command=self._export_audit_csv).pack(side="left", padx=(16, 0))
        tk.Button(actions, text="Экспорт JSON", command=self._export_audit_json).pack(side="left", padx=(8, 0))
        tk.Button(actions, text="Закрыть", command=self._close_audit_history).pack(side="right")

        columns = ("timestamp", "operation", "person", "tables", "service", "batch", "description")
        tree = ttk.Treeview(window, columns=columns, show="headings", height=12)
        self._audit_tree = tree
        for column, heading, width in (
            ("timestamp", "Время", 190), ("operation", "Операция", 140),
            ("person", "Человек", 120), ("tables", "Таблицы", 180),
            ("service", "Сервис", 150), ("batch", "Batch ID", 130),
            ("description", "Описание", 300),
        ):
            tree.heading(column, text=heading)
            tree.column(column, width=width, anchor="w")
        tree.pack(fill="both", expand=True, padx=12)
        tree.bind("<<TreeviewSelect>>", self._show_selected_audit_record)

        tk.Label(window, text="До  ↓  После").pack(pady=(8, 2))
        comparison = tk.PanedWindow(window, orient="horizontal", sashwidth=6)
        comparison.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        before_frame = tk.LabelFrame(comparison, text="До")
        after_frame = tk.LabelFrame(comparison, text="После")
        comparison.add(before_frame, stretch="always")
        comparison.add(after_frame, stretch="always")
        self._audit_before_text = tk.Text(before_frame, wrap="none", height=12)
        self._audit_after_text = tk.Text(after_frame, wrap="none", height=12)
        self._audit_before_text.pack(fill="both", expand=True)
        self._audit_after_text.pack(fill="both", expand=True)
        self._set_audit_comparison({}, {})
        self._load_audit_history()

    def _load_audit_history(self):
        values = {key: variable.get().strip() for key, variable in self._audit_filter_vars.items()}
        self._audit_records = self._get_audit_service().list_records(**values)
        self._audit_record_map = {str(record.id): record for record in self._audit_records}
        tree = self._audit_tree
        for item_id in tree.get_children():
            tree.delete(item_id)
        for record in self._audit_records:
            identity = record.database_id
            if record.gedcom_id:
                identity = f"{identity} / {record.gedcom_id}" if identity else record.gedcom_id
            tree.insert("", "end", iid=str(record.id), values=(
                record.timestamp, record.operation_type, identity,
                ", ".join(record.affected_tables), record.service,
                record.batch_id, record.description,
            ))
        self._set_audit_comparison({}, {})

    def _show_selected_audit_record(self, _event=None):
        selection = self._audit_tree.selection()
        if not selection:
            return
        record = self._audit_record_map.get(str(selection[0]))
        if record:
            self._set_audit_comparison(record.before_snapshot, record.after_snapshot)
            if hasattr(self, "workspace_integration_service"):
                self.workspace_integration_service.update("audit", active_module="audit")

    def _set_audit_comparison(self, before, after):
        for widget, snapshot in (
            (self._audit_before_text, before), (self._audit_after_text, after),
        ):
            if widget is None:
                continue
            widget.config(state="normal")
            widget.delete("1.0", "end")
            widget.insert("1.0", json.dumps(snapshot, ensure_ascii=False, indent=2, default=str))
            widget.config(state="disabled")

    def _reset_audit_filters(self):
        for variable in self._audit_filter_vars.values():
            variable.set("")
        self._load_audit_history()

    def _export_audit_csv(self):
        self._export_audit_history("csv")

    def _export_audit_json(self):
        self._export_audit_history("json")

    def _export_audit_history(self, extension):
        destination = filedialog.asksaveasfilename(
            parent=self._audit_window,
            title=f"Экспорт истории в {extension.upper()}",
            defaultextension=f".{extension}",
            filetypes=[(extension.upper(), f"*.{extension}")],
        )
        if not destination:
            return
        service = self._get_audit_service()
        if extension == "csv":
            service.export_csv(self._audit_records, destination)
        else:
            service.export_json(self._audit_records, destination)

    def _close_audit_history(self):
        if self._audit_window is not None:
            try:
                self._audit_window.destroy()
            except Exception:
                pass
        self._audit_window = None
        self._audit_tree = None
        self._audit_before_text = None
        self._audit_after_text = None

    @staticmethod
    def _filter_data_quality_issues(report, category="all", severity="All"):
        if report is None:
            return ()
        return tuple(
            issue for issue in report.issues
            if (category == "all" or issue.category == category)
            and (severity == "All" or issue.severity == severity)
        )

    def open_validation_center(self):
        if self._validation_center_window is not None:
            try:
                self._validation_center_window.lift()
                self._refresh_validation_center()
                return
            except Exception:
                self._validation_center_window = None
        dialog = self._create_dialog()
        self._validation_center_window = dialog
        dialog.title("Проверка и исправление")
        dialog.geometry("1420x780")
        self._validation_filter_vars = {
            "severity": tk.StringVar(value=""), "object_type": tk.StringVar(value=""),
            "risk": tk.StringVar(value=""), "automatic": tk.BooleanVar(value=False),
            "resolved": tk.StringVar(value="Нерешенные"), "text": tk.StringVar(value=""),
        }
        dialog.protocol("WM_DELETE_WINDOW", self._close_validation_center)
        toolbar = tk.Frame(dialog)
        toolbar.pack(fill="x", padx=12, pady=12)
        for label, action in (("Обновить", self._refresh_validation_center), ("Открыть выбранное", self._open_validation_issue), ("Проверить исправление", self._dry_run_validation_fix), ("Применить безопасное", self._apply_selected_validation_fix), ("Применить все безопасные", self._apply_all_safe_validation_fixes), ("Игнорировать", self._ignore_validation_issue), ("Восстановить", self._restore_validation_issue), ("Экспорт", self._export_validation_report)):
            tk.Button(toolbar, text=label, command=action).pack(side="left", padx=(0, 5))
        tk.Button(toolbar, text="Закрыть", command=self._close_validation_center).pack(side="right")
        filters = tk.Frame(dialog)
        filters.pack(fill="x", padx=12, pady=(0, 8))
        for label, key, values in (("Важность", "severity", ("", "Critical", "Error", "Warning", "Information")), ("Объект", "object_type", ("", "person", "family", "family_child", "event", "source", "citation", "attachment", "layout", "audit")), ("Риск", "risk", ("", "Safe", "Review required", "Dangerous"))):
            tk.Label(filters, text=label).pack(side="left", padx=(0, 3))
            combo = ttk.Combobox(filters, textvariable=self._validation_filter_vars[key], values=values, state="readonly", width=16)
            combo.pack(side="left", padx=(0, 8))
            combo.bind("<<ComboboxSelected>>", lambda _event: self._render_validation_issues())
        tk.Checkbutton(filters, text="Только авто", variable=self._validation_filter_vars["automatic"], command=self._render_validation_issues).pack(side="left")
        tk.Label(filters, text="Статус").pack(side="left", padx=(10, 3))
        resolved = ttk.Combobox(filters, textvariable=self._validation_filter_vars["resolved"], values=("Все", "Нерешенные", "Решенные"), state="readonly", width=13)
        resolved.pack(side="left")
        resolved.bind("<<ComboboxSelected>>", lambda _event: self._render_validation_issues())
        tk.Label(filters, text="Поиск").pack(side="left", padx=(10, 3))
        search = ttk.Entry(filters, textvariable=self._validation_filter_vars["text"], width=28)
        search.pack(side="left")
        search.bind("<KeyRelease>", lambda _event: self._render_validation_issues())
        body = ttk.Panedwindow(dialog, orient="horizontal")
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        left, center, right = tk.Frame(body), tk.Frame(body), tk.Frame(body)
        body.add(left, weight=1); body.add(center, weight=4); body.add(right, weight=3)
        category = ttk.Treeview(left, columns=("count",), show="tree headings", selectmode="browse")
        category.heading("#0", text="Категория"); category.heading("count", text="Кол-во")
        category.column("#0", width=180); category.column("count", width=55, anchor="e")
        category.pack(fill="both", expand=True)
        category.bind("<<TreeviewSelect>>", lambda _event: self._render_validation_issues())
        self._validation_category_tree = category
        columns = ("severity", "category", "object", "id", "gedcom", "name", "risk", "auto")
        issues = ttk.Treeview(center, columns=columns, show="headings", selectmode="browse")
        for column, title, width in (("severity", "Важность", 85), ("category", "Категория", 150), ("object", "Объект", 90), ("id", "ID", 70), ("gedcom", "GEDCOM", 80), ("name", "Имя", 135), ("risk", "Риск", 120), ("auto", "Авто", 50)):
            issues.heading(column, text=title, command=lambda key=column: self._sort_validation_issues(key))
            issues.column(column, width=width, anchor="w")
        issues.pack(fill="both", expand=True)
        issues.bind("<<TreeviewSelect>>", lambda _event: self._render_validation_detail())
        self._validation_issue_tree = issues
        detail = tk.Text(right, wrap="word", state="disabled")
        detail.pack(fill="both", expand=True)
        self._validation_detail_text = detail
        self._refresh_validation_center()

    def _close_validation_center(self):
        if self._validation_center_window is not None:
            try: self._validation_center_window.destroy()
            except Exception: pass
        self._validation_center_window = None
        self._validation_report = None
        self._validation_category_tree = None
        self._validation_issue_tree = None
        self._validation_detail_text = None
        self._validation_issue_map = {}

    def _refresh_validation_center(self):
        return self._submit_repository_task(
            "Проверка и исправление", lambda repository, context: ValidationCenterService(repository).analyze(
                progress_callback=(lambda message, done, total: context.report(message, done, total)) if context else None,
                cancel_callback=context.raise_if_cancelled if context else None,
            ), self._render_validation_report,
            on_error=lambda error: messagebox.showerror("Проверка и исправление", str(error), parent=self._validation_center_window), cancellable=True,
        )

    def _render_validation_report(self, report):
        self._validation_report = report
        tree = self._validation_category_tree
        if tree is None: return
        for item in tree.get_children(): tree.delete(item)
        tree.insert("", "end", iid="all", text="Все", values=(len(report.issues),))
        for category, count in report.counters.items(): tree.insert("", "end", iid=category, text=category, values=(count,))
        self._render_validation_issues()

    def _selected_validation_category(self):
        tree = self._validation_category_tree
        selected = tree.selection()[0] if tree is not None and tree.selection() else ""
        return "" if selected == "all" else selected

    def _filtered_validation_issues(self):
        if self._validation_report is None: return ()
        values = self._validation_filter_vars
        issues = ValidationCenterService(self.repository).filter_issues(
            self._validation_report, category=self._selected_validation_category(), severity=values["severity"].get(),
            object_type=values["object_type"].get(), automatic_only=bool(values["automatic"].get()),
            risk_level=values["risk"].get(), text=values["text"].get(),
        )
        if values["resolved"].get() == "Решенные":
            return tuple(issue for issue in issues if issue.resolved)
        if values["resolved"].get() == "Нерешенные":
            return tuple(issue for issue in issues if not issue.resolved)
        return issues

    def _render_validation_issues(self):
        tree = self._validation_issue_tree
        if tree is None: return
        for item in tree.get_children(): tree.delete(item)
        self._validation_issue_map = {}
        for issue in self._filtered_validation_issues():
            tree.insert("", "end", iid=issue.issue_id, values=(issue.severity, issue.category, issue.object_type, issue.database_id or "", issue.gedcom_id, issue.display_name, issue.risk_level, "Да" if issue.automatic_fix_available else ""))
            self._validation_issue_map[issue.issue_id] = issue
        self._render_validation_detail()

    def _sort_validation_issues(self, column):
        tree = self._validation_issue_tree
        if tree is None: return
        values = sorted(tree.get_children(), key=lambda item: str(tree.set(item, column)).casefold())
        for index, item in enumerate(values): tree.move(item, "", index)

    def _selected_validation_issue(self):
        tree = self._validation_issue_tree
        return self._validation_issue_map.get(tree.selection()[0]) if tree is not None and tree.selection() else None

    def _render_validation_detail(self):
        detail = self._validation_detail_text
        if detail is None: return
        issue = self._selected_validation_issue()
        detail.configure(state="normal"); detail.delete("1.0", "end")
        if issue:
            if hasattr(self, "workspace_integration_service"):
                if issue.object_type == "person":
                    self.workspace_integration_service.select_person(issue.database_id, "validation")
                elif issue.object_type == "family":
                    self.workspace_integration_service.select_family(issue.database_id, "validation")
                elif issue.object_type == "event":
                    self.workspace_integration_service.select_event(issue.database_id, "validation")
            detail.insert("end", f"{issue.category}\n\n{issue.explanation}\n\nРекомендация: {issue.recommended_action}\nРиск: {issue.risk_level}\nАвто: {'Да' if issue.automatic_fix_available else 'Нет'}\n\nДоказательства:\n{json.dumps(issue.evidence, ensure_ascii=False, indent=2)}\n\nСвязанные аудит/источники: проверьте историю изменений и карточку объекта.")
        detail.configure(state="disabled")

    def _open_validation_issue(self):
        issue = self._selected_validation_issue()
        if issue and issue.object_type == "person" and issue.database_id is not None: self.show_person(issue.database_id)

    def _dry_run_validation_fix(self):
        issue = self._selected_validation_issue()
        if not issue: return
        preview = ValidationCenterService(self.repository).preview_fixes((issue,))
        messagebox.showinfo("Предпросмотр", f"Безопасных изменений: {len(preview.changes)}\nБлокировок: {len(preview.blockers)}", parent=self._validation_center_window)

    def _apply_selected_validation_fix(self):
        issue = self._selected_validation_issue()
        self._apply_validation_fixes((issue,) if issue else ())

    def _apply_all_safe_validation_fixes(self):
        self._apply_validation_fixes(self._filtered_validation_issues())

    def _apply_validation_fixes(self, issues):
        preview = ValidationCenterService(self.repository).preview_fixes(issues)
        if not preview.can_apply:
            messagebox.showwarning("Исправление", "Нет безопасных исправлений в выбранном наборе.", parent=self._validation_center_window); return
        if not messagebox.askyesno("Исправление", f"Применить {len(preview.changes)} безопасных исправлений?", parent=self._validation_center_window): return
        return self._submit_repository_task(
            "Применение безопасных исправлений", lambda repository, context: ValidationCenterService(repository).apply_fixes(preview, cancel_callback=context.raise_if_cancelled if context else None),
            self._complete_validation_fixes, on_error=lambda error: messagebox.showerror("Исправление", str(error), parent=self._validation_center_window), cancellable=True,
        )

    def _complete_validation_fixes(self, result):
        self._get_undo_manager().record_applied(ValidationFixCommand(self.repository, result))
        self._refresh_validation_center()
        self._refresh_person_card()

    def _ignore_validation_issue(self):
        issue = self._selected_validation_issue()
        if issue:
            ValidationCenterService(self.repository).ignore(issue, simpledialog.askstring("Игнорировать", "Причина:", parent=self._validation_center_window) or "")
            self._refresh_validation_center()

    def _restore_validation_issue(self):
        issue = self._selected_validation_issue()
        if issue:
            ValidationCenterService(self.repository).restore_ignored(issue)
            self._refresh_validation_center()

    def _export_validation_report(self):
        if self._validation_report is None: return
        destination = filedialog.asksaveasfilename(parent=self._validation_center_window, title="Экспорт отчёта", defaultextension=".json", filetypes=[("JSON", "*.json"), ("CSV", "*.csv"), ("HTML", "*.html")])
        if destination:
            extension = Path(destination).suffix.lower().lstrip(".") or "json"
            ValidationCenterService(self.repository).export_report(self._validation_report, destination, extension)

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
        if not hasattr(self, "task_manager"):
            self._data_quality_report = self.data_quality_service.analyze()
            return self._render_data_quality_report()

        def apply_report(report):
            self._data_quality_report = report
            self._render_data_quality_report()

        return self._submit_repository_task(
            "Центр качества данных",
            lambda repository, _context: DataQualityService(repository).analyze(),
            apply_report,
        )

    def _render_data_quality_report(self):
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
        if not hasattr(self, "task_manager"):
            self._timeline_entries = self.timeline_service.build_timeline(person_id)
            self._timeline_source_map = {
                source.get("id"): source
                for source in self.attachment_service.list_sources(person_id)
                if source.get("id") is not None
            }
            return self._populate_timeline_tree(tree)

        def load(repository, _context):
            entries = PersonTimelineService(repository).build_timeline(person_id)
            sources = repository.list_person_sources(person_id)
            return entries, sources

        def apply_timeline(result):
            entries, sources = result
            self._timeline_entries = entries
            self._timeline_source_map = {
                source.get("id"): source
                for source in sources
                if source.get("id") is not None
            }
            self._populate_timeline_tree(tree)

        return self._submit_repository_task("Хронология", load, apply_timeline)

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
        person_id = marker.get("person_id") or self._life_map_current_person_id
        self.show_person(int(person_id))

    def _select_life_map_marker(self, marker):
        if not marker or self._life_map_detail_label is None:
            return
        self._life_map_detail_label.config(
            text=(
                f"Событие: {marker.get('event_label', '')}\n"
                f"Дата: {marker.get('date_text', '')}\n"
                f"Человек: {marker.get('person_name', '')}\n"
                f"Заметки: {marker.get('description', '')}"
            )
        )

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
            canvas.tag_bind(item_id, "<Button-1>", lambda _event, mark=marker: self._select_life_map_marker(mark))
            canvas.tag_bind(item_id, "<Double-1>", lambda _event, mark=marker: self._open_life_map_event_details(mark))

    def _render_life_map_tree(self):
        if self._life_map_tree is None:
            return

        tree = self._life_map_tree
        for item in tree.get_children():
            tree.delete(item)
        self._life_map_tree_markers = {}

        for index, marker in enumerate(self._life_map_data.get("markers", [])):
            status = marker.get("geocode_status", "missing")
            date_text = marker.get("date_text", "")
            status_text = {
                "ok": "ok",
                "manual": "вручную",
                "failed": "ошибка",
                "needs_key": "нет ключа",
                "missing": "нет координат",
            }.get(status, status)
            item_id = tree.insert(
                "",
                "end",
                iid=f"life-map-{index}",
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
            self._life_map_tree_markers[item_id] = marker

    def _select_life_map_tree_marker(self, _event=None):
        if self._life_map_tree is None:
            return
        selection = self._life_map_tree.selection()
        if selection:
            self._select_life_map_marker(self._life_map_tree_markers.get(selection[0]))

    def _open_selected_life_map_person(self, _event=None):
        if self._life_map_tree is None:
            return
        selection = self._life_map_tree.selection()
        if selection:
            self._open_life_map_event_details(self._life_map_tree_markers.get(selection[0]))

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

    def _export_life_map_html(self):
        self._export_life_map("html", "HTML", self.life_map_service.export_html)

    def _export_life_map_png(self):
        self._export_life_map("png", "PNG", self.life_map_service.export_png)

    def _export_life_map(self, extension, label, exporter):
        if not self._life_map_data.get("markers"):
            messagebox.showinfo("Карта жизни", "Нет данных для экспорта.")
            return
        destination = filedialog.asksaveasfilename(
            parent=self._life_map_window or self._person_dialog or self.root,
            title=f"Экспорт карты жизни в {label}",
            defaultextension=f".{extension}",
            filetypes=[(label, f"*.{extension}"), ("Все файлы", "*.*")],
        )
        if not destination:
            return
        try:
            saved = exporter(self._life_map_data, destination)
            messagebox.showinfo("Экспорт", f"{label} сохранен: {saved}")
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

        if hasattr(self, "task_manager"):
            person_id = self._life_map_current_person_id

            def geocode(repository, context):
                service = PersonLifeMapService(
                    repository,
                    timeline_service=PersonTimelineService(repository),
                )
                return service.update_missing_coordinates(
                    person_id,
                    progress_callback=lambda stage, processed, total, _percent: context.report(
                        stage, processed, total
                    ),
                    cancel_event=context.cancel_event,
                )

            def complete(summary):
                if self._life_map_progress_label is not None:
                    if summary.get("needs_key"):
                        self._life_map_progress_label.config(text="Геокодирование недоступно: не настроен ключ.")
                    else:
                        self._life_map_progress_label.config(
                            text=f"Обновление завершено: успешно {summary.get('updated', 0)}, ошибок {summary.get('failed', 0)}"
                        )
                self._refresh_life_map_data(person_id)

            return self._submit_repository_task(
                "Геокодирование карты жизни",
                geocode,
                complete,
                cancellable=True,
            )

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
        tk.Button(controls, text="Экспорт HTML", command=self._export_life_map_html).pack(side="left", padx=(8, 0))
        tk.Button(controls, text="Экспорт PNG", command=self._export_life_map_png).pack(side="left", padx=(8, 0))
        tk.Button(controls, text="Экспорт KML", command=self._export_life_map_kml).pack(side="left", padx=(8, 0))

        self._life_map_progress_label = tk.Label(parent, text="")
        self._life_map_progress_label.pack(anchor="w", padx=8, pady=(0, 4))

        self._life_map_key_label = tk.Label(parent, text="", justify="left", wraplength=860)
        self._life_map_key_label.pack(anchor="w", padx=8, pady=(0, 6))

        canvas_frame = tk.Frame(parent)
        canvas_frame.pack(fill="x", padx=8, pady=(0, 8))
        self._life_map_canvas = tk.Canvas(canvas_frame, height=360, highlightthickness=1, highlightbackground="#d1d9e0")
        self._life_map_canvas.pack(fill="x", expand=False)

        details_frame = tk.LabelFrame(parent, text="Выбранное событие")
        details_frame.pack(fill="x", padx=8, pady=(0, 8))
        self._life_map_detail_label = tk.Label(
            details_frame,
            text="Выберите маркер, чтобы увидеть событие, дату, человека и заметки.",
            justify="left",
            anchor="w",
        )
        self._life_map_detail_label.pack(fill="x", padx=8, pady=6)

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

        tree.bind("<<TreeviewSelect>>", self._select_life_map_tree_marker)
        tree.bind("<Double-1>", self._open_selected_life_map_person)
        self._life_map_tree = tree

        self._refresh_life_map_data(person_id)

    def open_life_map(self):
        person_reference = self._choose_person("Выберите человека для карты жизни")
        if not person_reference:
            return
        person_id = self.repository.resolve_person_reference(person_reference)
        if person_id is None:
            messagebox.showerror("Карта жизни", "Человек не найден.")
            return
        if self._life_map_window is not None:
            try:
                self._life_map_window.destroy()
            except Exception:
                pass
        window = self._create_dialog(self.root)
        self._life_map_window = window
        window.title("Карта жизни")
        window.geometry("1040x780")
        window.minsize(780, 560)
        window.protocol("WM_DELETE_WINDOW", self._close_life_map_window)
        self._build_life_map_tab(window, int(person_id))

    def open_research_workspace(self):
        if self._research_window is not None:
            try: self._research_window.lift(); self._research_window.focus_force(); self._load_research_projects(); return
            except Exception: self._research_window = None
        window = self._create_dialog(self.root); self._research_window = window; window.title("Исследование"); window.geometry("1320x760"); window.minsize(920, 560); window.protocol("WM_DELETE_WINDOW", self._close_research_workspace)
        toolbar = tk.Frame(window); toolbar.pack(fill="x", padx=12, pady=(12, 6))
        for label, command in (("Новый проект", self._create_research_project), ("Удалить проект", self._delete_research_project), ("Гипотеза", self._create_research_hypothesis), ("Задача", self._create_research_task), ("Вопрос", self._create_research_question), ("Вывод", self._create_research_conclusion), ("Kanban", lambda: self._render_research_workspace("kanban")), ("Список", lambda: self._render_research_workspace("list")), ("Календарь", lambda: self._render_research_workspace("calendar")), ("Хронология", self._open_research_timeline), ("Карта", self._open_research_map), ("Дерево", self._highlight_research_tree), ("Экспорт", self._export_research_workspace)):
            tk.Button(toolbar, text=label, command=command).pack(side="left", padx=(0, 4))
        panes = ttk.Panedwindow(window, orient="horizontal"); panes.pack(fill="both", expand=True, padx=12, pady=6)
        projects, work, details = tk.Frame(panes), tk.Frame(panes), tk.Frame(panes); panes.add(projects, weight=1); panes.add(work, weight=3); panes.add(details, weight=2)
        project_tree = ttk.Treeview(projects, columns=("updated",), show="tree headings", selectmode="browse"); project_tree.heading("#0", text="Проекты"); project_tree.heading("updated", text="Обновлён"); project_tree.pack(fill="both", expand=True); project_tree.bind("<<TreeviewSelect>>", self._select_research_project); self._research_project_tree = project_tree
        notebook = ttk.Notebook(work); notebook.pack(fill="both", expand=True)
        hypothesis_tab, task_tab = tk.Frame(notebook), tk.Frame(notebook); notebook.add(hypothesis_tab, text="Гипотезы"); notebook.add(task_tab, text="Задачи")
        hypothesis_tree = ttk.Treeview(hypothesis_tab, columns=("state", "statement", "links"), show="headings", selectmode="browse")
        for key, title, width in (("state", "Состояние", 120), ("statement", "Формулировка", 350), ("links", "Связи", 90)): hypothesis_tree.heading(key, text=title); hypothesis_tree.column(key, width=width, anchor="w")
        hypothesis_tree.pack(fill="both", expand=True); hypothesis_tree.bind("<<TreeviewSelect>>", self._select_research_hypothesis); self._research_hypothesis_tree = hypothesis_tree
        task_tree = ttk.Treeview(task_tab, columns=("status", "priority", "due", "hypothesis"), show="headings")
        for key, title, width in (("status", "Статус", 120), ("priority", "Приоритет", 100), ("due", "Срок", 100), ("hypothesis", "Гипотеза", 180)): task_tree.heading(key, text=title); task_tree.column(key, width=width, anchor="w")
        task_tree.pack(fill="both", expand=True); self._research_task_tree = task_tree
        details_text = tk.Text(details, wrap="word", state="disabled"); details_text.pack(fill="both", expand=True); self._research_details = details_text
        self._load_research_projects()

    def _load_research_projects(self):
        return self._submit_repository_task("Исследование", lambda repository, _context: ResearchWorkspaceService(repository).list_projects(), self._render_research_projects, on_error=lambda error: messagebox.showerror("Исследование", str(error), parent=self._research_window), cancellable=True)
    def _render_research_projects(self, projects):
        tree = self._research_project_tree
        if tree is None: return
        for item in tree.get_children(): tree.delete(item)
        for project in projects: tree.insert("", "end", iid=project.project_id, text=project.title, values=(project.updated_at,))
    def _select_research_project(self, _event=None):
        tree = self._research_project_tree
        if not tree or not tree.selection(): return
        self._research_project_id = tree.selection()[0]
        return self._submit_repository_task("Загрузка исследования", lambda repository, context: ResearchWorkspaceService(repository).load(self._research_project_id, progress_callback=(lambda text, done, total: context.report(text, done, total)) if context else None, cancel_callback=context.raise_if_cancelled if context else None), lambda workspace: self._set_research_workspace(workspace), on_error=lambda error: messagebox.showerror("Исследование", str(error), parent=self._research_window), cancellable=True)
    def _set_research_workspace(self, workspace): self._research_workspace = workspace; self._render_research_workspace("list")
    def _render_research_workspace(self, mode):
        workspace = self._research_workspace
        if workspace is None: return
        for tree in (self._research_hypothesis_tree, self._research_task_tree):
            if tree:
                for item in tree.get_children(): tree.delete(item)
        if self._research_hypothesis_tree:
            for item in workspace.hypotheses: self._research_hypothesis_tree.insert("", "end", iid=item.hypothesis_id, text=item.title, values=(item.state, item.statement, len(item.people) + len(item.evidence)))
        tasks = workspace.tasks if mode == "list" else (task for status in TASK_STATUSES for task in workspace.tasks if task.status == status) if mode == "kanban" else self.research_workspace_service.calendar(workspace)
        if self._research_task_tree:
            for item in tasks: self._research_task_tree.insert("", "end", iid=item.task_id, text=item.title, values=(item.status, item.priority, item.due_date, item.hypothesis_id))
        self._set_research_details(f"{workspace.project.title}\n\nРежим: {mode}\nГипотез: {len(workspace.hypotheses)}\nЗадач: {len(workspace.tasks)}\n\nОткрытые вопросы:\n" + "\n".join(f"- {item['text']}" for item in workspace.questions) + "\n\nВыводы:\n" + "\n".join(f"- {item['text']}" for item in workspace.conclusions))
    def _selected_research_hypothesis(self):
        tree = self._research_hypothesis_tree
        if not tree or not tree.selection() or not self._research_workspace: return None
        return next((item for item in self._research_workspace.hypotheses if item.hypothesis_id == tree.selection()[0]), None)
    def _select_research_hypothesis(self, _event=None):
        hypothesis = self._selected_research_hypothesis()
        if not hypothesis: return
        if hasattr(self, "workspace_integration_service"):
            self.workspace_integration_service.update("research", active_module="research")
            if hypothesis.people:
                self.workspace_integration_service.select_person(hypothesis.people[0], "research")
            if hypothesis.families:
                self.workspace_integration_service.select_family(hypothesis.families[0], "research")
            if hypothesis.events:
                self.workspace_integration_service.select_event(hypothesis.events[0], "research")
            if hypothesis.sources or hypothesis.evidence:
                self.workspace_integration_service.select_source(
                    hypothesis.sources[0] if hypothesis.sources else None,
                    hypothesis.evidence[0] if hypothesis.evidence else None,
                    "research",
                )
        evidence = self.research_workspace_service.evidence_summary(hypothesis); issues = self.research_workspace_service.validation_issues(hypothesis)
        self._set_research_details(f"{hypothesis.title}\n\n{hypothesis.statement}\n\nСостояние: {hypothesis.state}\nДостоверность: {evidence['confidence']}\nПоддерживает: {len(evidence['supporting'])}\nПротиворечит: {len(evidence['contradicting'])}\nПроблемы валидации: {len(issues)}\n\nЗаметки:\n{hypothesis.notes}")
    def _set_research_details(self, text):
        if self._research_details: self._research_details.config(state="normal"); self._research_details.delete("1.0", "end"); self._research_details.insert("end", text); self._research_details.config(state="disabled")
    def _create_research_project(self):
        title = simpledialog.askstring("Проект", "Название:", parent=self._research_window)
        if title: self.research_workspace_service.create_project(title); self._load_research_projects()
    def _delete_research_project(self):
        if self._research_project_id and messagebox.askyesno("Проект", "Удалить рабочее пространство?", parent=self._research_window): self.research_workspace_service.delete_project(self._research_project_id); self._research_workspace = None; self._research_project_id = None; self._load_research_projects()
    def _create_research_hypothesis(self):
        if not self._research_project_id: return
        title = simpledialog.askstring("Гипотеза", "Название:", parent=self._research_window); statement = simpledialog.askstring("Гипотеза", "Формулировка:", parent=self._research_window)
        if title and statement: self.research_workspace_service.create_hypothesis(self._research_project_id, title, statement, state="Draft", people=(self.current_person_id,) if self.current_person_id else ()); self._select_research_project()
    def _create_research_task(self):
        if not self._research_project_id: return
        title = simpledialog.askstring("Задача", "Название:", parent=self._research_window)
        if title: self.research_workspace_service.create_task(self._research_project_id, title, priority="Normal", status="Backlog", people=(self.current_person_id,) if self.current_person_id else ()); self._select_research_project()
    def _create_research_question(self):
        text = simpledialog.askstring("Вопрос", "Открытый вопрос:", parent=self._research_window)
        if text and self._research_project_id: self.research_workspace_service.add_question(self._research_project_id, text); self._select_research_project()
    def _create_research_conclusion(self):
        text = simpledialog.askstring("Вывод", "Вывод:", parent=self._research_window)
        if text and self._research_project_id: self.research_workspace_service.add_conclusion(self._research_project_id, text); self._select_research_project()
    def _open_research_timeline(self):
        hypothesis = self._selected_research_hypothesis()
        if hypothesis and hypothesis.people: self.current_person_id = hypothesis.people[0]
        self.open_timeline_studio()
    def _open_research_map(self):
        hypothesis = self._selected_research_hypothesis()
        if hypothesis and hypothesis.people: self.current_person_id = hypothesis.people[0]
        self.open_geo_map_studio()
    def _highlight_research_tree(self):
        hypothesis = self._selected_research_hypothesis()
        if hypothesis:
            for person_id in hypothesis.people: self.highlight_tree_canvas_person(person_id)
    def _export_research_workspace(self):
        if not self._research_workspace: return
        destination = filedialog.asksaveasfilename(parent=self._research_window, title="Экспорт исследования", initialdir=str(EXPORT_DIR), defaultextension=".md", filetypes=[("Markdown", "*.md"), ("HTML", "*.html"), ("PDF", "*.pdf")])
        if destination:
            export_format = {"md": "markdown"}.get(Path(destination).suffix.lower().lstrip("."), Path(destination).suffix.lower().lstrip("."))
            return self._submit_repository_task("Экспорт исследования", lambda repository, _context: ResearchWorkspaceService(repository).export(self._research_workspace, destination, export_format), lambda _path: None, on_error=lambda error: messagebox.showerror("Экспорт", str(error), parent=self._research_window))
    def _close_research_workspace(self):
        if self._research_window is not None:
            try: self._research_window.destroy()
            except Exception: pass
        self._research_window = self._research_workspace = self._research_project_tree = self._research_hypothesis_tree = self._research_task_tree = self._research_details = None; self._research_project_id = None

    def open_geo_map_studio(self):
        if self._geo_map_window is not None:
            try: self._geo_map_window.lift(); self._geo_map_window.focus_force(); return
            except Exception: self._geo_map_window = None
        window = self._create_dialog(self.root)
        self._geo_map_window = window; window.title("Карта"); window.geometry("1360x780"); window.minsize(900, 580); window.protocol("WM_DELETE_WINDOW", self._close_geo_map_studio)
        self._geo_map_vars = {key: tk.StringVar(value=value) for key, value in {"scope": "current_person", "people": str(self.current_person_id or ""), "surname": "", "person": "", "family": "", "event_type": "", "year_from": "", "year_to": "", "country": "", "text": "", "year": ""}.items()}
        for key, value in {"unresolved": False, "people_layer": True, "routes": True, "borders": False, "clusters": False, "heat": False}.items(): self._geo_map_vars[key] = tk.BooleanVar(value=value)
        toolbar = tk.Frame(window); toolbar.pack(fill="x", padx=12, pady=(12, 4))
        for label, command in (("Загрузить", self._load_geo_map), ("Геокодировать", self._geocode_geo_map), ("Координаты", self._correct_geo_map_coordinates), ("Подогнать", self._fit_geo_map), ("Play/Pause", self._toggle_geo_map_play), ("К событию", self._jump_geo_map_event), ("Сохранить вид", self._save_geo_map_view), ("Загрузить вид", self._load_geo_map_view), ("Импорт вида", self._import_geo_map_view), ("Экспорт вида", self._export_geo_map_view), ("Экспорт", self._export_geo_map)):
            tk.Button(toolbar, text=label, command=command).pack(side="left", padx=(0, 4))
        filters = tk.LabelFrame(window, text="Область, фильтры и слои"); filters.pack(fill="x", padx=12, pady=(0, 5))
        specs = (("scope", "Область", GEO_MAP_SCOPES), ("people", "Люди ID", None), ("surname", "Фамилия", None), ("person", "Человек", None), ("family", "Семья", None), ("event_type", "Событие", ("", *SUPPORTED_EVENT_TYPES)), ("year_from", "Год от", None), ("year_to", "Год до", None), ("country", "Страна", None), ("text", "Текст", None), ("year", "Год анимации", None))
        for index, (key, title, values) in enumerate(specs):
            row, column = divmod(index, 6); tk.Label(filters, text=title).grid(row=row, column=column*2, sticky="e", padx=(5, 2), pady=3)
            control = ttk.Combobox(filters, textvariable=self._geo_map_vars[key], values=values, state="readonly", width=15) if values else tk.Entry(filters, textvariable=self._geo_map_vars[key], width=15)
            control.grid(row=row, column=column*2+1, sticky="ew", padx=(0, 4), pady=3)
        for index, (key, title) in enumerate((("unresolved", "Неопределённые"), ("people_layer", "Люди"), ("routes", "Маршруты"), ("borders", "Исторические границы"), ("clusters", "Кластеры"), ("heat", "Тепловая карта"))):
            tk.Checkbutton(filters, text=title, variable=self._geo_map_vars[key], command=self._apply_geo_map_filters).grid(row=2, column=index*2, columnspan=2, sticky="w", padx=5)
        panes = ttk.Panedwindow(window, orient="horizontal"); panes.pack(fill="both", expand=True, padx=12, pady=6)
        table_frame, canvas_frame = tk.Frame(panes), tk.Frame(panes); panes.add(table_frame, weight=3); panes.add(canvas_frame, weight=4)
        columns = ("date", "person", "event", "place", "status")
        tree = ttk.Treeview(table_frame, columns=columns, show="headings", selectmode="browse")
        for column, title, width in (("date", "Дата", 90), ("person", "Человек", 170), ("event", "Событие", 115), ("place", "Место", 230), ("status", "Статус", 90)):
            tree.heading(column, text=title); tree.column(column, width=width, anchor="w")
        tree.pack(fill="both", expand=True); tree.bind("<<TreeviewSelect>>", self._select_geo_map_marker); tree.bind("<Double-1>", self._open_geo_map_marker)
        self._geo_map_tree = tree
        canvas = tk.Canvas(canvas_frame, background="#f7f8fa", highlightthickness=0); canvas.pack(fill="both", expand=True); canvas.bind("<MouseWheel>", self._zoom_geo_map); canvas.bind("<ButtonPress-1>", lambda event: canvas.scan_mark(event.x, event.y)); canvas.bind("<B1-Motion>", lambda event: canvas.scan_dragto(event.x, event.y, gain=1)); self._geo_map_canvas = canvas
        footer = tk.Frame(window); footer.pack(fill="x", padx=12, pady=(0, 12)); self._geo_map_status = tk.Label(footer, text=""); self._geo_map_status.pack(side="left")
        self._load_geo_map()

    def _geo_map_ids(self): return tuple(int(value) for value in re.findall(r"\d+", self._geo_map_vars["people"].get()))
    def _load_geo_map(self):
        return self._submit_repository_task("Карта", lambda repository, context: GeoMapStudioService(repository).build(scope=self._geo_map_vars["scope"].get(), selected_person_ids=self._geo_map_ids(), progress_callback=(lambda label, done, total: context.report(label, done, total)) if context else None, cancel_callback=context.raise_if_cancelled if context else None), self._apply_geo_map_model, on_error=lambda error: messagebox.showerror("Карта", str(error), parent=self._geo_map_window), cancellable=True)
    def _apply_geo_map_model(self, model): self._geo_map_model = model; self._apply_geo_map_filters()
    def _geo_map_filters(self):
        values = self._geo_map_vars
        def year(key):
            text = values[key].get().strip(); return int(text) if text else None
        return GeoMapFilters(surname=values["surname"].get(), person=values["person"].get(), family=values["family"].get(), event_type=values["event_type"].get(), year_from=year("year_from"), year_to=year("year_to"), country=values["country"].get(), text=values["text"].get(), unresolved_only=bool(values["unresolved"].get()))
    def _apply_geo_map_filters(self):
        if self._geo_map_model is None: return
        try: self._geo_map_markers = self.geo_map_studio_service.filter(self._geo_map_model, self._geo_map_filters())
        except ValueError as error: messagebox.showerror("Карта", str(error), parent=self._geo_map_window); return
        year = self._geo_map_vars["year"].get().strip()
        if year.isdigit(): self._geo_map_markers = self.geo_map_studio_service.markers_at_year(self._geo_map_markers, int(year))
        self._render_geo_map()
    def _render_geo_map(self):
        tree = self._geo_map_tree
        if tree:
            for item in tree.get_children(): tree.delete(item)
        self._geo_map_marker_map = {}
        for marker in self._geo_map_markers:
            if tree: tree.insert("", "end", iid=marker.marker_id, values=(marker.date_text, marker.person_name, marker.event_label, marker.place, marker.geocode_status))
            self._geo_map_marker_map[marker.marker_id] = marker
        self._draw_geo_map()
        if self._geo_map_status: self._geo_map_status.config(text=f"Маркеров: {len(self._geo_map_markers)} | Расстояние: {self._geo_map_model.total_distance_km:.1f} км | Неопределённых: {sum(marker.latitude is None for marker in self._geo_map_markers)}")
    def _draw_geo_map(self):
        canvas = self._geo_map_canvas
        if canvas is None: return
        canvas.delete("all"); canvas.create_text(10, 10, anchor="nw", text="Geo Map Studio (offline)", fill="#445b74")
        def point(marker): return ((marker.longitude + 180) * 3.1 + 20, (90 - marker.latitude) * 3.0 + 45)
        if self._geo_map_vars["routes"].get():
            by_person = defaultdict(list)
            for marker in self._geo_map_markers:
                if marker.latitude is not None: by_person[marker.person_id].append(marker)
            for markers in by_person.values():
                ordered = sorted(markers, key=lambda item: (item.year is None, item.year or 9999)); points = [point(marker) for marker in ordered]
                for left, right in zip(points, points[1:]): canvas.create_line(*left, *right, arrow="last", fill="#2878b5", width=2)
        if self._geo_map_vars["people_layer"].get():
            for marker in self._geo_map_markers:
                if marker.latitude is None: continue
                x, y = point(marker); item = canvas.create_oval(x-5, y-5, x+5, y+5, fill=marker.color, outline="#5a1a1a" if marker.geocode_status == "manual" else ""); canvas.tag_bind(item, "<Button-1>", lambda _event, item=marker: self._highlight_geo_map_marker(item))
        if self._geo_map_vars["clusters"].get():
            for key, marker_ids in self._geo_map_model.clusters.items():
                if key != "unresolved" and len(marker_ids) > 1: canvas.create_text(20, 35 + len(marker_ids)*14, anchor="nw", text=f"Кластер {key}: {len(marker_ids)}")
        if self._geo_map_vars["heat"].get(): canvas.create_text(10, 70, anchor="nw", text="Heat map: интенсивность обозначена количеством маркеров", fill="#9a3535")
        if self._geo_map_vars["borders"].get(): canvas.create_rectangle(20, 45, 1135, 565, outline="#b8a27c", dash=(4, 3))
        canvas.configure(scrollregion=(0, 0, 1200, 620))
    def _selected_geo_map_marker(self):
        return self._geo_map_marker_map.get(self._geo_map_tree.selection()[0]) if self._geo_map_tree and self._geo_map_tree.selection() else None
    def _select_geo_map_marker(self, _event=None):
        marker = self._selected_geo_map_marker()
        if marker: self._highlight_geo_map_marker(marker)
    def _highlight_geo_map_marker(self, marker):
        if self._geo_map_tree and marker.marker_id in self._geo_map_marker_map: self._geo_map_tree.selection_set(marker.marker_id); self._geo_map_tree.see(marker.marker_id)
        self.current_person_id = marker.person_id
        if hasattr(self, "workspace_integration_service"):
            self.workspace_integration_service.select_person(marker.person_id, "map")
            self.workspace_integration_service.select_event(getattr(marker, "event_id", None), "map")
        self.highlight_tree_canvas_person(marker.person_id)
    def highlight_tree_canvas_person(self, person_id):
        """Map integration point: highlight an open Tree Canvas node for a map marker."""
        canvas = getattr(self, "_tree_canvas", None)
        if canvas is not None:
            try: canvas.itemconfigure(f"tree-canvas-person:{person_id}", outline="#c63d2f", width=4)
            except Exception: pass
    def _open_geo_map_marker(self, _event=None):
        marker = self._selected_geo_map_marker()
        if marker: self.show_person(marker.person_id)
    def highlight_geo_map_person(self, person_id):
        """Tree Canvas integration point: select a person's first visible map marker."""
        if self._geo_map_model:
            markers = self.geo_map_studio_service.markers_for_tree_person(self._geo_map_model, person_id)
            if markers: self._highlight_geo_map_marker(markers[0])
    def center_geo_map_timeline_event(self, event_id):
        """Timeline Studio integration point: center/select its matching location."""
        if self._geo_map_model:
            marker = self.geo_map_studio_service.marker_for_timeline_event(self._geo_map_model, event_id)
            if marker: self._highlight_geo_map_marker(marker)
    def _zoom_geo_map(self, event):
        if self._geo_map_canvas: self._geo_map_canvas.scale("all", event.x, event.y, 1.12 if event.delta > 0 else 0.89, 1.12 if event.delta > 0 else 0.89)
    def _fit_geo_map(self):
        if self._geo_map_canvas: self._geo_map_canvas.xview_moveto(0); self._geo_map_canvas.yview_moveto(0)
    def _toggle_geo_map_play(self):
        self._geo_map_playing = not self._geo_map_playing
        if self._geo_map_playing: self._play_geo_map_step()
    def _play_geo_map_step(self):
        if not self._geo_map_playing or self._geo_map_window is None: return
        years = sorted({marker.year for marker in self._geo_map_model.markers if marker.year is not None}) if self._geo_map_model else []
        if years:
            current = int(self._geo_map_vars["year"].get()) if self._geo_map_vars["year"].get().isdigit() else years[0]; self._geo_map_vars["year"].set(str(next((year for year in years if year > current), years[0]))); self._apply_geo_map_filters()
        self._geo_map_window.after(800, self._play_geo_map_step)
    def _jump_geo_map_event(self):
        marker = self._selected_geo_map_marker()
        if marker and marker.year is not None: self._geo_map_vars["year"].set(str(marker.year)); self._apply_geo_map_filters()
    def _geocode_geo_map(self):
        if self._geo_map_model is None: return
        return self._submit_repository_task("Геокодирование", lambda repository, context: GeoMapStudioService(repository).update_missing_coordinates(self._geo_map_model, progress_callback=(lambda label, done, total: context.report(label, done, total)) if context else None, cancel_callback=context.raise_if_cancelled if context else None), lambda _result: self._load_geo_map(), on_error=lambda error: messagebox.showerror("Геокодирование", str(error), parent=self._geo_map_window), cancellable=True)
    def _correct_geo_map_coordinates(self):
        marker = self._selected_geo_map_marker()
        if not marker: return
        latitude = simpledialog.askfloat("Координаты", "Широта:", initialvalue=marker.latitude, parent=self._geo_map_window); longitude = simpledialog.askfloat("Координаты", "Долгота:", initialvalue=marker.longitude, parent=self._geo_map_window)
        if latitude is not None and longitude is not None: self.geo_map_studio_service.set_manual_coordinates(marker.place, latitude, longitude); self._load_geo_map()
    def _save_geo_map_view(self):
        name = simpledialog.askstring("Сохранить вид", "Название:", parent=self._geo_map_window)
        if name: self.geo_map_studio_service.save_view(name, {"scope": self._geo_map_vars["scope"].get(), "people": self._geo_map_vars["people"].get(), "filters": asdict(self._geo_map_filters()), "layers": {key: value.get() for key, value in self._geo_map_vars.items() if isinstance(value, tk.BooleanVar)}, "zoom": 1, "center": [0, 0]})
    def _load_geo_map_view(self):
        names = ", ".join(view["name"] for view in self.geo_map_studio_service.list_views()); name = simpledialog.askstring("Загрузить вид", "Название: " + names, parent=self._geo_map_window)
        if name:
            try:
                config = self.geo_map_studio_service.load_view(name)["configuration"]
                for key, value in config.get("filters", {}).items():
                    if key in self._geo_map_vars and not isinstance(self._geo_map_vars[key], tk.BooleanVar): self._geo_map_vars[key].set(str(value or ""))
                for key, value in config.get("layers", {}).items():
                    if key in self._geo_map_vars: self._geo_map_vars[key].set(bool(value))
                for key in ("scope", "people"):
                    if key in config: self._geo_map_vars[key].set(config[key])
                self._load_geo_map()
            except (OSError, ValueError) as error: messagebox.showerror("Карта", str(error), parent=self._geo_map_window)
    def _import_geo_map_view(self):
        source = filedialog.askopenfilename(parent=self._geo_map_window, title="Импорт вида", filetypes=[("JSON", "*.json")])
        if source: self.geo_map_studio_service.import_view(source)
    def _export_geo_map_view(self):
        names = ", ".join(view["name"] for view in self.geo_map_studio_service.list_views()); name = simpledialog.askstring("Экспорт вида", "Название: " + names, parent=self._geo_map_window)
        destination = filedialog.asksaveasfilename(parent=self._geo_map_window, defaultextension=".json", filetypes=[("JSON", "*.json")]) if name else ""
        if destination: self.geo_map_studio_service.export_view(name, destination)
    def _export_geo_map(self):
        if self._geo_map_model is None: return
        destination = filedialog.asksaveasfilename(parent=self._geo_map_window, title="Экспорт карты", initialdir=str(EXPORT_DIR), defaultextension=".png", filetypes=[("PNG", "*.png"), ("SVG", "*.svg"), ("PDF", "*.pdf"), ("HTML", "*.html")])
        if destination:
            export_format = Path(destination).suffix.lower().lstrip("."); layers = tuple(key for key in ("people_layer", "routes", "borders", "clusters", "heat") if self._geo_map_vars[key].get())
            return self._submit_repository_task("Экспорт карты", lambda repository, _context: GeoMapStudioService(repository).export(self._geo_map_model, self._geo_map_markers, destination, export_format, filters=self._geo_map_filters(), layers=layers), lambda _path: None, on_error=lambda error: messagebox.showerror("Экспорт", str(error), parent=self._geo_map_window))
    def _close_geo_map_studio(self):
        self._geo_map_playing = False
        if self._geo_map_window is not None:
            try: self._geo_map_window.destroy()
            except Exception: pass
        self._geo_map_window = self._geo_map_model = self._geo_map_tree = self._geo_map_canvas = self._geo_map_status = None; self._geo_map_markers = (); self._geo_map_marker_map = {}; self._geo_map_vars = {}

    def _close_life_map_window(self):
        if self._life_map_window is not None:
            try:
                self._life_map_window.destroy()
            except Exception:
                pass
        self._life_map_window = None

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
            command = RecoveryUpdateCommand(
                self.repository,
                lambda: self.recovery_wizard_service.update_existing_person(record.person_id, form_data),
            )
            self._get_undo_manager().execute(command)
            person = self.repository.get_person_record(record.person_id)
            self._record_audit_command(
                "recovery_wizard", command, database_id=record.person_id,
                gedcom_id=person["gedcom_id"] if person else "",
                description="Карточка восстановлена в пакетном режиме.",
                service="recovery_wizard_service",
            )
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

        if hasattr(self, "task_manager"):
            return self._submit_repository_task(
                "Мастер восстановления",
                lambda repository, _context: RecoveryWizardService(repository).list_incomplete_people(),
                self._show_recovery_wizard,
                on_error=lambda error: messagebox.showerror(
                    "Мастер восстановления",
                    f"Не удалось получить список карточек:\n{error}",
                ),
            )
        try:
            records = self.recovery_wizard_service.list_incomplete_people()
        except Exception as exc:
            messagebox.showerror("Мастер восстановления", f"Не удалось получить список карточек:\n{exc}")
            return
        return self._show_recovery_wizard(records)

    def _show_recovery_wizard(self, records) -> None:

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
            command = RecoveryUpdateCommand(
                self.repository,
                lambda: self.recovery_wizard_service.update_existing_person(record.person_id, data),
            )
            self._get_undo_manager().execute(command)
            person = self.repository.get_person_record(record.person_id)
            self._record_audit_command(
                "recovery_wizard", command, database_id=record.person_id,
                gedcom_id=person["gedcom_id"] if person else "",
                description="Карточка восстановлена мастером восстановления.",
                service="recovery_wizard_service",
            )
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
        if hasattr(self, "task_manager"):
            return self._submit_repository_task(
                "Поиск совпадений",
                lambda repository, _context: RecoveryWizardService(repository).find_matches(
                    record.person_id, criteria
                ),
                lambda candidates: self._show_recovery_matches(record, candidates),
                on_error=lambda error: messagebox.showerror(
                    "Поиск совпадений", str(error), parent=self._recovery_window
                ),
            )
        try:
            candidates = self.recovery_wizard_service.find_matches(record.person_id, criteria)
        except Exception as exc:
            messagebox.showerror("Поиск совпадений", str(exc), parent=self._recovery_window)
            return
        return self._show_recovery_matches(record, candidates)

    def _show_recovery_matches(self, record, candidates) -> None:
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
        workspace_menu = tk.Menu(self._plugin_menu_bar, tearoff=False)
        self._plugin_menu_bar.add_cascade(label="Рабочее пространство", menu=workspace_menu)
        for label, shortcut, command in (
            ("Главная карточка", "Ctrl+1", self._workspace_open_main),
            ("Дерево", "Ctrl+2", lambda: self._workspace_open(self.open_tree_canvas)),
            ("Хронология", "Ctrl+3", lambda: self._workspace_open(self.open_timeline_studio)),
            ("Карта", "Ctrl+4", lambda: self._workspace_open(self.open_geo_map_studio)),
            ("Источники", "Ctrl+5", lambda: self._workspace_open(self.open_evidence_manager)),
            ("Проверка данных", "Ctrl+6", lambda: self._workspace_open(self.open_validation_center)),
            ("Исследование", "Ctrl+7", lambda: self._workspace_open(self.open_research_workspace)),
            ("История изменений", "Ctrl+8", lambda: self._workspace_open(self.open_audit_history)),
        ):
            workspace_menu.add_command(label=label, accelerator=shortcut, command=command)
        workspace_menu.add_separator()
        workspace_menu.add_command(label="Назад", accelerator="Alt+Left", command=self._workspace_back)
        workspace_menu.add_command(label="Вперёд", accelerator="Alt+Right", command=self._workspace_forward)
        workspace_menu.add_separator()
        workspace_menu.add_command(label="Диагностика интеграции", command=self.open_integration_diagnostics)
        for sequence, command in (
            ("<Alt-Left>", self._workspace_back), ("<Alt-Right>", self._workspace_forward),
            ("<Control-1>", self._workspace_open_main), ("<Control-2>", lambda event: self._workspace_open(self.open_tree_canvas, event)),
            ("<Control-3>", lambda event: self._workspace_open(self.open_timeline_studio, event)), ("<Control-4>", lambda event: self._workspace_open(self.open_geo_map_studio, event)),
            ("<Control-5>", lambda event: self._workspace_open(self.open_evidence_manager, event)), ("<Control-6>", lambda event: self._workspace_open(self.open_validation_center, event)),
            ("<Control-7>", lambda event: self._workspace_open(self.open_research_workspace, event)), ("<Control-8>", lambda event: self._workspace_open(self.open_audit_history, event)),
        ):
            self.root.bind(sequence, command)
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
        workspace_status = tk.Frame(self.root)
        workspace_status.pack(side="bottom", fill="x", padx=10, pady=(0, 6))
        self.workspace_status_label = tk.Label(workspace_status, anchor="w")
        self.workspace_status_label.pack(fill="x")
        self._update_workspace_status()

        self.backup_button = tk.Button(top, text="Backup database", command=self.backup_database)
        self.backup_button.pack(side="left", padx=(10, 5))
        self.restore_button = tk.Button(top, text="Restore database", command=self.restore_database)
        self.restore_button.pack(side="left", padx=(0, 5))
        self.relationship_button = tk.Button(top, text="Edit relationships", command=self.open_relationship_editor)
        self.relationship_button.pack(side="left")
        self.family_tree_button = tk.Button(top, text="Семейное дерево", command=self.open_family_tree)
        self.family_tree_button.pack(side="left", padx=(10, 0))
        self.graph_editor_button = tk.Button(
            top,
            text="Редактор дерева",
            command=self.open_graph_editor,
        )
        self.graph_editor_button.pack(side="left", padx=(10, 0))
        self.family_timeline_button = tk.Button(top, text="Хронология", command=self.open_family_timeline)
        self.family_timeline_button.pack(side="left", padx=(10, 0))
        self.timeline_studio_button = tk.Button(top, text="Хронология 2.0", command=self.open_timeline_studio)
        self.timeline_studio_button.pack(side="left", padx=(10, 0))
        self.geo_map_button = tk.Button(top, text="Карта", command=self.open_geo_map_studio)
        self.geo_map_button.pack(side="left", padx=(10, 0))
        self.research_button = tk.Button(top, text="Исследование", command=self.open_research_workspace)
        self.research_button.pack(side="left", padx=(10, 0))
        self.source_manager_button = tk.Button(top, text="Источники", command=self.open_source_manager)
        self.source_manager_button.pack(side="left", padx=(10, 0))
        self.evidence_manager_button = tk.Button(
            top,
            text="Источники и доказательства",
            command=self.open_evidence_manager,
        )
        self.evidence_manager_button.pack(side="left", padx=(10, 0))
        self.gedcom_repair_button = tk.Button(
            top,
            text="Исправление GEDCOM",
            command=self.open_gedcom_repair_center,
        )
        self.gedcom_repair_button.pack(side="left", padx=(10, 0))
        self.relationship_inspector_button = tk.Button(
            top,
            text="Связь между людьми",
            command=self.open_relationship_inspector,
        )
        self.relationship_inspector_button.pack(side="left", padx=(10, 0))
        self.kinship_button = tk.Button(top, text="Анализ родства", command=self.open_kinship_analyzer)
        self.kinship_button.pack(side="left", padx=(10, 0))
        self.life_map_button = tk.Button(top, text="Карта жизни", command=self.open_life_map)
        self.life_map_button.pack(side="left", padx=(10, 0))
        self.batch_operations_button = tk.Button(
            top,
            text="Пакетные операции",
            command=self.open_batch_operations,
        )
        self.batch_operations_button.pack(side="left", padx=(10, 0))
        self.merge_people_button = tk.Button(
            top,
            text="Объединить людей",
            command=self.open_merge_wizard,
        )
        self.merge_people_button.pack(side="left", padx=(10, 0))
        self.split_person_button = tk.Button(
            top,
            text="Разделить человека",
            command=self.open_split_wizard,
        )
        self.split_person_button.pack(side="left", padx=(10, 0))
        self.audit_history_button = tk.Button(
            top,
            text="История изменений",
            command=self.open_audit_history,
        )
        self.audit_history_button.pack(side="left", padx=(10, 0))
        self.integrity_button = tk.Button(top, text="Проверка базы", command=self.open_integrity_report)
        self.integrity_button.pack(side="left", padx=(10, 0))
        self.data_quality_button = tk.Button(top, text="Качество данных", command=self.open_data_quality_center)
        self.data_quality_button.pack(side="left", padx=(10, 0))
        self.validation_center_button = tk.Button(top, text="Проверка и исправление", command=self.open_validation_center)
        self.validation_center_button.pack(side="left", padx=(10, 0))
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

    def _get_audit_service(self):
        service = getattr(self, "audit_service", None)
        if service is None:
            service = AuditService.for_database(self.repository.db_name)
            self.audit_service = service
        return service

    def _record_audit_command(
        self, operation_type, command, *, database_id="", gedcom_id="",
        description, service, reverse=False,
    ):
        if command is None or not command.delta:
            return None
        return self._get_audit_service().record_delta(
            operation_type,
            command.delta,
            database_id=database_id,
            gedcom_id=gedcom_id,
            description=description,
            service=service,
            reverse=reverse,
        )

    def _undo_command(self, _event=None):
        manager = self._get_undo_manager()
        command = manager._undo_stack[-1] if manager.can_undo else None
        if manager.undo():
            self._record_audit_command(
                "undo", command, description=f"Отменено: {command.name}.",
                service="undo_manager", reverse=True,
            )
            self.refresh_views()
            self._refresh_person_card()
        return "break"

    def _redo_command(self, _event=None):
        manager = self._get_undo_manager()
        command = manager._redo_stack[-1] if manager.can_redo else None
        if manager.redo():
            self._record_audit_command(
                "redo", command, description=f"Повторено: {command.name}.",
                service="undo_manager",
            )
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
            except ValueError as error:
                self._advanced_search_results = ()
                self.status_label.config(text=str(error))
                return
            if not hasattr(self, "task_manager"):
                try:
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

            def search(repository, _context):
                service = AdvancedSearchService(repository, DATA_DIR / "advanced_search_last.json")
                rows = service.search(filters)
                service.save_last_search(filters)
                return rows

            def apply_rows(rows):
                self._advanced_search_results = rows
                for person in rows:
                    self.tree.insert("", "end", values=(
                        person.database_id, person.display_name,
                        person.birth_date, person.death_date,
                    ))
                self.status_label.config(text=f"Найдено: {len(rows)}")

            def search_error(error):
                self._advanced_search_results = ()
                self.status_label.config(text=str(error))

            return self._submit_repository_task(
                "Расширенный поиск", search, apply_rows, on_error=search_error
            )

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
        if not hasattr(self, "task_manager"):
            try:
                analysis = self.kinship_service.analyze(source_reference, target_reference)
            except ValueError as error:
                messagebox.showerror("Анализ родства", str(error), parent=self.root)
                return
            return self._show_kinship_analysis(analysis)
        return self._submit_repository_task(
            "Анализ родства",
            lambda repository, _context: KinshipService(repository).analyze(
                source_reference, target_reference
            ),
            self._show_kinship_analysis,
            on_error=lambda error: messagebox.showerror(
                "Анализ родства", str(error), parent=self.root
            ),
        )

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

    def open_graph_editor(self):
        person_id = self._selected_person_id() or self.current_person_id
        if person_id is None:
            messagebox.showwarning("Редактор дерева", "Сначала выберите человека.")
            return
        if self._graph_editor_window is not None:
            try:
                self._graph_editor_window.lift()
                self._graph_editor_window.focus_force()
                self._graph_editor_selected_person_id = int(person_id)
                self._load_graph_editor()
                return
            except Exception:
                self._graph_editor_window = None
        window = self._create_dialog()
        self._graph_editor_window = window
        window.title("Редактор дерева")
        window.geometry("1280x820")
        window.minsize(900, 600)
        window.protocol("WM_DELETE_WINDOW", self._close_graph_editor)
        self._graph_editor_selected_person_id = int(person_id)
        self._graph_editor_zoom = 1.0
        self._graph_editor_positions = {}

        toolbar = tk.Frame(window)
        toolbar.pack(fill="x", padx=12, pady=(12, 6))
        self._graph_editor_mode_var = tk.StringVar(value="Перемещение")
        ttk.Combobox(
            toolbar,
            textvariable=self._graph_editor_mode_var,
            values=("Перемещение", "Родитель → ребёнок", "Супруги"),
            state="readonly",
            width=22,
        ).pack(side="left")
        self._graph_editor_role_var = tk.StringVar(value="father")
        ttk.Combobox(
            toolbar,
            textvariable=self._graph_editor_role_var,
            values=("father", "mother"),
            state="readonly",
            width=9,
        ).pack(side="left", padx=(6, 0))
        tk.Button(toolbar, text="−", command=lambda: self._zoom_graph_editor(-0.1)).pack(side="left", padx=(12, 0))
        tk.Button(toolbar, text="+", command=lambda: self._zoom_graph_editor(0.1)).pack(side="left", padx=(4, 0))
        tk.Button(toolbar, text="Вписать", command=self._fit_graph_editor).pack(side="left", padx=(8, 0))
        tk.Button(toolbar, text="Центр", command=self._center_graph_editor_selected).pack(side="left", padx=(8, 0))
        tk.Button(toolbar, text="Интерактивное полотно", command=self.open_tree_canvas).pack(side="left", padx=(8, 0))
        tk.Button(toolbar, text="Обновить", command=self._load_graph_editor).pack(side="left", padx=(8, 0))
        tk.Button(toolbar, text="Закрыть", command=self._close_graph_editor).pack(side="right")
        self._graph_editor_status = tk.Label(toolbar, text="")
        self._graph_editor_status.pack(side="right", padx=12)

        canvas_frame = tk.Frame(window)
        canvas_frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        canvas = tk.Canvas(canvas_frame, background="#f4f7f8", highlightthickness=0)
        self._graph_editor_canvas = canvas
        horizontal = ttk.Scrollbar(canvas_frame, orient="horizontal", command=canvas.xview)
        vertical = ttk.Scrollbar(canvas_frame, orient="vertical", command=canvas.yview)
        canvas.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        canvas_frame.grid_rowconfigure(0, weight=1)
        canvas_frame.grid_columnconfigure(0, weight=1)
        canvas.bind("<MouseWheel>", self._graph_editor_mousewheel)
        canvas.bind("<ButtonPress-2>", self._start_graph_editor_pan)
        canvas.bind("<B2-Motion>", self._pan_graph_editor)
        canvas.bind("<Button-3>", self._open_graph_context_menu)
        self._load_graph_editor()

    def open_tree_canvas(self) -> None:
        center_id = self._graph_editor_selected_person_id or self.current_person_id
        if center_id is None:
            messagebox.showwarning("Интерактивное полотно", "Выберите человека.")
            return
        if self._tree_canvas_window is not None:
            try:
                self._tree_canvas_window.lift()
                self._tree_canvas_window.focus_force()
                if self._tree_canvas_navigation.current != int(center_id):
                    self._tree_canvas_navigation.visit(center_id)
                    self._load_tree_canvas()
                return
            except Exception:
                self._tree_canvas_window = None
        window = self._create_dialog(self._graph_editor_window)
        self._tree_canvas_window = window
        window.title("Интерактивное полотно дерева")
        window.geometry("1320x820")
        window.minsize(940, 620)
        window.protocol("WM_DELETE_WINDOW", self._close_tree_canvas)
        self._tree_canvas_navigation = TreeCanvasNavigation(center_id)
        self._tree_canvas_positions = {}
        self._tree_canvas_collapsed_ids = set()
        self._tree_canvas_zoom = 1.0
        self._tree_canvas_edit_mode_var = tk.StringVar(value="Просмотр")
        self._tree_canvas_edit_action_var = tk.StringVar(value="add_parent")
        self._tree_canvas_relationship_type_var = tk.StringVar(value="marriage")
        self._tree_canvas_pending_changes = []
        self._tree_canvas_edit_source_id = None
        self._tree_canvas_selected_connector = None
        self._tree_canvas_pending_list = None
        self._tree_canvas_pinned_nodes = set()
        self._tree_canvas_selected_card_id = None
        self._tree_canvas_layout_name_var = tk.StringVar(value="default")
        self._tree_canvas_layout_type_var = tk.StringVar(value="hourglass")
        self._tree_canvas_horizontal_spacing_var = tk.StringVar(value=str(CARD_WIDTH // 4))
        self._tree_canvas_vertical_spacing_var = tk.StringVar(value=str(CARD_HEIGHT - 16))
        self._tree_canvas_card_width_var = tk.StringVar(value=str(CARD_WIDTH))
        self._tree_canvas_card_height_var = tk.StringVar(value=str(CARD_HEIGHT))
        self._tree_canvas_compact_var = tk.BooleanVar(value=False)
        self._tree_canvas_routing_var = tk.StringVar(value="orthogonal")
        _positions, pinned, _metadata = TreeCanvasService(self.repository).load_named_layout(center_id)
        self._tree_canvas_pinned_nodes = set(pinned)
        toolbar = tk.Frame(window)
        toolbar.pack(fill="x", padx=12, pady=(12, 6))
        tk.Button(toolbar, text="Назад", command=self._tree_canvas_back).pack(side="left")
        tk.Button(toolbar, text="Вперёд", command=self._tree_canvas_forward).pack(side="left", padx=(4, 0))
        tk.Label(toolbar, text="Режим").pack(side="left", padx=(14, 3))
        ttk.Combobox(
            toolbar, textvariable=self._tree_canvas_edit_mode_var,
            values=("Просмотр", "Редактирование"), state="readonly", width=16,
        ).pack(side="left")
        tk.Label(toolbar, text="Действие").pack(side="left", padx=(8, 3))
        ttk.Combobox(
            toolbar, textvariable=self._tree_canvas_edit_action_var,
            values=(
                "add_parent", "add_child", "add_spouse", "add_partner",
                "remove_relationship", "reassign_child", "replace_parent", "change_relationship_type",
            ),
            state="readonly", width=14,
        ).pack(side="left")
        ttk.Combobox(
            toolbar, textvariable=self._tree_canvas_relationship_type_var,
            values=RelationshipService.RELATIONSHIP_TYPES, state="readonly", width=14,
        ).pack(side="left", padx=(4, 0))
        tk.Label(toolbar, text="Предки").pack(side="left", padx=(14, 3))
        self._tree_canvas_ancestor_var = tk.StringVar(value="3")
        ttk.Combobox(toolbar, textvariable=self._tree_canvas_ancestor_var, values=tuple(str(value) for value in range(1, 9)), state="readonly", width=3).pack(side="left")
        tk.Label(toolbar, text="Потомки").pack(side="left", padx=(10, 3))
        self._tree_canvas_descendant_var = tk.StringVar(value="3")
        ttk.Combobox(toolbar, textvariable=self._tree_canvas_descendant_var, values=tuple(str(value) for value in range(1, 9)), state="readonly", width=3).pack(side="left")
        self._tree_canvas_mode_var = tk.StringVar(value="hourglass")
        ttk.Combobox(toolbar, textvariable=self._tree_canvas_mode_var, values=("top_to_bottom", "bottom_to_top", "left_to_right", "right_to_left", "ancestors_only", "descendants_only", "hourglass", "fan", "compact_family_groups"), state="readonly", width=18).pack(side="left", padx=(10, 0))
        tk.Button(toolbar, text="Обновить", command=self._load_tree_canvas).pack(side="left", padx=(8, 0))
        tk.Button(toolbar, text="Автораскладка", command=self._preview_tree_canvas_auto_layout).pack(side="left", padx=(8, 0))
        tk.Button(toolbar, text="Отменить раскладку", command=self._undo_tree_canvas_layout).pack(side="left", padx=(4, 0))
        tk.Button(toolbar, text="Повторить раскладку", command=self._redo_tree_canvas_layout).pack(side="left", padx=(4, 0))
        tk.Button(toolbar, text="Сбросить расположение", command=self._reset_tree_canvas_layout).pack(side="left", padx=(4, 0))
        tk.Button(toolbar, text="−", command=lambda: self._zoom_tree_canvas(-0.1)).pack(side="left", padx=(12, 0))
        tk.Button(toolbar, text="+", command=lambda: self._zoom_tree_canvas(0.1)).pack(side="left", padx=(4, 0))
        tk.Button(toolbar, text="Подогнать к окну", command=self._fit_tree_canvas).pack(side="left", padx=(8, 0))
        tk.Button(toolbar, text="Центр", command=self._center_tree_canvas).pack(side="left", padx=(4, 0))
        tk.Button(toolbar, text="Сохранить позиции", command=self._save_tree_canvas_positions).pack(side="left", padx=(8, 0))
        tk.Button(toolbar, text="SVG", command=lambda: self._export_tree_canvas("svg")).pack(side="left", padx=(12, 0))
        tk.Button(toolbar, text="PNG", command=lambda: self._export_tree_canvas("png")).pack(side="left", padx=(4, 0))
        tk.Button(toolbar, text="PDF", command=lambda: self._export_tree_canvas("pdf")).pack(side="left", padx=(4, 0))
        tk.Button(toolbar, text="Печать / Экспорт", command=self.open_tree_canvas_print_export).pack(side="left", padx=(8, 0))
        tk.Button(toolbar, text="JSON", command=self._export_tree_canvas_preview).pack(side="left", padx=(4, 0))
        self._tree_canvas_status = tk.Label(toolbar, text="")
        self._tree_canvas_status.pack(side="right")
        layout_controls = tk.Frame(window)
        layout_controls.pack(fill="x", padx=12, pady=(0, 6))
        tk.Label(layout_controls, text="Раскладка").pack(side="left")
        ttk.Entry(layout_controls, textvariable=self._tree_canvas_layout_name_var, width=14).pack(side="left", padx=(4, 8))
        ttk.Combobox(layout_controls, textvariable=self._tree_canvas_layout_type_var, values=("top_to_bottom", "bottom_to_top", "left_to_right", "right_to_left", "ancestors_only", "descendants_only", "hourglass", "fan", "compact_family_groups"), state="readonly", width=19).pack(side="left")
        for label, variable, width in (("H", self._tree_canvas_horizontal_spacing_var, 4), ("V", self._tree_canvas_vertical_spacing_var, 4), ("Ш", self._tree_canvas_card_width_var, 4), ("В", self._tree_canvas_card_height_var, 4)):
            tk.Label(layout_controls, text=label).pack(side="left", padx=(6, 2))
            ttk.Entry(layout_controls, textvariable=variable, width=width).pack(side="left")
        tk.Checkbutton(layout_controls, text="Компактно", variable=self._tree_canvas_compact_var).pack(side="left", padx=(8, 0))
        ttk.Combobox(layout_controls, textvariable=self._tree_canvas_routing_var, values=("orthogonal", "direct"), state="readonly", width=12).pack(side="left", padx=(6, 0))
        tk.Button(layout_controls, text="Закрепить карточку", command=self._pin_tree_canvas_card).pack(side="left", padx=(8, 0))
        tk.Button(layout_controls, text="Открепить карточку", command=self._unpin_tree_canvas_card).pack(side="left", padx=(4, 0))
        tk.Button(layout_controls, text="Открепить все", command=self._unpin_all_tree_canvas_cards).pack(side="left", padx=(4, 0))
        tk.Button(layout_controls, text="Сохранить", command=self._save_named_tree_canvas_layout).pack(side="right")
        tk.Button(layout_controls, text="Загрузить", command=self._load_named_tree_canvas_layout).pack(side="right", padx=(4, 0))
        tk.Button(layout_controls, text="Удалить", command=self._delete_named_tree_canvas_layout).pack(side="right", padx=(4, 0))
        tk.Button(layout_controls, text="Дублировать", command=self._duplicate_named_tree_canvas_layout).pack(side="right", padx=(4, 0))
        tk.Button(layout_controls, text="Переименовать", command=self._rename_named_tree_canvas_layout).pack(side="right", padx=(4, 0))
        tk.Button(layout_controls, text="Экспорт JSON", command=self._export_tree_canvas_layout_json).pack(side="right", padx=(4, 0))
        tk.Button(layout_controls, text="Импорт JSON", command=self._import_tree_canvas_layout_json).pack(side="right", padx=(4, 0))
        frame = tk.Frame(window)
        frame.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        canvas = tk.Canvas(frame, background="#f4f7f8", highlightthickness=0)
        self._tree_canvas = canvas
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=canvas.xview)
        vertical = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        canvas.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        canvas.grid(row=0, column=0, sticky="nsew")
        vertical.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        canvas.bind("<MouseWheel>", self._tree_canvas_mousewheel)
        canvas.bind("<ButtonPress-2>", self._start_tree_canvas_pan)
        canvas.bind("<B2-Motion>", self._pan_tree_canvas)
        pending = tk.Frame(window)
        pending.pack(fill="x", padx=12, pady=(0, 12))
        tk.Label(pending, text="Неподтверждённые изменения").pack(anchor="w")
        self._tree_canvas_pending_list = tk.Listbox(pending, height=3)
        self._tree_canvas_pending_list.pack(side="left", fill="x", expand=True, pady=(4, 0))
        controls = tk.Frame(pending)
        controls.pack(side="right", padx=(8, 0), pady=(4, 0))
        tk.Button(controls, text="Применить", command=self._preview_tree_canvas_changes).pack(fill="x")
        tk.Button(controls, text="Отменить", command=self._cancel_tree_canvas_change).pack(fill="x", pady=(3, 0))
        tk.Button(controls, text="Очистить все", command=self._clear_tree_canvas_changes).pack(fill="x", pady=(3, 0))
        self._load_tree_canvas()

    def _load_tree_canvas(self) -> None:
        if self._tree_canvas_navigation is None:
            return
        center_id = self._tree_canvas_navigation.current
        ancestor_depth = int(self._tree_canvas_ancestor_var.get())
        descendant_depth = int(self._tree_canvas_descendant_var.get())
        mode = self._tree_canvas_mode_var.get()
        collapsed = tuple(self._tree_canvas_collapsed_ids)

        def build(repository, context):
            cancel = context.raise_if_cancelled if context is not None else None
            progress = (lambda message, completed, total: context.report(message, completed, total)) if context is not None else None
            return TreeCanvasService(repository).build(
                center_id, ancestor_depth=ancestor_depth, descendant_depth=descendant_depth,
                mode=mode, collapsed_ids=collapsed, progress_callback=progress, cancel_callback=cancel,
            )

        return self._submit_repository_task(
            "Разметка интерактивного дерева", build, self._render_tree_canvas,
            on_error=lambda error: messagebox.showerror("Интерактивное полотно", str(error), parent=self._tree_canvas_window),
            cancellable=True,
        )

    def _render_tree_canvas(self, model) -> None:
        self._tree_canvas_model = model
        self._tree_canvas_positions = dict(model.positions)
        self._draw_tree_canvas()
        if self._tree_canvas_status is not None:
            self._tree_canvas_status.config(text=f"Узлов: {len(model.nodes)} | Масштаб: {self._tree_canvas_zoom:.2f}")
        self._tree_canvas.after_idle(self._center_tree_canvas)

    def _draw_tree_canvas(self) -> None:
        canvas, model = self._tree_canvas, self._tree_canvas_model
        if canvas is None or model is None:
            return
        canvas.delete("all")
        zoom = self._tree_canvas_zoom
        card_width, card_height = self._tree_canvas_card_dimensions()
        bounds = {}
        for connector in model.connectors:
            source, target = self._tree_canvas_positions.get(connector.source_id), self._tree_canvas_positions.get(connector.target_id)
            if source is None or target is None:
                continue
            if connector.kind == "parent":
                points = ((source[0] + card_width / 2) * zoom, (source[1] + card_height) * zoom, (source[0] + card_width / 2) * zoom, ((source[1] + card_height + target[1]) / 2) * zoom, (target[0] + card_width / 2) * zoom, ((source[1] + card_height + target[1]) / 2) * zoom, (target[0] + card_width / 2) * zoom, target[1] * zoom)
            else:
                points = ((source[0] + card_width) * zoom, (source[1] + card_height / 2) * zoom, target[0] * zoom, (target[1] + card_height / 2) * zoom)
            tag = f"tree-canvas-connector:{connector.key}"
            canvas.create_line(
                *points, fill="#8c4b19" if connector.special else "#5e7180", width=2,
                dash=(7, 4) if connector.special else (),
                arrow="last" if connector.kind == "parent" else "both", tags=(tag,),
            )
            canvas.tag_bind(tag, "<Button-1>", lambda _event, item=connector: self._select_tree_canvas_connector(item))
        for node in model.nodes:
            x, y = self._tree_canvas_positions[node.person_id]
            left, top = x * zoom, y * zoom
            right, bottom = left + card_width * zoom, top + card_height * zoom
            bounds[node.person_id] = (left, top, right, bottom)
            if "selected" in node.states:
                fill, outline = "#dceeff", "#146c94"
            elif "warning" in node.states:
                fill, outline = "#fff2cf", "#b67b00"
            elif "duplicate" in node.states:
                fill, outline = "#ffe1db", "#bf4f38"
            elif "unnamed" in node.states:
                fill, outline = "#e4e7ea", "#75808a"
            else:
                fill, outline = "white", "#687681"
            tag = f"tree-canvas-person:{node.person_id}"
            canvas.create_rectangle(left, top, right, bottom, fill=fill, outline=outline, width=3 if "selected" in node.states else 2, tags=(tag,))
            for index, text in enumerate((node.full_name, f"{node.birth_year or '-'} — {node.death_year or '-'}", f"ID {node.person_id} | {node.gedcom_id or '-'}", node.role + (" [+]" if node.collapsed else ""))):
                canvas.create_text(left + 9 * zoom, top + (13 + index * 20) * zoom, text=text, anchor="nw", tags=(tag,), font=("Segoe UI", max(7, round((10 if index == 0 else 8) * zoom)), "bold" if index == 0 else "normal"))
            canvas.tag_bind(tag, "<ButtonPress-1>", lambda event, person_id=node.person_id: self._start_tree_canvas_drag(event, person_id))
            canvas.tag_bind(tag, "<B1-Motion>", self._drag_tree_canvas_card)
            canvas.tag_bind(tag, "<ButtonRelease-1>", self._finish_tree_canvas_drag)
            canvas.tag_bind(tag, "<Double-1>", lambda _event, person_id=node.person_id: self._visit_tree_canvas_person(person_id))
            canvas.tag_bind(tag, "<Button-3>", lambda event, person_id=node.person_id: self._tree_canvas_context_menu(event, person_id))
        self._tree_canvas_bounds = bounds
        maximum_x = max((position[0] for position in self._tree_canvas_positions.values()), default=800) + card_width + 100
        maximum_y = max((position[1] for position in self._tree_canvas_positions.values()), default=600) + card_height + 100
        canvas.configure(scrollregion=(0, 0, maximum_x * zoom, maximum_y * zoom))

    def _select_tree_canvas_person(self, person_id) -> None:
        if self._tree_canvas_edit_source_id is None:
            self._tree_canvas_edit_source_id = person_id
            self._tree_canvas_status.config(text=f"Источник изменения: ID {person_id}")
            return
        if person_id == self._tree_canvas_edit_source_id:
            self._tree_canvas_edit_source_id = None
            self._tree_canvas_status.config(text="Источник изменения снят")
            return
        change = TreeCanvasChange(
            self._tree_canvas_edit_action_var.get(), self._tree_canvas_edit_source_id, person_id,
            family_id=(self._tree_canvas_selected_connector.family_id if self._tree_canvas_selected_connector else 0),
            old_parent_id=(self._tree_canvas_selected_connector.source_id if self._tree_canvas_selected_connector else 0),
            relationship_type=self._tree_canvas_relationship_type_var.get(),
        )
        self._tree_canvas_pending_changes.append(change)
        self._tree_canvas_edit_source_id = None
        self._refresh_tree_canvas_pending_changes()

    def _select_tree_canvas_connector(self, connector) -> None:
        self._tree_canvas_selected_connector = connector
        self._tree_canvas_status.config(
            text=(f"Связь: {connector.relationship_type} | семья ID {connector.family_id} | "
                  f"{connector.source_id} -> {connector.target_id}")
        )

    def _tree_canvas_context_menu(self, event, person_id) -> None:
        menu = tk.Menu(self._tree_canvas_window, tearoff=0)
        menu.add_command(label="Добавить родителя", command=lambda: self._begin_tree_canvas_context_change(person_id, "add_parent"))
        menu.add_command(label="Добавить ребёнка", command=lambda: self._begin_tree_canvas_context_change(person_id, "add_child"))
        menu.add_command(label="Добавить супруга", command=lambda: self._begin_tree_canvas_context_change(person_id, "add_spouse"))
        menu.add_command(label="Добавить партнёра", command=lambda: self._begin_tree_canvas_context_change(person_id, "add_partner"))
        menu.add_command(label="Удалить связь", command=self._queue_tree_canvas_connector_removal)
        menu.add_command(label="Открыть карточку", command=lambda: self.show_person(person_id))
        menu.tk_popup(event.x_root, event.y_root)

    def _begin_tree_canvas_context_change(self, person_id, action) -> None:
        self._tree_canvas_edit_mode_var.set("Редактирование")
        self._tree_canvas_edit_action_var.set(action)
        self._tree_canvas_edit_source_id = person_id
        self._tree_canvas_status.config(text=f"Источник ID {person_id}; выберите целевую карточку")

    def _queue_tree_canvas_connector_removal(self) -> None:
        connector = self._tree_canvas_selected_connector
        if connector is None:
            messagebox.showwarning("Интерактивное полотно", "Сначала выберите линию связи.", parent=self._tree_canvas_window)
            return
        self._tree_canvas_pending_changes.append(TreeCanvasChange(
            "remove_relationship", connector.source_id, connector.target_id, connector.family_id,
        ))
        self._refresh_tree_canvas_pending_changes()

    def _refresh_tree_canvas_pending_changes(self) -> None:
        pending = self._tree_canvas_pending_list
        if pending is not None:
            pending.delete(0, tk.END)
            for change in self._tree_canvas_pending_changes:
                pending.insert("end", f"{change.kind}: ID {change.source_id} -> ID {change.target_id}")
        if self._tree_canvas_status is not None:
            self._tree_canvas_status.config(text=f"Неподтверждённых изменений: {len(self._tree_canvas_pending_changes)}")

    def _cancel_tree_canvas_change(self) -> None:
        if self._tree_canvas_pending_changes:
            self._tree_canvas_pending_changes.pop()
        self._tree_canvas_edit_source_id = None
        self._refresh_tree_canvas_pending_changes()

    def _clear_tree_canvas_changes(self) -> None:
        self._tree_canvas_pending_changes.clear()
        self._tree_canvas_edit_source_id = None
        self._refresh_tree_canvas_pending_changes()

    def _preview_tree_canvas_changes(self) -> None:
        changes = tuple(self._tree_canvas_pending_changes)
        if not changes:
            return
        return self._submit_repository_task(
            "Проверка изменений полотна", lambda repository, _context: TreeCanvasService(repository).preview_changes(changes),
            self._show_tree_canvas_preview,
            on_error=lambda error: messagebox.showerror("Интерактивное полотно", str(error), parent=self._tree_canvas_window),
            cancellable=True,
        )

    def _show_tree_canvas_preview(self, preview) -> None:
        details = [
            "Операции:", *[f"- {change.kind}: {change.source_id} -> {change.target_id}" for change in preview.changes],
            "Добавляется:", *[f"- {item}" for item in preview.links_to_create],
            "Удаляется:", *[f"- {item}" for item in preview.links_to_remove],
        ]
        if preview.warnings:
            details.extend(("Предупреждения:", *[f"- {item}" for item in preview.warnings]))
        if preview.blockers:
            details.extend(("Блокировки:", *[f"- {item}" for item in preview.blockers]))
            messagebox.showwarning("Предпросмотр изменений", "\n".join(details), parent=self._tree_canvas_window)
            return
        if messagebox.askyesno("Предпросмотр изменений", "\n".join(details) + "\n\nПодтвердить изменения?", parent=self._tree_canvas_window):
            self._execute_tree_canvas_preview(preview)

    def _execute_tree_canvas_preview(self, preview) -> None:
        return self._submit_repository_task(
            "Применение изменений полотна", lambda repository, _context: TreeCanvasService(repository).execute_changes(preview),
            self._complete_tree_canvas_changes,
            on_error=lambda error: messagebox.showerror("Интерактивное полотно", str(error), parent=self._tree_canvas_window),
        )

    def _complete_tree_canvas_changes(self, result) -> None:
        self._get_undo_manager().record_applied(
            AppliedDeltaCommand("Изменения интерактивного полотна", self.repository, result.delta, result)
        )
        self._clear_tree_canvas_changes()
        self._load_tree_canvas()

    def _export_tree_canvas_preview(self) -> None:
        if not self._tree_canvas_pending_changes:
            return
        destination = filedialog.asksaveasfilename(
            parent=self._tree_canvas_window, title="Экспорт предпросмотра", initialdir=str(EXPORT_DIR),
            initialfile="tree_canvas_preview.json", defaultextension=".json", filetypes=[("JSON", "*.json")],
        )
        if not destination:
            return
        changes = tuple(self._tree_canvas_pending_changes)
        return self._submit_repository_task(
            "Экспорт предпросмотра полотна",
            lambda repository, _context: TreeCanvasService(repository).export_preview_json(
                TreeCanvasService(repository).preview_changes(changes), destination,
            ), lambda _path: None,
            on_error=lambda error: messagebox.showerror("Экспорт предпросмотра", str(error), parent=self._tree_canvas_window),
        )

    def _tree_canvas_layout_options(self):
        return TreeLayoutOptions(
            layout_type=self._tree_canvas_layout_type_var.get(),
            horizontal_spacing=float(self._tree_canvas_horizontal_spacing_var.get()),
            vertical_spacing=float(self._tree_canvas_vertical_spacing_var.get()),
            card_width=float(self._tree_canvas_card_width_var.get()),
            card_height=float(self._tree_canvas_card_height_var.get()),
            compact=bool(self._tree_canvas_compact_var.get()),
            line_routing=self._tree_canvas_routing_var.get(),
        )

    def _tree_canvas_card_dimensions(self):
        try:
            return max(40.0, float(self._tree_canvas_card_width_var.get())), max(30.0, float(self._tree_canvas_card_height_var.get()))
        except (AttributeError, TypeError, ValueError):
            return float(CARD_WIDTH), float(CARD_HEIGHT)

    def _preview_tree_canvas_auto_layout(self) -> None:
        if self._tree_canvas_model is None:
            return
        try:
            options = self._tree_canvas_layout_options()
        except ValueError:
            messagebox.showwarning("Автораскладка", "Параметры расстояний и карточек должны быть числами.", parent=self._tree_canvas_window)
            return
        model, positions, pinned = self._tree_canvas_model, dict(self._tree_canvas_positions), frozenset(self._tree_canvas_pinned_nodes)

        def preview(repository, context):
            cancel = context.raise_if_cancelled if context is not None else None
            progress = (lambda message, completed, total: context.report(message, completed, total)) if context is not None else None
            return TreeCanvasService(repository).preview_auto_layout(
                model, positions=positions, pinned_nodes=pinned, options=options,
                progress_callback=progress, cancel_callback=cancel,
            )

        return self._submit_repository_task(
            "Предпросмотр автораскладки", preview, self._show_tree_canvas_layout_preview,
            on_error=lambda error: messagebox.showerror("Автораскладка", str(error), parent=self._tree_canvas_window),
            cancellable=True,
        )

    def _show_tree_canvas_layout_preview(self, preview) -> None:
        details = (
            f"Перемещено карточек: {preview.moved_node_count}\n"
            f"Пересечений карточек: {preview.overlap_count}\n"
            f"Оценка пересечений линий: {preview.edge_crossing_count}\n\n"
            "Применить раскладку?"
        )
        if messagebox.askyesno("Предпросмотр автораскладки", details, parent=self._tree_canvas_window):
            self._apply_tree_canvas_auto_layout(preview)

    def _apply_tree_canvas_auto_layout(self, preview) -> None:
        model = self._tree_canvas_model
        name = self._tree_canvas_layout_name_var.get()
        return self._submit_repository_task(
            "Применение автораскладки",
            lambda repository, _context: TreeCanvasService(repository).apply_auto_layout(
                model, preview, name=name, scale=self._tree_canvas_zoom,
            ), self._complete_tree_canvas_auto_layout,
            on_error=lambda error: messagebox.showerror("Автораскладка", str(error), parent=self._tree_canvas_window),
        )

    def _complete_tree_canvas_auto_layout(self, result) -> None:
        self._get_undo_manager().record_applied(TreeCanvasLayoutCommand(result))
        self._tree_canvas_positions = dict(result.preview.positions)
        self._tree_canvas_pinned_nodes = set(result.preview.pinned_nodes)
        self._draw_tree_canvas()
        self._tree_canvas_status.config(text=f"Автораскладка: {result.preview.moved_node_count} карточек")

    def _reload_tree_canvas_named_layout(self) -> None:
        if self._tree_canvas_model is None:
            return
        positions, pinned, _metadata = TreeCanvasService(self.repository).load_named_layout(
            self._tree_canvas_model.center_id, self._tree_canvas_layout_name_var.get(),
            {node.person_id for node in self._tree_canvas_model.nodes},
        )
        if positions:
            self._tree_canvas_positions = positions
        self._tree_canvas_pinned_nodes = set(pinned)
        self._draw_tree_canvas()

    def _undo_tree_canvas_layout(self) -> None:
        if self._get_undo_manager().undo():
            self._reload_tree_canvas_named_layout()

    def _redo_tree_canvas_layout(self) -> None:
        if self._get_undo_manager().redo():
            self._reload_tree_canvas_named_layout()

    def _reset_tree_canvas_layout(self) -> None:
        if self._tree_canvas_model is None:
            return
        TreeCanvasService(self.repository).delete_named_layout(
            self._tree_canvas_model.center_id, self._tree_canvas_layout_name_var.get(),
        )
        self._tree_canvas_pinned_nodes.clear()
        self._load_tree_canvas()

    def _pin_tree_canvas_card(self) -> None:
        if self._tree_canvas_selected_card_id is not None:
            self._tree_canvas_pinned_nodes.add(self._tree_canvas_selected_card_id)
            self._save_named_tree_canvas_layout()

    def _unpin_tree_canvas_card(self) -> None:
        if self._tree_canvas_selected_card_id is not None:
            self._tree_canvas_pinned_nodes.discard(self._tree_canvas_selected_card_id)
            self._save_named_tree_canvas_layout()

    def _unpin_all_tree_canvas_cards(self) -> None:
        self._tree_canvas_pinned_nodes.clear()
        self._save_named_tree_canvas_layout()

    def _save_named_tree_canvas_layout(self) -> None:
        if self._tree_canvas_model is None:
            return
        try:
            TreeCanvasService(self.repository).save_named_layout(
                self._tree_canvas_layout_name_var.get(), self._tree_canvas_model,
                self._tree_canvas_positions, pinned_nodes=self._tree_canvas_pinned_nodes,
                options=self._tree_canvas_layout_options(), scale=self._tree_canvas_zoom,
            )
            self._tree_canvas_status.config(text="Раскладка сохранена")
        except (ValueError, OSError) as error:
            messagebox.showerror("Раскладка", str(error), parent=self._tree_canvas_window)

    def _load_named_tree_canvas_layout(self) -> None:
        self._reload_tree_canvas_named_layout()

    def _delete_named_tree_canvas_layout(self) -> None:
        if self._tree_canvas_model is not None:
            TreeCanvasService(self.repository).delete_named_layout(self._tree_canvas_model.center_id, self._tree_canvas_layout_name_var.get())
            self._tree_canvas_pinned_nodes.clear()
            self._load_tree_canvas()

    def _rename_named_tree_canvas_layout(self) -> None:
        if self._tree_canvas_model is None:
            return
        old_name = self._tree_canvas_layout_name_var.get()
        new_name = simpledialog.askstring("Переименовать раскладку", "Новое имя:", parent=self._tree_canvas_window)
        if new_name:
            TreeCanvasService(self.repository).rename_named_layout(self._tree_canvas_model.center_id, old_name, new_name)
            self._tree_canvas_layout_name_var.set(new_name)

    def _duplicate_named_tree_canvas_layout(self) -> None:
        if self._tree_canvas_model is None:
            return
        source_name = self._tree_canvas_layout_name_var.get()
        target_name = simpledialog.askstring("Дублировать раскладку", "Имя копии:", parent=self._tree_canvas_window)
        if target_name:
            TreeCanvasService(self.repository).duplicate_named_layout(self._tree_canvas_model.center_id, source_name, target_name)
            self._tree_canvas_layout_name_var.set(target_name)

    def _export_tree_canvas_layout_json(self) -> None:
        if self._tree_canvas_model is None:
            return
        destination = filedialog.asksaveasfilename(parent=self._tree_canvas_window, title="Экспорт раскладки", initialdir=str(EXPORT_DIR), defaultextension=".json", filetypes=[("JSON", "*.json")])
        if destination:
            TreeCanvasService(self.repository).export_layout_configuration(self._tree_canvas_model.center_id, self._tree_canvas_layout_name_var.get(), destination)

    def _import_tree_canvas_layout_json(self) -> None:
        if self._tree_canvas_model is None:
            return
        source = filedialog.askopenfilename(parent=self._tree_canvas_window, title="Импорт раскладки", filetypes=[("JSON", "*.json")])
        if source:
            TreeCanvasService(self.repository).import_layout_configuration(self._tree_canvas_layout_name_var.get(), source, center_id=self._tree_canvas_model.center_id)
            self._reload_tree_canvas_named_layout()

    def _start_tree_canvas_drag(self, event, person_id) -> None:
        self._tree_canvas_selected_card_id = person_id
        if self._tree_canvas_edit_mode_var.get() == "Редактирование":
            self._select_tree_canvas_person(person_id)
            return
        x, y = self._tree_canvas.canvasx(event.x), self._tree_canvas.canvasy(event.y)
        position = self._tree_canvas_positions[person_id]
        self._tree_canvas_drag = (person_id, x / self._tree_canvas_zoom - position[0], y / self._tree_canvas_zoom - position[1])

    def _drag_tree_canvas_card(self, event) -> None:
        if not self._tree_canvas_drag:
            return
        person_id, offset_x, offset_y = self._tree_canvas_drag
        self._tree_canvas_positions[person_id] = (self._tree_canvas.canvasx(event.x) / self._tree_canvas_zoom - offset_x, self._tree_canvas.canvasy(event.y) / self._tree_canvas_zoom - offset_y)
        self._draw_tree_canvas()

    def _finish_tree_canvas_drag(self, _event=None) -> None:
        self._tree_canvas_drag = None

    def _start_tree_canvas_pan(self, event) -> None:
        self._tree_canvas.scan_mark(event.x, event.y)

    def _pan_tree_canvas(self, event) -> None:
        self._tree_canvas.scan_dragto(event.x, event.y, gain=1)

    def _tree_canvas_mousewheel(self, event) -> None:
        self._zoom_tree_canvas(0.1 if event.delta > 0 else -0.1)

    def _zoom_tree_canvas(self, change) -> None:
        self._tree_canvas_zoom = max(TREE_CANVAS_MIN_ZOOM, min(TREE_CANVAS_MAX_ZOOM, round(self._tree_canvas_zoom + change, 2)))
        self._draw_tree_canvas()

    def _fit_tree_canvas(self) -> None:
        if not self._tree_canvas_positions:
            return
        card_width, card_height = self._tree_canvas_card_dimensions()
        width = max(x for x, _y in self._tree_canvas_positions.values()) + card_width + 100
        height = max(y for _x, y in self._tree_canvas_positions.values()) + card_height + 100
        available_width = max(1, self._tree_canvas.winfo_width())
        available_height = max(1, self._tree_canvas.winfo_height())
        self._tree_canvas_zoom = max(TREE_CANVAS_MIN_ZOOM, min(TREE_CANVAS_MAX_ZOOM, round(min(available_width / width, available_height / height), 2)))
        self._draw_tree_canvas()

    def _center_tree_canvas(self) -> None:
        center_id = self._tree_canvas_navigation.current if self._tree_canvas_navigation else None
        if center_id not in self._tree_canvas_positions:
            return
        x, y = self._tree_canvas_positions[center_id]
        canvas = self._tree_canvas
        canvas.xview_moveto(max(0, (x * self._tree_canvas_zoom - canvas.winfo_width() / 2) / max(1, canvas.bbox("all")[2])))
        canvas.yview_moveto(max(0, (y * self._tree_canvas_zoom - canvas.winfo_height() / 2) / max(1, canvas.bbox("all")[3])))

    def _toggle_tree_canvas_branch(self, person_id) -> None:
        if person_id in self._tree_canvas_collapsed_ids:
            self._tree_canvas_collapsed_ids.remove(person_id)
        else:
            self._tree_canvas_collapsed_ids.add(person_id)
        self._load_tree_canvas()

    def _visit_tree_canvas_person(self, person_id) -> None:
        if hasattr(self, "workspace_integration_service"):
            self.workspace_integration_service.select_person(person_id, "tree")
        self._tree_canvas_navigation.visit(person_id)
        self._tree_canvas_collapsed_ids.clear()
        self._load_tree_canvas()

    def _tree_canvas_back(self) -> None:
        self._tree_canvas_navigation.back()
        self._tree_canvas_collapsed_ids.clear()
        self._load_tree_canvas()

    def _tree_canvas_forward(self) -> None:
        self._tree_canvas_navigation.forward()
        self._tree_canvas_collapsed_ids.clear()
        self._load_tree_canvas()

    def _save_tree_canvas_positions(self) -> None:
        if self._tree_canvas_model is not None:
            TreeCanvasService(self.repository).save_positions(self._tree_canvas_model.center_id, self._tree_canvas_positions)
            self._tree_canvas_status.config(text="Позиции сохранены")

    def _export_tree_canvas(self, export_format) -> None:
        if self._tree_canvas_model is None:
            return
        destination = filedialog.asksaveasfilename(parent=self._tree_canvas_window, title="Экспорт полотна дерева", initialdir=str(EXPORT_DIR), initialfile=f"tree_canvas.{export_format}", defaultextension=f".{export_format}", filetypes=[(export_format.upper(), f"*.{export_format}")])
        if not destination:
            return
        model = self._tree_canvas_model.__class__(**{**self._tree_canvas_model.__dict__, "positions": dict(self._tree_canvas_positions)})
        return self._submit_repository_task("Экспорт полотна дерева", lambda repository, _context: getattr(TreeCanvasService(repository), f"export_{export_format}")(model, destination, scale=self._tree_canvas_zoom), lambda _path: None, on_error=lambda error: messagebox.showerror("Экспорт полотна", str(error), parent=self._tree_canvas_window))

    def open_tree_canvas_print_export(self) -> None:
        if self._tree_canvas_model is None:
            return
        if self._tree_canvas_print_window is not None:
            try:
                self._tree_canvas_print_window.lift()
                return
            except Exception:
                self._tree_canvas_print_window = None
        window = self._create_dialog(self._tree_canvas_window)
        self._tree_canvas_print_window = window
        window.title("Печать / Экспорт")
        window.geometry("620x440")
        window.protocol("WM_DELETE_WINDOW", self._close_tree_canvas_print_export)
        self._tree_canvas_print_vars = {
            "scope": tk.StringVar(value="current_view"), "format": tk.StringVar(value="pdf"),
            "orientation": tk.StringVar(value="landscape"), "fit": tk.StringVar(value="fit_page"),
            "margin": tk.StringVar(value="24"), "scale": tk.StringVar(value="1.0"),
            "overlap": tk.StringVar(value="18"), "title": tk.StringVar(value="GenealogyDB Tree"),
            "poster": tk.BooleanVar(value=False), "legend": tk.BooleanVar(value=True),
            "generations": tk.BooleanVar(value=True), "dpi": tk.StringVar(value="300"),
        }
        fields = tk.Frame(window)
        fields.pack(fill="x", padx=12, pady=12)
        for row, (label, key, values) in enumerate((
            ("Область", "scope", ("current_view", "selected_branch", "complete_tree")),
            ("Формат", "format", ("pdf", "svg", "png", "jpeg")),
            ("Ориентация", "orientation", ("portrait", "landscape")),
            ("Масштаб", "fit", ("manual", "fit_width", "fit_page")),
        )):
            tk.Label(fields, text=label).grid(row=row, column=0, sticky="w", pady=3)
            ttk.Combobox(fields, textvariable=self._tree_canvas_print_vars[key], values=values, state="readonly", width=20).grid(row=row, column=1, sticky="w", pady=3)
        for row, (label, key) in enumerate((("Поля", "margin"), ("Коэффициент", "scale"), ("Перекрытие", "overlap"), ("DPI", "dpi")), start=0):
            tk.Label(fields, text=label).grid(row=row, column=2, sticky="w", padx=(24, 3), pady=3)
            ttk.Entry(fields, textvariable=self._tree_canvas_print_vars[key], width=8).grid(row=row, column=3, sticky="w", pady=3)
        tk.Label(fields, text="Заголовок").grid(row=4, column=0, sticky="w", pady=3)
        ttk.Entry(fields, textvariable=self._tree_canvas_print_vars["title"], width=44).grid(row=4, column=1, columnspan=3, sticky="ew", pady=3)
        for index, (label, key) in enumerate((("Постер", "poster"), ("Легенда", "legend"), ("Подписи поколений", "generations"))):
            tk.Checkbutton(fields, text=label, variable=self._tree_canvas_print_vars[key]).grid(row=5, column=index, sticky="w", pady=8)
        self._tree_canvas_print_summary = tk.Label(window, text="Настройте параметры и создайте предпросмотр.", justify="left", anchor="w")
        self._tree_canvas_print_summary.pack(fill="x", padx=12, pady=(4, 12))
        actions = tk.Frame(window)
        actions.pack(fill="x", padx=12, pady=(0, 12))
        tk.Button(actions, text="Предпросмотр", command=self._preview_tree_canvas_print_export).pack(side="left")
        tk.Button(actions, text="Экспорт", command=self._export_tree_canvas_from_center).pack(side="right")
        tk.Button(actions, text="Печать PDF", command=self._print_tree_canvas_from_center).pack(side="right", padx=(0, 6))

    def _tree_canvas_print_options(self):
        values = self._tree_canvas_print_vars
        return TreeCanvasPrintOptions(
            scope=values["scope"].get(), orientation=values["orientation"].get(),
            margin=float(values["margin"].get()), scale=float(values["scale"].get()),
            fit_mode=values["fit"].get(), poster=bool(values["poster"].get()),
            overlap=float(values["overlap"].get()), title=values["title"].get().strip() or "GenealogyDB Tree",
            include_legend=bool(values["legend"].get()), include_generation_labels=bool(values["generations"].get()),
            dpi=max(72, int(values["dpi"].get())),
        )

    def _preview_tree_canvas_print_export(self) -> None:
        try:
            options = self._tree_canvas_print_options()
        except ValueError:
            messagebox.showwarning("Печать / Экспорт", "Поля, масштаб, перекрытие и DPI должны быть числами.", parent=self._tree_canvas_print_window)
            return
        model = self._tree_canvas_model.__class__(**{**self._tree_canvas_model.__dict__, "positions": dict(self._tree_canvas_positions)})
        return self._submit_repository_task(
            "Предпросмотр печати", lambda repository, _context: TreeCanvasService(repository).prepare_print_preview(model, options),
            self._show_tree_canvas_print_preview,
            on_error=lambda error: messagebox.showerror("Печать / Экспорт", str(error), parent=self._tree_canvas_print_window),
        )

    def _show_tree_canvas_print_preview(self, preview) -> None:
        self._tree_canvas_print_preview = preview
        self._tree_canvas_print_summary.config(text=(
            f"Страниц: {preview.page_count} ({preview.page_columns} x {preview.page_rows})\n"
            f"Людей: {preview.metadata['number_of_people']} | Семей: {preview.metadata['number_of_families']}\n"
            f"Корень: {preview.metadata['root_person']} | Глубина: {preview.metadata['generation_depth']}"
        ))

    def _export_tree_canvas_from_center(self) -> None:
        if self._tree_canvas_print_preview is None:
            self._preview_tree_canvas_print_export()
            return
        export_format = self._tree_canvas_print_vars["format"].get()
        destination = filedialog.asksaveasfilename(parent=self._tree_canvas_print_window, title="Экспорт полотна", initialdir=str(EXPORT_DIR), initialfile=f"tree_canvas.{export_format}", defaultextension=f".{export_format}", filetypes=[(export_format.upper(), f"*.{export_format}")])
        if destination:
            self._submit_tree_canvas_export(self._tree_canvas_print_preview, destination, export_format)

    def _print_tree_canvas_from_center(self) -> None:
        if self._tree_canvas_print_preview is None:
            self._preview_tree_canvas_print_export()
            return
        destination = filedialog.asksaveasfilename(parent=self._tree_canvas_print_window, title="Печать полотна", initialdir=str(EXPORT_DIR), initialfile="tree_canvas_print.pdf", defaultextension=".pdf", filetypes=[("PDF", "*.pdf")])
        if destination:
            self._submit_tree_canvas_export(self._tree_canvas_print_preview, destination, "pdf")

    def _submit_tree_canvas_export(self, preview, destination, export_format) -> None:
        return self._submit_repository_task(
            "Печать / Экспорт полотна", lambda repository, _context: TreeCanvasService(repository).export_canvas(preview, destination, export_format),
            lambda _path: None,
            on_error=lambda error: messagebox.showerror("Печать / Экспорт", str(error), parent=self._tree_canvas_print_window),
        )

    def _close_tree_canvas_print_export(self) -> None:
        if self._tree_canvas_print_window is not None:
            try:
                self._tree_canvas_print_window.destroy()
            except Exception:
                pass
        self._tree_canvas_print_window = None
        self._tree_canvas_print_preview = None

    def _close_tree_canvas(self) -> None:
        if self._tree_canvas_pending_changes and not messagebox.askyesno(
            "Интерактивное полотно", "Отменить неподтверждённые изменения?", parent=self._tree_canvas_window,
        ):
            return
        if self._tree_canvas_window is not None:
            try:
                self._tree_canvas_window.destroy()
            except Exception:
                pass
        self._tree_canvas_window = None
        self._tree_canvas = None
        self._tree_canvas_model = None
        self._tree_canvas_navigation = None
        self._tree_canvas_pending_changes = []
        self._tree_canvas_edit_source_id = None

    def _load_graph_editor(self):
        return self._submit_repository_task(
            "Загрузка редактора дерева",
            lambda repository, _context: GraphEditorService(repository).build_graph(),
            self._render_graph_editor,
            on_error=lambda error: messagebox.showerror(
                "Редактор дерева", str(error), parent=self._graph_editor_window
            ),
        )

    def _render_graph_editor(self, model):
        self._graph_editor_model = model
        node_ids = {node.person_id for node in model.nodes}
        self._graph_editor_positions = {
            person_id: position for person_id, position in self._graph_editor_positions.items()
            if person_id in node_ids
        }
        if len(self._graph_editor_positions) != len(model.nodes):
            self._graph_editor_positions.update(self._graph_editor_auto_layout(model))
        self._draw_graph_editor()
        counts = Counter(issue.kind for issue in model.issues)
        summary = " | ".join(f"{kind}: {count}" for kind, count in sorted(counts.items()))
        self._graph_editor_status.configure(text=summary or "Связи корректны")
        if self._graph_editor_selected_person_id in node_ids:
            self._graph_editor_canvas.after_idle(self._center_graph_editor_selected)

    @staticmethod
    def _graph_editor_auto_layout(model):
        parent_edges = [edge for edge in model.edges if edge.kind == "parent"]
        incoming = Counter(edge.target_id for edge in parent_edges)
        children = defaultdict(set)
        for edge in parent_edges:
            children[edge.source_id].add(edge.target_id)
        roots = [node.person_id for node in model.nodes if not incoming[node.person_id]]
        levels = {person_id: 0 for person_id in roots}
        pending = list(roots)
        while pending:
            parent = pending.pop(0)
            for child in children[parent]:
                proposed = levels[parent] + 1
                if proposed > levels.get(child, -1):
                    levels[child] = proposed
                    pending.append(child)
        for node in model.nodes:
            levels.setdefault(node.person_id, 0)
        rows = defaultdict(list)
        for node in model.nodes:
            rows[levels[node.person_id]].append(node.person_id)
        positions = {}
        for level, person_ids in sorted(rows.items()):
            for index, person_id in enumerate(sorted(person_ids)):
                positions[person_id] = (100 + index * 230, 90 + level * 190)
        return positions

    def _draw_graph_editor(self):
        canvas = self._graph_editor_canvas
        model = self._graph_editor_model
        if canvas is None or model is None:
            return
        canvas.delete("all")
        zoom = self._graph_editor_zoom
        card_width = 180 * zoom
        card_height = 98 * zoom
        issue_nodes = defaultdict(set)
        issue_edges = defaultdict(set)
        for issue in model.issues:
            for person_id in issue.node_ids:
                issue_nodes[person_id].add(issue.kind)
            for edge_key in issue.edge_keys:
                issue_edges[edge_key].add(issue.kind)
        self._graph_editor_card_bounds = {}
        self._graph_editor_edge_items = {}
        for edge in model.edges:
            if edge.source_id not in self._graph_editor_positions or edge.target_id not in self._graph_editor_positions:
                continue
            source = self._graph_editor_positions[edge.source_id]
            target = self._graph_editor_positions[edge.target_id]
            source_x = (source[0] + 90) * zoom
            target_x = (target[0] + 90) * zoom
            if edge.kind == "parent":
                source_y = (source[1] + 98) * zoom
                target_y = target[1] * zoom
                middle_y = (source_y + target_y) / 2
                points = (source_x, source_y, source_x, middle_y, target_x, middle_y, target_x, target_y)
            else:
                source_y = (source[1] + 49) * zoom
                target_y = (target[1] + 49) * zoom
                middle_x = (source_x + target_x) / 2
                points = (source_x, source_y, middle_x, source_y, middle_x, target_y, target_x, target_y)
            color = self._graph_editor_issue_color(issue_edges.get(edge.key, set()), default="#687681")
            item = canvas.create_line(
                *points, fill=color, width=4 if edge.key == self._graph_editor_selected_edge else 2,
                arrow="last" if edge.kind == "parent" else "both",
                tags=(f"graph-edge:{edge.key}", "graph-edge"),
            )
            self._graph_editor_edge_items[edge.key] = item
            canvas.tag_bind(
                f"graph-edge:{edge.key}", "<Button-1>",
                lambda _event, key=edge.key: self._select_graph_editor_edge(key),
            )
            canvas.tag_bind(
                f"graph-edge:{edge.key}", "<Button-3>",
                lambda event, key=edge.key: self._open_graph_context_menu(event, edge_key=key),
            )
        for node in model.nodes:
            left, top = self._graph_editor_positions[node.person_id]
            left *= zoom
            top *= zoom
            right = left + card_width
            bottom = top + card_height
            self._graph_editor_card_bounds[node.person_id] = (left, top, right, bottom)
            issues = issue_nodes.get(node.person_id, set())
            outline = self._graph_editor_issue_color(issues, default="#687681")
            fill = "#dceeff" if node.person_id == self._graph_editor_selected_person_id else "white"
            tag = f"graph-person:{node.person_id}"
            canvas.create_rectangle(
                left, top, right, bottom, fill=fill, outline=outline,
                width=4 if issues or node.person_id == self._graph_editor_selected_person_id else 2,
                tags=(tag, "graph-person"),
            )
            lines = (
                node.full_name,
                f"ID {node.person_id} | {node.gedcom_id or '-'}",
                f"{node.birth_date or '-'} — {node.death_date or '-'}",
                ", ".join(sorted(issues)) or "ok",
            )
            for index, text in enumerate(lines):
                canvas.create_text(
                    left + 9 * zoom, top + (12 + index * 21) * zoom,
                    text=text, anchor="nw", tags=(tag,),
                    font=("Segoe UI", max(7, round((10 if index == 0 else 8) * zoom)), "bold" if index == 0 else "normal"),
                )
            canvas.tag_bind(tag, "<ButtonPress-1>", lambda event, value=node.person_id: self._start_graph_card_drag(event, value))
            canvas.tag_bind(tag, "<B1-Motion>", self._drag_graph_card)
            canvas.tag_bind(tag, "<ButtonRelease-1>", self._finish_graph_card_drag)
            canvas.tag_bind(tag, "<Double-1>", lambda _event, value=node.person_id: self.show_person(value))
            canvas.tag_bind(tag, "<Button-3>", lambda event, value=node.person_id: self._open_graph_context_menu(event, person_id=value))
        max_x = max((value[0] for value in self._graph_editor_positions.values()), default=900) + 320
        max_y = max((value[1] for value in self._graph_editor_positions.values()), default=600) + 240
        canvas.configure(scrollregion=(0, 0, max_x * zoom, max_y * zoom))

    @staticmethod
    def _graph_editor_issue_color(kinds, default):
        priority = (
            ("cycle", "#c62828"), ("invalid", "#ad1457"),
            ("duplicate", "#ef6c00"), ("orphan", "#7b8790"),
        )
        return next((color for kind, color in priority if kind in kinds), default)

    def _start_graph_card_drag(self, event, person_id):
        self._graph_editor_selected_person_id = int(person_id)
        canvas = self._graph_editor_canvas
        x = canvas.canvasx(event.x)
        y = canvas.canvasy(event.y)
        mode = self._graph_editor_mode_var.get()
        position = self._graph_editor_positions[int(person_id)]
        self._graph_editor_card_drag = {
            "person_id": int(person_id), "mode": mode,
            "offset": (x / self._graph_editor_zoom - position[0], y / self._graph_editor_zoom - position[1]),
        }
        if mode != "Перемещение":
            self._graph_editor_link_line = canvas.create_line(x, y, x, y, fill="#276f86", width=3, dash=(6, 4))
        self._draw_graph_editor()

    def _drag_graph_card(self, event):
        drag = self._graph_editor_card_drag
        if not drag:
            return
        canvas = self._graph_editor_canvas
        x = canvas.canvasx(event.x)
        y = canvas.canvasy(event.y)
        if drag["mode"] == "Перемещение":
            offset_x, offset_y = drag["offset"]
            self._graph_editor_positions[drag["person_id"]] = (
                x / self._graph_editor_zoom - offset_x,
                y / self._graph_editor_zoom - offset_y,
            )
            self._draw_graph_editor()
        elif self._graph_editor_link_line is not None:
            source = self._graph_editor_positions[drag["person_id"]]
            source_x = (source[0] + 90) * self._graph_editor_zoom
            source_y = (source[1] + 49) * self._graph_editor_zoom
            canvas.coords(self._graph_editor_link_line, source_x, source_y, x, y)

    def _finish_graph_card_drag(self, event):
        drag = self._graph_editor_card_drag
        self._graph_editor_card_drag = None
        if not drag:
            return
        if drag["mode"] == "Перемещение":
            return
        canvas = self._graph_editor_canvas
        if self._graph_editor_link_line is not None:
            canvas.delete(self._graph_editor_link_line)
            self._graph_editor_link_line = None
        target = self._graph_editor_person_at(canvas.canvasx(event.x), canvas.canvasy(event.y))
        if target is None or target == drag["person_id"]:
            return
        if drag["mode"] == "Супруги":
            modification = GraphModification(
                "add_spouse", drag["person_id"], target, relationship_type="marriage"
            )
        else:
            modification = GraphModification(
                "link_parent", target, drag["person_id"], role=self._graph_editor_role_var.get()
            )
        self._preview_graph_editor_modifications((modification,))

    def _graph_editor_person_at(self, x, y):
        for person_id, (left, top, right, bottom) in self._graph_editor_card_bounds.items():
            if left <= x <= right and top <= y <= bottom:
                return person_id
        return None

    def _select_graph_editor_edge(self, edge_key):
        self._graph_editor_selected_edge = edge_key
        self._draw_graph_editor()

    def _open_graph_context_menu(self, event, person_id=None, edge_key=None):
        if person_id is not None:
            self._graph_editor_selected_person_id = int(person_id)
        if edge_key is not None:
            self._graph_editor_selected_edge = edge_key
        menu = tk.Menu(self._graph_editor_window, tearoff=False)
        person_menu = tk.Menu(menu, tearoff=False)
        family_menu = tk.Menu(menu, tearoff=False)
        relationship_menu = tk.Menu(menu, tearoff=False)
        menu.add_cascade(label="Person", menu=person_menu)
        menu.add_cascade(label="Family", menu=family_menu)
        menu.add_cascade(label="Relationship", menu=relationship_menu)
        person_menu.add_command(label="Открыть карточку", command=self._open_graph_editor_person)
        person_menu.add_command(label="Центрировать", command=self._center_graph_editor_selected)
        family_menu.add_command(label="Добавить супруга", command=self._graph_editor_add_spouse)
        relationship_menu.add_command(label="Удалить связь", command=self._graph_editor_remove_selected_relationship)
        relationship_menu.add_command(label="Сменить родителя", command=self._graph_editor_change_parent)
        relationship_menu.add_command(label="Перепривязать ребёнка", command=self._graph_editor_reattach_child)
        menu.tk_popup(event.x_root, event.y_root)

    def _open_graph_editor_person(self):
        if self._graph_editor_selected_person_id is not None:
            self.show_person(self._graph_editor_selected_person_id)

    def _graph_editor_add_spouse(self):
        person_id = self._graph_editor_selected_person_id
        if person_id is None:
            return
        reference = self._choose_person("Выберите супруга/партнёра", exclude_reference=str(person_id))
        related_id = self.repository.resolve_person_reference(reference) if reference else None
        if related_id is not None:
            self._preview_graph_editor_modifications((GraphModification(
                "add_spouse", person_id, int(related_id), relationship_type="marriage"
            ),))

    def _graph_editor_selected_edge_record(self):
        if self._graph_editor_model is None or self._graph_editor_selected_edge is None:
            return None
        return next(
            (edge for edge in self._graph_editor_model.edges if edge.key == self._graph_editor_selected_edge),
            None,
        )

    def _graph_editor_remove_selected_relationship(self):
        edge = self._graph_editor_selected_edge_record()
        if edge is None:
            return
        if edge.kind == "spouse":
            modification = GraphModification("remove_spouse", edge.source_id, family_id=edge.family_id)
        else:
            family = next(item for item in self._graph_editor_model.families if item.family_id == edge.family_id)
            role = "father" if family.husband_id == edge.source_id else "mother"
            modification = GraphModification(
                "remove_parent", edge.target_id, edge.source_id,
                family_id=edge.family_id, role=role,
            )
        self._preview_graph_editor_modifications((modification,))

    def _graph_editor_change_parent(self):
        edge = self._graph_editor_selected_edge_record()
        if edge is None or edge.kind != "parent":
            return
        reference = self._choose_person("Выберите нового родителя", exclude_reference=str(edge.target_id))
        new_parent_id = self.repository.resolve_person_reference(reference) if reference else None
        if new_parent_id is None:
            return
        family = next(item for item in self._graph_editor_model.families if item.family_id == edge.family_id)
        role = "father" if family.husband_id == edge.source_id else "mother"
        self._preview_graph_editor_modifications((GraphModification(
            "change_parent", edge.target_id, int(new_parent_id),
            family_id=edge.family_id, role=role, old_parent_id=edge.source_id,
        ),))

    def _graph_editor_reattach_child(self):
        edge = self._graph_editor_selected_edge_record()
        if edge is None or edge.kind != "parent":
            return
        reference = self._choose_person("Выберите нового родителя", exclude_reference=str(edge.target_id))
        new_parent_id = self.repository.resolve_person_reference(reference) if reference else None
        if new_parent_id is None:
            return
        self._preview_graph_editor_modifications((GraphModification(
            "reattach_child", edge.target_id, int(new_parent_id),
            family_id=edge.family_id, role="father",
        ),))

    def _preview_graph_editor_modifications(self, modifications):
        return self._submit_repository_task(
            "Предварительный просмотр изменения дерева",
            lambda repository, _context: GraphEditorService(repository).preview(modifications),
            self._show_graph_editor_preview,
            on_error=lambda error: messagebox.showerror(
                "Редактор дерева", str(error), parent=self._graph_editor_window
            ),
        )

    def _show_graph_editor_preview(self, preview):
        if self._graph_preview_window is not None:
            try:
                self._graph_preview_window.destroy()
            except Exception:
                pass
        self._graph_preview = preview
        window = self._create_dialog(self._graph_editor_window)
        self._graph_preview_window = window
        window.title("Предварительный просмотр")
        window.geometry("900x560")
        tk.Label(window, text="\n".join(preview.descriptions), justify="left", anchor="w").pack(fill="x", padx=12, pady=12)
        comparison = tk.PanedWindow(window, orient="horizontal", sashwidth=6)
        comparison.pack(fill="both", expand=True, padx=12)
        for label, model in (("До", preview.before), ("После", preview.after)):
            frame = tk.LabelFrame(comparison, text=label)
            text = tk.Text(frame, wrap="word")
            issue_text = "\n".join(
                f"{issue.kind}: {issue.description}" for issue in model.issues
            ) or "Проблем не обнаружено"
            text.insert("1.0", issue_text)
            text.config(state="disabled")
            text.pack(fill="both", expand=True)
            comparison.add(frame, stretch="always")
        if preview.blockers:
            tk.Label(
                window, text="\n".join(preview.blockers), foreground="#9b1c1c",
                justify="left", anchor="w",
            ).pack(fill="x", padx=12, pady=8)
        controls = tk.Frame(window)
        controls.pack(fill="x", padx=12, pady=12)
        tk.Button(
            controls, text="Применить", command=self._execute_graph_editor_preview,
            state="normal" if preview.can_execute else "disabled",
        ).pack(side="left")
        tk.Button(controls, text="Отмена", command=self._close_graph_editor_preview).pack(side="right")

    def _execute_graph_editor_preview(self):
        preview = self._graph_preview
        if preview is None or not preview.can_execute:
            return
        return self._submit_repository_task(
            "Изменение дерева",
            lambda repository, _context: GraphEditorService(repository).execute(preview),
            self._complete_graph_editor_modification,
            on_error=lambda error: messagebox.showerror(
                "Редактор дерева", str(error), parent=self._graph_preview_window
            ),
        )

    def _complete_graph_editor_modification(self, result):
        self._get_undo_manager().record_applied(
            AppliedDeltaCommand("Редактор дерева", self.repository, result.delta, result)
        )
        self._close_graph_editor_preview()
        self.refresh_views()
        self._load_graph_editor()

    def _close_graph_editor_preview(self):
        if self._graph_preview_window is not None:
            try:
                self._graph_preview_window.destroy()
            except Exception:
                pass
        self._graph_preview_window = None
        self._graph_preview = None

    def _zoom_graph_editor(self, change):
        self._graph_editor_zoom = max(0.35, min(2.5, round(self._graph_editor_zoom + change, 2)))
        self._draw_graph_editor()

    def _graph_editor_mousewheel(self, event):
        self._zoom_graph_editor(0.1 if getattr(event, "delta", 0) > 0 else -0.1)
        return "break"

    def _start_graph_editor_pan(self, event):
        self._graph_editor_canvas.scan_mark(event.x, event.y)

    def _pan_graph_editor(self, event):
        self._graph_editor_canvas.scan_dragto(event.x, event.y, gain=1)

    def _fit_graph_editor(self):
        if not self._graph_editor_positions:
            return
        min_x = min(position[0] for position in self._graph_editor_positions.values())
        max_x = max(position[0] for position in self._graph_editor_positions.values()) + 180
        min_y = min(position[1] for position in self._graph_editor_positions.values())
        max_y = max(position[1] for position in self._graph_editor_positions.values()) + 98
        width = max(1, self._graph_editor_canvas.winfo_width() - 40)
        height = max(1, self._graph_editor_canvas.winfo_height() - 40)
        self._graph_editor_zoom = max(0.35, min(2.5, min(width / max(1, max_x - min_x), height / max(1, max_y - min_y))))
        self._draw_graph_editor()
        self._graph_editor_canvas.xview_moveto(0)
        self._graph_editor_canvas.yview_moveto(0)

    def _center_graph_editor_selected(self):
        person_id = self._graph_editor_selected_person_id
        if person_id not in self._graph_editor_positions:
            return
        canvas = self._graph_editor_canvas
        canvas.update_idletasks()
        x, y = self._graph_editor_positions[person_id]
        region = canvas.cget("scrollregion").split()
        if len(region) != 4:
            return
        total_width = max(1, float(region[2]) - float(region[0]))
        total_height = max(1, float(region[3]) - float(region[1]))
        canvas.xview_moveto(max(0, min(1, (x * self._graph_editor_zoom - canvas.winfo_width() / 2) / total_width)))
        canvas.yview_moveto(max(0, min(1, (y * self._graph_editor_zoom - canvas.winfo_height() / 2) / total_height)))

    def _close_graph_editor(self):
        self._close_graph_editor_preview()
        if self._graph_editor_window is not None:
            try:
                self._graph_editor_window.destroy()
            except Exception:
                pass
        self._graph_editor_window = None
        self._graph_editor_canvas = None
        self._graph_editor_model = None
        self._graph_editor_positions = {}

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

    def open_timeline_studio(self):
        if self._timeline_studio_window is not None:
            try:
                self._timeline_studio_window.lift()
                self._timeline_studio_window.focus_force()
                return
            except Exception:
                self._timeline_studio_window = None
        window = self._create_dialog()
        self._timeline_studio_window = window
        window.title("Хронология 2.0")
        window.geometry("1420x800")
        window.minsize(960, 600)
        window.protocol("WM_DELETE_WINDOW", self._close_timeline_studio)
        self._timeline_studio_vars = {key: tk.StringVar(value=value) for key, value in {
            "scope": "selected_person", "people": str(self.current_person_id or ""), "year_from": "", "year_to": "", "surname": "", "person": "", "family": "", "place": "", "event_type": "", "sourced": "", "confidence": "", "text": "", "jump": "",
        }.items()}
        self._timeline_studio_vars["historical"] = tk.BooleanVar(value=False)
        self._timeline_studio_vars["conflicts"] = tk.BooleanVar(value=False)
        controls = tk.Frame(window); controls.pack(fill="x", padx=12, pady=(12, 4))
        for label, command in (("Загрузить", self._load_timeline_studio), ("Сравнить", self._compare_timeline_studio), ("Подогнать", self._fit_timeline_studio), ("Назад", self._timeline_studio_back), ("Вперёд", self._timeline_studio_forward), ("Свернуть/развернуть", self._toggle_timeline_studio_lane), ("Выше", lambda: self._move_timeline_studio_lane(-1)), ("Ниже", lambda: self._move_timeline_studio_lane(1)), ("Историческая заметка", self._add_timeline_studio_context), ("Сохранить вид", self._save_timeline_studio_view), ("Загрузить вид", self._load_timeline_studio_view), ("Переименовать вид", self._rename_timeline_studio_view), ("Дублировать вид", self._duplicate_timeline_studio_view), ("Удалить вид", self._delete_timeline_studio_view), ("Импорт вида", self._import_timeline_studio_view), ("Экспорт вида", self._export_timeline_studio_view), ("Экспорт", self._export_timeline_studio)):
            tk.Button(controls, text=label, command=command).pack(side="left", padx=(0, 4))
        filters = tk.LabelFrame(window, text="Область и фильтры"); filters.pack(fill="x", padx=12, pady=(0, 5))
        fields = (("scope", "Область", SCOPES), ("people", "Люди ID", None), ("year_from", "Год от", None), ("year_to", "Год до", None), ("surname", "Фамилия", None), ("person", "Человек", None), ("family", "Семья", None), ("place", "Место", None), ("event_type", "Событие", ("", *SUPPORTED_EVENT_TYPES)), ("sourced", "Источники", ("", "sourced", "unsourced")), ("confidence", "Достоверность", ("", "high", "primary", "medium", "secondary", "low", "unknown")), ("text", "Текст", None))
        for index, (key, label, choices) in enumerate(fields):
            row, column = divmod(index, 6)
            tk.Label(filters, text=label).grid(row=row, column=column * 2, sticky="e", padx=(6, 2), pady=3)
            control = ttk.Combobox(filters, textvariable=self._timeline_studio_vars[key], values=choices, width=15, state="readonly") if choices else tk.Entry(filters, textvariable=self._timeline_studio_vars[key], width=16)
            control.grid(row=row, column=column * 2 + 1, sticky="ew", padx=(0, 4), pady=3)
        tk.Checkbutton(filters, text="Исторический контекст", variable=self._timeline_studio_vars["historical"]).grid(row=2, column=0, columnspan=3, sticky="w", padx=6)
        tk.Checkbutton(filters, text="Только конфликты", variable=self._timeline_studio_vars["conflicts"], command=self._apply_timeline_studio_filters).grid(row=2, column=3, columnspan=3, sticky="w")
        tk.Label(filters, text="Перейти к году").grid(row=2, column=6, sticky="e")
        tk.Entry(filters, textvariable=self._timeline_studio_vars["jump"], width=10).grid(row=2, column=7, sticky="w")
        tk.Button(filters, text="Центр", command=self._jump_timeline_studio_year).grid(row=2, column=8, sticky="w")
        panes = ttk.Panedwindow(window, orient="horizontal"); panes.pack(fill="both", expand=True, padx=12, pady=6)
        lane_frame, event_frame, canvas_frame = tk.Frame(panes), tk.Frame(panes), tk.Frame(panes)
        panes.add(lane_frame, weight=1); panes.add(event_frame, weight=3); panes.add(canvas_frame, weight=3)
        lane_tree = ttk.Treeview(lane_frame, columns=("kind",), show="tree headings", selectmode="browse")
        lane_tree.heading("#0", text="Дорожка"); lane_tree.heading("kind", text="Тип"); lane_tree.pack(fill="both", expand=True)
        self._timeline_studio_lane_tree = lane_tree
        columns = ("date", "original", "type", "subject", "place", "age", "sources", "confidence", "conflicts")
        event_tree = ttk.Treeview(event_frame, columns=columns, show="headings", selectmode="browse")
        for column, title, width in (("date", "Дата", 90), ("original", "Оригинал", 105), ("type", "Событие", 100), ("subject", "Человек/семья", 180), ("place", "Место", 130), ("age", "Возраст", 65), ("sources", "Ист.", 45), ("confidence", "Дов.", 70), ("conflicts", "Конфликты", 130)):
            event_tree.heading(column, text=title); event_tree.column(column, width=width, anchor="w")
        event_tree.pack(fill="both", expand=True)
        event_tree.bind("<Double-1>", self._open_timeline_studio_event)
        event_tree.bind("<<TreeviewSelect>>", lambda _event: self._center_selected_timeline_studio_event())
        event_tree.bind("<Button-3>", self._timeline_studio_context_menu)
        self._timeline_studio_event_tree = event_tree
        canvas = tk.Canvas(canvas_frame, background="#f7f8fa", highlightthickness=0)
        canvas.pack(fill="both", expand=True); canvas.bind("<MouseWheel>", self._zoom_timeline_studio); canvas.bind("<ButtonPress-1>", lambda event: canvas.scan_mark(event.x, event.y)); canvas.bind("<B1-Motion>", lambda event: canvas.scan_dragto(event.x, event.y, gain=1))
        self._timeline_studio_canvas = canvas
        footer = tk.Frame(window); footer.pack(fill="x", padx=12, pady=(0, 12))
        self._timeline_studio_status = tk.Label(footer, text="") ; self._timeline_studio_status.pack(side="left")
        self._load_timeline_studio()

    def _timeline_studio_ids(self):
        values = re.findall(r"\d+", self._timeline_studio_vars["people"].get())
        return tuple(int(value) for value in values)

    def _load_timeline_studio(self):
        scope = self._timeline_studio_vars["scope"].get(); people = self._timeline_studio_ids(); historical = bool(self._timeline_studio_vars["historical"].get())
        return self._submit_repository_task("Хронология 2.0", lambda repository, context: TimelineStudioService(repository).build(scope=scope, selected_person_ids=people, include_historical=historical, progress_callback=(lambda label, done, total: context.report(label, done, total)) if context else None, cancel_callback=context.raise_if_cancelled if context else None), self._apply_timeline_studio_model, on_error=lambda error: messagebox.showerror("Хронология 2.0", str(error), parent=self._timeline_studio_window), cancellable=True)

    def _apply_timeline_studio_model(self, model):
        self._timeline_studio_model = model
        self._timeline_studio_history = self._timeline_studio_history[:self._timeline_studio_history_index + 1] + [model]
        self._timeline_studio_history_index = len(self._timeline_studio_history) - 1
        self._apply_timeline_studio_filters()

    def _timeline_studio_filters(self):
        values = self._timeline_studio_vars
        def year(key):
            value = values[key].get().strip(); return int(value) if value else None
        return TimelineStudioFilters(year_from=year("year_from"), year_to=year("year_to"), surname=values["surname"].get(), person=values["person"].get(), family=values["family"].get(), place=values["place"].get(), event_type=values["event_type"].get(), sourced=values["sourced"].get(), confidence=values["confidence"].get(), only_conflicts=bool(values["conflicts"].get()), text=values["text"].get())

    def _apply_timeline_studio_filters(self):
        if self._timeline_studio_model is None: return
        try: self._timeline_studio_events = self.timeline_studio_service.filter(self._timeline_studio_model, self._timeline_studio_filters())
        except ValueError as error: messagebox.showerror("Хронология 2.0", str(error), parent=self._timeline_studio_window); return
        self._render_timeline_studio()

    def _render_timeline_studio(self):
        lane_tree, event_tree = self._timeline_studio_lane_tree, self._timeline_studio_event_tree
        for tree in (lane_tree, event_tree):
            if tree:
                for item in tree.get_children(): tree.delete(item)
        if lane_tree:
            for lane in self._timeline_studio_model.lanes: lane_tree.insert("", "end", iid=lane.lane_id, text=("[+] " if lane.collapsed else "[-] ") + lane.label, values=(lane.kind,))
        self._timeline_studio_event_map = {}
        if event_tree:
            for event in self._timeline_studio_events:
                event_tree.insert("", "end", iid=event.event_id, values=(event.normalized_date, event.original_date, event.event_label, event.subject_label, event.place, event.age if event.age is not None else "", event.source_count, event.confidence, ", ".join(event.conflicts)))
                self._timeline_studio_event_map[event.event_id] = event
        self._draw_timeline_studio()
        if self._timeline_studio_status: self._timeline_studio_status.config(text=f"Событий: {len(self._timeline_studio_events)} | Масштаб: {self.timeline_studio_service.time_scale(self._timeline_studio_events)}")

    def _draw_timeline_studio(self):
        canvas = self._timeline_studio_canvas
        if canvas is None: return
        canvas.delete("all"); events = self._timeline_studio_events; years = [event.earliest.year for event in events if event.earliest]
        if not years: canvas.create_text(20, 20, anchor="nw", text="Нет дат для отображения"); return
        minimum, maximum = min(years), max(years); span = max(1, maximum - minimum); lanes = {lane.lane_id: index for index, lane in enumerate(self._timeline_studio_model.lanes)}
        canvas.create_text(16, 14, anchor="nw", text=f"{minimum} - {maximum} ({self.timeline_studio_service.time_scale(events)})")
        for lane, index in lanes.items():
            y = 48 + index * 42; canvas.create_line(120, y, 1500, y, fill="#cbd5df"); canvas.create_text(8, y, anchor="w", text=lane)
        for event in events:
            if event.earliest and event.lane_id in lanes:
                x, y = 120 + (event.earliest.year - minimum) * 1320 / span, 48 + lanes[event.lane_id] * 42
                canvas.create_oval(x - 5, y - 5, x + 5, y + 5, fill=event.color, outline="#8b1e1e" if event.conflicts else "")
        canvas.configure(scrollregion=(0, 0, 1550, max(120, 80 + len(lanes) * 42)))

    def _selected_timeline_studio_event(self):
        tree = self._timeline_studio_event_tree
        return self._timeline_studio_event_map.get(tree.selection()[0]) if tree and tree.selection() else None

    def _open_timeline_studio_event(self, _event=None):
        event = self._selected_timeline_studio_event()
        if event and hasattr(self, "workspace_integration_service"):
            self.workspace_integration_service.select_event(event.event_id.split(":")[-1], "timeline")
            if event.person_id is not None:
                self.workspace_integration_service.select_person(event.person_id, "timeline")
            elif event.family_id is not None:
                self.workspace_integration_service.select_family(event.family_id, "timeline")
        if event and event.person_id is not None: self.show_person(event.person_id)
        elif event and event.family_id is not None: self._show_data_quality_family_context(type("Issue", (), {"database_id": event.family_id, "issue_type": event.event_label, "severity": "Information", "gedcom_id": "", "explanation": event.description})())

    def _center_selected_timeline_studio_event(self):
        event = self._selected_timeline_studio_event()
        if event and event.earliest: self._timeline_studio_vars["jump"].set(str(event.earliest.year)); self._jump_timeline_studio_year()

    def _jump_timeline_studio_year(self):
        canvas = self._timeline_studio_canvas
        try: year = int(self._timeline_studio_vars["jump"].get())
        except ValueError: return
        years = [event.earliest.year for event in self._timeline_studio_events if event.earliest]
        if canvas and years: canvas.xview_moveto(max(0.0, min(1.0, (year - min(years)) / max(1, max(years) - min(years)))))

    def _fit_timeline_studio(self):
        if self._timeline_studio_canvas: self._timeline_studio_canvas.xview_moveto(0); self._timeline_studio_canvas.yview_moveto(0)

    def _zoom_timeline_studio(self, event):
        canvas = self._timeline_studio_canvas
        if canvas: canvas.scale("all", event.x, event.y, 1.15 if event.delta > 0 else 0.87, 1.0)

    def _toggle_timeline_studio_lane(self):
        tree = self._timeline_studio_lane_tree
        if not tree or not tree.selection() or self._timeline_studio_model is None: return
        lane_id = tree.selection()[0]; collapsed = {lane.lane_id for lane in self._timeline_studio_model.lanes if lane.collapsed}; collapsed.symmetric_difference_update({lane_id})
        self._timeline_studio_model = self.timeline_studio_service.build(scope=self._timeline_studio_model.scope, selected_person_ids=self._timeline_studio_model.selected_person_ids, collapsed_lane_ids=collapsed)
        self._apply_timeline_studio_filters()

    def _move_timeline_studio_lane(self, delta):
        tree = self._timeline_studio_lane_tree
        if not tree or not tree.selection() or self._timeline_studio_model is None: return
        order = [lane.lane_id for lane in self._timeline_studio_model.lanes]; index = order.index(tree.selection()[0]); target = max(0, min(len(order) - 1, index + delta)); order[index], order[target] = order[target], order[index]
        self._timeline_studio_model = self.timeline_studio_service.build(scope=self._timeline_studio_model.scope, selected_person_ids=self._timeline_studio_model.selected_person_ids, lane_order=order)
        self._apply_timeline_studio_filters()

    def _timeline_studio_back(self):
        if self._timeline_studio_history_index > 0: self._timeline_studio_history_index -= 1; self._timeline_studio_model = self._timeline_studio_history[self._timeline_studio_history_index]; self._apply_timeline_studio_filters()
    def _timeline_studio_forward(self):
        if self._timeline_studio_history_index + 1 < len(self._timeline_studio_history): self._timeline_studio_history_index += 1; self._timeline_studio_model = self._timeline_studio_history[self._timeline_studio_history_index]; self._apply_timeline_studio_filters()
    def _timeline_studio_context_menu(self, event):
        selected = self._selected_timeline_studio_event()
        if not selected: return
        menu = tk.Menu(self._timeline_studio_window, tearoff=False)
        menu.add_command(label="Открыть человека", command=self._open_timeline_studio_event)
        menu.add_command(label="Открыть событие", command=lambda: self._manage_person_event(self._timeline_studio_window, selected.person_id, int(selected.event_id.split(":")[1]), close_parent_on_save=False) if selected.event_id.startswith("event:") else None)
        menu.add_command(label="Открыть доказательства", command=self.open_evidence_manager)
        menu.add_command(label="Показать связанных людей", command=lambda: self._open_timeline_studio_related(selected))
        menu.add_command(label="Показать на дереве", command=self.open_tree_canvas)
        menu.add_command(label="Показать на карте жизни", command=self.open_life_map)
        menu.add_command(label="Копировать детали", command=lambda: self.root.clipboard_append(json.dumps(asdict(selected), ensure_ascii=False, default=str)))
        menu.tk_popup(event.x_root, event.y_root)

    def _open_timeline_studio_related(self, event):
        if event.person_id is not None:
            self.current_person_id = event.person_id
            self.open_relationship_editor()

    def _compare_timeline_studio(self):
        if self._timeline_studio_model is None: return
        try:
            comparison = self.timeline_studio_service.compare(self._timeline_studio_model, self._timeline_studio_ids())
        except ValueError as error:
            messagebox.showwarning("Сравнение", str(error), parent=self._timeline_studio_window); return
        messagebox.showinfo("Сравнение", f"Одновременных событий: {len(comparison.simultaneous_event_ids)}\nОбщих мест: {', '.join(comparison.shared_places) or '-'}\nПересечений проживания: {len(comparison.overlapping_residences)}\nРазница возраста: {comparison.age_differences or '-'}", parent=self._timeline_studio_window)

    def _add_timeline_studio_context(self):
        title = simpledialog.askstring("Исторический контекст", "Название:", parent=self._timeline_studio_window)
        if title:
            date_text = simpledialog.askstring("Исторический контекст", "Дата GEDCOM:", parent=self._timeline_studio_window) or ""
            category = simpledialog.askstring("Исторический контекст", "Тип: война, миграция, политическое изменение, эпидемия или заметка", parent=self._timeline_studio_window) or "note"
            self.timeline_studio_service.save_historical_event({"title": title, "date": date_text, "category": category, "note": ""})
            self._load_timeline_studio()

    def _save_timeline_studio_view(self):
        name = simpledialog.askstring("Сохранить вид", "Название:", parent=self._timeline_studio_window)
        if name: self.timeline_studio_service.save_view(name, {"scope": self._timeline_studio_vars["scope"].get(), "people": self._timeline_studio_vars["people"].get(), "filters": asdict(self._timeline_studio_filters())})
    def _load_timeline_studio_view(self):
        views = self.timeline_studio_service.list_views(); name = simpledialog.askstring("Загрузить вид", "Название: " + ", ".join(view["name"] for view in views), parent=self._timeline_studio_window)
        if name:
            try:
                configuration = self.timeline_studio_service.load_view(name)["configuration"]
                for key, value in configuration.get("filters", {}).items():
                    if key in self._timeline_studio_vars and not isinstance(self._timeline_studio_vars[key], tk.BooleanVar): self._timeline_studio_vars[key].set(str(value or ""))
                for key in ("scope", "people"):
                    if key in configuration: self._timeline_studio_vars[key].set(configuration[key])
                self._load_timeline_studio()
            except (OSError, ValueError) as error: messagebox.showerror("Хронология 2.0", str(error), parent=self._timeline_studio_window)

    def _timeline_studio_view_name(self, title):
        return simpledialog.askstring(title, "Название: " + ", ".join(view["name"] for view in self.timeline_studio_service.list_views()), parent=self._timeline_studio_window)
    def _rename_timeline_studio_view(self):
        old_name = self._timeline_studio_view_name("Переименовать вид")
        new_name = simpledialog.askstring("Переименовать вид", "Новое название:", parent=self._timeline_studio_window) if old_name else None
        if old_name and new_name: self.timeline_studio_service.rename_view(old_name, new_name)
    def _duplicate_timeline_studio_view(self):
        name = self._timeline_studio_view_name("Дублировать вид")
        copy_name = simpledialog.askstring("Дублировать вид", "Название копии:", parent=self._timeline_studio_window) if name else None
        if name and copy_name: self.timeline_studio_service.duplicate_view(name, copy_name)
    def _delete_timeline_studio_view(self):
        name = self._timeline_studio_view_name("Удалить вид")
        if name and messagebox.askyesno("Удалить вид", f"Удалить {name}?", parent=self._timeline_studio_window): self.timeline_studio_service.delete_view(name)
    def _import_timeline_studio_view(self):
        source = filedialog.askopenfilename(parent=self._timeline_studio_window, title="Импорт вида", filetypes=[("JSON", "*.json")])
        if source: self.timeline_studio_service.import_view(source)
    def _export_timeline_studio_view(self):
        name = self._timeline_studio_view_name("Экспорт вида")
        destination = filedialog.asksaveasfilename(parent=self._timeline_studio_window, title="Экспорт вида", defaultextension=".json", filetypes=[("JSON", "*.json")]) if name else ""
        if destination: self.timeline_studio_service.export_view(name, destination)

    def _export_timeline_studio(self):
        if self._timeline_studio_model is None: return
        destination = filedialog.asksaveasfilename(parent=self._timeline_studio_window, title="Экспорт Хронологии 2.0", initialdir=str(EXPORT_DIR), defaultextension=".svg", filetypes=[("PDF", "*.pdf"), ("SVG", "*.svg"), ("PNG", "*.png"), ("HTML", "*.html"), ("CSV", "*.csv")])
        if destination:
            export_format = Path(destination).suffix.lower().lstrip(".")
            return self._submit_repository_task("Экспорт Хронологии 2.0", lambda repository, _context: TimelineStudioService(repository).export(self._timeline_studio_model, self._timeline_studio_events, destination, export_format, filters=self._timeline_studio_filters()), lambda _path: None, on_error=lambda error: messagebox.showerror("Экспорт", str(error), parent=self._timeline_studio_window))

    def _close_timeline_studio(self):
        if self._timeline_studio_window is not None:
            try: self._timeline_studio_window.destroy()
            except Exception: pass
        self._timeline_studio_window = self._timeline_studio_model = self._timeline_studio_lane_tree = self._timeline_studio_event_tree = self._timeline_studio_canvas = self._timeline_studio_status = None
        self._timeline_studio_events = (); self._timeline_studio_event_map = {}; self._timeline_studio_vars = {}

    def open_gedcom_repair_center(self) -> None:
        if self._gedcom_repair_window is not None:
            try:
                self._gedcom_repair_window.lift()
                self._gedcom_repair_window.focus_force()
                return
            except Exception:
                self._gedcom_repair_window = None
        window = self._create_dialog()
        self._gedcom_repair_window = window
        window.title("Исправление GEDCOM")
        window.geometry("1160x680")
        window.minsize(850, 500)
        window.protocol("WM_DELETE_WINDOW", self._close_gedcom_repair_center)
        controls = tk.Frame(window)
        controls.pack(fill="x", padx=12, pady=12)
        tk.Button(controls, text="Выбрать GEDCOM", command=self._choose_gedcom_repair_file).pack(side="left")
        tk.Button(controls, text="Исправить все безопасные", command=self._repair_all_safe_gedcom).pack(side="left", padx=(8, 0))
        tk.Button(controls, text="Экспорт отчёта CSV", command=lambda: self._export_gedcom_repair_report("csv")).pack(side="left", padx=(16, 0))
        tk.Button(controls, text="JSON", command=lambda: self._export_gedcom_repair_report("json")).pack(side="left", padx=(8, 0))
        self._gedcom_repair_diagnostics_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            controls, text="Только диагностика", variable=self._gedcom_repair_diagnostics_var,
            command=self._update_gedcom_repair_controls,
        ).pack(side="right")
        columns = ("selected", "severity", "location", "description", "repair", "automatic")
        tree = ttk.Treeview(window, columns=columns, show="headings")
        for column, label, width in (
            ("selected", "Выбор", 60), ("severity", "Важность", 85),
            ("location", "Расположение", 205), ("description", "Проблема", 310),
            ("repair", "Рекомендация", 290), ("automatic", "Авто", 55),
        ):
            tree.heading(column, text=label)
            tree.column(column, width=width, anchor="center" if column in {"selected", "automatic"} else "w")
        tree.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        tree.bind("<Double-1>", self._toggle_gedcom_repair_issue)
        self._gedcom_repair_issue_tree = tree
        footer = tk.Frame(window)
        footer.pack(fill="x", padx=12, pady=(0, 12))
        self._gedcom_repair_status = tk.Label(footer, text="Выберите GEDCOM для анализа.")
        self._gedcom_repair_status.pack(side="left")
        self._gedcom_repair_apply_button = tk.Button(
            footer, text="Исправить выбранные", command=self._repair_selected_gedcom, state="disabled",
        )
        self._gedcom_repair_apply_button.pack(side="right")

    def _choose_gedcom_repair_file(self) -> None:
        path = filedialog.askopenfilename(
            parent=self._gedcom_repair_window, title="Выберите GEDCOM",
            filetypes=[("GEDCOM", "*.ged *.GED"), ("Все файлы", "*.*")],
        )
        if path:
            self._gedcom_repair_source_path = Path(path)
            self._analyze_gedcom_repair_file()

    def _analyze_gedcom_repair_file(self) -> None:
        source_path = self._gedcom_repair_source_path
        if source_path is None:
            return
        diagnostics_only = bool(self._gedcom_repair_diagnostics_var and self._gedcom_repair_diagnostics_var.get())
        return self._submit_repository_task(
            "Анализ GEDCOM",
            lambda repository, _context: GedcomRepairService(
                repository.db_name, diagnostics_only=diagnostics_only
            ).analyze(source_path),
            self._render_gedcom_repair_preview,
            on_error=lambda error: messagebox.showerror("Исправление GEDCOM", str(error), parent=self._gedcom_repair_window),
        )

    def _render_gedcom_repair_preview(self, preview) -> None:
        self._gedcom_repair_preview = preview
        tree = self._gedcom_repair_issue_tree
        for item in tree.get_children():
            tree.delete(item)
        for issue in preview.issues:
            tree.insert("", "end", iid=issue.issue_id, values=(
                "[x]" if issue.automatic_repair else "", issue.severity, issue.location,
                issue.description, issue.recommended_repair, "Да" if issue.automatic_repair else "Нет",
            ))
        self._update_gedcom_repair_controls()

    def _toggle_gedcom_repair_issue(self, _event=None) -> None:
        tree = self._gedcom_repair_issue_tree
        selected = tree.selection()
        if not selected or self._gedcom_repair_preview is None:
            return
        item = selected[0]
        issue = next(issue for issue in self._gedcom_repair_preview.issues if issue.issue_id == item)
        if not issue.automatic_repair:
            return
        values = list(tree.item(item)["values"])
        values[0] = "" if values[0] else "[x]"
        tree.item(item, values=values)
        self._update_gedcom_repair_controls()

    def _selected_gedcom_repair_issue_ids(self):
        if self._gedcom_repair_issue_tree is None:
            return ()
        return tuple(
            item for item in self._gedcom_repair_issue_tree.get_children()
            if self._gedcom_repair_issue_tree.item(item)["values"][0] == "[x]"
        )

    def _update_gedcom_repair_controls(self) -> None:
        diagnostics_only = bool(self._gedcom_repair_diagnostics_var and self._gedcom_repair_diagnostics_var.get())
        selected = self._selected_gedcom_repair_issue_ids()
        if self._gedcom_repair_apply_button is not None:
            self._gedcom_repair_apply_button.config(
                state="normal" if selected and not diagnostics_only else "disabled"
            )
        if self._gedcom_repair_status is not None and self._gedcom_repair_preview is not None:
            self._gedcom_repair_status.config(
                text=f"Проблем: {len(self._gedcom_repair_preview.issues)} | Выбрано безопасных: {len(selected)}"
            )

    def _repair_selected_gedcom(self) -> None:
        self._repair_gedcom_issue_ids(self._selected_gedcom_repair_issue_ids())

    def _repair_all_safe_gedcom(self) -> None:
        if self._gedcom_repair_preview is not None:
            self._repair_gedcom_issue_ids(self._gedcom_repair_preview.safe_issue_ids)

    def _repair_gedcom_issue_ids(self, issue_ids) -> None:
        if not issue_ids or self._gedcom_repair_preview is None:
            return
        if self._gedcom_repair_diagnostics_var and self._gedcom_repair_diagnostics_var.get():
            messagebox.showinfo("Исправление GEDCOM", "В режиме диагностики исправления отключены.", parent=self._gedcom_repair_window)
            return
        destination = filedialog.asksaveasfilename(
            parent=self._gedcom_repair_window, title="Сохранить исправленный GEDCOM",
            initialdir=str(self._gedcom_repair_preview.source_path.parent),
            initialfile=f"{self._gedcom_repair_preview.source_path.stem}.repaired.ged",
            defaultextension=".ged", filetypes=[("GEDCOM", "*.ged")],
        )
        if not destination:
            return
        source_path = self._gedcom_repair_preview.source_path
        diagnostics_only = bool(self._gedcom_repair_diagnostics_var and self._gedcom_repair_diagnostics_var.get())

        def repair(repository, _context):
            service = GedcomRepairService(repository.db_name, diagnostics_only=diagnostics_only)
            preview = service.preview(source_path, issue_ids)
            return service.execute(preview, destination)

        return self._submit_repository_task(
            "Исправление GEDCOM", repair, self._complete_gedcom_repair,
            on_error=lambda error: messagebox.showerror("Исправление GEDCOM", str(error), parent=self._gedcom_repair_window),
        )

    def _complete_gedcom_repair(self, result) -> None:
        self._get_undo_manager().record_applied(GedcomRepairCommand(result))
        self._analyze_gedcom_repair_file()
        if self._gedcom_repair_window is not None:
            messagebox.showinfo("Исправление GEDCOM", f"Исправленный файл сохранён: {result.repaired_path}", parent=self._gedcom_repair_window)

    def _export_gedcom_repair_report(self, export_format) -> None:
        preview = self._gedcom_repair_preview
        if preview is None:
            return
        destination = filedialog.asksaveasfilename(
            parent=self._gedcom_repair_window, title="Экспорт отчёта GEDCOM",
            initialdir=str(preview.source_path.parent), initialfile=f"{preview.source_path.stem}.repair-report.{export_format}",
            defaultextension=f".{export_format}", filetypes=[(export_format.upper(), f"*.{export_format}")],
        )
        if not destination:
            return
        return self._submit_repository_task(
            "Экспорт отчёта GEDCOM",
            lambda repository, _context: getattr(GedcomRepairService(repository.db_name), f"export_report_{export_format}")(preview, destination),
            lambda _path: None,
            on_error=lambda error: messagebox.showerror("Экспорт отчёта", str(error), parent=self._gedcom_repair_window),
        )

    def _close_gedcom_repair_center(self) -> None:
        if self._gedcom_repair_window is not None:
            try:
                self._gedcom_repair_window.destroy()
            except Exception:
                pass
        self._gedcom_repair_window = None
        self._gedcom_repair_preview = None
        self._gedcom_repair_source_path = None
        self._gedcom_repair_issue_tree = None
        self._gedcom_repair_status = None
        self._gedcom_repair_diagnostics_var = None
        self._gedcom_repair_apply_button = None

    def open_evidence_manager(self) -> None:
        if self._evidence_window is not None:
            try:
                self._evidence_window.lift()
                self._evidence_window.focus_force()
                self._load_evidence_manager()
                return
            except Exception:
                self._evidence_window = None

        window = self._create_dialog()
        self._evidence_window = window
        window.title("Источники и доказательства")
        window.geometry("1320x720")
        window.minsize(960, 560)
        window.protocol("WM_DELETE_WINDOW", self._close_evidence_manager)

        toolbar = tk.Frame(window)
        toolbar.pack(fill="x", padx=12, pady=(12, 6))
        actions = (
            ("Создать источник", self._create_evidence_source),
            ("Изменить источник", self._edit_evidence_source),
            ("Дублировать", self._duplicate_evidence_source),
            ("Объединить дубликаты", self._merge_evidence_sources),
            ("Прикрепить цитату", self._attach_evidence_citation),
            ("Открепить цитату", self._detach_evidence_citation),
        )
        self._evidence_mutation_buttons = []
        for label, command in actions:
            button = tk.Button(toolbar, text=label, command=command)
            button.pack(side="left", padx=(0, 6))
            self._evidence_mutation_buttons.append(button)
        tk.Button(toolbar, text="CSV", command=lambda: self._export_evidence("csv")).pack(side="left", padx=(12, 4))
        tk.Button(toolbar, text="JSON", command=lambda: self._export_evidence("json")).pack(side="left")
        self._evidence_read_only_var = tk.BooleanVar(value=False)
        tk.Checkbutton(
            toolbar, text="Только диагностика", variable=self._evidence_read_only_var,
            command=self._toggle_evidence_read_only,
        ).pack(side="right")

        panes = tk.PanedWindow(window, orient="horizontal", sashwidth=6)
        panes.pack(fill="both", expand=True, padx=12, pady=6)
        source_frame = tk.LabelFrame(panes, text="Источники")
        citation_frame = tk.LabelFrame(panes, text="Выбранная цитата")
        usage_frame = tk.LabelFrame(panes, text="Объекты, использующие источник")
        panes.add(source_frame, width=380, stretch="always")
        panes.add(citation_frame, width=430, stretch="always")
        panes.add(usage_frame, width=440, stretch="always")

        self._evidence_source_tree = self._evidence_tree(
            source_frame,
            (("id", "ID", 55), ("title", "Название", 190),
             ("repository", "Репозиторий", 120), ("count", "Цитат", 55)),
        )
        self._evidence_source_tree.bind("<<TreeviewSelect>>", self._select_evidence_source)
        self._evidence_source_tree.bind("<Double-1>", lambda _event: self._edit_evidence_source())

        self._evidence_citation_tree = self._evidence_tree(
            citation_frame,
            (("id", "ID", 50), ("page", "Страница", 85),
             ("confidence", "Достоверность", 105), ("proof", "Статус", 105)),
        )
        self._evidence_citation_tree.bind("<<TreeviewSelect>>", self._select_evidence_citation)
        self._evidence_citation_tree.bind("<Double-1>", lambda _event: self._edit_evidence_citation())
        self._evidence_details_text = tk.Text(citation_frame, height=10, wrap="word")
        self._evidence_details_text.pack(fill="both", expand=False, padx=8, pady=(0, 8))

        self._evidence_usage_tree = self._evidence_tree(
            usage_frame,
            (("type", "Тип", 90), ("target", "Объект", 250),
             ("confidence", "Достоверность", 105)),
        )
        self._evidence_usage_tree.bind("<Double-1>", self._open_evidence_usage)
        self._evidence_diagnostics_text = tk.Text(window, height=7, wrap="word")
        self._evidence_diagnostics_text.pack(fill="x", padx=12, pady=(6, 12))
        self._load_evidence_manager()

    @staticmethod
    def _evidence_tree(parent, definitions):
        columns = tuple(item[0] for item in definitions)
        tree = ttk.Treeview(parent, columns=columns, show="headings")
        for column, label, width in definitions:
            tree.heading(column, text=label)
            tree.column(column, width=width, anchor="center" if column in {"id", "count"} else "w")
        tree.pack(fill="both", expand=True, padx=8, pady=8)
        return tree

    def _load_evidence_manager(self) -> None:
        read_only = bool(self._evidence_read_only_var and self._evidence_read_only_var.get())
        return self._submit_repository_task(
            "Источники и доказательства",
            lambda repository, _context: EvidenceService(repository, read_only=read_only).build_model(),
            self._render_evidence_manager,
            on_error=lambda error: messagebox.showerror(
                "Источники и доказательства", str(error), parent=self._evidence_window
            ),
        )

    def _render_evidence_manager(self, model) -> None:
        self._evidence_model = model
        for tree in (self._evidence_source_tree, self._evidence_citation_tree, self._evidence_usage_tree):
            if tree is not None:
                for item in tree.get_children():
                    tree.delete(item)
        if self._evidence_source_tree is not None:
            for source in model.sources:
                self._evidence_source_tree.insert("", "end", iid=f"source-{source['id']}", values=(
                    source["id"], source["title"], source["repository"], source.get("citation_count", 0),
                ))
        issue_counts = Counter(issue.kind for issue in model.issues)
        lines = [f"{kind}: {count}" for kind, count in sorted(issue_counts.items())]
        if self._evidence_diagnostics_text is not None:
            self._evidence_diagnostics_text.config(state="normal")
            self._evidence_diagnostics_text.delete("1.0", "end")
            self._evidence_diagnostics_text.insert("1.0", "\n".join(lines) or "Проблем не обнаружено")
            self._evidence_diagnostics_text.config(state="disabled")
        self._set_evidence_mutation_state(model.read_only)

    def _toggle_evidence_read_only(self) -> None:
        self._set_evidence_mutation_state(bool(self._evidence_read_only_var.get()))
        self._load_evidence_manager()

    def _set_evidence_mutation_state(self, read_only) -> None:
        for button in self._evidence_mutation_buttons:
            button.config(state="disabled" if read_only else "normal")

    def _selected_evidence_source_id(self):
        if self._evidence_source_tree is None or not self._evidence_source_tree.selection():
            return None
        return int(self._evidence_source_tree.item(self._evidence_source_tree.selection()[0])["values"][0])

    def _selected_evidence_citation_id(self):
        if self._evidence_citation_tree is None or not self._evidence_citation_tree.selection():
            return None
        return int(self._evidence_citation_tree.item(self._evidence_citation_tree.selection()[0])["values"][0])

    def _select_evidence_source(self, _event=None) -> None:
        source_id = self._selected_evidence_source_id()
        if source_id is None or self._evidence_model is None:
            return
        if hasattr(self, "workspace_integration_service"):
            self.workspace_integration_service.select_source(source_id, None, "evidence")
        for tree in (self._evidence_citation_tree, self._evidence_usage_tree):
            for item in tree.get_children():
                tree.delete(item)
        for citation in self._evidence_model.citations:
            if int(citation["source_id"]) == source_id:
                self._evidence_citation_tree.insert("", "end", iid=f"citation-{citation['id']}", values=(
                    citation["id"], citation["page"], citation["confidence"], citation["proof_status"],
                ))
        for usage in self._evidence_model.usages:
            if int(usage["source_id"]) == source_id:
                self._evidence_usage_tree.insert("", "end", iid=f"usage-{usage['id']}", values=(
                    usage["target_type"], usage["target"], usage["confidence"],
                ))
        self._show_evidence_details(None)

    def _select_evidence_citation(self, _event=None) -> None:
        citation_id = self._selected_evidence_citation_id()
        citation = next((
            item for item in self._evidence_model.citations
            if int(item["id"]) == citation_id
        ), None) if self._evidence_model else None
        if citation and hasattr(self, "workspace_integration_service"):
            self.workspace_integration_service.select_source(citation.get("source_id"), citation_id, "evidence")
        self._show_evidence_details(citation)

    def _show_evidence_details(self, citation) -> None:
        if self._evidence_details_text is None:
            return
        lines = [] if citation is None else [
            f"Страница: {citation['page']}",
            f"Достоверность: {citation['confidence']}",
            f"Статус: {citation['proof_status']}",
            f"Медиа: {citation['media_reference'] or '-'}",
            f"Транскрипция: {citation['transcription']}",
            f"Комментарий: {citation['comment']}",
        ]
        self._evidence_details_text.config(state="normal")
        self._evidence_details_text.delete("1.0", "end")
        self._evidence_details_text.insert("1.0", "\n".join(lines))
        self._evidence_details_text.config(state="disabled")

    def _create_evidence_source(self) -> None:
        self._edit_evidence_source_dialog()

    def _edit_evidence_source(self) -> None:
        source_id = self._selected_evidence_source_id()
        if source_id is None or self._evidence_model is None:
            return
        source = next(item for item in self._evidence_model.sources if int(item["id"]) == source_id)
        self._edit_evidence_source_dialog(source)

    def _edit_evidence_source_dialog(self, source=None) -> None:
        dialog = self._create_dialog(self._evidence_window)
        dialog.title("Источник")
        fields = {}
        labels = {
            "title": "Название", "author": "Автор", "publication": "Публикация",
            "repository": "Репозиторий", "call_number": "Шифр", "url": "URL", "notes": "Примечания",
        }
        for row, field in enumerate(SOURCE_FIELDS):
            tk.Label(dialog, text=f"{labels[field]}:").grid(row=row, column=0, sticky="w", padx=12, pady=4)
            entry = tk.Entry(dialog, width=58)
            entry.insert(0, (source or {}).get(field, ""))
            entry.grid(row=row, column=1, sticky="ew", padx=(0, 12), pady=4)
            fields[field] = entry

        def save():
            data = {field: entry.get().strip() for field, entry in fields.items()}
            operation = EvidenceOperation(
                "edit_source" if source else "create_source",
                source_id=int(source["id"]) if source else 0,
                data=data,
            )
            dialog.destroy()
            self._run_evidence_operations((operation,))

        tk.Button(dialog, text="Сохранить", command=save).grid(row=len(SOURCE_FIELDS), column=1, sticky="e", padx=12, pady=12)

    def _duplicate_evidence_source(self) -> None:
        source_id = self._selected_evidence_source_id()
        if source_id is not None:
            self._run_evidence_operations((EvidenceOperation("duplicate_source", source_id=source_id),))

    def _merge_evidence_sources(self) -> None:
        target_id = self._selected_evidence_source_id()
        if target_id is None or self._evidence_model is None:
            return
        duplicates = tuple(
            source_id for issue in self._evidence_model.issues if issue.kind == "duplicate_source"
            and target_id in issue.source_ids for source_id in issue.source_ids if source_id != target_id
        )
        if not duplicates:
            messagebox.showinfo("Доказательства", "Для выбранного источника дубликаты не обнаружены.", parent=self._evidence_window)
            return
        if messagebox.askyesno(
            "Объединение источников", f"Объединить источники {duplicates} с ID {target_id}?",
            parent=self._evidence_window,
        ):
            self._run_evidence_operations((EvidenceOperation(
                "merge_sources", source_id=target_id, source_ids=duplicates,
            ),))

    def _attach_evidence_citation(self) -> None:
        source_id = self._selected_evidence_source_id()
        if source_id is not None:
            self._edit_evidence_citation_dialog(source_id)

    def _edit_evidence_citation(self) -> None:
        source_id = self._selected_evidence_source_id()
        citation_id = self._selected_evidence_citation_id()
        if source_id is None or citation_id is None or self._evidence_model is None:
            return
        citation = next(item for item in self._evidence_model.citations if int(item["id"]) == citation_id)
        self._edit_evidence_citation_dialog(source_id, citation)

    def _edit_evidence_citation_dialog(self, source_id, citation=None) -> None:
        dialog = self._create_dialog(self._evidence_window)
        dialog.title("Цитата")
        values = {
            "target_type": tk.StringVar(value=(citation or {}).get("target_type", "person")),
            "target_id": tk.StringVar(value=(citation or {}).get("target_id", str(self.current_person_id or ""))),
            "page": tk.StringVar(value=(citation or {}).get("page", "")),
            "confidence": tk.StringVar(value=(citation or {}).get("confidence", "Unknown")),
            "proof_status": tk.StringVar(value=(citation or {}).get("proof_status", "Unreviewed")),
            "media_reference": tk.StringVar(value=(citation or {}).get("media_reference", "")),
            "transcription": tk.StringVar(value=(citation or {}).get("transcription", "")),
            "comment": tk.StringVar(value=(citation or {}).get("comment", "")),
        }
        definitions = (
            ("target_type", "Тип объекта", TARGET_TYPES), ("target_id", "ID объекта", None),
            ("page", "Страница", None), ("confidence", "Достоверность", CONFIDENCE_LEVELS),
            ("proof_status", "Статус доказательства", PROOF_STATUSES),
            ("media_reference", "ID/путь медиа", None), ("transcription", "Транскрипция", None),
            ("comment", "Комментарий", None),
        )
        for row, (key, label, options) in enumerate(definitions):
            tk.Label(dialog, text=f"{label}:").grid(row=row, column=0, sticky="w", padx=12, pady=4)
            control = ttk.Combobox(dialog, textvariable=values[key], values=options, state="readonly", width=40) if options else tk.Entry(dialog, textvariable=values[key], width=43)
            control.grid(row=row, column=1, sticky="ew", padx=(0, 12), pady=4)

        def save():
            data = {key: variable.get().strip() for key, variable in values.items()}
            operation = EvidenceOperation(
                "edit_citation" if citation else "attach_citation",
                source_id=source_id,
                citation_id=int(citation["id"]) if citation else 0,
                target_type=data.pop("target_type"),
                target_id=data.pop("target_id"),
                data=data,
            )
            dialog.destroy()
            self._run_evidence_operations((operation,))

        tk.Button(dialog, text="Сохранить", command=save).grid(row=len(definitions), column=1, sticky="e", padx=12, pady=12)

    def _detach_evidence_citation(self) -> None:
        citation_id = self._selected_evidence_citation_id()
        if citation_id is not None and messagebox.askyesno(
            "Цитата", "Открепить выбранную цитату?", parent=self._evidence_window,
        ):
            self._run_evidence_operations((EvidenceOperation("detach_citation", citation_id=citation_id),))

    def _run_evidence_operations(self, operations) -> None:
        read_only = bool(self._evidence_read_only_var and self._evidence_read_only_var.get())

        def execute(repository, _context):
            service = EvidenceService(repository, read_only=read_only)
            return service.execute(service.preview(operations))

        return self._submit_repository_task(
            "Изменение доказательств", execute, self._complete_evidence_operations,
            on_error=lambda error: messagebox.showerror(
                "Источники и доказательства", str(error), parent=self._evidence_window
            ),
        )

    def _complete_evidence_operations(self, result) -> None:
        self._get_undo_manager().record_applied(EvidenceAppliedCommand(self.repository, result))
        self.refresh_views()

    def _open_evidence_usage(self, _event=None) -> None:
        if self._evidence_usage_tree is None or not self._evidence_usage_tree.selection() or self._evidence_model is None:
            return
        citation_id = int(self._evidence_usage_tree.selection()[0].split("-", 1)[1])
        usage = next(item for item in self._evidence_model.usages if int(item["id"]) == citation_id)
        if usage.get("linked_person_id") is not None:
            self.show_person(int(usage["linked_person_id"]))

    def _export_evidence(self, export_format) -> None:
        destination = filedialog.asksaveasfilename(
            parent=self._evidence_window,
            title="Экспорт доказательств",
            initialdir=str(EXPORT_DIR),
            initialfile=f"evidence.{export_format}",
            defaultextension=f".{export_format}",
            filetypes=[(export_format.upper(), f"*.{export_format}")],
        )
        if not destination:
            return
        return self._submit_repository_task(
            "Экспорт доказательств",
            lambda repository, _context: getattr(EvidenceService(repository), f"export_{export_format}")(destination),
            lambda path: messagebox.showinfo("Экспорт", f"Файл сохранён: {path}", parent=self._evidence_window),
            on_error=lambda error: messagebox.showerror("Экспорт", str(error), parent=self._evidence_window),
        )

    def _close_evidence_manager(self) -> None:
        if self._evidence_window is not None:
            try:
                self._evidence_window.destroy()
            except Exception:
                pass
        self._evidence_window = None
        self._evidence_model = None
        self._evidence_source_tree = None
        self._evidence_citation_tree = None
        self._evidence_usage_tree = None
        self._evidence_details_text = None
        self._evidence_diagnostics_text = None
        self._evidence_read_only_var = None
        self._evidence_mutation_buttons = []

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
        if hasattr(self, "task_manager"):
            return self._submit_repository_task(
                "Хронология",
                lambda repository, _context: FamilyTimelineService(repository).build_timeline(),
                self._apply_family_timeline_entries,
            )
        return self._apply_family_timeline_entries(
            self.family_timeline_service.build_timeline()
        )

    def _apply_family_timeline_entries(self, entries) -> None:
        self._family_timeline_entries = entries
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
        if hasattr(self, "workspace_integration_service"):
            self._set_workspace_person(person_id, "main")
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
            command = AddPersonCommand(self.repository, payload)
            person_id = self._get_undo_manager().execute(command)
            person = self.repository.get_person_record(person_id)
            self._record_audit_command(
                "person_create", command, database_id=person_id,
                gedcom_id=person["gedcom_id"] if person else "",
                description="Создана карточка человека.", service="viewer",
            )
        else:
            command = EditPersonCommand(self.repository, person_id, payload)
            self._get_undo_manager().execute(command)
            person = self.repository.get_person_record(person_id)
            self._record_audit_command(
                "person_edit", command, database_id=person_id,
                gedcom_id=person["gedcom_id"] if person else "",
                description="Изменена карточка человека.", service="viewer",
            )
        self.current_person_id = person_id
        return person_id

    def _delete_person(self, person_id):
        if person_id is None:
            return False
        if messagebox.askyesno("Удаление", "Удалить выбранного человека?"):
            person = self.repository.get_person_record(person_id)
            command = DeletePersonCommand(self.repository, person_id)
            deleted = self._get_undo_manager().execute(command)
            self._record_audit_command(
                "person_delete", command, database_id=person_id,
                gedcom_id=person["gedcom_id"] if person else "",
                description="Удалена карточка человека.", service="viewer",
            )
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
        if getattr(self, "_evidence_window", None) is not None:
            self._load_evidence_manager()

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
        if hasattr(self, "task_manager"):
            self.task_manager.shutdown()
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
