from .records import create_family, create_person, parse_family_line, parse_person_line


def parse_gedcom(filename):
    people = []
    families = []

    current_person = None
    current_family = None

    birth_mode = False
    death_mode = False

    with open(filename, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = raw.rstrip("\r\n")
            normalized_line = line.lstrip("\ufeff").strip()

            if not normalized_line:
                continue

            if normalized_line.startswith("0 @") and " INDI" in normalized_line:
                if current_person is not None:
                    people.append(current_person)
                current_person = create_person(normalized_line.split("@")[1])
                birth_mode = False
                death_mode = False
                continue

            if normalized_line.startswith("0 @") and " FAM" in normalized_line:
                if current_person is not None:
                    people.append(current_person)
                    current_person = None
                if current_family is not None:
                    families.append(current_family)
                current_family = create_family(normalized_line.split("@")[1])
                continue

            if current_person is not None:
                birth_mode, death_mode = parse_person_line(normalized_line, current_person, birth_mode, death_mode)

            if current_family is not None:
                parse_family_line(normalized_line, current_family)

    if current_person is not None:
        people.append(current_person)
    if current_family is not None:
        families.append(current_family)

    return {
        "people": people,
        "families": families,
    }
