import sqlite3
from pathlib import Path

import importer
import viewer
from config import DATA_DIR, DB_NAME, APP_VERSION
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
        print(f"\n=== GenealogyDB {APP_VERSION} ===")
        print("1. Импорт GEDCOM")
        print("2. Просмотр базы")
        print("3. Статистика базы")
        print("0. Выход")

    def read_input(self, prompt):
        try:
            return input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nРабота программы завершена.")
            self.running = False
            return None

    def import_gedcom(self):
        raw_filename = self.read_input("Введите имя GEDCOM-файла: ")
        if raw_filename is None:
            return

        raw_filename = raw_filename.strip('"')
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
            result = importer.import_gedcom(str(filename))
        except (OSError, ValueError) as error:
            print(f"Ошибка импорта: {error}")
        except Exception as error:
            print(f"Не удалось импортировать GEDCOM: {error}")
        else:
            if result:
                print(
                    "Импорт завершён: "
                    f"людей — {result['people']}, "
                    f"семей — {result['families']}, "
                    f"связей — {result['family_children']}."
                )

    def open_viewer(self):
        try:
            viewer.main()
        except sqlite3.Error as error:
            print(f"Не удалось открыть базу: {error}")
        except Exception as error:
            print(f"Не удалось открыть просмотрщик: {error}")

    def get_statistics(self):
        with sqlite3.connect(DB_NAME) as connection:
            return {
                "people": connection.execute("SELECT COUNT(*) FROM people").fetchone()[0],
                "families": connection.execute("SELECT COUNT(*) FROM families").fetchone()[0],
                "family_children": connection.execute(
                    "SELECT COUNT(*) FROM family_children"
                ).fetchone()[0],
            }

    def show_statistics(self):
        try:
            statistics = self.get_statistics()
        except sqlite3.Error as error:
            print(f"Не удалось прочитать статистику базы: {error}")
            return

        print("\n--- Статистика базы ---")
        print(f"Людей: {statistics['people']}")
        print(f"Семей: {statistics['families']}")
        print(f"Связей родитель—ребёнок: {statistics['family_children']}")

    def exit_application(self):
        print("До свидания!")
        self.running = False

    def run(self):
        self.prepare_database()

        while self.running:
            self.display_menu()
            choice = self.read_input("Выберите пункт: ")
            if choice is None:
                break

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
