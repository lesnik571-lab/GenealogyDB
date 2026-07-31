import re
import sqlite3
import tkinter as tk
import unicodedata
from tkinter import messagebox, ttk

from config import DB_NAME


class GenealogyRepository:
    def __init__(self, database_name):
        self.connection = sqlite3.connect(database_name)
        self.cursor = self.connection.cursor()

    def close(self):
        self.connection.close()

    def find_people(self, query="", limit=500):
        search_value = f"%{query}%"
        self.cursor.execute(
            """
            SELECT
                MIN(id) AS id,
                TRIM(COALESCE(last_name, '')) AS last_name,
                TRIM(COALESCE(first_name, '')) AS first_name,
                TRIM(COALESCE(birth_date, '')) AS birth_date,
                TRIM(COALESCE(death_date, '')) AS death_date
            FROM people
            WHERE
                TRIM(COALESCE(last_name, '') || COALESCE(first_name, '')) <> ''
                AND (
                    ? = ''
                    OR last_name LIKE ?
                    OR first_name LIKE ?
                    OR TRIM(COALESCE(last_name, '') || ' ' || COALESCE(first_name, '')) LIKE ?
                )
            GROUP BY
                LOWER(TRIM(COALESCE(last_name, ''))),
                LOWER(TRIM(COALESCE(first_name, ''))),
                LOWER(TRIM(COALESCE(birth_date, ''))),
                LOWER(TRIM(COALESCE(death_date, '')))
            ORDER BY
                CASE WHEN TRIM(COALESCE(last_name, '')) = '' THEN 1 ELSE 0 END,
                last_name,
                first_name,
                birth_date
            LIMIT ?
            """,
            (query, search_value, search_value, search_value, limit),
        )
        return self.cursor.fetchall()

    def get_person(self, person_id):
        self.cursor.execute(
            """
            SELECT gedcom_id, last_name, first_name, sex, birth_date, death_date, note
            FROM people
            WHERE id = ?
            """,
            (person_id,),
        )
        return self.cursor.fetchone()

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

        self.tree = ttk.Treeview(self.root, columns=("id", "name", "birth", "death"), show="headings")
        headings = {"id": "ID", "name": "Имя", "birth": "Рождение", "death": "Смерть"}
        widths = {"id": 80, "name": 350, "birth": 120, "death": 120}
        for column, heading in headings.items():
            self.tree.heading(column, text=heading)
            self.tree.column(column, width=widths[column])
        self.tree.pack(fill="both", expand=True, padx=10, pady=(0, 10))
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

    def show_person(self, person_id):
        person = self.repository.get_person(person_id)
        if not person:
            messagebox.showerror("Ошибка", "Человек не найден.")
            return
        gedcom_id, last_name, first_name, sex, birth_date, death_date, note = person
        parents = self.repository.get_parents(gedcom_id)
        spouses = self.repository.get_spouses(gedcom_id)
        children = self.repository.get_children(gedcom_id)
        parents, spouses, children = self._remove_conflicting_relations(parents, spouses, children)

        window = tk.Toplevel(self.root)
        window.title(f"{last_name or ''} {first_name or ''}".strip() or "Карточка человека")
        window.geometry("700x600")
        text = tk.Text(window, wrap="word", cursor="arrow")
        text.pack(fill="both", expand=True)
        self._insert_person_details(text, last_name, first_name, sex, birth_date, death_date, note)
        self._insert_relatives(text, "Родители:", parents, "неизвестны")
        self._insert_relatives(text, "\nСупруги:", spouses, "нет")
        self._insert_relatives(text, "\nДети:", children, "нет")
        text.config(state="disabled")

    @staticmethod
    def _normalize_name(value):
        value = unicodedata.normalize("NFKC", value or "").casefold().replace("ё", "е")
        return " ".join(re.findall(r"[a-zа-я0-9]+", value))

    @classmethod
    def _relative_key(cls, relative):
        _person_id, last_name, first_name = relative
        return cls._normalize_name(f"{last_name or ''} {first_name or ''}")

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
        children = [
            relative for relative in children
            if cls._relative_key(relative) not in parent_keys
        ]
        return parents, spouses, children

    @staticmethod
    def _insert_person_details(text, last_name, first_name, sex, birth_date, death_date, note):
        text.insert("end", f"Фамилия: {last_name or ''}\n")
        text.insert("end", f"Имя: {first_name or ''}\n")
        text.insert("end", f"Пол: {sex or ''}\n")
        text.insert("end", f"Рождение: {birth_date or ''}\n")
        text.insert("end", f"Смерть: {death_date or ''}\n\n")
        if note:
            text.insert("end", "Примечания:\n")
            text.insert("end", note + "\n\n")

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
            text.tag_bind(tag_name, "<Double-Button-1>", lambda _event, target_id=person_id: self.show_person(target_id))

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
