import tkinter as tk
from tkinter import messagebox, ttk

from repository import PersonRepository
from config import DB_NAME


class GenealogyViewer:
    def __init__(self, root):
        self.root = root
        self.repository = PersonRepository(DB_NAME)
        self.root.title("Genealogy Viewer")
        self.root.geometry("800x600")

        top = tk.Frame(self.root)
        top.pack(fill="x", padx=10, pady=10)
        tk.Label(top, text="Фамилия:").pack(side="left")

        self.search_entry = tk.Entry(top, width=30)
        self.search_entry.pack(side="left", padx=5)
        self.search_entry.bind("<Return>", lambda _e: self.search_people())

        tk.Button(top, text="Поиск", command=self.search_people).pack(side="left")

        self.tree = ttk.Treeview(self.root, columns=("id", "name", "birth", "death"), show="headings")
        self.tree.heading("id", text="ID")
        self.tree.heading("name", text="Имя")
        self.tree.heading("birth", text="Рождение")
        self.tree.heading("death", text="Смерть")
        self.tree.pack(fill="both", expand=True, padx=10, pady=10)
        self.tree.bind("<Double-1>", self.open_person)

        self.search_people()

    def search_people(self):
        query = self.search_entry.get().strip()
        for item in self.tree.get_children():
            self.tree.delete(item)
        rows = self.repository.list_people(query)
        for person_id, last_name, first_name, birth_date, death_date in rows:
            full_name = f"{last_name or ''} {first_name or ''}".strip()
            self.tree.insert("", "end", values=(person_id, full_name, birth_date or "", death_date or ""))

    def open_person(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        person_id = self.tree.item(sel[0])["values"][0]
        person = self.repository.get_person(person_id)
        if not person:
            messagebox.showerror("Ошибка", "Человек не найден.")
            return
        gedcom_id, last_name, first_name, sex, birth_date, birth_place, death_date, death_place, occupation, note = person
        messagebox.showinfo("Карточка", f"{last_name or ''} {first_name or ''}\n{birth_date or ''} - {death_date or ''}")

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


    def update_person(self, person_id, last_name, first_name, sex, birth_date, death_date, note):
        self.cursor.execute(
            """
            UPDATE people
            SET last_name = ?, first_name = ?, sex = ?, birth_date = ?, death_date = ?, note = ?
            WHERE id = ?
            """,
            (last_name, first_name, sex, birth_date, death_date, note, person_id),
        )
        self.connection.commit()
        return self.cursor.rowcount > 0

    def get_parents(self, gedcom_id):
        self.cursor.execute(
            """
            SELECT DISTINCT p.id,
                TRIM(COALESCE(p.last_name, '')) AS last_name,
                TRIM(COALESCE(p.first_name, '')) AS first_name
            FROM families AS f
            JOIN family_children AS fc ON f.gedcom_id = fc.family_id
            JOIN people AS p ON p.gedcom_id = f.husband_id OR p.gedcom_id = f.wife_id
            WHERE fc.child_id = ? AND p.gedcom_id <> ?
            ORDER BY last_name, first_name
            """,
            (gedcom_id, gedcom_id),
        )
        return self.cursor.fetchall()

    def get_spouses(self, gedcom_id):
        self.cursor.execute(
            """
            SELECT DISTINCT p.id,
                TRIM(COALESCE(p.last_name, '')) AS last_name,
                TRIM(COALESCE(p.first_name, '')) AS first_name
            FROM families AS f
            JOIN people AS p
                ON p.gedcom_id = CASE
                    WHEN f.husband_id = ? THEN f.wife_id
                    WHEN f.wife_id = ? THEN f.husband_id
                END
            WHERE (f.husband_id = ? OR f.wife_id = ?) AND p.gedcom_id <> ?
            ORDER BY last_name, first_name
            """,
            (gedcom_id, gedcom_id, gedcom_id, gedcom_id, gedcom_id),
        )
        return self.cursor.fetchall()

    def get_children(self, gedcom_id):
        self.cursor.execute(
            """
            SELECT DISTINCT c.id,
                TRIM(COALESCE(c.last_name, '')) AS last_name,
                TRIM(COALESCE(c.first_name, '')) AS first_name
            FROM families AS f
            JOIN family_children AS fc ON f.gedcom_id = fc.family_id
            JOIN people AS c ON c.gedcom_id = fc.child_id
            WHERE (f.husband_id = ? OR f.wife_id = ?) AND c.gedcom_id <> ?
            ORDER BY last_name, first_name
            """,
            (gedcom_id, gedcom_id, gedcom_id),
        )
        return self.cursor.fetchall()


class GenealogyViewer:
    def __init__(self, root):
        self.root = root
        self.repository = GenealogyRepository(DB_NAME)
        self.card_window = None
        self.card_text = None
        self.back_button = None
        self.forward_button = None
        self.edit_button = None
        self.current_person_id = None
        self.card_history = []
        self.card_history_index = -1
        self.root.title("Genealogy Viewer")
        self.root.geometry("1000x700")
        self._create_widgets()
        self.search_people()
        self.search_entry.focus_set()

    def _create_widgets(self):
        top = tk.Frame(self.root)
        top.pack(fill="x", padx=10, pady=10)
        tk.Label(top, text="Имя или фамилия:").pack(side="left")
        self.search_entry = tk.Entry(top, width=30)
        self.search_entry.pack(side="left", padx=5)
        self.search_entry.bind("<Return>", self._search_from_event)
        tk.Button(top, text="Поиск", command=self.search_people).pack(side="left")
        self.status_label = tk.Label(top, text="Загрузка...")
        self.status_label.pack(side="left", padx=12)

        table_frame = tk.Frame(self.root)
        table_frame.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.tree = ttk.Treeview(table_frame, columns=("id", "name", "birth", "death"), show="headings")
        tree_scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scrollbar.set)
        headings = {"id": "ID", "name": "Имя", "birth": "Рождение", "death": "Смерть"}
        widths = {"id": 80, "name": 350, "birth": 120, "death": 120}
        for column, heading in headings.items():
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=widths[column])
        self.tree.pack(side="left", fill="both", expand=True)
        tree_scrollbar.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self.open_person)

    def _search_from_event(self, _event):
        self.search_people()

    def _clear_results(self):
        for item in self.tree.get_children():
            self.tree.delete(item)

    def search_people(self):
        query = self.search_entry.get().strip()
        self._clear_results()
        self.status_label.config(text="Поиск..." if query else "Загрузка...")
        self.root.update_idletasks()
        people = self.repository.find_people(query)
        for person_id, last_name, first_name, birth_date, death_date in people:
            full_name = f"{last_name or ''} {first_name or ''}".strip()
            self.tree.insert("", "end", values=(person_id, full_name, birth_date or "", death_date or ""))
        self.status_label.config(text=f"Показано: {len(people)}" if people else "Ничего не найдено")

    def open_person(self, _event):
        selected = self.tree.selection()
        if selected:
            self.show_person(self.tree.item(selected[0])["values"][0])

    def _create_person_window(self):
        self.card_window = tk.Toplevel(self.root)
        self.card_window.geometry("700x600")
        self.card_window.protocol("WM_DELETE_WINDOW", self._close_person_window)
        navigation = tk.Frame(self.card_window)
        navigation.pack(fill="x", padx=8, pady=8)
        self.back_button = tk.Button(navigation, text="← Назад", command=lambda: self._move_in_history(-1))
        self.back_button.pack(side="left")
        self.forward_button = tk.Button(navigation, text="Вперёд →", command=lambda: self._move_in_history(1))
        self.forward_button.pack(side="left", padx=(8, 0))
        self.edit_button = tk.Button(navigation, text="Изменить", command=self._open_edit_dialog)
        self.edit_button.pack(side="right")

        content_frame = tk.Frame(self.card_window)
        content_frame.pack(fill="both", expand=True)
        self.card_text = tk.Text(content_frame, wrap="word", cursor="arrow")
        text_scrollbar = ttk.Scrollbar(content_frame, orient="vertical", command=self.card_text.yview)
        self.card_text.configure(yscrollcommand=text_scrollbar.set)
        self.card_text.pack(side="left", fill="both", expand=True)
        text_scrollbar.pack(side="right", fill="y")

    def _close_person_window(self):
        if self.card_window is not None:
            self.card_window.destroy()
        self.card_window = None
        self.card_text = None
        self.back_button = None
        self.forward_button = None
        self.edit_button = None
        self.current_person_id = None
        self.card_history = []
        self.card_history_index = -1

    def _move_in_history(self, offset):
        target_index = self.card_history_index + offset
        if 0 <= target_index < len(self.card_history):
            self.card_history_index = target_index
            self.show_person(self.card_history[target_index], add_to_history=False)

    def _update_navigation_buttons(self):
        if self.back_button is None or self.forward_button is None:
            return
        self.back_button.config(state="normal" if self.card_history_index > 0 else "disabled")
        self.forward_button.config(
            state="normal" if self.card_history_index < len(self.card_history) - 1 else "disabled"
        )

    def show_person(self, person_id, add_to_history=True):
        person = self.repository.get_person(person_id)
        if not person:
            messagebox.showerror("Ошибка", "Человек не найден.")
            return
        if self.card_window is None or not self.card_window.winfo_exists():
            self._create_person_window()
        if add_to_history:
            if self.card_history_index < len(self.card_history) - 1:
                self.card_history = self.card_history[:self.card_history_index + 1]
            if not self.card_history or self.card_history[-1] != person_id:
                self.card_history.append(person_id)
            self.card_history_index = len(self.card_history) - 1

        self.current_person_id = person_id
        gedcom_id, last_name, first_name, sex, birth_date, death_date, note = person
        parents = self.repository.get_parents(gedcom_id)
        spouses = self.repository.get_spouses(gedcom_id)
        children = self.repository.get_children(gedcom_id)
        parents, spouses, children = self._remove_conflicting_relations(parents, spouses, children)
        title = f"{last_name or ''} {first_name or ''}".strip() or "Карточка человека"
        self.card_window.title(title)
        self.card_window.deiconify()
        self.card_window.lift()
        self.card_text.config(state="normal")
        self.card_text.delete("1.0", "end")
        self._insert_person_details(self.card_text, last_name, first_name, sex, birth_date, death_date, note)
        self._insert_relatives(self.card_text, "Родители:", parents, "неизвестны")
        self._insert_relatives(self.card_text, "\nСупруги:", spouses, "нет")
        self._insert_relatives(self.card_text, "\nДети:", children, "нет")
        self.card_text.config(state="disabled")
        self.card_text.yview_moveto(0)
        self._update_navigation_buttons()

    def _open_edit_dialog(self):
        if self.current_person_id is None:
            return
        person = self.repository.get_person(self.current_person_id)
        if not person:
            messagebox.showerror("Ошибка", "Человек не найден.")
            return
        _gedcom_id, last_name, first_name, sex, birth_date, death_date, note = person
        dialog = tk.Toplevel(self.card_window)
        dialog.title("Изменение данных")
        dialog.geometry("520x470")
        dialog.transient(self.card_window)
        dialog.grab_set()

        form = tk.Frame(dialog)
        form.pack(fill="both", expand=True, padx=12, pady=12)
        fields = [
            ("Фамилия", last_name or ""),
            ("Имя", first_name or ""),
            ("Пол", sex or ""),
            ("Рождение", birth_date or ""),
            ("Смерть", death_date or ""),
        ]
        entries = {}
        for row, (label, value) in enumerate(fields):
            tk.Label(form, text=label + ":", anchor="w").grid(row=row, column=0, sticky="w", pady=4)
            entry = tk.Entry(form)
            entry.insert(0, value)
            entry.grid(row=row, column=1, sticky="ew", padx=(10, 0), pady=4)
            entries[label] = entry

        tk.Label(form, text="Примечания:", anchor="nw").grid(row=5, column=0, sticky="nw", pady=4)
        note_text = tk.Text(form, height=10, wrap="word")
        note_text.insert("1.0", note or "")
        note_text.grid(row=5, column=1, sticky="nsew", padx=(10, 0), pady=4)
        form.columnconfigure(1, weight=1)
        form.rowconfigure(5, weight=1)

        buttons = tk.Frame(dialog)
        buttons.pack(fill="x", padx=12, pady=(0, 12))
        tk.Button(buttons, text="Отмена", command=dialog.destroy).pack(side="right")
        tk.Button(
            buttons,
            text="Сохранить",
            command=lambda: self._save_person_changes(dialog, entries, note_text),
        ).pack(side="right", padx=(0, 8))
        entries["Фамилия"].focus_set()

    def _save_person_changes(self, dialog, entries, note_text):
        last_name = entries["Фамилия"].get().strip()
        first_name = entries["Имя"].get().strip()
        if not last_name and not first_name:
            messagebox.showwarning("Проверка данных", "Укажите имя или фамилию.", parent=dialog)
            return
        updated = self.repository.update_person(
            self.current_person_id,
            last_name,
            first_name,
<<<<<<< HEAD
            entries["Пол"].get().strip(),
            entries["Рождение"].get().strip(),
            entries["Смерть"].get().strip(),
            note_text.get("1.0", "end-1c").strip(),
        )
        if not updated:
            messagebox.showerror("Ошибка", "Не удалось сохранить изменения.", parent=dialog)
            return
        dialog.destroy()
        self.search_people()
        self.show_person(self.current_person_id, add_to_history=False)
        messagebox.showinfo("Сохранено", "Данные человека обновлены.", parent=self.card_window)
=======
            sex,
            birth_date,
            birth_place,
            death_date,
            death_place,
            occupation,
            note,
        ) = person
        self.current_person_gedcom_id = gedcom_id
>>>>>>> 1637f85 (GenealogyDB 2.0 - modular architecture, repositories, relationship navigation, graphical family tree)

    @staticmethod
    def _normalize_name(value):
        value = unicodedata.normalize("NFKC", value or "").casefold().replace("ё", "е")
        return " ".join(re.findall(r"[a-zа-я0-9]+", value))

    @classmethod
    def _relative_key(cls, relative):
        _person_id, last_name, first_name = relative
        return cls._normalize_name(f"{last_name or ''} {first_name or ''}")

<<<<<<< HEAD
    @classmethod
    def _deduplicate_relatives(cls, relatives):
        unique = []
        seen = set()
        for relative in relatives:
            key = cls._relative_key(relative)
            if key and key not in seen:
                seen.add(key)
                unique.append(relative)
        return unique

    @classmethod
    def _remove_conflicting_relations(cls, parents, spouses, children):
        parents = cls._deduplicate_relatives(parents)
        spouses = cls._deduplicate_relatives(spouses)
        children = cls._deduplicate_relatives(children)
        parent_keys = {cls._relative_key(relative) for relative in parents}
        child_keys = {cls._relative_key(relative) for relative in children}
        spouses = [
            relative for relative in spouses
            if cls._relative_key(relative) not in parent_keys
            and cls._relative_key(relative) not in child_keys
        ]
        children = [relative for relative in children if cls._relative_key(relative) not in parent_keys]
        return parents, spouses, children

    @staticmethod
    def _insert_person_details(text, last_name, first_name, sex, birth_date, death_date, note):
=======
        self._populate_person_details(text, gedcom_id, last_name, first_name, sex, birth_date, birth_place, death_date, death_place, occupation, note)
        text.config(state="disabled")

        if self.view_mode.get() == "tree":
            self._refresh_family_tree()

    def _fetch_person(self, person_id):
        return self.repository.get_person(person_id)

    def _populate_person_details(self, text, gedcom_id, last_name, first_name, sex, birth_date, birth_place, death_date, death_place, occupation, note):
        text.insert("end", f"GEDCOM ID: {gedcom_id or ''}\n")
>>>>>>> 1637f85 (GenealogyDB 2.0 - modular architecture, repositories, relationship navigation, graphical family tree)
        text.insert("end", f"Фамилия: {last_name or ''}\n")
        text.insert("end", f"Имя: {first_name or ''}\n")
        text.insert("end", f"Пол: {sex or ''}\n")
        text.insert("end", f"Рождение: {birth_date or ''}\n")
        text.insert("end", f"Смерть: {death_date or ''}\n\n")
        if note:
            text.insert("end", "Примечания:\n")
            text.insert("end", note + "\n\n")

<<<<<<< HEAD
    def _insert_relatives(self, text, title, relatives, empty_text):
        text.insert("end", title + "\n")
        if not relatives:
            text.insert("end", f"  {empty_text}\n")
            return
        for person_id, last_name, first_name in relatives:
            full_name = f"{last_name or ''} {first_name or ''}".strip() or "Без имени"
            tag_name = f"person_{person_id}_{text.index('end-1c').replace('.', '_')}"
            text.insert("end", f"  {full_name}\n", tag_name)
            text.tag_config(tag_name, foreground="blue", underline=True)
            text.tag_bind(tag_name, "<Enter>", lambda _event, widget=text: widget.config(cursor="hand2"))
            text.tag_bind(tag_name, "<Leave>", lambda _event, widget=text: widget.config(cursor="arrow"))
            text.tag_bind(
                tag_name,
                "<Double-Button-1>",
                lambda _event, target_id=person_id: self.show_person(target_id),
            )
=======
        text.insert("end", "Родители:\n")
        self.show_parents(text, gedcom_id)

        text.insert("end", "\nСупруги:\n")
        self.show_spouses(text, gedcom_id)

        text.insert("end", "\nДети:\n")
        self.show_children(text, gedcom_id)

        text.insert("end", "\nБратья и сестры:\n")
        self.show_siblings(text, gedcom_id)

    def show_parents(self, text, person_gedcom_id):
        if not person_gedcom_id:
            text.insert("end", "  неизвестны\n")
            return

        rows = self.repository.get_parents(person_gedcom_id)
        self.insert_people(text, rows, "неизвестны")

    def show_spouses(self, text, person_gedcom_id):
        if not person_gedcom_id:
            text.insert("end", "  нет\n")
            return

        rows = self.repository.get_spouses(person_gedcom_id)
        self.insert_people(text, rows, "нет")

    def show_children(self, text, person_gedcom_id):
        if not person_gedcom_id:
            text.insert("end", "  нет\n")
            return

        rows = self.repository.get_children(person_gedcom_id)
        self.insert_people(text, rows, "нет")

    def show_siblings(self, text, person_gedcom_id):
        if not person_gedcom_id:
            text.insert("end", "  нет\n")
            return

        rows = self.repository.get_siblings(person_gedcom_id)
        self.insert_people(text, rows, "нет")

    def build_family_tree_nodes(self, gedcom_id):
        person_id_row = self.repository.get_person_by_gedcom_id(gedcom_id)
        if not person_id_row:
            return []

        person = self.repository.get_person(person_id_row[0])
        if not person:
            return []

        center_name = self.format_name(person[1], person[2])
        nodes = [{"id": gedcom_id, "name": center_name or gedcom_id, "role": "center", "x": 0, "y": 0}]

        parents = self.repository.get_parents(gedcom_id)
        for index, (last_name, first_name, parent_gedcom_id) in enumerate(parents):
            nodes.append({
                "id": parent_gedcom_id,
                "name": self.format_name(last_name, first_name) or parent_gedcom_id,
                "role": "parent",
                "x": -220,
                "y": -120 + index * 70,
            })

        spouses = self.repository.get_spouses(gedcom_id)
        for index, (last_name, first_name, spouse_gedcom_id) in enumerate(spouses):
            nodes.append({
                "id": spouse_gedcom_id,
                "name": self.format_name(last_name, first_name) or spouse_gedcom_id,
                "role": "spouse",
                "x": 220,
                "y": -120 + index * 70,
            })

        children = self.repository.get_children(gedcom_id)
        for index, (last_name, first_name, child_gedcom_id) in enumerate(children):
            nodes.append({
                "id": child_gedcom_id,
                "name": self.format_name(last_name, first_name) or child_gedcom_id,
                "role": "child",
                "x": -120 + index * 120,
                "y": 140,
            })

        siblings = self.repository.get_siblings(gedcom_id)
        for index, (last_name, first_name, sibling_gedcom_id) in enumerate(siblings):
            nodes.append({
                "id": sibling_gedcom_id,
                "name": self.format_name(last_name, first_name) or sibling_gedcom_id,
                "role": "sibling",
                "x": 120 + index * 120,
                "y": 140,
            })

        return nodes

    def _refresh_family_tree(self):
        if not self.current_person_gedcom_id:
            self.family_canvas.render_tree([])
            return
        self.family_canvas.render_tree(
            self.build_family_tree_nodes(self.current_person_gedcom_id),
            self._handle_tree_node_click,
        )

    def _handle_tree_node_click(self, gedcom_id):
        person = self.repository.get_person_by_gedcom_id(gedcom_id)
        if person:
            self.show_person(person[0])

    @staticmethod
    def format_name(last_name, first_name):
        return f"{last_name or ''} {first_name or ''}".strip()

    def insert_people(self, text, rows, empty_text):
        if not rows:
            text.insert("end", f"  {empty_text}\n")
            return

        for last_name, first_name, gedcom_id in rows:
            name = self.format_name(last_name, first_name)
            display_text = f"  {name or '(без имени)'} [{gedcom_id}]"
            tag_name = f"person:{gedcom_id}"
            text.insert("end", display_text + "\n")
            text.tag_add(tag_name, "end-1c", "end")
            text.tag_configure(tag_name, foreground="blue", underline=True)
            text.tag_bind(tag_name, "<Button-1>", lambda _event, target_id=gedcom_id: self.open_related_person(target_id))

    def open_related_person(self, gedcom_id):
        person = self.repository.get_person_by_gedcom_id(gedcom_id)
        if not person:
            messagebox.showerror("Ошибка", "Человек не найден.")
            return

        self.show_person(person[0])
>>>>>>> 1637f85 (GenealogyDB 2.0 - modular architecture, repositories, relationship navigation, graphical family tree)

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
