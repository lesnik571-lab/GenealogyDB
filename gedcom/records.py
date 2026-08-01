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
    if line.startswith("1 NAME"):
        name = line[7:].strip()

        if not name:
            current_person["first_name"] = ""
            current_person["last_name"] = ""
        else:
            cleaned_name = name.strip().strip('/')
            if "/" in cleaned_name:
                parts = [part.strip() for part in cleaned_name.split('/') if part.strip()]
                if len(parts) >= 2:
                    current_person["first_name"] = parts[0]
                    current_person["last_name"] = parts[1]
                else:
                    current_person["first_name"] = cleaned_name
                    current_person["last_name"] = ""
            else:
                parts = [part.strip() for part in re.split(r"\s+", cleaned_name) if part.strip()]
                if len(parts) >= 2:
                    current_person["first_name"] = parts[0]
                    current_person["last_name"] = parts[-1]
                else:
                    current_person["first_name"] = cleaned_name
                    current_person["last_name"] = ""
    elif line.startswith("1 SEX"):
        current_person["sex"] = line[6:].strip()
    elif line.startswith("1 BIRT"):
        birth_mode, death_mode = True, False
    elif line.startswith("1 DEAT"):
        birth_mode, death_mode = False, True
    elif line.startswith("2 DATE"):
        if birth_mode:
            current_person["birth_date"] = line[7:].strip()
        elif death_mode:
            current_person["death_date"] = line[7:].strip()
    elif line.startswith("2 PLAC"):
        if birth_mode:
            current_person["birth_place"] = line[7:].strip()
        elif death_mode:
            current_person["death_place"] = line[7:].strip()
    elif line.startswith("1 OCCU"):
        current_person["occupation"] = line[7:].strip()
    elif line.startswith("1 NOTE"):
        current_person["note"] = line[7:].strip()
    elif line.startswith("1 FAMC"):
        current_person["famc"].append(line.split("@")[1])
    elif line.startswith("1 FAMS"):
        current_person["fams"].append(line.split("@")[1])

    return birth_mode, death_mode


def parse_family_line(line, current_family):
    if line.startswith("1 HUSB"):
        current_family["husband"] = line.split("@")[1]
    elif line.startswith("1 WIFE"):
        current_family["wife"] = line.split("@")[1]
    elif line.startswith("1 CHIL"):
        current_family["children"].append(line.split("@")[1])
