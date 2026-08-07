from person_duplicate_service import PersonDuplicateService


class StubPersonRepository:
    def __init__(self, people):
        self.people = people
        self.list_calls = 0

    def list_people_full(self):
        self.list_calls += 1
        return list(self.people)


def person(person_id, first_name, last_name, **overrides):
    record = {
        "id": person_id,
        "gedcom_id": f"@I{person_id}@",
        "first_name": first_name,
        "last_name": last_name,
        "sex": "",
        "birth_date": "",
        "birth_place": "",
        "death_date": "",
        "death_place": "",
        "occupation": "",
        "note": "",
    }
    record.update(overrides)
    return record


def test_duplicate_candidates_match_name_birth_year_place_and_sex():
    repository = StubPersonRepository(
        [
            person(
                1,
                "Михаил",
                "Лесник",
                sex="M",
                birth_date="10.11.1957",
                birth_place="г. Винница, Украина",
            ),
            person(
                2,
                "МИХАИЛ",
                "ЛЕСНИК",
                sex="m",
                birth_date="1957-11-10",
                birth_place="г Винница Украина",
            ),
        ]
    )

    candidates = PersonDuplicateService(repository).find_candidates()

    assert len(candidates) == 1
    candidate = candidates[0]
    assert (candidate.person_id, candidate.duplicate_id) == (1, 2)
    assert candidate.score == 100
    assert "same birth year: 1957" in candidate.reasons
    assert "same birth place" in candidate.reasons
    assert repository.list_calls == 1


def test_same_name_without_supporting_birth_data_stays_below_default_threshold():
    repository = StubPersonRepository(
        [
            person(1, "Анна", "Коэн", sex="F"),
            person(2, "Анна", "Коэн", sex="F"),
        ]
    )

    assert PersonDuplicateService(repository).find_candidates() == ()


def test_adjacent_birth_years_with_same_place_are_duplicate_candidates():
    repository = StubPersonRepository(
        [
            person(1, "Boris", "Lesnik", birth_date="1900", birth_place="Moscow"),
            person(2, "Борис", "Лесник", birth_date="1901", birth_place="Moscow"),
        ]
    )

    candidates = PersonDuplicateService(repository).find_candidates()

    assert len(candidates) == 1
    assert candidates[0].score == 85
    assert candidates[0].reasons == (
        "same name",
        "birth years differ by 1: 1900/1901",
        "same birth place",
    )


def test_incompatible_birth_years_are_not_duplicate_candidates():
    repository = StubPersonRepository(
        [
            person(
                1,
                "Boris",
                "Lesnik",
                sex="M",
                birth_date="1900",
                birth_place="Moscow",
                death_date="1970",
            ),
            person(
                2,
                "Борис",
                "Лесник",
                sex="M",
                birth_date="1902",
                birth_place="Moscow",
                death_date="1970",
            ),
        ]
    )

    assert PersonDuplicateService(repository).find_candidates(min_score=0) == ()


def test_different_names_are_not_duplicate_candidates():
    repository = StubPersonRepository(
        [
            person(1, "Анна", "Коэн", birth_date="1950", birth_place="Хайфа"),
            person(2, "Сара", "Коэн", birth_date="1950", birth_place="Хайфа"),
        ]
    )

    assert PersonDuplicateService(repository).find_candidates(min_score=0) == ()


def test_lower_threshold_can_expose_same_name_for_manual_review():
    repository = StubPersonRepository(
        [
            person(7, "Исаак", "Леви"),
            person(9, "Исаак", "Леви"),
        ]
    )

    candidates = PersonDuplicateService(repository).find_candidates(min_score=60)

    assert len(candidates) == 1
    assert candidates[0].score == 60
    assert candidates[0].reasons == ("same name",)
