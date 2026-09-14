"""Saved workflow definitions and QSettings persistence."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field

from PySide6.QtCore import QSettings

from ..utils.file_rename import PRESET_BY_ID, PRESET_LABELS, PRESET_TRACKNO_TRACKNAME, resolve_preset

SETTINGS_ORG = "Snowsky Echo Mini Toolbox"
SETTINGS_APP = "Snowsky Echo Mini Toolbox"
SETTINGS_KEY = "workflows/data"

WORKFLOW_ACTIONS: dict[str, str] = {
    "backup": "Backup Files",
    "file_cleanup": "File Cleanup",
    "file_rename": "File Rename",
    "metadata_manager": "Metadata Manager",
    "lyrics_manager": "Lyrics Manager",
    "fix_album_art": "Fix Album Art",
    "make_music_compatible": "Make Music Compatible (FLAC)",
    "make_music_eq_compatible": "Make Music EQ-Compatible",
}

CONFIGURABLE_STEPS = {
    "backup",
    "file_cleanup",
    "file_rename",
    "metadata_manager",
    "lyrics_manager",
}

LIBRARY_STEPS = {
    "file_rename",
    "metadata_manager",
    "lyrics_manager",
    "fix_album_art",
    "make_music_compatible",
    "make_music_eq_compatible",
}

MUTATING_STEPS = {
    "file_cleanup",
    "file_rename",
    "metadata_manager",
    "lyrics_manager",
    "fix_album_art",
    "make_music_compatible",
    "make_music_eq_compatible",
}

METADATA_TAG_CHOICES: tuple[tuple[str, str], ...] = (
    ("title", "Title"),
    ("artist", "Artist"),
    ("album", "Album"),
    ("albumartist", "Album Artist"),
    ("genre", "Genre"),
    ("date", "Year"),
    ("tracknumber", "Track Number"),
)

_TAG_KEY_ALIASES = {
    "title": "title",
    "artist": "artist",
    "album": "album",
    "album artist": "albumartist",
    "albumartist": "albumartist",
    "genre": "genre",
    "year": "date",
    "date": "date",
    "track": "tracknumber",
    "track number": "tracknumber",
    "tracknumber": "tracknumber",
    "composer": "composer",
}

_LEGACY_RENAME_PRESETS = {
    "track no. title": PRESET_TRACKNO_TRACKNAME,
    "track no title": PRESET_TRACKNO_TRACKNAME,
}


@dataclass
class WorkflowStep:
    type: str
    config: dict = field(default_factory=dict)


@dataclass
class Workflow:
    id: str
    name: str
    steps: list[WorkflowStep] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "steps": [{"type": step.type, "config": step.config} for step in self.steps],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Workflow:
        steps = [
            WorkflowStep(type=str(step.get("type") or ""), config=dict(step.get("config") or {}))
            for step in data.get("steps", [])
            if step.get("type")
        ]
        return cls(
            id=str(data.get("id") or uuid.uuid4()),
            name=str(data.get("name") or "Untitled Workflow"),
            steps=steps,
        )

    def needs_library(self) -> bool:
        return any(step.type in LIBRARY_STEPS for step in self.steps)

    def needs_rescan(self) -> bool:
        return any(step.type in MUTATING_STEPS for step in self.steps)


def new_workflow(name: str = "New Workflow") -> Workflow:
    return Workflow(id=str(uuid.uuid4()), name=name, steps=[])


def action_label(step_type: str) -> str:
    return WORKFLOW_ACTIONS.get(step_type, step_type)


def normalize_tag_key(name: str) -> str:
    lowered = " ".join(name.replace("_", " ").strip().lower().split())
    return _TAG_KEY_ALIASES.get(lowered, name.strip())


def tag_choice_label(key: str) -> str:
    for choice_key, label in METADATA_TAG_CHOICES:
        if choice_key == key:
            return label
    return key


def resolve_rename_preset_id(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return PRESET_TRACKNO_TRACKNAME
    if text in PRESET_BY_ID:
        return text
    lowered = text.lower()
    if lowered in _LEGACY_RENAME_PRESETS:
        return _LEGACY_RENAME_PRESETS[lowered]
    for preset_id, label in PRESET_LABELS.items():
        if label.lower() == lowered:
            return preset_id
    return resolve_preset(text).id


def describe_step(step: WorkflowStep) -> str:
    label = action_label(step.type)
    extra = ""
    if step.type == "backup":
        path = str(step.config.get("backup_path") or "").strip()
        extra = path or "folder not set"
    elif step.type == "file_cleanup":
        parts = []
        if step.config.get("clean_hidden", True):
            parts.append("hidden files")
        if step.config.get("clean_forks", True):
            parts.append("resource forks")
        if step.config.get("clean_empty", True):
            parts.append("empty folders")
        extra = ", ".join(parts) or "nothing selected"
    elif step.type == "file_rename":
        preset_id = resolve_rename_preset_id(str(step.config.get("preset") or ""))
        extra = PRESET_LABELS.get(preset_id, preset_id)
    elif step.type == "metadata_manager":
        tag_name = normalize_tag_key(str(step.config.get("tag_name") or ""))
        action = str(step.config.get("action") or "Set").strip() or "Set"
        extra = f"{action} {tag_choice_label(tag_name)}" if tag_name else "tag not set"
    elif step.type == "lyrics_manager":
        extra = "auto-apply first match" if step.config.get("auto_apply") else "review each match"
    if extra:
        return f"{label} — {extra}"
    return label


class WorkflowManager:
    def __init__(self) -> None:
        self.settings = QSettings(SETTINGS_ORG, SETTINGS_APP)
        self.workflows: list[Workflow] = self._load_workflows()

    def _load_workflows(self) -> list[Workflow]:
        data = self.settings.value(SETTINGS_KEY, "[]")
        try:
            parsed = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            return []
        if not isinstance(parsed, list):
            return []
        workflows = []
        for item in parsed:
            if isinstance(item, dict):
                workflows.append(Workflow.from_dict(item))
        return workflows

    def save_workflows(self) -> None:
        data = json.dumps([workflow.to_dict() for workflow in self.workflows])
        self.settings.setValue(SETTINGS_KEY, data)
        self.settings.sync()

    def add_workflow(self, workflow: Workflow) -> None:
        self.workflows.append(workflow)
        self.save_workflows()

    def update_workflow(self, workflow: Workflow) -> None:
        for index, existing in enumerate(self.workflows):
            if existing.id == workflow.id:
                self.workflows[index] = workflow
                break
        self.save_workflows()

    def remove_workflow(self, workflow_id: str) -> None:
        self.workflows = [workflow for workflow in self.workflows if workflow.id != workflow_id]
        self.save_workflows()

    def get_workflow(self, workflow_id: str) -> Workflow | None:
        return next((workflow for workflow in self.workflows if workflow.id == workflow_id), None)
