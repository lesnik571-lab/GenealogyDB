import sqlite3
from pathlib import Path

import importer
import viewer
from config import DATA_DIR, DB_NAME
from database import initialize_database


class GenealogyApplication:
    def __init__(self):
        self.actions = {
            "1": self.import_gedcom,
            "2": self.open_viewer,
            "3": self.show_statistics,
            "0": self.exit_application,
        }
        self.running = True

    def prepare_database(self):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        initialize_database()

    def display_menu(self):
        print("\n=== GenealogyDB 1.1 ===")
        print("1. Импорт GEDCOM")
        print("2. Просмотр базы")
        print("3. Статистика базы")
        print("0. Выход")

    def import_gedcom(self):
        raw_filename = input("Введите имя GEDCOM-файла: ").strip().strip('"')
        if not raw_filename:
            print("Импорт отменён: имя файла не указано.")
            return

        filename = Path(raw_filename).expanduser()
        if not filename.is_file():
            print(f"Файл не найден: {filename}")
            return

        if filename.suffix.lower() != ".ged":
            print(f"Выбран не GEDCOM-файл: {filename}")
            return

        try:
            importer.import_gedcom(str(filename))
        except (OSError, ValueError) as error:
            print(f"Ошибка импорта: {error}")
        except Exception as error:
            print(f"Не удалось импортировать GEDCOM: {error}")

    def open_viewer(self):
        viewer.main()

    def show_statistics(self):
        try:
            with sqlite3.connect(DB_NAME) as connection:
                cursor = connection.cursor()
                people_count = cursor.execute("SELECT COUNT(*) FROM people").fetchone()[0]
                families_count = cursor.execute("SELECT COUNT(*) FROM families").fetchone()[0]
                relations_count = cursor.execute("SELECT COUNT(*) FROM family_children").fetchone()[0]
        except sqlite3.Error as error:
            print(f"Не удалось прочитать статистику базы: {error}")
            return

        print("\n--- Статистика базы ---")
        print(f"Людей: {people_count}")
        print(f"Семей: {families_count}")
        print(f"Связей родитель—ребёнок: {relations_count}")

    def exit_application(self):
        print("До свидания!")
        self.running = False

    def run(self):
        self.prepare_database()

        while self.running:
            self.display_menu()
            choice = input("Выберите пункт: ").strip()
            action = self.actions.get(choice)

            if action is None:
                print("Неверный выбор.")
                continue

            action()


def prepare_database():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    initialize_database()


def menu():
    GenealogyApplication().run()


if __name__ == "__main__":
    menu()
