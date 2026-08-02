import sqlite3
from pathlib import Path

from config import APP_VERSION, DB_NAME, prepare_user_environment
from database import initialize_database
from logging_service import configure_logging, get_logger, install_exception_logging


class GenealogyApplication:
    """Run the interactive command-line launcher for GenealogyDB."""

    def __init__(self):
        self.actions = {
            "1": self.import_gedcom,
            "2": self.open_viewer,
            "3": self.show_statistics,
            "0": self.exit_application,
        }
        self.running = True

    def prepare_database(self):
        """Create the data directory and initialize the configured database."""
        prepare_user_environment()
        initialize_database()

    def display_menu(self):
        """Print the available launcher actions."""
        print(f"\n=== GenealogyDB {APP_VERSION} ===")
        print("1. Импорт GEDCOM")
        print("2. Просмотр базы")
        print("3. Статистика базы")
        print("0. Выход")

    def read_input(self, prompt):
        """Read one menu value and handle interrupted input gracefully."""
        try:
            return input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nРабота программы завершена.")
            self.running = False
            return None

    def import_gedcom(self):
        """Prompt for a GEDCOM file and delegate its import."""
        import importer

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
            get_logger("app").exception("Unexpected GEDCOM import failure")
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
        """Load and run the graphical viewer on demand."""
        import viewer

        try:
            viewer.main()
        except sqlite3.Error as error:
            print(f"Не удалось открыть базу: {error}")
        except Exception as error:
            get_logger("app").exception("Unexpected Viewer failure")
            print(f"Не удалось открыть просмотрщик: {error}")

    def get_statistics(self):
        """Return basic database counts for the launcher summary."""
        with sqlite3.connect(DB_NAME) as connection:
            return {
                "people": connection.execute("SELECT COUNT(*) FROM people").fetchone()[0],
                "families": connection.execute("SELECT COUNT(*) FROM families").fetchone()[0],
                "family_children": connection.execute(
                    "SELECT COUNT(*) FROM family_children"
                ).fetchone()[0],
            }

    def show_statistics(self):
        """Print basic database counts."""
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
        """Stop the launcher loop."""
        print("До свидания!")
        self.running = False

    def run(self):
        """Run the launcher until the user exits or input closes."""
        self.prepare_database()
        configure_logging()
        install_exception_logging()
        get_logger("startup").info("Application startup version=%s", APP_VERSION)

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
    """Initialize the configured database for compatibility callers."""
    prepare_user_environment()
    initialize_database()


def menu():
    """Run the default GenealogyDB command-line menu."""
    GenealogyApplication().run()


if __name__ == "__main__":
    menu()
