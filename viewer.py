import json
import re
import queue
import threading
import time
import tkinter as tk
import unicodedata
import os
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Mapping

try:
    from PIL import Image, ImageTk
except Exception:  # pragma: no cover - optional dependency
    Image = None
    ImageTk = None

from config import DATA_DIR
from config import DB_NAME
from database import backup_database, restore_database
from integrity_service import IntegrityCheckService
from recovery_wizard_service import RecoveryRecord, RecoveryWizardService
from repository import PersonRepository
from repository.person_attachment_service import PersonAttachmentService
from repository.person_event_service import PersonEventService
from repository.person_life_map_service import PersonLifeMapService
from repository.person_timeline_service import PersonTimelineService
from repository.relationship_service import RelationshipService


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
        tk.StringVar = _FallbackStringVar
        tk.Toplevel = _FallbackToplevel
        tk.Listbox = _FallbackListbox
        ttk.Treeview = _FallbackTreeview
        ttk.Scrollbar = _FallbackScrollbar
        ttk.Combobox = _FallbackCombobox
        ttk.Progressbar = _FallbackProgressbar
        ttk.Notebook = _FallbackNotebook


_install_tk_fallback()


class GenealogyViewer:
    def __init__(self, root):
        self.root = root
        self.repository = PersonRepository(DB_NAME)
        self.relationship_service = RelationshipService(self.repository)
        self.event_service = PersonEventService(self.repository)
        self.timeline_service = PersonTimelineService(self.repository)
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
        self.root.title("Genealogy Viewer")
        self.root.geometry("1000x700")
        self._create_widgets()
        self.search_people()

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
        dialog = tk.Toplevel(self._person_dialog or self.root)
        dialog.title(title)
        dialog.geometry("720x420")
        dialog.transient(self._person_dialog or self.root)
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
        dialog = tk.Toplevel(self._person_dialog or self.root)
        dialog.title(title)
        dialog.geometry("360x140")
        dialog.transient(self._person_dialog or self.root)
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
        dialog = tk.Toplevel(self._person_dialog or self.root)
        dialog.title(title)
        dialog.geometry("520x360")
        dialog.transient(self._person_dialog or self.root)
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

        dialog = tk.Toplevel(self._person_dialog or self.root)
        dialog.title("Выберите второго родителя")
        dialog.geometry("620x320")
        dialog.transient(self._person_dialog or self.root)
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
            callback()
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

        dialog = tk.Toplevel(self.root)
        dialog.title("Отчет проверки базы")
        dialog.geometry("980x700")
        dialog.transient(self.root)
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

        dialog = tk.Toplevel(self.root)
        dialog.title("Проверка базы")
        dialog.geometry("460x180")
        dialog.transient(self.root)

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
        path = Path(file_path).expanduser()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError("Файл изображения не найден")

        suffix = path.suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".bmp", ".gif"}:
            raise ValueError("Неподдерживаемый формат изображения")

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

        dialog = tk.Toplevel(self._person_dialog or self.root)
        dialog.title("Переименовать документ")
        dialog.geometry("420x140")
        dialog.transient(self._person_dialog or self.root)
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

        dialog = tk.Toplevel(self._person_dialog or self.root)
        dialog.title("Изменить описание")
        dialog.geometry("520x220")
        dialog.transient(self._person_dialog or self.root)
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
        dialog = tk.Toplevel(self._person_dialog or self.root)
        dialog.title("Источник")
        dialog.geometry("520x300")
        dialog.transient(self._person_dialog or self.root)
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

        dialog = tk.Toplevel(self._person_dialog or self.root)
        dialog.title("Ручная коррекция координат")
        dialog.geometry("420x220")
        dialog.transient(self._person_dialog or self.root)
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
        snapshot = self.repository.get_person_record(record.person_id)
        try:
            self.recovery_wizard_service.update_existing_person(record.person_id, self._batch_form_data())
        except Exception as exc:
            messagebox.showerror("Пакетный режим", str(exc), parent=self._batch_window)
            return "break"

        saved_index = self._batch_index
        self._batch_last_save = {"person_id": record.person_id, "snapshot": snapshot, "index": saved_index}
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
        try:
            self.recovery_wizard_service.restore_existing_person(undo["person_id"], undo["snapshot"])
        except Exception as exc:
            messagebox.showerror("Пакетный режим", str(exc), parent=self._batch_window)
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

        win = tk.Toplevel(self.root)
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
        win = tk.Toplevel(self.root)
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
            self.recovery_wizard_service.update_existing_person(record.person_id, data)
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

        dialog = tk.Toplevel(self._recovery_window)
        dialog.title("Найденные совпадения")
        dialog.geometry(RECOVERY_MATCH_WINDOW_GEOMETRY)
        dialog.transient(self._recovery_window)

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
        top = tk.Frame(self.root)
        top.pack(fill="x", padx=10, pady=10)

        tk.Label(top, text="Имя или фамилия:").pack(side="left")
        self.search_entry = tk.Entry(top, width=30)
        self.search_entry.pack(side="left", padx=5)
        self.search_entry.bind("<Return>", lambda _event: self.search_people())

        tk.Button(top, text="Поиск", command=self.search_people).pack(side="left", padx=(10, 5))
        self.status_label = tk.Label(top, text="")
        self.status_label.pack(side="left", padx=12)

        self.backup_button = tk.Button(top, text="Backup database", command=self.backup_database)
        self.backup_button.pack(side="left", padx=(10, 5))
        self.restore_button = tk.Button(top, text="Restore database", command=self.restore_database)
        self.restore_button.pack(side="left", padx=(0, 5))
        self.relationship_button = tk.Button(top, text="Edit relationships", command=self.open_relationship_editor)
        self.relationship_button.pack(side="left")
        self.integrity_button = tk.Button(top, text="Проверка базы", command=self.open_integrity_report)
        self.integrity_button.pack(side="left", padx=(10, 0))
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

    def search_people(self):
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
            dialog = tk.Toplevel(self.root)
            dialog.geometry("860x620")
            dialog.transient(self.root)
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
        dialog = tk.Toplevel(self.root)
        dialog.title("События")
        dialog.geometry("640x420")
        dialog.transient(self.root)
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
        event_window = tk.Toplevel(dialog)
        event_window.title("Событие")
        event_window.geometry("480x320")
        event_window.transient(dialog)
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
        dialog = tk.Toplevel(self.root)
        dialog.title("Редактор человека")
        dialog.geometry("640x520")
        dialog.transient(self.root)
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
            person_id = self.repository.create_person(payload)
        else:
            self.repository.update_person(person_id, payload)
        self.current_person_id = person_id
        return person_id

    def _delete_person(self, person_id):
        if person_id is None:
            return False
        if messagebox.askyesno("Удаление", "Удалить выбранного человека?"):
            deleted = self.repository.delete_person(person_id)
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

        dialog = tk.Toplevel(self.root)
        dialog.title("Редактор отношений")
        dialog.geometry("880x620")
        dialog.transient(self.root)
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
    root = tk.Tk()
    app = GenealogyViewer(root)

    def close_application():
        app.close()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close_application)
    root.mainloop()


if __name__ == "__main__":
    main()
