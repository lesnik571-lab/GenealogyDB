import re


class PersonHandler:
    def __init__(self):
        self.current = None
        self.birth_mode = False
        self.death_mode = False

    def start(self, line):
        self.current = {
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
        self.birth_mode = False
        self.death_mode = False

    def finish(self):
        person = self.current
        self.current = None
        self.birth_mode = False
        self.death_mode = False
        return person

    def handle(self, line):
        if self.current is None:
            return

        if line.startswith("1 NAME"):
            name = line[7:].strip()
            match = re.match(r"(.*?)/(.*?)/", name)
            if match:
                self.current["first_name"] = match.group(1).strip()
                self.current["last_name"] = match.group(2).strip()
            else:
                self.current["first_name"] = name
        elif line.startswith("1 SEX"):
            self.current["sex"] = line[6:].strip()
        elif line.startswith("1 BIRT"):
            self.birth_mode, self.death_mode = True, False
        elif line.startswith("1 DEAT"):
            self.birth_mode, self.death_mode = False, True
        elif line.startswith("2 DATE"):
            if self.birth_mode:
                self.current["birth_date"] = line[7:].strip()
            elif self.death_mode:
                self.current["death_date"] = line[7:].strip()
        elif line.startswith("2 PLAC"):
            if self.birth_mode:
                self.current["birth_place"] = line[7:].strip()
            elif self.death_mode:
                self.current["death_place"] = line[7:].strip()
        elif line.startswith("1 OCCU"):
            self.current["occupation"] = line[7:].strip()
        elif line.startswith("1 NOTE"):
            self.current["note"] = line[7:].strip()
        elif line.startswith("1 FAMC"):
            self.current["famc"].append(line.split("@")[1])
        elif line.startswith("1 FAMS"):
            self.current["fams"].append(line.split("@")[1])


class FamilyHandler:
    def __init__(self):
        self.current = None

    def start(self, line):
        self.current = {
            "gedcom_id": line.split("@")[1],
            "husband": "",
            "wife": "",
            "children": []
        }

    def finish(self):
        family = self.current
        self.current = None
        return family

    def handle(self, line):
        if self.current is None:
            return

        if line.startswith("1 HUSB"):
            self.current["husband"] = line.split("@")[1]
        elif line.startswith("1 WIFE"):
            self.current["wife"] = line.split("@")[1]
        elif line.startswith("1 CHIL"):
            self.current["children"].append(line.split("@")[1])


class EventHandler:
    @staticmethod
    def is_person_start(line):
        return line.startswith("0 @") and " INDI" in line

    @staticmethod
    def is_family_start(line):
        return line.startswith("0 @") and " FAM" in line


class GedcomParser:
    def __init__(self):
        self.people = []
        self.families = []
        self.person_handler = PersonHandler()
        self.family_handler = FamilyHandler()
        self.event_handler = EventHandler()

    def parse(self, filename):
        with open(filename, "r", encoding="utf-8", errors="ignore") as source:
            for raw_line in source:
                line = raw_line.rstrip()
                if not line:
                    continue
                self._handle_line(line)

        self._finish_current_records()
        return {
            "people": self.people,
            "families": self.families
        }

    def _handle_line(self, line):
        if self.event_handler.is_person_start(line):
            self._finish_person()
            self._finish_family()
            self.person_handler.start(line)
            return

        if self.event_handler.is_family_start(line):
            self._finish_person()
            self._finish_family()
            self.family_handler.start(line)
            return

        self.person_handler.handle(line)
        self.family_handler.handle(line)

    def _finish_person(self):
        if self.person_handler.current is not None:
            self.people.append(self.person_handler.finish())

    def _finish_family(self):
        if self.family_handler.current is not None:
            self.families.append(self.family_handler.finish())

    def _finish_current_records(self):
        self._finish_person()
        self._finish_family()


def parse_gedcom(filename):
    return GedcomParser().parse(filename)
