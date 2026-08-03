"""Sidecar-backed research projects for GenealogyDB without genealogy mutations."""

from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from audit_service import AuditService
from config import DATA_DIR
from evidence_service import EvidenceService
from repository.person_timeline_service import PersonTimelineService
from validation_center_service import ValidationCenterService


HYPOTHESIS_STATES = ("Draft", "Active", "Needs evidence", "Confirmed", "Rejected")
TASK_STATUSES = ("Backlog", "In progress", "Blocked", "Done")
TASK_PRIORITIES = ("Low", "Normal", "High", "Critical")


@dataclass(frozen=True)
class ResearchProject:
    project_id: str
    title: str
    description: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class Hypothesis:
    hypothesis_id: str
    title: str
    statement: str
    state: str
    people: tuple[int, ...] = ()
    families: tuple[int, ...] = ()
    events: tuple[int, ...] = ()
    sources: tuple[int, ...] = ()
    evidence: tuple[int, ...] = ()
    notes: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class ResearchTask:
    task_id: str
    title: str
    priority: str
    due_date: str
    status: str
    hypothesis_id: str
    people: tuple[int, ...] = ()
    attachments: tuple[str, ...] = ()
    notes: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass(frozen=True)
class ResearchWorkspace:
    project: ResearchProject
    hypotheses: tuple[Hypothesis, ...]
    tasks: tuple[ResearchTask, ...]
    questions: tuple[dict, ...]
    conclusions: tuple[dict, ...]


class ResearchWorkspaceService:
    """Persist research planning outside the genealogy database."""

    def __init__(self, repository, *, data_dir=None):
        self.repository = repository
        self.data_dir = Path(data_dir or DATA_DIR) / "research"
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def create_project(self, title, description=""):
        project = {"project_id": self._id("project"), "title": self._required(title), "description": str(description), "created_at": self._now(), "updated_at": self._now(), "hypotheses": [], "tasks": [], "questions": [], "conclusions": []}
        self._write(project); self._audit("research_project_create", project["project_id"], {}, project, f"Создан проект исследования: {project['title']}.")
        return self.load(project["project_id"])

    def list_projects(self):
        return tuple(sorted((self._project(self._read(path)) for path in self.data_dir.glob("*.json")), key=lambda item: (item.title.casefold(), item.project_id)))

    def load(self, project_id, *, progress_callback=None, cancel_callback=None):
        if cancel_callback: cancel_callback()
        payload = self._read(self._path(project_id)); project = self._project(payload)
        if progress_callback: progress_callback("Загрузка гипотез", 1, 3)
        if cancel_callback: cancel_callback()
        hypotheses = tuple(sorted((self._hypothesis(item) for item in payload.get("hypotheses", [])), key=lambda item: (item.state, item.title.casefold(), item.hypothesis_id)))
        if progress_callback: progress_callback("Загрузка задач", 2, 3)
        if cancel_callback: cancel_callback()
        tasks = tuple(sorted((self._task(item) for item in payload.get("tasks", [])), key=lambda item: (TASK_STATUSES.index(item.status), TASK_PRIORITIES.index(item.priority), item.due_date or "9999-12-31", item.title.casefold(), item.task_id)))
        if progress_callback: progress_callback("Рабочее пространство готово", 3, 3)
        return ResearchWorkspace(project, hypotheses, tasks, tuple(payload.get("questions", [])), tuple(payload.get("conclusions", [])))

    def update_project(self, project_id, title, description):
        return self._mutate(project_id, "research_project_update", lambda data: data.update(title=self._required(title), description=str(description)))
    def delete_project(self, project_id):
        workspace = self.load(project_id); self._path(project_id).unlink(missing_ok=True); self._audit("research_project_delete", project_id, asdict(workspace.project), {}, f"Удалён проект исследования: {workspace.project.title}.")

    def create_hypothesis(self, project_id, title, statement, *, state="Draft", **links):
        self._state(state); item = self._item("hypothesis", title=title, statement=str(statement), state=state, **links)
        return self._mutate(project_id, "research_hypothesis_create", lambda data: data["hypotheses"].append(item))
    def update_hypothesis(self, project_id, hypothesis_id, **changes):
        if "state" in changes: self._state(changes["state"])
        return self._update_item(project_id, "hypotheses", "hypothesis_id", hypothesis_id, changes, "research_hypothesis_update")
    def delete_hypothesis(self, project_id, hypothesis_id): return self._delete_item(project_id, "hypotheses", "hypothesis_id", hypothesis_id, "research_hypothesis_delete")

    def create_task(self, project_id, title, *, priority="Normal", due_date="", status="Backlog", hypothesis_id="", **links):
        self._priority(priority); self._task_status(status); item = self._item("task", title=title, priority=priority, due_date=str(due_date), status=status, hypothesis_id=str(hypothesis_id), **links)
        return self._mutate(project_id, "research_task_create", lambda data: data["tasks"].append(item))
    def update_task(self, project_id, task_id, **changes):
        if "priority" in changes: self._priority(changes["priority"])
        if "status" in changes: self._task_status(changes["status"])
        return self._update_item(project_id, "tasks", "task_id", task_id, changes, "research_task_update")
    def delete_task(self, project_id, task_id): return self._delete_item(project_id, "tasks", "task_id", task_id, "research_task_delete")

    def add_question(self, project_id, text, *, hypothesis_id=""):
        item = {"question_id": self._id("question"), "text": self._required(text), "hypothesis_id": str(hypothesis_id), "created_at": self._now()}; return self._mutate(project_id, "research_question_create", lambda data: data["questions"].append(item))
    def add_conclusion(self, project_id, text, *, hypothesis_id=""):
        item = {"conclusion_id": self._id("conclusion"), "text": self._required(text), "hypothesis_id": str(hypothesis_id), "created_at": self._now()}; return self._mutate(project_id, "research_conclusion_create", lambda data: data["conclusions"].append(item))

    def kanban(self, workspace): return {status: tuple(task for task in workspace.tasks if task.status == status) for status in TASK_STATUSES}
    def calendar(self, workspace): return tuple(task for task in workspace.tasks if task.due_date)

    def evidence_summary(self, hypothesis):
        model = EvidenceService(self.repository, read_only=True).build_model(); citations = {int(item["id"]): item for item in model.citations}; linked = [citations[item] for item in hypothesis.evidence if item in citations]
        supporting = tuple(item for item in linked if item.get("proof_status") == "Supported"); contradicting = tuple(item for item in linked if item.get("proof_status") == "Contradicted")
        confidence = self._confidence(linked)
        return {"supporting": supporting, "contradicting": contradicting, "confidence": confidence, "citation_count": len(linked)}

    def validation_issues(self, hypothesis):
        report = ValidationCenterService(self.repository).analyze()
        ids = {str(value) for value in (*hypothesis.people, *hypothesis.families, *hypothesis.events)}
        return tuple(issue for issue in report.issues if str(issue.database_id) in ids or issue.gedcom_id in ids)

    def related_timeline(self, hypothesis):
        entries = []
        for person_id in hypothesis.people:
            entries.extend(PersonTimelineService(self.repository).build_timeline(person_id))
        return tuple(sorted(entries, key=PersonTimelineService._timeline_sort_key))
    def linked_people(self, hypothesis): return tuple(sorted(set(hypothesis.people)))

    def export(self, workspace, destination, export_format):
        path = Path(destination); path.parent.mkdir(parents=True, exist_ok=True)
        if export_format == "markdown": path.write_text(self._markdown(workspace), encoding="utf-8")
        elif export_format == "html": path.write_text(f"<!doctype html><meta charset='utf-8'><title>{html.escape(workspace.project.title)}</title><pre>{html.escape(self._markdown(workspace))}</pre>", encoding="utf-8")
        elif export_format == "pdf": path.write_bytes(PersonTimelineService._build_simple_pdf([line.encode("latin-1", errors="replace").decode("latin-1") for line in self._markdown(workspace).splitlines()]))
        else: raise ValueError("Неподдерживаемый формат экспорта")
        return path

    def _mutate(self, project_id, operation, action):
        path = self._path(project_id); before = self._read(path); after = json.loads(json.dumps(before)); action(after); after["updated_at"] = self._now(); self._write(after); self._audit(operation, project_id, before, after, f"Изменено рабочее пространство: {after['title']}."); return self.load(project_id)
    def _update_item(self, project_id, collection, key, item_id, changes, operation):
        def action(data):
            item = next((item for item in data[collection] if item[key] == item_id), None)
            if item is None: raise ValueError("Объект исследования не найден")
            item.update({name: self._normalize_link(value) if name in {"people", "families", "events", "sources", "evidence", "attachments"} else value for name, value in changes.items()}); item["updated_at"] = self._now()
        return self._mutate(project_id, operation, action)
    def _delete_item(self, project_id, collection, key, item_id, operation): return self._mutate(project_id, operation, lambda data: data.__setitem__(collection, [item for item in data[collection] if item[key] != item_id]))
    def _item(self, kind, **values):
        now = self._now(); item = {f"{kind}_id": self._id(kind), "created_at": now, "updated_at": now, "notes": ""}; item.update({key: self._normalize_link(value) if key in {"people", "families", "events", "sources", "evidence", "attachments"} else value for key, value in values.items()}); item["title"] = self._required(item.get("title", "")); return item
    @staticmethod
    def _normalize_link(value): return list(dict.fromkeys(value or ()))
    @staticmethod
    def _required(value):
        text = str(value).strip()
        if not text: raise ValueError("Название обязательно")
        return text
    @staticmethod
    def _state(value):
        if value not in HYPOTHESIS_STATES: raise ValueError("Недопустимое состояние гипотезы")
    @staticmethod
    def _priority(value):
        if value not in TASK_PRIORITIES: raise ValueError("Недопустимый приоритет")
    @staticmethod
    def _task_status(value):
        if value not in TASK_STATUSES: raise ValueError("Недопустимый статус задачи")
    @staticmethod
    def _project(data): return ResearchProject(data["project_id"], data["title"], data.get("description", ""), data["created_at"], data.get("updated_at", data["created_at"]))
    @staticmethod
    def _hypothesis(data): return Hypothesis(**{**data, "people": tuple(data.get("people", ())), "families": tuple(data.get("families", ())), "events": tuple(data.get("events", ())), "sources": tuple(data.get("sources", ())), "evidence": tuple(data.get("evidence", ()))})
    @staticmethod
    def _task(data): return ResearchTask(**{**data, "people": tuple(data.get("people", ())), "attachments": tuple(data.get("attachments", ()))})
    def _path(self, project_id): return self.data_dir / f"{str(project_id)}.json"
    def _write(self, data): self._path(data["project_id"]).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    @staticmethod
    def _read(path): return json.loads(Path(path).read_text(encoding="utf-8"))
    def _audit(self, operation, project_id, before, after, description): AuditService.for_database(self.repository.db_name).record(operation, database_id=project_id, affected_tables=("research_workspace",), before_snapshot={"research_workspace": before}, after_snapshot={"research_workspace": after}, description=description, service="research_workspace_service")
    @staticmethod
    def _confidence(citations):
        values = [item.get("confidence", "Unknown") for item in citations]
        for level in ("Proven", "Strong", "Probable", "Weak", "Disputed"):
            if level in values: return level
        return "Unknown"
    @staticmethod
    def _markdown(workspace):
        lines = [f"# {workspace.project.title}", workspace.project.description, "", "## Hypotheses"]
        lines.extend(f"- [{item.state}] {item.title}: {item.statement}" for item in workspace.hypotheses)
        lines.extend(["", "## Tasks"]); lines.extend(f"- [{item.status}] {item.title} ({item.priority}) {item.due_date}" for item in workspace.tasks)
        lines.extend(["", "## Open questions"]); lines.extend(f"- {item['text']}" for item in workspace.questions)
        lines.extend(["", "## Conclusions"]); lines.extend(f"- {item['text']}" for item in workspace.conclusions)
        return "\n".join(lines)
    @staticmethod
    def _id(prefix): return f"{prefix}-{uuid4().hex[:12]}"
    @staticmethod
    def _now(): return datetime.now(timezone.utc).isoformat(timespec="seconds")