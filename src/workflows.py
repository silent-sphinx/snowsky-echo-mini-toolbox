import json
import uuid
from dataclasses import dataclass, field
from PySide6.QtCore import QSettings

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
            "steps": [{"type": step.type, "config": step.config} for step in self.steps]
        }

    @classmethod
    def from_dict(cls, data: dict) -> 'Workflow':
        steps = [WorkflowStep(**step_data) for step_data in data.get("steps", [])]
        return cls(id=data.get("id", str(uuid.uuid4())), name=data.get("name", ""), steps=steps)

class WorkflowManager:
    def __init__(self):
        self.settings = QSettings()
        self.workflows: list[Workflow] = self._load_workflows()

    def _load_workflows(self) -> list[Workflow]:
        data = self.settings.value("workflows/data", "[]")
        try:
            parsed = json.loads(data)
            return [Workflow.from_dict(w) for w in parsed]
        except (json.JSONDecodeError, TypeError):
            return []

    def save_workflows(self) -> None:
        data = json.dumps([w.to_dict() for w in self.workflows])
        self.settings.setValue("workflows/data", data)
        self.settings.sync()

    def add_workflow(self, workflow: Workflow) -> None:
        self.workflows.append(workflow)
        self.save_workflows()

    def update_workflow(self, workflow: Workflow) -> None:
        for i, w in enumerate(self.workflows):
            if w.id == workflow.id:
                self.workflows[i] = workflow
                break
        self.save_workflows()

    def remove_workflow(self, workflow_id: str) -> None:
        self.workflows = [w for w in self.workflows if w.id != workflow_id]
        self.save_workflows()
