import sqlite3
from pathlib import Path

import pytest

import viewer as viewer_module
from repository import PersonRepository
from repository.person_attachment_service import PersonAttachmentService


@pytest.fixture
def attachment_repo(tmp_path):
    db_path = tmp_path / "attachments.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(Path("schema.sql").read_text(encoding="utf-8"))
    conn.commit()
    conn.close()

    repo = PersonRepository(str(db_path))
    try:
        yield repo
    finally:
        repo.close()


def _create_person(repo, gedcom_id="I1"):
    return repo.create_person(
        {
            "gedcom_id": gedcom_id,
            "first_name": "John",
            "last_name": "Doe",
            "sex": "M",
            "birth_date": "",
            "birth_place": "",
            "death_date": "",
            "death_place": "",
            "occupation": "",
            "note": "",
        }
    )


def test_photo_attachment_copies_file_and_creates_record(attachment_repo, tmp_path):
    person_id = _create_person(attachment_repo, "I1")
    service = PersonAttachmentService(attachment_repo, media_root=tmp_path / "managed_media")

    source_photo = tmp_path / "photo.jpg"
    source_photo.write_bytes(b"fake-jpeg")

    media = service.attach_media_file(person_id, "photo", str(source_photo), title="Портрет")

    assert media["media_type"] == "photo"
    assert media["title"] == "Портрет"
    stored_path = Path(media["file_path"])
    assert stored_path.exists()
    assert stored_path.parent == (tmp_path / "managed_media" / "photos")
    assert stored_path.suffix == ".jpg"
    assert stored_path.read_bytes() == b"fake-jpeg"


def test_document_attachment_copies_file_and_creates_record(attachment_repo, tmp_path):
    person_id = _create_person(attachment_repo, "I2")
    service = PersonAttachmentService(attachment_repo, media_root=tmp_path / "managed_media")

    source_document = tmp_path / "certificate.pdf"
    source_document.write_bytes(b"fake-pdf")

    media = service.attach_media_file(person_id, "document", str(source_document), title="Свидетельство")

    assert media["media_type"] == "document"
    assert media["title"] == "Свидетельство"
    stored_path = Path(media["file_path"])
    assert stored_path.exists()
    assert stored_path.parent == (tmp_path / "managed_media" / "documents")
    assert stored_path.suffix == ".pdf"
    assert stored_path.read_bytes() == b"fake-pdf"


def test_source_link_crud_supports_url_and_archive_reference(attachment_repo):
    person_id = _create_person(attachment_repo, "I3")
    service = PersonAttachmentService(attachment_repo)

    created = service.create_source(
        person_id,
        title="Birth Register",
        source_url="https://example.org/source",
        archive_reference="Archive-12/F-7",
        note="Scanned by museum",
    )

    assert created["title"] == "Birth Register"
    assert created["source_url"] == "https://example.org/source"
    assert created["archive_reference"] == "Archive-12/F-7"

    updated = service.update_source(
        created["id"],
        title="Birth Register Updated",
        source_url="",
        archive_reference="Archive-12/F-8",
        note="URL optional",
    )

    assert updated["title"] == "Birth Register Updated"
    assert updated["source_url"] == ""
    assert updated["archive_reference"] == "Archive-12/F-8"

    listed = service.list_sources(person_id)
    assert len(listed) == 1
    assert listed[0]["id"] == created["id"]

    assert service.delete_source(created["id"]) is True
    assert service.list_sources(person_id) == []


def test_safe_deletion_only_removes_managed_copied_files(attachment_repo, tmp_path):
    person_id = _create_person(attachment_repo, "I4")
    service = PersonAttachmentService(attachment_repo, media_root=tmp_path / "managed_media")

    managed_source = tmp_path / "managed_photo.png"
    managed_source.write_bytes(b"photo")
    managed_media = service.attach_media_file(person_id, "photo", str(managed_source), title="Фото")
    managed_copy = Path(managed_media["file_path"])
    assert managed_copy.exists()

    external_file = tmp_path / "external_note.txt"
    external_file.write_text("external", encoding="utf-8")
    external_media_id = attachment_repo.create_person_media(
        {
            "person_id": person_id,
            "media_type": "document",
            "title": "External",
            "file_path": str(external_file.resolve()),
            "description": "outside managed dir",
        }
    )

    assert service.delete_media(managed_media["id"]) is True
    assert not managed_copy.exists()

    assert service.delete_media(external_media_id) is True
    assert external_file.exists()


def test_delete_person_cascades_media_and_sources_cleanup(attachment_repo, tmp_path):
    person_id = _create_person(attachment_repo, "I5")
    service = PersonAttachmentService(attachment_repo, media_root=tmp_path / "managed_media")

    source_file = tmp_path / "doc.txt"
    source_file.write_text("doc", encoding="utf-8")
    service.attach_media_file(person_id, "document", str(source_file), title="Doc")
    service.create_source(person_id, title="Archive", archive_reference="A-1")

    assert len(service.list_media(person_id)) == 1
    assert len(service.list_sources(person_id)) == 1

    assert attachment_repo.delete_person(person_id) is True

    assert service.list_media(person_id) == []
    assert service.list_sources(person_id) == []


def test_primary_portrait_is_first_and_switchable(attachment_repo, tmp_path):
    person_id = _create_person(attachment_repo, "I6")
    service = PersonAttachmentService(attachment_repo, media_root=tmp_path / "managed_media")

    first = tmp_path / "first.jpg"
    second = tmp_path / "second.jpg"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    media1 = service.attach_media_file(person_id, "photo", str(first), title="Первое")
    media2 = service.attach_media_file(person_id, "photo", str(second), title="Второе")

    grouped = service.list_media_grouped(person_id)
    assert [item["id"] for item in grouped["photos"]] == [media1["id"], media2["id"]]

    service.set_primary_photo(media2["id"])
    grouped_after = service.list_media_grouped(person_id)
    assert [item["id"] for item in grouped_after["photos"]] == [media2["id"], media1["id"]]
    assert grouped_after["photos"][0]["is_primary"] is True


def test_media_placeholder_and_navigation_and_missing_file(monkeypatch):
    viewer = viewer_module.GenealogyViewer.__new__(viewer_module.GenealogyViewer)
    viewer._card_photo_records = []
    viewer._card_photo_index = 0
    viewer._card_photo_image = None

    class FakeLabel:
        def __init__(self):
            self.state = {}

        def config(self, **kwargs):
            self.state.update(kwargs)

    preview = FakeLabel()
    counter = FakeLabel()
    title = FakeLabel()
    description = FakeLabel()

    viewer._render_photo_preview(preview, counter, title, description)
    assert preview.state.get("text") == "Фотография отсутствует"
    assert counter.state.get("text") == "0 из 0"

    viewer._card_photo_records = [
        {"id": 1, "title": "A", "description": "descA", "file_path": "a.jpg"},
        {"id": 2, "title": "B", "description": "descB", "file_path": "b.jpg"},
    ]
    viewer._card_photo_index = 0
    monkeypatch.setattr(viewer, "_load_photo_preview_image", lambda *_args, **_kwargs: object())
    viewer._render_photo_preview(preview, counter, title, description)
    assert counter.state.get("text") == "1 из 2"

    viewer._show_next_photo(preview, counter, title, description)
    assert counter.state.get("text") == "2 из 2"

    viewer._show_previous_photo(preview, counter, title, description)
    assert counter.state.get("text") == "1 из 2"

    monkeypatch.setattr(viewer, "_load_photo_preview_image", lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("missing")))
    viewer._render_photo_preview(preview, counter, title, description)
    assert preview.state.get("text") == "Файл фотографии не найден"


def test_thumbnail_creation_preserves_aspect_ratio(monkeypatch, tmp_path):
    viewer = viewer_module.GenealogyViewer.__new__(viewer_module.GenealogyViewer)

    image_path = tmp_path / "ratio.png"
    image_path.write_bytes(b"fake")

    class FakeImage:
        def __init__(self):
            self.size = (400, 200)
            self.thumbnail_args = None

        def thumbnail(self, bounds, _resample):
            self.thumbnail_args = bounds
            w, h = self.size
            ratio = min(bounds[0] / w, bounds[1] / h)
            self.size = (int(w * ratio), int(h * ratio))

    fake_image = FakeImage()

    class FakeImageModule:
        class Resampling:
            LANCZOS = 1

        @staticmethod
        def open(_path):
            return fake_image

    class FakeImageTkModule:
        @staticmethod
        def PhotoImage(img):
            return {"width": img.size[0], "height": img.size[1]}

    monkeypatch.setattr(viewer_module, "Image", FakeImageModule)
    monkeypatch.setattr(viewer_module, "ImageTk", FakeImageTkModule)

    preview = viewer._load_photo_preview_image(str(image_path), max_width=320, max_height=220)

    assert fake_image.thumbnail_args == (320, 220)
    assert preview["width"] == 320
    assert preview["height"] == 160


def test_document_row_formatting_contains_required_fields():
    row_text = viewer_module.GenealogyViewer._document_row_text(
        {
            "title": "Справка",
            "file_path": "C:/docs/cert.pdf",
            "description": "Архивная справка",
            "created_at": "2026-08-01 10:00:00",
        }
    )

    assert "Справка" in row_text
    assert "PDF" in row_text
    assert "Архивная справка" in row_text
    assert "2026-08-01" in row_text
