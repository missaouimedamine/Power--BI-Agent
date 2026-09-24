"""Workspace-confined file operations.

Writes are atomic, and nothing is overwritten silently: an existing file is first copied
to ``.agent/history/<run_id>/<relative path>`` so every previous version stays
recoverable.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from powerbi_agent.utils.security import confine_path

HISTORY_DIR = Path(".agent") / "history"


def atomic_write(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data.encode("utf-8") if isinstance(data, str) else data)
        Path(tmp).replace(path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class Workspace:
    """A directory the agent may write into, with versioned overwrites."""

    def __init__(self, root: Path, run_id: str) -> None:
        self.root = root
        self.run_id = run_id
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, relative: str | Path) -> Path:
        return confine_path(self.root, relative)

    def _archive(self, target: Path) -> Path | None:
        if not target.exists():
            return None
        rel = target.relative_to(self.root.resolve())
        backup = confine_path(self.root, HISTORY_DIR / self.run_id / rel)
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup)
        return backup

    def write_bytes(self, relative: str | Path, data: bytes) -> Path:
        target = self.path(relative)
        self._archive(target)
        atomic_write(target, data)
        return target

    def write_text(self, relative: str | Path, text: str) -> Path:
        # PBIP guidance: UTF-8 without BOM. We use it for every text artifact.
        return self.write_bytes(relative, text.encode("utf-8"))

    def write_json(self, relative: str | Path, payload: BaseModel | Any) -> Path:
        if isinstance(payload, BaseModel):
            text = payload.model_dump_json(indent=2, by_alias=True)
        else:
            text = json.dumps(payload, indent=2, default=str, ensure_ascii=False)
        return self.write_text(relative, text + "\n")

    def relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root.resolve()).as_posix()
