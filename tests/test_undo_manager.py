from pathlib import Path
import sqlite3

from repository.person_repository import PersonRepository
from repository.relationship_service import RelationshipService
from undo_manager import (
    AddPersonCommand,
    DeletePersonCommand,
    EditPersonCommand,
    MergePeopleCommand,
    RecoveryUpdateCommand,
    RelationshipEditCommand,
    RepositoryDeltaCommand,
    UndoManager,
)


def build_repository(tmp_path):
    database = tmp_path / "undo.db"
    connection = sqlite3.connect(database)
    connection.executescript(Path("schema.sql").read_text(encoding="utf-8"))
    connection.close()
    return PersonRepository(str(database))


def person_data(first_name, last_name, **values):
    return {"first_name": first_name, "last_name": last_name, **values}


def test_multi_level_person_undo_and_redo_store_only_changed_rows(tmp_path):
    repository = build_repository(tmp_path)
    try:
        manager = UndoManager()
        add = AddPersonCommand(repository, person_data("Anna", "One", gedcom_id="I1"))
        person_id = manager.execute(add)
        edit = EditPersonCommand(repository, person_id, person_data("Anna", "Two"))
        manager.execute(edit)

        assert repository.get_person_record(person_id)["last_name"] == "Two"
        assert add.changed_row_count == 1
        assert edit.changed_row_count == 1
        assert manager.undo() is True
        assert repository.get_person_record(person_id)["last_name"] == "One"
        assert manager.undo() is True
        assert repository.get_person_record(person_id) is None
        assert manager.redo() is True
        assert repository.get_person_record(person_id)["last_name"] == "One"
        assert manager.redo() is True
        assert repository.get_person_record(person_id)["last_name"] == "Two"
    finally:
        repository.close()


def test_delete_restores_person_and_dependent_rows(tmp_path):
    repository = build_repository(tmp_path)
    try:
        person_id = repository.create_person(person_data("Delete", "Me", gedcom_id="I1"))
        repository.create_person_event({"person_id": person_id, "event_type": "custom"})
        repository.create_person_media({"person_id": person_id, "media_type": "photo", "file_path": "x.jpg"})
        manager = UndoManager()

        manager.execute(DeletePersonCommand(repository, person_id))
        assert repository.get_person_record(person_id) is None
        manager.undo()

        assert repository.get_person_record(person_id)["first_name"] == "Delete"
        assert len(repository.list_person_events(person_id)) == 1
        assert len(repository.list_person_media(person_id)) == 1
    finally:
        repository.close()


def test_edit_undo_preserves_unchanged_dependent_rows(tmp_path):
    repository = build_repository(tmp_path)
    try:
        person_id = repository.create_person(person_data("Edit", "Me", gedcom_id="I1"))
        repository.create_person_event({"person_id": person_id, "event_type": "custom"})
        manager = UndoManager()

        manager.execute(EditPersonCommand(repository, person_id, person_data("Edited", "Me")))
        manager.undo()
        manager.redo()

        assert repository.get_person_record(person_id)["first_name"] == "Edited"
        assert len(repository.list_person_events(person_id)) == 1
    finally:
        repository.close()


def test_relationship_and_recovery_commands_are_reversible(tmp_path):
    repository = build_repository(tmp_path)
    try:
        parent_id = repository.create_person(person_data("Parent", "One", gedcom_id="I1"))
        child_id = repository.create_person(person_data("Child", "One", gedcom_id="I2"))
        relationships = RelationshipService(repository)
        manager = UndoManager()
        manager.execute(RelationshipEditCommand(
            repository, lambda: relationships.link_parent("I2", "I1", "father")
        ))
        manager.execute(RecoveryUpdateCommand(
            repository,
            lambda: repository.update_person_fields(child_id, {"occupation": "Engineer"}),
        ))

        assert repository.get_parents("I2")
        assert repository.get_person_record(child_id)["occupation"] == "Engineer"
        manager.undo()
        assert repository.get_person_record(child_id)["occupation"] == ""
        manager.undo()
        assert repository.get_parents("I2") == []
        manager.redo()
        assert repository.get_parents("I2")
        assert parent_id == 1
    finally:
        repository.close()


def test_merge_is_reversible_and_rewires_relationships(tmp_path):
    repository = build_repository(tmp_path)
    try:
        target_id = repository.create_person(person_data("Target", "Person", gedcom_id="I1"))
        source_id = repository.create_person(person_data("Source", "Person", gedcom_id="I2", occupation="Doctor"))
        child_id = repository.create_person(person_data("Child", "Person", gedcom_id="I3"))
        RelationshipService(repository).link_parent("I3", "I2", "father")
        manager = UndoManager()

        manager.execute(MergePeopleCommand(repository, target_id, source_id))

        assert repository.get_person_record(source_id) is None
        assert repository.get_person_record(target_id)["occupation"] == "Doctor"
        assert {row[2] for row in repository.get_parents("I3")} == {"I1"}
        manager.undo()
        assert repository.get_person_record(source_id)["first_name"] == "Source"
        assert {row[2] for row in repository.get_parents("I3")} == {"I2"}
        manager.redo()
        assert repository.get_person_record(source_id) is None
        assert child_id == 3
    finally:
        repository.close()


def test_noop_command_is_not_added_to_history(tmp_path):
    repository = build_repository(tmp_path)
    try:
        manager = UndoManager()
        manager.execute(RepositoryDeltaCommand("Read only", repository, repository.list_people_full))
        assert manager.can_undo is False
        assert manager.can_redo is False
    finally:
        repository.close()