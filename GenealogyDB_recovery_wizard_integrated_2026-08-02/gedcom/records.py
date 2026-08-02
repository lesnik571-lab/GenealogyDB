import re


def create_person(gedcom_id):
    return {
        "gedcom_id": gedcom_id,
        "first_name": "",
        "last_name": "",
        "sex": "",
        "birth_date": "",
        "birth_place": "",
        "death_date": "",
        "death_place": "",
        "occupation": "",
        "note": "",
        "famc": [],
        "fams": [],
    }


def create_family(gedcom_id):
    return {
        "gedcom_id": gedcom_id,
        "husband": "",
        "wife": "",
        "children": [],
    }


def parse_person_line(line, current_person, birth_mode, death_mode):
    normalized_line = line.lstrip("\ufeff").strip()

    if normalized_line.startswith("1 NAME"):
        name = normalized_line[7:].strip()

        if not name:
            current_person["first_name"] = ""
            current_person["last_name"] = ""
        else:
            raw_name = name.strip()
            if raw_name.startswith("/") and raw_name.endswith("/"):
                current_person["first_name"] = ""
                current_person["last_name"] = raw_name.strip("/").strip()
            elif "/" in raw_name:
                parts = [part.strip() for part in raw_name.split("/") if part.strip()]
                if len(parts) >= 2:
                    current_person["first_name"] = parts[0]
                    current_person["last_name"] = parts[-1]
                elif len(parts) == 1:
                    current_person["first_name"] = ""
                    current_person["last_name"] = parts[0]
                else:
                    current_person["first_name"] = ""
                    current_person["last_name"] = ""
            else:
                parts = [part.strip() for part in re.split(r"\s+", raw_name) if part.strip()]
                if len(parts) >= 2:
                    current_person["first_name"] = parts[0]
                    current_person["last_name"] = parts[-1]
                else:
                    current_person["first_name"] = raw_name
                    current_person["last_name"] = ""
    elif normalized_line.startswith("1 SEX"):
        current_person["sex"] = normalized_line[6:].strip()
    elif normalized_line.startswith("1 BIRT"):
        birth_mode, death_mode = True, False
    elif normalized_line.startswith("1 DEAT"):
        birth_mode, death_mode = False, True
    elif normalized_line.startswith("2 DATE"):
        if birth_mode:
            current_person["birth_date"] = normalized_line[7:].strip()
        elif death_mode:
            current_person["death_date"] = normalized_line[7:].strip()
    elif normalized_line.startswith("2 PLAC"):
        if birth_mode:
            current_person["birth_place"] = normalized_line[7:].strip()
        elif death_mode:
            current_person["death_place"] = normalized_line[7:].strip()
    elif normalized_line.startswith("1 OCCU"):
        current_person["occupation"] = normalized_line[7:].strip()
    elif normalized_line.startswith("1 NOTE"):
        current_person["note"] = normalized_line[7:].strip()
    elif normalized_line.startswith("1 FAMC"):
        current_person["famc"].append(normalized_line.split("@")[1])
    elif normalized_line.startswith("1 FAMS"):
        current_person["fams"].append(normalized_line.split("@")[1])

    return birth_mode, death_mode


def parse_family_line(line, current_family):
    normalized_line = line.lstrip("\ufeff").strip()

    if normalized_line.startswith("1 HUSB"):
        current_family["husband"] = normalized_line.split("@")[1]
    elif normalized_line.startswith("1 WIFE"):
        current_family["wife"] = normalized_line.split("@")[1]
    elif normalized_line.startswith("1 CHIL"):
        current_family["children"].append(normalized_line.split("@")[1])
