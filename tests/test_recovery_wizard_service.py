import sqlite3

from repository.person_repository import PersonRepository
from recovery_wizard_service import RecoveryWizardService


def build_repository(tmp_path):
    db_path = tmp_path / "recovery.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE people (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gedcom_id TEXT,
            first_name TEXT,
            last_name TEXT,
            sex TEXT,
            birth_date TEXT,
            birth_place TEXT,
            death_date TEXT,
            death_place TEXT,
            occupation TEXT,
            note TEXT
        );
        CREATE TABLE families (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gedcom_id TEXT,
            husband_id TEXT,
            wife_id TEXT,
            relationship_type TEXT NOT NULL DEFAULT 'unknown'
        );
        CREATE TABLE family_children (family_id TEXT, child_id TEXT);
        CREATE TABLE person_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            person_id INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            event_date TEXT,
            event_place TEXT,
            description TEXT
        );
        """
    )
    conn.executemany(
        "INSERT INTO people (gedcom_id, first_name, last_name, sex, birth_date, birth_place, death_date, death_place, occupation, note) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            ("I1", "", "", "M", "1957", "Москва", "", "", "", "Семейный архив"),
            ("I2", "Яков", "Лесник", "M", "", "", "", "", "", ""),
            ("I3", "Маргарита", "Лесник", "F", "", "", "", "", "", ""),
            ("I4", "Антон", "Ермаков", "M", "", "", "", "", "", ""),
            ("I5", "Михаил", "Лесник", "M", "1957", "Москва", "", "", "", "Семейный архив"),
            ("I6", "Михаил", "Петров", "M", "1968", "Тула", "", "", "", "Другая запись"),
        ],
    )
    conn.execute("INSERT INTO families (gedcom_id, husband_id, wife_id) VALUES ('F1','I2','I3')")
    conn.execute("INSERT INTO family_children (family_id, child_id) VALUES ('F1','I1')")
    conn.execute("INSERT INTO family_children (family_id, child_id) VALUES ('F1','I5')")
    conn.execute("INSERT INTO families (gedcom_id, husband_id, wife_id) VALUES ('F2','I1','')")
    conn.execute("INSERT INTO family_children (family_id, child_id) VALUES ('F2','I4')")
    conn.execute(
        "INSERT INTO person_events (person_id,event_type,event_date,description) VALUES (1,'birth','1957','Запись о рождении')"
    )
    conn.commit()
    conn.close()
    return PersonRepository(str(db_path))


def test_lists_only_incomplete_people_with_family_context(tmp_path):
    repo = build_repository(tmp_path)
    try:
        service = RecoveryWizardService(repo)
        records = service.list_incomplete_people()

        assert len(records) == 1
        record = records[0]
        assert record.person_id == 1
        assert record.gedcom_id == "I1"
        assert record.first_name == ""
        assert record.last_name == ""
        assert record.sex == "M"
        assert record.birth_date == "1957"
        assert record.birth_place == "Москва"
        assert "Лесник Яков" in record.parents
        assert "Лесник Маргарита" in record.parents
        assert "Ермаков Антон" in record.children
        assert ("Лесник Яков", 2) in record.parent_links
        assert ("Лесник Маргарита", 3) in record.parent_links
        assert record.partner_links == ()
        assert ("Ермаков Антон", 4) in record.child_links
        assert record.event_count == 1
        assert record.event_descriptions == ("Запись о рождении",)
    finally:
        repo.close()


def test_updates_existing_person_without_creating_new_or_relinking(tmp_path):
    repo = build_repository(tmp_path)
    try:
        service = RecoveryWizardService(repo)

        before_people_count = len(repo.list_people_full())
        before_family_links = list(repo.list_family_children_raw())
        before_families = list(repo.list_families_raw())
        before_events = [e for e in repo.list_all_person_events() if e["person_id"] == 1]

        updated = service.update_existing_person(
            1,
            {
                "first_name": "Михаил",
                "last_name": "Лесник",
                "sex": "M",
                "birth_date": "1957",
                "birth_place": "Москва",
                "death_date": "",
                "death_place": "",
                "occupation": "Инженер",
                "note": "Восстановлено вручную",
            },
        )

        assert updated is True

        person = repo.get_person_record(1)
        assert person["id"] == 1
        assert person["gedcom_id"] == "I1"
        assert person["first_name"] == "Михаил"
        assert person["last_name"] == "Лесник"

        after_people_count = len(repo.list_people_full())
        after_family_links = list(repo.list_family_children_raw())
        after_families = list(repo.list_families_raw())
        after_events = [e for e in repo.list_all_person_events() if e["person_id"] == 1]

        assert after_people_count == before_people_count
        assert after_family_links == before_family_links
        assert after_families == before_families
        assert after_events == before_events
    finally:
        repo.close()


def test_undo_last_recovery_save_restores_fields_only(tmp_path):
    repo = build_repository(tmp_path)
    try:
        service = RecoveryWizardService(repo)
        snapshot = repo.get_person_record(1)
        before_people_count = len(repo.list_people_full())
        before_families = repo.list_families_raw()
        before_children = repo.list_family_children_raw()
        before_events = repo.list_all_person_events()

        service.update_existing_person(1, {"first_name": "Михаил", "last_name": "Лесник"})
        assert service.restore_existing_person(1, snapshot) is True

        restored = repo.get_person_record(1)
        assert restored["id"] == 1
        assert restored["gedcom_id"] == "I1"
        assert restored["first_name"] == ""
        assert restored["last_name"] == ""
        assert len(repo.list_people_full()) == before_people_count
        assert repo.list_families_raw() == before_families
        assert repo.list_family_children_raw() == before_children
        assert repo.list_all_person_events() == before_events
    finally:
        repo.close()


def test_updates_only_modified_fields(tmp_path):
    repo = build_repository(tmp_path)
    try:
        service = RecoveryWizardService(repo)
        captured = {}
        original_update = repo.update_person_fields

        def capture_update(person_id, changes):
            captured.update(changes)
            return original_update(person_id, changes)

        repo.update_person_fields = capture_update
        service.update_existing_person(
            1,
            {
                "first_name": "Михаил",
                "last_name": "Лесник",
                "sex": "M",
                "birth_date": "1957",
                "birth_place": "Москва",
                "death_date": "",
                "death_place": "",
                "occupation": "",
                "note": "Семейный архив",
            },
        )

        assert captured == {"first_name": "Михаил", "last_name": "Лесник"}
    finally:
        repo.close()


def test_requires_non_empty_first_and_last_name(tmp_path):
    repo = build_repository(tmp_path)
    try:
        service = RecoveryWizardService(repo)

        try:
            service.update_existing_person(1, {"first_name": "Михаил", "last_name": ""})
        except ValueError as error:
            assert "имя и фамилию" in str(error).lower()
        else:
            raise AssertionError("Expected ValueError for empty last name")

        try:
            service.update_existing_person(1, {"first_name": "", "last_name": "Лесник"})
        except ValueError as error:
            assert "имя и фамилию" in str(error).lower()
        else:
            raise AssertionError("Expected ValueError for empty first name")
    finally:
        repo.close()


def test_finds_read_only_matches_ordered_by_confidence(tmp_path):
    repo = build_repository(tmp_path)
    try:
        service = RecoveryWizardService(repo)
        before_people = repo.list_people_full()
        before_families = repo.list_families_raw()
        before_children = repo.list_family_children_raw()

        matches = service.find_matches(
            1,
            {
                "first_name": "Михаил",
                "last_name": "Лесник",
                "birth_date": "1957",
                "birth_place": "Москва",
                "note": "Семейный архив",
            },
        )
        matches_without_note = service.find_matches(
            1,
            {
                "first_name": "Михаил",
                "last_name": "Лесник",
                "birth_date": "1957",
                "birth_place": "Москва",
                "note": "",
            },
        )

        assert matches[0].person_id == 5
        assert 6 in {candidate.person_id for candidate in matches}
        assert matches[0].gedcom_id == "I5"
        assert matches[0].full_name == "Лесник Михаил"
        assert matches[0].birth_date == "1957"
        assert [candidate.confidence for candidate in matches] == sorted(
            [candidate.confidence for candidate in matches], reverse=True
        )
        assert 0 < matches[0].confidence <= 100
        score_without_note = next(candidate.confidence for candidate in matches_without_note if candidate.person_id == 5)
        assert matches[0].confidence > score_without_note
        assert all(candidate.person_id != 1 for candidate in matches)
        assert repo.list_people_full() == before_people
        assert repo.list_families_raw() == before_families
        assert repo.list_family_children_raw() == before_children
    finally:
        repo.close()


def test_finds_all_named_candidates_for_sparse_incomplete_person(tmp_path):
    repo = build_repository(tmp_path)
    try:
        repo.conn.execute(
            "INSERT INTO people (gedcom_id, first_name, last_name) VALUES ('I7', '', '')"
        )
        repo.conn.commit()
        service = RecoveryWizardService(repo)
        before_people = repo.list_people_full()
        before_families = repo.list_families_raw()
        before_children = repo.list_family_children_raw()

        matches = service.find_matches(7, {})

        assert {candidate.person_id for candidate in matches} == {2, 3, 4, 5, 6}
        assert [candidate.confidence for candidate in matches] == sorted(
            [candidate.confidence for candidate in matches], reverse=True
        )
        assert all(candidate.confidence == 0 for candidate in matches)
        assert repo.list_people_full() == before_people
        assert repo.list_families_raw() == before_families
        assert repo.list_family_children_raw() == before_children
    finally:
        repo.close()
