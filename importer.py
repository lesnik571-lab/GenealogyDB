from config import DB_NAME
from audit_service import AuditService
from gedcom.parser import parse_gedcom
from logging_service import log_operation
from repository import DatabaseRepository


class GedcomImporter:
    def __init__(self, db_name=DB_NAME):
        self.db_name = db_name
        self.repository = DatabaseRepository(db_name)

    @log_operation("GEDCOM import")
    def import_gedcom(self, filename):
        audit = AuditService.for_database(self.db_name)
        before_state = audit.capture_database_state(self.db_name)
        data = self._load_data(filename)
        conn = self.repository.connect()

        try:
            self.repository.clear_tables(conn)
            imported_people = self.repository.import_people(conn, data["people"])
            self.repository.import_families(conn, data["families"])
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        audit.record_state_change(
            "import",
            before_state,
            audit.capture_database_state(self.db_name),
            description=f"Импортирован GEDCOM-файл: {filename}.",
            service="importer",
        )
        return {"people": imported_people, "families": len(data['families']), "family_children": None}

    def _load_data(self, filename):
        return parse_gedcom(filename)


def import_gedcom(filename):
    importer = GedcomImporter()
    return importer.import_gedcom(filename)

