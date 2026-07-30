from pathlib import Path

import importer
import viewer
from database import initialize_database


def prepare_database():
    Path("data").mkdir(parents=True, exist_ok=True)
    initialize_database()


def menu():
    prepare_database()

    while True:
        print("\n=== GenealogyDB 1.1 ===")
        print("1. Импорт GEDCOM")
        print("2. Просмотр базы")
        print("0. Выход")

        choice = input("Выберите пункт: ")

        if choice == "1":
            filename = input("Введите имя GEDCOM-файла: ")
            importer.import_gedcom(filename)
        elif choice == "2":
            viewer.main()
        elif choice == "0":
            print("До свидания!")
            break
        else:
            print("Неверный выбор.")


if __name__ == "__main__":
    menu()
