import sqlite3
import tkinter as tk
from tkinter import messagebox, ttk

from config import DB_NAME


class GenealogyViewer:
    def __init__(self, root):
        self.root = root
        self.root.title("Genealogy Viewer")
        self.root.geometry("1000x700")

        self.conn = sqlite3.connect(DB_NAME)
        self.cur = self.conn.cursor()

        self.create_widgets()
        self.search_people()

    def create_widgets(self):
        top = tk.Frame(self.root)
        top.pack(fill="x", padx=10, pady=10)

        tk.Label(top, text="Фамилия:").pack(side="left")

        self.search_entry = tk.Entry(top, width=30)
        self.search_entry.pack(side="left", padx=5)
        self.search_entry.bind("<Return>", lambda _event: self.search_people())

        tk.Button(top, text="Поиск", command=self.search_people).pack(side="left")

        self.tree = ttk.Treeview(
            self.root,
            columns=("id", "name", "birth", "death"),
            show="headings",
        )

        self.tree.heading("id", text="ID")
        self.tree.heading("name", text="Имя")
        self.tree.heading("birth", text="Рождение")
        self.tree.heading("death", text="Смерть")

        self.tree.column("id", width=80, anchor="center")
        self.tree.column("name", width=350)
        self.tree.column("birth", width=120)
        self.tree.column("death", width=120)

        self.tree.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self.tree.bind("<Double-1>", self.open_person)

    def search_people(self):
        surname = self.search_entry.get().strip()

        for row in self.tree.get_children():
            self.tree.delete(row)

        if surname:
            self.cur.execute(
                """
                SELECT id, last_name, first_name, birth_date, death_date
                FROM people
                WHERE last_name LIKE ? COLLATE NOCASE
                ORDER BY last_name, first_name
                """,
                (surname + "%",),
            )
        else:
            self.cur.execute(
                """
                SELECT id, last_name, first_name, birth_date, death_date
                FROM people
                ORDER BY last_name, first_name
                LIMIT 500
                """
            )

        for person_id, last_name, first_name, birth_date, death_date in self.cur.fetchall():
            full_name = self.format_name(last_name, first_name)
            self.tree.insert(
                "",
                "end",
                values=(person_id, full_name, birth_date or "", death_date or ""),
            )

    def open_person(self, _event=None):
        selected = self.tree.selection()
        if not selected:
            return

        person_id = self.tree.item(selected[0])["values"][0]
        self.show_person(person_id)

    def show_person(self, person_id):
        self.cur.execute(
            """
            SELECT
                gedcom_id,
                last_name,
                first_name,
                sex,
                birth_date,
                birth_place,
                death_date,
                death_place,
                occupation,
                note
            FROM people
            WHERE id = ?
            """,
            (person_id,),
        )
        person = self.cur.fetchone()

        if not person:
            messagebox.showerror("Ошибка", "Человек не найден.")
            return

        (
            gedcom_id,
            last_name,
            first_name,
            sex,
            birth_date,
            birth_place,
            death_date,
            death_place,
            occupation,
            note,
        ) = person

        win = tk.Toplevel(self.root)
        win.title(self.format_name(last_name, first_name) or gedcom_id or "Человек")
        win.geometry("700x600")

        text = tk.Text(win, wrap="word", padx=10, pady=10)
        text.pack(fill="both", expand=True)

        text.insert("end", f"GEDCOM ID: {gedcom_id or ''}\n")
        text.insert("end", f"Фамилия: {last_name or ''}\n")
        text.insert("end", f"Имя: {first_name or ''}\n")
        text.insert("end", f"Пол: {sex or ''}\n")
        text.insert("end", f"Рождение: {birth_date or ''}\n")
        text.insert("end", f"Место рождения: {birth_place or ''}\n")
        text.insert("end", f"Смерть: {death_date or ''}\n")
        text.insert("end", f"Место смерти: {death_place or ''}\n")
        text.insert("end", f"Занятие: {occupation or ''}\n\n")

        if note:
            text.insert("end", "Примечания:\n")
            text.insert("end", note + "\n\n")

        text.insert("end", "Родители:\n")
        self.show_parents(text, gedcom_id)

        text.insert("end", "\nСупруги:\n")
        self.show_spouses(text, gedcom_id)

        text.insert("end", "\nДети:\n")
        self.show_children(text, gedcom_id)

        text.config(state="disabled")

    def show_parents(self, text, person_gedcom_id):
        if not person_gedcom_id:
            text.insert("end", "  неизвестны\n")
            return

        self.cur.execute(
            """
            SELECT DISTINCT p.last_name, p.first_name, p.gedcom_id
            FROM family_children fc
            JOIN families f ON f.gedcom_id = fc.family_id
            JOIN people p
              ON p.gedcom_id = f.husband_id
              OR p.gedcom_id = f.wife_id
            WHERE fc.child_id = ?
            ORDER BY p.last_name, p.first_name
            """,
            (person_gedcom_id,),
        )
        self.insert_people(text, self.cur.fetchall(), "неизвестны")

    def show_spouses(self, text, person_gedcom_id):
        if not person_gedcom_id:
            text.insert("end", "  нет\n")
            return

        self.cur.execute(
            """
            SELECT DISTINCT p.last_name, p.first_name, p.gedcom_id
            FROM families f
            JOIN people p
              ON (f.husband_id = ? AND p.gedcom_id = f.wife_id)
              OR (f.wife_id = ? AND p.gedcom_id = f.husband_id)
            WHERE f.husband_id = ? OR f.wife_id = ?
            ORDER BY p.last_name, p.first_name
            """,
            (
                person_gedcom_id,
                person_gedcom_id,
                person_gedcom_id,
                person_gedcom_id,
            ),
        )
        self.insert_people(text, self.cur.fetchall(), "нет")

    def show_children(self, text, person_gedcom_id):
        if not person_gedcom_id:
            text.insert("end", "  нет\n")
            return

        self.cur.execute(
            """
            SELECT DISTINCT c.last_name, c.first_name, c.gedcom_id
            FROM families f
            JOIN family_children fc ON fc.family_id = f.gedcom_id
            JOIN people c ON c.gedcom_id = fc.child_id
            WHERE f.husband_id = ? OR f.wife_id = ?
            ORDER BY c.last_name, c.first_name
            """,
            (person_gedcom_id, person_gedcom_id),
        )
        self.insert_people(text, self.cur.fetchall(), "нет")

    @staticmethod
    def format_name(last_name, first_name):
        return f"{last_name or ''} {first_name or ''}".strip()

    def insert_people(self, text, rows, empty_text):
        if not rows:
            text.insert("end", f"  {empty_text}\n")
            return

        for last_name, first_name, gedcom_id in rows:
            name = self.format_name(last_name, first_name)
            text.insert("end", f"  {name or '(без имени)'} [{gedcom_id}]\n")

    def close(self):
        self.conn.close()


def main():
    root = tk.Tk()
    app = GenealogyViewer(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (app.close(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
