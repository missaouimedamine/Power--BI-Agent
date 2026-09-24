from __future__ import annotations

from pathlib import Path

import pytest

from powerbi_agent.tools.filesystem import Workspace
from powerbi_agent.utils.security import PathOutsideWorkspaceError


def test_overwrite_archives_previous_version(tmp_path: Path) -> None:
    Workspace(tmp_path, run_id="run1").write_text("analysis/a.json", "v1")
    ws2 = Workspace(tmp_path, run_id="run2")
    ws2.write_text("analysis/a.json", "v2")
    assert (tmp_path / "analysis" / "a.json").read_text() == "v2"
    backup = tmp_path / ".agent" / "history" / "run2" / "analysis" / "a.json"
    assert backup.read_text() == "v1"


def test_first_write_creates_no_history(tmp_path: Path) -> None:
    Workspace(tmp_path, run_id="r").write_json("x.json", {"a": 1})
    assert not (tmp_path / ".agent").exists()


def test_writes_are_utf8_without_bom(tmp_path: Path) -> None:
    path = Workspace(tmp_path, run_id="r").write_text("t.md", "München")
    assert path.read_bytes() == "München".encode()


def test_cannot_escape_workspace(tmp_path: Path) -> None:
    ws = Workspace(tmp_path / "ws", run_id="r")
    with pytest.raises(PathOutsideWorkspaceError):
        ws.write_text("../outside.txt", "x")
