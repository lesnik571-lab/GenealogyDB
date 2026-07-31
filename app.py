from pathlib import Path

import importer
import viewer
from database import initialize_database


class GenealogyApplication:
    def __init__(self):
        self.actions = {
            "1": self.import_gedcom,
            "2": self.open_viewer,
            "0": self.exit_application,
        }
        self.running = True

    def prepare_database(self):
        Path("data").mkdir(parents=True, exist_ok=True)
        initialize_database()

    def display_menu(self):
        print("\n=== GenealogyDB 1.1 ===")
        print("1. Импорт GEDCOM")
        print("2. Просмотр базы")
        print("0. Выход")

    def import_gedcom(self):
        filename = input("Введите имя GEDCOM-файла: ")
        importer.import_gedcom(filename)

    def open_viewer(self):
        viewer.main()

    def exit_application(self):
        print("До свидания!")
        self.running = False

    def run(self):
        self.prepare_database()

        while self.running:
            self.display_menu()
            choice = input("Выберите пункт: ")
            action = self.actions.get(choice)

            if action is None:
                print("Неверный выбор.")
                continue

            action()


def prepare_database():
    Path("data").mkdir(parents=True, exist_ok=True)
    initialize_database()


def menu():
    GenealogyApplication().run()


if __name__ == "__main__":
    menu()
