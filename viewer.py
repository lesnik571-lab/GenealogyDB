import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk

from config import DB_NAME


class GenealogyRepository:
    def __init__(self, database_name):
        self.connection = sqlite3.connect(database_name)
        self.cursor = self.connection.cursor()

    def close(self):
        self.connection.close()

    def find_people(self, query, limit=500):
        search_value = f"%{query}%"
        self.cursor.execute(
            """
            SELECT
                id,
                last_name,
                first_name,
                birth_date,
                death_date
            FROM people
            WHERE
                TRIM(COALESCE(last_name, '') || COALESCE(first_name, '')) <> ''
                AND (
                    last_name LIKE ?
                    OR first_name LIKE ?
                    OR TRIM(COALESCE(last_name, '') || ' ' || COALESCE(first_name, '')) LIKE ?
                )
            ORDER BY
                CASE WHEN TRIM(COALESCE(last_name, '')) = '' THEN 1 ELSE 0 END,
                last_name,
                first_name
            LIMIT ?
            """,
            (search_value, search_value, search_value, limit),
        )
        return self.cursor.fetchall()

    def get_person(self, person_id):
        self.cursor.execute(
            """
            SELECT
                gedcom_id,
                last_name,
                first_name,
                sex,
                birth_date,
                death_date,
                note
            FROM people
            WHERE id = ?
            """,
            (person_id,),
        )
        return self.cursor.fetchone()

    def get_parents(self, gedcom_id):
        self.cursor.execute(
            """
            SELECT DISTINCT
                p.last_name,
                p.first_name
            FROM families AS f
            JOIN family_children AS fc
                ON f.gedcom_id = fc.family_id
            JOIN people AS p
                ON p.gedcom_id = f.husband_id
                OR p.gedcom_id = f.wife_id
            WHERE fc.child_id = ?
            ORDER BY p.last_name, p.first_name
            """,
            (gedcom_id,),
        )
        return self.cursor.fetchall()

    def get_children(self, gedcom_id):
        self.cursor.execute(
            """
            SELECT DISTINCT
                COALESCE(c.last_name, ''),
                CASE
                    WHEN c.gedcom_id IS NULL THEN '[нет записи: ' || fc.child_id || ']'
                    WHEN TRIM(COALESCE(c.last_name, '') || COALESCE(c.first_name, '')) = ''
                        THEN '[без имени: ' || fc.child_id || ']'
                    ELSE COALESCE(c.first_name, '')
                END
            FROM families AS f
            JOIN family_children AS fc
                ON f.gedcom_id = fc.family_id
            LEFT JOIN people AS c
                ON c.gedcom_id = fc.child_id
            WHERE f.husband_id = ? OR f.wife_id = ?
            ORDER BY 1, 2
            """,
            (gedcom_id, gedcom_id),
        )
        return self.cursor.fetchall()


class GenealogyViewer:
    def __init__(self, root):
        self.root = root
        self.repository = GenealogyRepository(DB_NAME)

        self.root.title("Genealogy Viewer")
        self.root.geometry("1000x700")

        self._create_widgets()
        self.search_entry.focus_set()

    def _create_widgets(self):
        top = tk.Frame(self.root)
        top.pack(fill="x", padx=10, pady=10)

        tk.Label(top, text="Имя или фамилия:").pack(side="left")

        self.search_entry = tk.Entry(top, width=30)
        self.search_entry.pack(side="left", padx=5)
        self.search_entry.bind("<Return>", self._search_from_event)

        tk.Button(top, text="Поиск", command=self.search_people).pack(side="left")

        self.status_label = tk.Label(top, text="Введите имя или фамилию")
        self.status_label.pack(side="left", padx=12)

        self.tree = ttk.Treeview(
            self.root,
            columns=("id", "name", "birth", "death"),
            show="headings",
        )

        headings = {
            "id": "ID",
            "name": "Имя",
            "birth": "Рождение",
            "death": "Смерть",
        }
        widths = {
            "id": 80,
            "name": 350,
            "birth": 120,
            "death": 120,
        }

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

        if not query:
            self.status_label.config(text="Введите имя или фамилию")
            self.search_entry.focus_set()
            return

        self.status_label.config(text="Поиск...")
        self.root.update_idletasks()

        people = self.repository.find_people(query)
        for person_id, last_name, first_name, birth_date, death_date in people:
            full_name = f"{last_name or ''} {first_name or ''}".strip()
            self.tree.insert(
                "",
                "end",
                values=(person_id, full_name, birth_date or "", death_date or ""),
            )

        if people:
            self.status_label.config(text=f"Найдено: {len(people)}")
        else:
            self.status_label.config(text="Ничего не найдено")

    def open_person(self, _event):
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

        gedcom_id, last_name, first_name, sex, birth_date, death_date, note = person

        window = tk.Toplevel(self.root)
        window.title(f"{last_name or ''} {first_name or ''}".strip() or "Карточка человека")
        window.geometry("700x600")

        text = tk.Text(window, wrap="word")
        text.pack(fill="both", expand=True)

        self._insert_person_details(text, last_name, first_name, sex, birth_date, death_date, note)
        self._insert_relatives(text, "Родители:", self.repository.get_parents(gedcom_id), "неизвестны")
        self._insert_relatives(text, "\nДети:", self.repository.get_children(gedcom_id), "нет")

        text.config(state="disabled")

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

    @staticmethod
    def _insert_relatives(text, title, relatives, empty_text):
        text.insert("end", title + "\n")

        if not relatives:
            text.insert("end", f"  {empty_text}\n")
            return

        for last_name, first_name in relatives:
            full_name = f"{last_name or ''} {first_name or ''}".strip()
            text.insert("end", f"  {full_name or 'Без имени'}\n")

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
