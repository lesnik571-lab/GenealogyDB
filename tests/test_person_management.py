import sqlite3
from pathlib import Path

import viewer as viewer_module
from repository import PersonRepository
from viewer import GenealogyViewer


def build_viewer(tmp_path):
    db_path = tmp_path / "person_management.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(Path("schema.sql").read_text(encoding="utf-8"))
    conn.commit()
    conn.close()

    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer.repository = PersonRepository(str(db_path))
    viewer.root = object()
    viewer.current_person_id = None
    viewer.search_entry = type("SearchEntry", (), {"get": lambda self: ""})()
    viewer.status_label = type("StatusLabel", (), {"config": lambda self, *args, **kwargs: None})()
    viewer.tree = None
    viewer._person_dialog = None
    viewer._person_card_body = None
    viewer._person_history = []
    viewer._person_history_index = -1
    viewer.event_service = type("EventService", (), {"list_events": lambda self, person_id: []})()
    viewer._clear_tree = lambda: None
    viewer.search_people = lambda: None
    return viewer


class _FakeWidget:
    instances = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.command = kwargs.get("command")
        self.text = kwargs.get("text", "")
        self.title_value = ""
        self.destroyed = False
        self._bindings = {}
        _FakeWidget.instances.append(self)

    def pack(self, *args, **kwargs):
        return None

    def grid(self, *args, **kwargs):
        return None

    def bind(self, event_name, callback):
        self._bindings[event_name] = callback
        return None

    def configure(self, *args, **kwargs):
        return None

    def config(self, *args, **kwargs):
        return None

    def set(self, *args, **kwargs):
        return None

    def yview(self, *args, **kwargs):
        return None

    def bbox(self, *args, **kwargs):
        return (0, 0, 800, 1200)

    def create_window(self, *args, **kwargs):
        return 1

    def itemconfigure(self, *args, **kwargs):
        return None

    def grid_columnconfigure(self, *args, **kwargs):
        return None

    def grid_rowconfigure(self, *args, **kwargs):
        return None

    def title(self, *args, **kwargs):
        if args:
            self.title_value = args[0]
        return None

    def geometry(self, *args, **kwargs):
        return None

    def transient(self, *args, **kwargs):
        return None

    def grab_set(self, *args, **kwargs):
        return None

    def protocol(self, *args, **kwargs):
        return None

    def destroy(self):
        self.destroyed = True
        return None

    def deiconify(self):
        return None

    def lift(self):
        return None

    def focus_set(self):
        return None


class _FakeToplevel(_FakeWidget):
    instances = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _FakeToplevel.instances.append(self)


class _FakeText(_FakeWidget):
    instances = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.text = ""
        self.bindings = {}
        _FakeText.instances.append(self)

    def insert(self, index, value):
        self.text += value

    def index(self, index):
        return "1.0"

    def tag_add(self, *args, **kwargs):
        return None

    def tag_configure(self, *args, **kwargs):
        return None

    def tag_bind(self, tag_name, _event_name, callback):
        self.bindings[tag_name] = callback

    def config(self, *args, **kwargs):
        return None


class _FakeListbox(_FakeWidget):
    instances = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.items = []
        _FakeListbox.instances.append(self)

    def insert(self, index, value):
        self.items.append(value)


class _FakeLabelFrame(_FakeWidget):
    instances = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _FakeLabelFrame.instances.append(self)


class _FakeButton(_FakeWidget):
    instances = []

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _FakeButton.instances.append(self)

    def invoke(self):
        if callable(self.command):
            self.command()


def test_viewer_can_create_person(tmp_path):
    viewer = build_viewer(tmp_path)

    person_id = viewer._save_person(
        None,
        {
            "first_name": "Anna",
            "last_name": "Smith",
            "sex": "F",
            "birth_date": "1 JAN 1950",
            "birth_place": "Moscow",
            "death_date": "",
            "death_place": "",
            "occupation": "Teacher",
            "note": "Test person",
        },
    )

    assert person_id is not None
    stored = viewer.repository.get_person(person_id)
    assert stored[2] == "Anna"
    assert stored[1] == "Smith"


def test_viewer_can_update_person(tmp_path):
    viewer = build_viewer(tmp_path)
    person_id = viewer.repository.create_person({
        "gedcom_id": "I1",
        "first_name": "Jane",
        "last_name": "Doe",
        "sex": "F",
        "birth_date": "",
        "birth_place": "",
        "death_date": "",
        "death_place": "",
        "occupation": "",
        "note": "",
    })

    updated_id = viewer._save_person(
        person_id,
        {
            "first_name": "Janet",
            "last_name": "Doe",
            "sex": "F",
            "birth_date": "",
            "birth_place": "",
            "death_date": "",
            "death_place": "",
            "occupation": "",
            "note": "Updated",
        },
    )

    assert updated_id == person_id
    stored = viewer.repository.get_person(person_id)
    assert stored[2] == "Janet"
    assert stored[9] == "Updated"


def test_viewer_can_delete_person(tmp_path, monkeypatch):
    viewer = build_viewer(tmp_path)
    person_id = viewer.repository.create_person({
        "gedcom_id": "I2",
        "first_name": "Bob",
        "last_name": "Brown",
        "sex": "M",
        "birth_date": "",
        "birth_place": "",
        "death_date": "",
        "death_place": "",
        "occupation": "",
        "note": "",
    })

    monkeypatch.setattr(viewer_module.messagebox, "askyesno", lambda *args, **kwargs: True)

    deleted = viewer._delete_person(person_id)

    assert deleted is True
    assert viewer.repository.get_person(person_id) is None


def test_double_click_opens_person_card(tmp_path):
    viewer = build_viewer(tmp_path)

    class FakeTree:
        def __init__(self):
            self.selected = ["row1"]

        def selection(self):
            return self.selected

        def item(self, item_id):
            return {"values": [7]}

    viewer.tree = FakeTree()
    viewer.repository.get_person = lambda person_id: ("G7", "Smith", "Jane", "F", "", "", "", "", "", "")
    opened = []
    viewer.show_person = lambda person_id: opened.append(person_id)

    viewer.open_person()

    assert viewer.current_person_id == 7
    assert viewer.current_person_gedcom_id == "G7"
    assert opened == [7]


def test_open_related_person_uses_person_card_flow(tmp_path):
    viewer = build_viewer(tmp_path)
    opened = []
    viewer.repository.get_person_by_gedcom_id = lambda gedcom_id: (7,)
    viewer.show_person = lambda person_id: opened.append(person_id)

    viewer.open_related_person("I1")

    assert opened == [7]


def test_open_related_person_can_navigate_repeatedly(tmp_path):
    viewer = build_viewer(tmp_path)
    calls = []
    viewer.repository.get_person_by_gedcom_id = lambda gedcom_id: (gedcom_id,)
    viewer.show_person = lambda person_id: calls.append(person_id)

    viewer.open_related_person("I1")
    viewer.open_related_person("I2")

    assert calls == ["I1", "I2"]


def test_show_person_card_displays_full_information_and_events(tmp_path, monkeypatch):
    viewer = build_viewer(tmp_path)

    viewer.repository.get_person = lambda _person_id: (
        "I1",
        "Doe",
        "John",
        "M",
        "1 JAN 1900",
        "Paris",
        "2 FEB 1970",
        "Lyon",
        "Engineer",
        "Detailed life note",
    )
    viewer.repository.get_biological_fathers = lambda _gedcom_id: [("Doe", "Senior", "I2")]
    viewer.repository.get_biological_mothers = lambda _gedcom_id: [("Doe", "Mary", "I7")]
    viewer.repository.get_adoptive_parents = lambda _gedcom_id: [("Doe", "Adoptive", "I20")]
    viewer.repository.get_spouses = lambda _gedcom_id: [("Doe", "Jane", "I3")]
    viewer.repository.get_children = lambda _gedcom_id: [("Doe", "Alice", "I4")]
    viewer.repository.get_full_siblings = lambda _gedcom_id: [("Doe", "Bob", "I5")]
    viewer.repository.get_half_siblings_paternal = lambda _gedcom_id: [("Doe", "Pat", "I21")]
    viewer.repository.get_half_siblings_maternal = lambda _gedcom_id: [("Doe", "Mat", "I22")]
    viewer.repository.get_grandparents = lambda _gedcom_id: [("Doe", "Grand", "I6")]
    viewer.repository.get_grandchildren = lambda _gedcom_id: [("Doe", "Little", "I8")]
    viewer.repository.get_uncles_aunts = lambda _gedcom_id: [("Doe", "Uncle", "I23")]
    viewer.repository.get_nephews_nieces = lambda _gedcom_id: [("Doe", "Niece", "I24")]
    viewer.repository.get_first_cousins = lambda _gedcom_id: [("Doe", "Cousin", "I25")]
    viewer.repository.get_person_by_gedcom_id = lambda gedcom_id: {
        "I2": (22,),
        "I7": (77,),
        "I20": (200,),
        "I3": (33,),
        "I4": (44,),
        "I5": (55,),
        "I21": (210,),
        "I22": (220,),
        "I6": (66,),
        "I8": (88,),
        "I23": (230,),
        "I24": (240,),
        "I25": (250,),
    }.get(gedcom_id)
    viewer.event_service = type(
        "EventService",
        (),
        {
            "list_events": lambda self, _person_id: [
                {
                    "event_type": "residence",
                    "date": "1930",
                    "place": "Berlin",
                    "description": "Moved for work",
                }
            ]
        },
    )()

    _FakeWidget.instances = []
    _FakeButton.instances = []
    _FakeLabelFrame.instances = []
    _FakeListbox.instances = []
    _FakeToplevel.instances = []
    monkeypatch.setattr(viewer_module.tk, "Toplevel", _FakeToplevel)
    monkeypatch.setattr(viewer_module.tk, "Frame", _FakeWidget)
    monkeypatch.setattr(viewer_module.tk, "Canvas", _FakeWidget)
    monkeypatch.setattr(viewer_module.tk, "LabelFrame", _FakeLabelFrame)
    monkeypatch.setattr(viewer_module.tk, "Label", _FakeWidget)
    monkeypatch.setattr(viewer_module.tk, "Button", _FakeButton)
    monkeypatch.setattr(viewer_module.tk, "Listbox", _FakeListbox)
    monkeypatch.setattr(viewer_module.ttk, "Scrollbar", _FakeWidget)

    viewer.show_person(1)

    assert _FakeToplevel.instances[-1].title_value == "Person Card"
    labels = [widget.text for widget in _FakeWidget.instances if widget.text]
    assert "Doe John" in labels
    assert "Пол:" in labels
    assert "M" in labels
    assert "Дата рождения:" in labels
    assert "1 JAN 1900" in labels
    assert "Место рождения:" in labels
    assert "Paris" in labels
    assert "Дата смерти:" in labels
    assert "2 FEB 1970" in labels
    assert "Место смерти:" in labels
    assert "Lyon" in labels
    assert "Занятие:" in labels
    assert "Engineer" in labels
    assert "Примечания:" in labels
    assert "Detailed life note" in labels

    section_titles = [section.text for section in _FakeLabelFrame.instances]
    assert "Основные данные" in section_titles
    assert "Биологический отец" in section_titles
    assert "Биологическая мать" in section_titles
    assert "Приемные родители" in section_titles
    assert "Супруги" in section_titles
    assert "Дети" in section_titles
    assert "Родные братья и сестры" in section_titles
    assert "Единокровные братья и сестры" in section_titles
    assert "Единоутробные братья и сестры" in section_titles
    assert "Дедушки и бабушки" in section_titles
    assert "Внуки" in section_titles
    assert "Дяди и тети" in section_titles
    assert "Племянники и племянницы" in section_titles
    assert "Двоюродные братья и сестры" in section_titles
    assert "События" in section_titles

    button_texts = [button.text for button in _FakeButton.instances]
    assert "Редактировать семью" in button_texts
    assert "Doe Senior" in button_texts
    assert "Doe Mary" in button_texts
    assert "Doe Adoptive" in button_texts
    assert "Doe Jane" in button_texts
    assert "Doe Alice" in button_texts
    assert "Doe Bob" in button_texts
    assert "Doe Pat" in button_texts
    assert "Doe Mat" in button_texts
    assert "Doe Grand" in button_texts
    assert "Doe Little" in button_texts
    assert "Doe Uncle" in button_texts
    assert "Doe Niece" in button_texts
    assert "Doe Cousin" in button_texts
    assert all("I2" not in text and "I3" not in text and "I4" not in text and "I5" not in text and "I6" not in text and "I7" not in text and "I8" not in text and "I20" not in text and "I21" not in text and "I22" not in text and "I23" not in text and "I24" not in text and "I25" not in text for text in button_texts)

    assert _FakeListbox.instances
    assert any("residence: 1930 | Berlin | Moved for work" in listbox.items for listbox in _FakeListbox.instances)


def test_show_person_uses_db_id_reference_when_gedcom_id_is_missing(tmp_path, monkeypatch):
    viewer = build_viewer(tmp_path)
    calls = []

    viewer.repository.get_person = lambda _person_id: ("", "Doe", "Manual", "M", "", "", "", "", "", "")

    def _record(reference):
        calls.append(reference)
        return []

    viewer.repository.get_biological_fathers = _record
    viewer.repository.get_biological_mothers = _record
    viewer.repository.get_adoptive_parents = _record
    viewer.repository.get_spouses = _record
    viewer.repository.get_children = _record
    viewer.repository.get_full_siblings = _record
    viewer.repository.get_half_siblings_paternal = _record
    viewer.repository.get_half_siblings_maternal = _record
    viewer.repository.get_grandparents = _record
    viewer.repository.get_grandchildren = _record
    viewer.repository.get_uncles_aunts = _record
    viewer.repository.get_nephews_nieces = _record
    viewer.repository.get_first_cousins = _record
    viewer.event_service = type("EventService", (), {"list_events": lambda self, _person_id: []})()

    _FakeWidget.instances = []
    _FakeButton.instances = []
    _FakeLabelFrame.instances = []
    _FakeListbox.instances = []
    _FakeToplevel.instances = []
    monkeypatch.setattr(viewer_module.tk, "Toplevel", _FakeToplevel)
    monkeypatch.setattr(viewer_module.tk, "Frame", _FakeWidget)
    monkeypatch.setattr(viewer_module.tk, "Canvas", _FakeWidget)
    monkeypatch.setattr(viewer_module.tk, "LabelFrame", _FakeLabelFrame)
    monkeypatch.setattr(viewer_module.tk, "Label", _FakeWidget)
    monkeypatch.setattr(viewer_module.tk, "Button", _FakeButton)
    monkeypatch.setattr(viewer_module.tk, "Listbox", _FakeListbox)
    monkeypatch.setattr(viewer_module.ttk, "Scrollbar", _FakeWidget)

    viewer.show_person(7)

    assert calls
    assert set(calls) == {"7"}


def test_show_person_card_relatives_are_clickable_and_reopen_cards(tmp_path, monkeypatch):
    viewer = build_viewer(tmp_path)

    viewer.repository.get_person = lambda _person_id: ("I1", "Doe", "John", "M", "", "", "", "", "", "")
    viewer.repository.get_biological_fathers = lambda _gedcom_id: [("Doe", "Senior", "I2")]
    viewer.repository.get_biological_mothers = lambda _gedcom_id: [("Doe", "Mary", "I7")]
    viewer.repository.get_adoptive_parents = lambda _gedcom_id: [("Doe", "Adoptive", "I20")]
    viewer.repository.get_spouses = lambda _gedcom_id: [("Doe", "Jane", "I3")]
    viewer.repository.get_children = lambda _gedcom_id: [("Doe", "Alice", "I4")]
    viewer.repository.get_full_siblings = lambda _gedcom_id: [("Doe", "Bob", "I5")]
    viewer.repository.get_half_siblings_paternal = lambda _gedcom_id: [("Doe", "Pat", "I21")]
    viewer.repository.get_half_siblings_maternal = lambda _gedcom_id: [("Doe", "Mat", "I22")]
    viewer.repository.get_grandparents = lambda _gedcom_id: [("Doe", "Grand", "I6")]
    viewer.repository.get_grandchildren = lambda _gedcom_id: [("Doe", "Little", "I8")]
    viewer.repository.get_uncles_aunts = lambda _gedcom_id: [("Doe", "Uncle", "I23")]
    viewer.repository.get_nephews_nieces = lambda _gedcom_id: [("Doe", "Niece", "I24")]
    viewer.repository.get_first_cousins = lambda _gedcom_id: [("Doe", "Cousin", "I25")]
    viewer.repository.get_person_by_gedcom_id = lambda gedcom_id: {
        "I2": (22,),
        "I7": (77,),
        "I20": (200,),
        "I3": (33,),
        "I4": (44,),
        "I5": (55,),
        "I21": (210,),
        "I22": (220,),
        "I6": (66,),
        "I8": (88,),
        "I23": (230,),
        "I24": (240,),
        "I25": (250,),
    }.get(gedcom_id)

    _FakeButton.instances = []
    _FakeLabelFrame.instances = []
    _FakeListbox.instances = []
    _FakeToplevel.instances = []
    monkeypatch.setattr(viewer_module.tk, "Toplevel", _FakeToplevel)
    monkeypatch.setattr(viewer_module.tk, "Frame", _FakeWidget)
    monkeypatch.setattr(viewer_module.tk, "Canvas", _FakeWidget)
    monkeypatch.setattr(viewer_module.tk, "LabelFrame", _FakeLabelFrame)
    monkeypatch.setattr(viewer_module.tk, "Label", _FakeWidget)
    monkeypatch.setattr(viewer_module.tk, "Button", _FakeButton)
    monkeypatch.setattr(viewer_module.tk, "Listbox", _FakeListbox)
    monkeypatch.setattr(viewer_module.ttk, "Scrollbar", _FakeWidget)

    viewer.show_person(1)

    opened = []
    viewer.show_person = lambda person_id, add_to_history=True: opened.append(person_id)

    relation_buttons = [
        button
        for button in _FakeButton.instances
        if button.text in {
            "Doe Senior",
            "Doe Mary",
            "Doe Adoptive",
            "Doe Jane",
            "Doe Alice",
            "Doe Bob",
            "Doe Pat",
            "Doe Mat",
            "Doe Grand",
            "Doe Little",
            "Doe Uncle",
            "Doe Niece",
            "Doe Cousin",
        }
    ]
    assert len(relation_buttons) == 13
    for button in relation_buttons:
        button.invoke()

    assert opened == [22, 77, 200, 33, 44, 55, 210, 220, 66, 88, 230, 240, 250]


def test_show_person_reuses_single_person_card_window(tmp_path, monkeypatch):
    viewer = build_viewer(tmp_path)

    people = {
        1: ("I1", "Doe", "John", "M", "", "", "", "", "", ""),
        2: ("I2", "Doe", "Jane", "F", "", "", "", "", "", ""),
    }
    viewer.repository.get_person = lambda person_id: people.get(person_id)
    viewer.repository.get_biological_fathers = lambda _gedcom_id: []
    viewer.repository.get_biological_mothers = lambda _gedcom_id: []
    viewer.repository.get_adoptive_parents = lambda _gedcom_id: []
    viewer.repository.get_spouses = lambda gedcom_id: [("Doe", "Jane", "I2")] if gedcom_id == "I1" else []
    viewer.repository.get_children = lambda _gedcom_id: []
    viewer.repository.get_full_siblings = lambda _gedcom_id: []
    viewer.repository.get_half_siblings_paternal = lambda _gedcom_id: []
    viewer.repository.get_half_siblings_maternal = lambda _gedcom_id: []
    viewer.repository.get_grandparents = lambda _gedcom_id: []
    viewer.repository.get_grandchildren = lambda _gedcom_id: []
    viewer.repository.get_uncles_aunts = lambda _gedcom_id: []
    viewer.repository.get_nephews_nieces = lambda _gedcom_id: []
    viewer.repository.get_first_cousins = lambda _gedcom_id: []
    viewer.repository.get_person_by_gedcom_id = lambda gedcom_id: (2,) if gedcom_id == "I2" else None

    _FakeWidget.instances = []
    _FakeButton.instances = []
    _FakeLabelFrame.instances = []
    _FakeListbox.instances = []
    _FakeToplevel.instances = []
    monkeypatch.setattr(viewer_module.tk, "Toplevel", _FakeToplevel)
    monkeypatch.setattr(viewer_module.tk, "Frame", _FakeWidget)
    monkeypatch.setattr(viewer_module.tk, "Canvas", _FakeWidget)
    monkeypatch.setattr(viewer_module.tk, "LabelFrame", _FakeLabelFrame)
    monkeypatch.setattr(viewer_module.tk, "Label", _FakeWidget)
    monkeypatch.setattr(viewer_module.tk, "Button", _FakeButton)
    monkeypatch.setattr(viewer_module.tk, "Listbox", _FakeListbox)
    monkeypatch.setattr(viewer_module.ttk, "Scrollbar", _FakeWidget)

    viewer.show_person(1)
    spouse_button = next(button for button in _FakeButton.instances if button.text == "Doe Jane")
    spouse_button.invoke()

    assert len(_FakeToplevel.instances) == 1
    assert _FakeToplevel.instances[0].title_value == "Person Card"
    assert viewer.current_person_id == 2
    assert viewer._person_history == [1, 2]


def test_apply_relationship_change_refreshes_person_card_immediately(tmp_path):
    viewer = build_viewer(tmp_path)
    calls = []

    viewer.refresh_views = lambda: calls.append("views")
    viewer._refresh_person_card = lambda: calls.append("card")

    assert viewer._apply_relationship_change(lambda: None) is True
    assert calls == ["views", "card"]
