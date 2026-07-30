import re


def parse_gedcom(filename):
    people = []
    families = []

    current_person = None
    current_family = None

    birth_mode = False
    death_mode = False

    with open(filename, "r", encoding="utf-8", errors="ignore") as f:
        for raw in f:
            line = raw.rstrip()

            if not line:
                continue

            if line.startswith("0 @") and " INDI" in line:
                if current_person is not None:
                    people.append(current_person)
                current_person = {
                    "gedcom_id": line.split("@")[1],
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
                    "fams": []
                }
                birth_mode = False
                death_mode = False
                continue

            if line.startswith("0 @") and " FAM" in line:
                if current_person is not None:
                    people.append(current_person)
                    current_person = None
                if current_family is not None:
                    families.append(current_family)
                current_family = {
                    "gedcom_id": line.split("@")[1],
                    "husband": "",
                    "wife": "",
                    "children": []
                }
                continue

            if current_person is not None:
                if line.startswith("1 NAME"):
                    name = line[7:].strip()
                    m = re.match(r"(.*?)/(.*?)/", name)
                    if m:
                        current_person["first_name"] = m.group(1).strip()
                        current_person["last_name"] = m.group(2).strip()
                    else:
                        current_person["first_name"] = name
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

            if current_family is not None:
                if line.startswith("1 HUSB"):
                    current_family["husband"] = line.split("@")[1]
                elif line.startswith("1 WIFE"):
                    current_family["wife"] = line.split("@")[1]
                elif line.startswith("1 CHIL"):
                    current_family["children"].append(line.split("@")[1])

    if current_person is not None:
        people.append(current_person)
    if current_family is not None:
        families.append(current_family)

    return {
        "people": people,
        "families": families
    }
