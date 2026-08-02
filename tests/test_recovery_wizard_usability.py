from types import SimpleNamespace

import viewer as viewer_module
from viewer import GenealogyViewer


class FakeWindow:
    def geometry(self):
        return "1180x820+40+30"


class FakePane:
    def __init__(self, coordinates):
        self.coordinates = coordinates

    def sash_coord(self, index):
        return self.coordinates[index]


class FakeLabel:
    def __init__(self):
        self.text = ""

    def configure(self, **kwargs):
        self.text = kwargs.get("text", self.text)


class FakeVar:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeText:
    def __init__(self, value=""):
        self.value = value

    def get(self, *_args):
        return self.value

    def insert(self, _index, value):
        self.value += value


class FakeCandidateTree:
    def get_children(self):
        return ("candidate-1",)

    def selection_set(self, _item):
        return None

    def see(self, _item):
        return None


class FakeList:
    def __init__(self):
        self.selected = None

    def selection_clear(self, _first, _last):
        self.selected = None

    def selection_set(self, index):
        self.selected = index

    def see(self, _index):
        return None


class ReadOnlyRepository:
    def get_person_record(self, person_id):
        assert person_id == 7
        return {
            "first_name": "Анна",
            "last_name": "Орлова",
            "birth_date": "1901",
            "birth_place": "Москва",
            "note": "Архивная карточка",
        }


def test_recovery_ui_state_round_trip(tmp_path):
    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer._recovery_ui_path = tmp_path / "recovery_wizard_ui.json"
    viewer._recovery_ui_state = {}
    viewer._recovery_records = [SimpleNamespace(person_id=42)]
    viewer._recovery_index = 0
    viewer._recovery_window = FakeWindow()
    viewer._recovery_main_pane = FakePane([(275, 0)])
    viewer._recovery_content_pane = FakePane([(0, 220), (0, 470)])

    viewer._save_recovery_ui_state(capture_layout=True)
    state = viewer._load_recovery_ui_state()

    assert state == {
        "selected_person_id": 42,
        "geometry": "1180x820+40+30",
        "main_sashes": [275],
        "content_sashes": [220, 470],
    }


def test_recovery_progress_text():
    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer._recovery_records = [object(), object()]
    viewer._recovery_total = 5
    viewer._recovery_progress = FakeLabel()

    viewer._update_recovery_progress()

    assert viewer._recovery_progress.text == "Processed: 3 / 5    Remaining: 2    Completed: 60%"


def test_batch_progress_includes_estimated_time(monkeypatch):
    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer._batch_records = [object(), object()]
    viewer._batch_total = 5
    viewer._batch_started_at = 100.0
    viewer._batch_progress = FakeLabel()
    monkeypatch.setattr(viewer_module.time, "perf_counter", lambda: 160.0)

    viewer._update_batch_progress()

    assert viewer._batch_progress.text == (
        "Processed: 3    Remaining: 2    Percent: 60%    Estimated remaining time: 00:00:40"
    )


def test_loading_batch_person_immediately_calculates_candidates():
    viewer = GenealogyViewer.__new__(GenealogyViewer)
    record = SimpleNamespace(person_id=7, gedcom_id="I7")
    calls = []
    viewer._batch_records = [record]
    viewer._batch_current = FakeLabel()
    viewer._batch_vars = {}
    viewer._batch_note = FakeText()
    viewer._batch_list = FakeList()
    viewer._load_record_form = lambda *_args: calls.append("load")
    viewer._calculate_batch_candidates = lambda: calls.append("candidates")
    viewer._update_batch_progress = lambda: calls.append("progress")

    viewer._load_batch_record(0)

    assert viewer._batch_index == 0
    assert viewer._batch_list.selected == 0
    assert calls == ["load", "candidates", "progress"]


def test_choose_batch_candidate_only_stages_empty_fields():
    viewer = GenealogyViewer.__new__(GenealogyViewer)
    viewer.repository = ReadOnlyRepository()
    viewer._batch_candidates = [SimpleNamespace(person_id=7)]
    viewer._batch_vars = {
        "first_name": FakeVar(),
        "last_name": FakeVar(),
        "birth_date": FakeVar("1899"),
        "birth_place": FakeVar(),
    }
    viewer._batch_note = FakeText()
    viewer._batch_candidate_tree = FakeCandidateTree()

    result = viewer._choose_batch_candidate(1)

    assert result == "break"
    assert viewer._batch_vars["first_name"].get() == "Анна"
    assert viewer._batch_vars["last_name"].get() == "Орлова"
    assert viewer._batch_vars["birth_date"].get() == "1899"
    assert viewer._batch_vars["birth_place"].get() == "Москва"
    assert viewer._batch_note.get() == "Архивная карточка"
