import sqlite3
import tkinter as tk
from tkinter import ttk, messagebox

from config import DB_NAME


class GenealogyViewer:

    def __init__(self, root):
        self.root = root
        self.root.title("Genealogy Viewer")
        self.root.geometry("1000x700")

        self.conn = sqlite3.connect(DB_NAME)
        self.cur = self.conn.cursor()

        self.create_widgets()

    def create_widgets(self):

        top = tk.Frame(self.root)
        top.pack(fill="x", padx=10, pady=10)

        tk.Label(top, text="Фамилия:").pack(side="left")

        self.search_entry = tk.Entry(top, width=30)
        self.search_entry.pack(side="left", padx=5)

        tk.Button(
            top,
            text="Поиск",
            command=self.search_people
        ).pack(side="left")

        self.tree = ttk.Treeview(
            self.root,
            columns=("id", "name", "birth", "death"),
            show="headings"
        )

        self.tree.heading("id", text="ID")
        self.tree.heading("name", text="Имя")
        self.tree.heading("birth", text="Рождение")
        self.tree.heading("death", text="Смерть")

        self.tree.column("id", width=80)
        self.tree.column("name", width=350)
        self.tree.column("birth", width=120)
        self.tree.column("death", width=120)

        self.tree.pack(fill="both", expand=True, padx=10)

        self.tree.bind("<Double-1>", self.open_person)

    

    def open_person(self, event):

        selected = self.tree.selection()

        if not selected:
            return

        person_id = self.tree.item(selected[0])["values"][0]
        self.show_person(person_id)

    def show_person(self, person_id):
       # ===================== search_people =====================

def search_people(self):

    surname = self.search_entry.get().strip()

    for row in self.tree.get_children():
        self.tree.delete(row)

    if surname:
        self.cur.execute("""
            SELECT
                id,
                last_name,
                first_name,
                birth_date,
                death_date
            FROM people
            WHERE last_name LIKE ?
            ORDER BY last_name, first_name
        """, (surname + "%",))
    else:
        self.cur.execute("""
            SELECT
                id,
                last_name,
                first_name,
                birth_date,
                death_date
            FROM people
            ORDER BY last_name, first_name
            LIMIT 500
        """)

    rows = self.cur.fetchall()

    for person in rows:

        full_name = f"{person[1] or ''} {person[2] or ''}".strip()

        self.tree.insert(
            "",
            "end",
            values=(
                person[0],
                full_name,
                person[3] or "",
                person[4] or ""
            )
        )


# ===================== show_person =====================

def show_person(self, person_id):

    self.cur.execute("""
        SELECT
            last_name,
            first_name,
            sex,
            birth_date,
            death_date,
            note
        FROM people
        WHERE id=?
    """, (person_id,))

    person = self.cur.fetchone()

    if not person:
        messagebox.showerror("Ошибка", "Человек не найден.")
        return

    win = tk.Toplevel(self.root)
    win.title(f"{person[0]} {person[1]}")
    win.geometry("700x600")

    text = tk.Text(win, wrap="word")
    text.pack(fill="both", expand=True)

    text.insert("end", f"Фамилия: {person[0] or ''}\n")
    text.insert("end", f"Имя: {person[1] or ''}\n")
    text.insert("end", f"Пол: {person[2] or ''}\n")
    text.insert("end", f"Рождение: {person[3] or ''}\n")
    text.insert("end", f"Смерть: {person[4] or ''}\n\n")

    if person[5]:
        text.insert("end", "Примечания:\n")
        text.insert("end", person[5] + "\n\n")

    text.insert("end", "Родители:\n")
    self.show_parents(text, person_id)

    text.insert("end", "\nДети:\n")
    self.show_children(text, person_id)

    text.config(state="disabled")


# ===================== show_parents =====================

def show_parents(self, text, person_id):

# ===================== show_children =====================

def show_children(self, text, person_id):

if __name__ == "__main__":
    main()
