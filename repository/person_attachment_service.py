from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from config import DATA_DIR
from repository.person_repository import PersonRepository


PRIMARY_MARKER = "[PRIMARY]"
PHOTO_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".gif"}
DOCUMENT_EXTENSIONS = {".pdf", ".doc", ".docx", ".txt", ".jpg", ".jpeg", ".png"}


class PersonAttachmentService:
    """Manage documents, media, and sources attached to people."""
    def __init__(self, repository: PersonRepository, media_root: Path | None = None):
        self.repository = repository
        self.media_root = Path(media_root) if media_root else (DATA_DIR / "media")
        self.photos_dir = self.media_root / "photos"
        self.documents_dir = self.media_root / "documents"

    def attach_media_file(self, person_id, media_type, source_path, title="", description=""):
        if media_type not in {"photo", "document"}:
            raise ValueError("Неверный тип медиа")

        source = Path(source_path).expanduser()
        if not source.exists() or not source.is_file():
            raise ValueError("Файл не найден")

        extension = source.suffix.lower()
        if media_type == "photo" and extension not in PHOTO_EXTENSIONS:
            raise ValueError("Поддерживаются только JPG, JPEG, PNG, BMP и GIF")
        if media_type == "document" and extension not in DOCUMENT_EXTENSIONS:
            raise ValueError("Поддерживаются только PDF, DOC, DOCX, TXT, JPG, JPEG и PNG")

        target_dir = self.photos_dir if media_type == "photo" else self.documents_dir
        target_dir.mkdir(parents=True, exist_ok=True)

        file_name = self._build_unique_filename(person_id, extension)
        target = target_dir / file_name
        while target.exists():
            file_name = self._build_unique_filename(person_id, extension)
            target = target_dir / file_name

        shutil.copy2(source, target)
        stored_path = str(target.resolve())
        media_id = self.repository.create_person_media(
            {
                "person_id": person_id,
                "media_type": media_type,
                "title": title or source.stem,
                "file_path": stored_path,
                "description": description,
            }
        )
        return self.repository.get_person_media(media_id)

    def list_media(self, person_id):
        return self.repository.list_person_media(person_id)

    def list_media_grouped(self, person_id):
        records = self.repository.list_person_media(person_id)
        normalized = []
        for media in records:
            cleaned_description, is_primary = self._extract_primary_flag(media.get("description") or "")
            item = dict(media)
            item["description"] = cleaned_description
            item["is_primary"] = is_primary if media.get("media_type") == "photo" else False
            normalized.append(item)

        photos = [item for item in normalized if item.get("media_type") == "photo"]
        documents = [item for item in normalized if item.get("media_type") == "document"]

        photos.sort(key=lambda item: (0 if item.get("is_primary") else 1, item.get("id") or 0))
        documents.sort(key=lambda item: item.get("id") or 0)
        return {"photos": photos, "documents": documents}

    def set_primary_photo(self, media_id):
        media = self.repository.get_person_media(media_id)
        if not media or media.get("media_type") != "photo":
            raise ValueError("Фотография не найдена")

        person_id = media.get("person_id")
        grouped = self.list_media_grouped(person_id)
        for photo in grouped.get("photos", []):
            description = photo.get("description") or ""
            is_target = photo.get("id") == media_id
            updated_description = self._compose_description(description, is_target)
            self.repository.update_person_media(photo.get("id"), {"title": photo.get("title") or "", "description": updated_description})

        result = self.repository.get_person_media(media_id)
        cleaned_description, _is_primary = self._extract_primary_flag(result.get("description") or "")
        result["description"] = cleaned_description
        result["is_primary"] = True
        return result

    def update_media_title(self, media_id, title):
        media = self.repository.get_person_media(media_id)
        if not media:
            return False
        description, is_primary = self._extract_primary_flag(media.get("description") or "")
        return self.repository.update_person_media(
            media_id,
            {
                "title": (title or "").strip(),
                "description": self._compose_description(description, is_primary),
            },
        )

    def update_media_description(self, media_id, description):
        media = self.repository.get_person_media(media_id)
        if not media:
            return False
        _current_description, is_primary = self._extract_primary_flag(media.get("description") or "")
        return self.repository.update_person_media(
            media_id,
            {
                "title": media.get("title") or "",
                "description": self._compose_description((description or "").strip(), is_primary),
            },
        )

    def delete_media(self, media_id):
        media = self.repository.get_person_media(media_id)
        if not media:
            return False

        file_path = Path(media.get("file_path") or "").expanduser()
        managed_deleted = self._delete_managed_file(file_path)
        deleted = self.repository.delete_person_media(media_id)
        return deleted or managed_deleted

    def _delete_managed_file(self, file_path: Path):
        try:
            resolved = file_path.resolve()
            managed_root = self.media_root.resolve()
            try:
                resolved.relative_to(managed_root)
            except ValueError:
                return False
            if resolved.exists() and resolved.is_file():
                resolved.unlink()
                return True
        except OSError:
            return False
        return False

    @staticmethod
    def _build_unique_filename(person_id, extension):
        suffix = extension or ""
        return f"person_{person_id}_{uuid.uuid4().hex}{suffix}"

    def create_source(self, person_id, title="", source_url="", archive_reference="", note=""):
        if not title and not source_url and not archive_reference and not note:
            raise ValueError("Заполните хотя бы одно поле источника")
        source_id = self.repository.create_person_source(
            {
                "person_id": person_id,
                "title": title,
                "source_url": source_url,
                "archive_reference": archive_reference,
                "note": note,
            }
        )
        return self.repository.get_person_source(source_id)

    def update_source(self, source_id, title="", source_url="", archive_reference="", note=""):
        if not title and not source_url and not archive_reference and not note:
            raise ValueError("Заполните хотя бы одно поле источника")
        self.repository.update_person_source(
            source_id,
            {
                "title": title,
                "source_url": source_url,
                "archive_reference": archive_reference,
                "note": note,
            },
        )
        return self.repository.get_person_source(source_id)

    def list_sources(self, person_id):
        return self.repository.list_person_sources(person_id)

    def delete_source(self, source_id):
        return self.repository.delete_person_source(source_id)

    @staticmethod
    def _extract_primary_flag(description):
        text = (description or "").strip()
        if text.startswith(PRIMARY_MARKER):
            cleaned = text[len(PRIMARY_MARKER):].strip()
            return cleaned, True
        return text, False

    @staticmethod
    def _compose_description(description, is_primary):
        clean = (description or "").strip()
        if is_primary:
            return f"{PRIMARY_MARKER} {clean}".strip()
        return clean
