"""Persistent per-workspace project state (``<workspace>/.agent/``)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from powerbi_agent.models.datasource import DataSource
from powerbi_agent.models.tasks import Task, utcnow
from powerbi_agent.tools.filesystem import atomic_write
from powerbi_agent.utils.security import confine_path

STATE_DIR = ".agent"


class ProjectState(BaseModel):
    workspace: str
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    data_sources: list[DataSource] = Field(default_factory=list)
    task_ids: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list, description="Workspace-relative paths.")


class StateStore:
    """Reads and writes project state and task records for one workspace directory."""

    def __init__(self, workspace_dir: Path) -> None:
        self.workspace_dir = workspace_dir
        self.root = workspace_dir / STATE_DIR

    @property
    def state_file(self) -> Path:
        return self.root / "state.json"

    def load(self) -> ProjectState:
        if self.state_file.exists():
            return ProjectState.model_validate_json(self.state_file.read_text(encoding="utf-8"))
        return ProjectState(workspace=self.workspace_dir.name)

    def save(self, state: ProjectState) -> None:
        state.updated_at = utcnow()
        atomic_write(self.state_file, state.model_dump_json(indent=2))

    def save_task(self, task: Task) -> Path:
        path = confine_path(self.root, Path("tasks") / f"{task.id}.json")
        atomic_write(path, task.model_dump_json(indent=2))
        state = self.load()
        if task.id not in state.task_ids:
            state.task_ids.append(task.id)
            self.save(state)
        return path

    def load_task(self, task_id: str) -> Task | None:
        path = confine_path(self.root, Path("tasks") / f"{task_id}.json")
        if not path.exists():
            return None
        return Task.model_validate_json(path.read_text(encoding="utf-8"))
