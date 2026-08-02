from __future__ import annotations

from repository.person_repository import PersonRepository


class PersonEventService:
    """Manage dated events associated with people."""
    def __init__(self, repository: PersonRepository):
        self.repository = repository

    def create_event(self, person_id, event_type="custom", date="", place="", description=""):
        self._validate_event(person_id, event_type, date, place, description)
        return self.repository.create_person_event({
            "person_id": person_id,
            "event_type": event_type,
            "date": date,
            "place": place,
            "description": description,
        })

    def update_event(self, event_id, event_type="custom", date="", place="", description=""):
        self._validate_event(None, event_type, date, place, description)
        return self.repository.update_person_event(event_id, {
            "event_type": event_type,
            "date": date,
            "place": place,
            "description": description,
        })

    def delete_event(self, event_id):
        return self.repository.delete_person_event(event_id)

    def list_events(self, person_id):
        return self.repository.list_person_events(person_id)

    @staticmethod
    def _validate_event(person_id, event_type, date, place, description):
        if event_type not in {
            "birth",
            "baptism",
            "death",
            "marriage",
            "divorce",
            "burial",
            "residence",
            "education",
            "occupation",
            "military_service",
            "immigration",
            "emigration",
            "census",
            "awards",
            "custom",
        }:
            raise ValueError("Unsupported event type")
        if not person_id and not date and not place and not description:
            raise ValueError("Event details are required")
