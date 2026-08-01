import re
import tkinter as tk
import unicodedata
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from config import DB_NAME
from database import backup_database, restore_database
from repository import PersonRepository
from repository.person_event_service import PersonEventService
from repository.relationship_service import RelationshipService


class GenealogyViewer:
    def __init__(self, root):
        self.root = root
        self.repository = PersonRepository(DB_NAME)
        self.relationship_service = RelationshipService(self.repository)
        self.event_service = PersonEventService(self.repository)
        self.current_person_id = None
        self.current_person_gedcom_id = None
        self.root.title("Genealogy Viewer")
        self.root.geometry("1000x700")
        self._create_widgets()
        self.search_people()

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
        self.show_person(person_id)

    def show_person(self, person_id):
        person = self.repository.get_person(person_id)
        if not person:
            messagebox.showerror("Ошибка", "Человек не найден.")
            return
        self.current_person_id = person_id
        self.current_person_gedcom_id = person[0]
        gedcom_id, last_name, first_name, sex, birth_date, birth_place, death_date, death_place, occupation, note = person
        message = f"{last_name or ''} {first_name or ''}\n{birth_date or ''} - {death_date or ''}"
        if occupation:
            message += f"\n\nЗанятие: {occupation}"
        if note:
            message += f"\n\nПримечания:\n{note}"
        self._refresh_family_tree()
        self._show_person_events(person_id)
        messagebox.showinfo("Карточка", message)

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

    def _manage_person_event(self, dialog, person_id, event_id):
        event_window = tk.Toplevel(dialog)
        event_window.title("Событие")
        event_window.geometry("480x320")
        event_window.transient(dialog)
        event_window.grab_set()

        fields = {}
        form = tk.Frame(event_window)
        form.pack(fill="both", expand=True, padx=12, pady=12)

        event_types = ["birth", "death", "marriage", "divorce", "burial", "residence", "occupation", "custom"]
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

    def build_family_tree_nodes(self, gedcom_id):
        row = self.repository.get_person_by_gedcom_id(gedcom_id)
        if not row:
            return []
        person = self.repository.get_person(row[0])
        if not person:
            return []
        _, last_name, first_name, *_ = person
        nodes = [
            {
                "id": gedcom_id,
                "name": self.format_name(last_name, first_name) or gedcom_id,
                "role": "center",
                "x": 0,
                "y": 0,
            }
        ]
        for index, (p_last, p_first, p_gedcom) in enumerate(self.repository.get_parents(gedcom_id)):
            nodes.append(
                {
                    "id": p_gedcom,
                    "name": self.format_name(p_last, p_first) or p_gedcom,
                    "role": "parent",
                    "x": -220,
                    "y": -120 + index * 70,
                }
            )
        for index, (s_last, s_first, s_gedcom) in enumerate(self.repository.get_spouses(gedcom_id)):
            nodes.append(
                {
                    "id": s_gedcom,
                    "name": self.format_name(s_last, s_first) or s_gedcom,
                    "role": "spouse",
                    "x": 220,
                    "y": -120 + index * 70,
                }
            )
        for index, (c_last, c_first, c_gedcom) in enumerate(self.repository.get_children(gedcom_id)):
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

    def insert_people(self, text, rows, empty_text):
        if not rows:
            text.insert("end", f"  {empty_text}\n")
            return
        for last_name, first_name, gedcom_id in rows:
            name = self.format_name(last_name, first_name) or "(без имени)"
            display_text = f"  {name} [{gedcom_id}]\n"
            text.insert("end", display_text)
            tag_name = f"person:{gedcom_id}"
            start_index = text.index("end-1c linestart")
            end_index = text.index("end-1c")
            text.tag_add(tag_name, start_index, end_index)
            text.tag_configure(tag_name, foreground="blue", underline=True)
            text.tag_bind(tag_name, "<Button-1>", lambda _event, gid=gedcom_id: self.open_related_person(gid))

    def open_related_person(self, gedcom_id):
        person = self.repository.get_person_by_gedcom_id(gedcom_id)
        if not person:
            messagebox.showerror("Ошибка", "Человек не найден.")
            return
        self.show_person(person[0])

    def open_relationship_editor(self):
        if self.current_person_id is None:
            messagebox.showwarning("Связи", "Сначала выберите человека из списка.")
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("Редактор отношений")
        dialog.geometry("700x500")
        dialog.transient(self.root)
        dialog.grab_set()

        person = self.repository.get_person(self.current_person_id)
        if not person:
            dialog.destroy()
            return
        current_gedcom_id = person[0]

        families = []
        for family_id in self.repository.cur.execute("SELECT id FROM families ORDER BY id").fetchall():
            family = self.repository.get_family(family_id[0])
            if family and (family.get("husband") == current_gedcom_id or family.get("wife") == current_gedcom_id or current_gedcom_id in family.get("children", [])):
                families.append(family)

        tk.Label(dialog, text=f"Отношения для {self.format_name(person[1], person[2])}").pack(anchor="w", padx=12, pady=12)

        body = tk.Frame(dialog)
        body.pack(fill="both", expand=True, padx=12, pady=(0, 12))
        listbox = tk.Listbox(body, height=8)
        listbox.pack(fill="x")
        for family in families:
            listbox.insert("end", f"{family['gedcom_id']} | husband={family['husband']} wife={family['wife']} children={family['children']}")

        controls = tk.Frame(body)
        controls.pack(fill="x", pady=(8, 0))
        tk.Button(controls, text="Создать", command=lambda: self._create_relationship(dialog, current_gedcom_id)).pack(side="left")
        tk.Button(controls, text="Изменить", command=lambda: self._edit_relationship(dialog, current_gedcom_id, listbox)).pack(side="left", padx=(8, 0))
        tk.Button(controls, text="Удалить", command=lambda: self._delete_relationship(dialog, current_gedcom_id, listbox)).pack(side="left", padx=(8, 0))

    def _create_relationship(self, dialog, current_gedcom_id):
        relationship_window = tk.Toplevel(dialog)
        relationship_window.title("Создать семью")
        relationship_window.geometry("520x360")
        relationship_window.transient(dialog)
        relationship_window.grab_set()

        entries = {}
        form = tk.Frame(relationship_window)
        form.pack(fill="both", expand=True, padx=12, pady=12)

        for label, key in [("Отец", "husband"), ("Мать", "wife"), ("Ребёнок", "child")]:
            tk.Label(form, text=label).grid(row=len(entries), column=0, sticky="w", pady=4)
            entry = tk.Entry(form)
            entry.grid(row=len(entries), column=1, sticky="ew", padx=(8, 0), pady=4)
            entries[key] = entry

        def save():
            try:
                self.relationship_service.create_family(
                    husband_gedcom_id=entries["husband"].get().strip(),
                    wife_gedcom_id=entries["wife"].get().strip(),
                    child_gedcom_ids=[value for value in [entries["child"].get().strip()] if value],
                )
                relationship_window.destroy()
                dialog.destroy()
                self.refresh_views()
                messagebox.showinfo("Сохранено", "Семейная связь создана.")
            except ValueError as error:
                messagebox.showerror("Ошибка", str(error), parent=relationship_window)

        tk.Button(form, text="Сохранить", command=save).grid(row=3, column=1, sticky="e", pady=(12, 0))

    def _edit_relationship(self, dialog, current_gedcom_id, listbox):
        selection = listbox.curselection()
        if not selection:
            messagebox.showwarning("Выбор", "Сначала выберите семейную связь.")
            return
        family_id = self.repository.cur.execute("SELECT id FROM families ORDER BY id").fetchall()[selection[0]][0]
        family = self.repository.get_family(family_id)
        if not family:
            return
        relationship_window = tk.Toplevel(dialog)
        relationship_window.title("Изменить семью")
        relationship_window.geometry("520x360")
        relationship_window.transient(dialog)
        relationship_window.grab_set()

        entries = {}
        form = tk.Frame(relationship_window)
        form.pack(fill="both", expand=True, padx=12, pady=12)

        for label, key, default in [("Отец", "husband", family.get("husband", "")), ("Мать", "wife", family.get("wife", "")), ("Ребёнок", "child", ",".join(family.get("children", [])))]:
            tk.Label(form, text=label).grid(row=len(entries), column=0, sticky="w", pady=4)
            entry = tk.Entry(form)
            entry.insert(0, default)
            entry.grid(row=len(entries), column=1, sticky="ew", padx=(8, 0), pady=4)
            entries[key] = entry

        def save():
            try:
                self.relationship_service.update_family(
                    family_id,
                    husband_gedcom_id=entries["husband"].get().strip(),
                    wife_gedcom_id=entries["wife"].get().strip(),
                    child_gedcom_ids=[value for value in entries["child"].get().split(",") if value.strip()],
                )
                relationship_window.destroy()
                dialog.destroy()
                self.refresh_views()
                messagebox.showinfo("Сохранено", "Семейная связь обновлена.")
            except ValueError as error:
                messagebox.showerror("Ошибка", str(error), parent=relationship_window)

        tk.Button(form, text="Сохранить", command=save).grid(row=3, column=1, sticky="e", pady=(12, 0))

    def _delete_relationship(self, dialog, current_gedcom_id, listbox):
        selection = listbox.curselection()
        if not selection:
            messagebox.showwarning("Выбор", "Сначала выберите семейную связь.")
            return
        family_id = self.repository.cur.execute("SELECT id FROM families ORDER BY id").fetchall()[selection[0]][0]
        if messagebox.askyesno("Удаление", "Удалить выбранную семейную связь?"):
            self.relationship_service.delete_family(family_id)
            dialog.destroy()
            self.refresh_views()
            messagebox.showinfo("Удалено", "Семейная связь удалена.")

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
