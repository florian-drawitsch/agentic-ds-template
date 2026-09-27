"""Unit tests for the hook scripts the template ships."""

from __future__ import annotations

import io
import json
import subprocess
from pathlib import Path

import pytest

from tests.helpers import load_script
from tests.template.helpers import ROOT

guard = load_script(ROOT / "template/scripts/agent/guard.py", "guard")
status = load_script(ROOT / "template/scripts/agent/status.py", "status")


@pytest.mark.parametrize(
    "command",
    [
        "pip install pandas",
        "cd src && python -m pip install x",
        "uv pip install requests",
        "git push --force origin main",
        "git push origin +main",
        "git commit -n -m 'skip'",
        "cat .env",
        "source .env.local && task run",
    ],
)
def test_blocked_commands(command: str) -> None:
    assert guard.check_command(command)


@pytest.mark.parametrize(
    "command",
    [
        "uv add pandas",
        "git push origin feature",
        "git commit -m 'fix -n handling'",
        "cat .env.example",
        "task check",
    ],
)
def test_allowed_commands(command: str) -> None:
    assert guard.check_command(command) is None


@pytest.mark.parametrize(
    ("payload", "blocked"),
    [
        ({"tool_name": "Edit", "tool_input": {"file_path": "/repo/uv.lock"}}, True),
        ({"tool_name": "Read", "tool_input": {"file_path": "/repo/.env"}}, True),
        ({"tool_name": "Read", "tool_input": {"file_path": "/repo/uv.lock"}}, False),
        ({"tool_name": "Write", "tool_input": {"file_path": "/repo/.env.example"}}, False),
        (
            {"tool_name": "apply_patch", "tool_input": {"command": "*** Begin Patch\n*** Update File: uv.lock\n"}},
            True,
        ),
        (
            {"tool_name": "apply_patch", "tool_input": {"command": "*** Begin Patch\n*** Add File: src/a.py\n"}},
            False,
        ),
        ({"tool_name": "Bash", "tool_input": {"command": "pip install x"}}, True),
    ],
)
def test_decide_for_both_harnesses(payload: dict, blocked: bool) -> None:
    assert bool(guard.decide(payload)) is blocked


def test_guard_prints_a_deny_decision(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    payload = {"tool_name": "Bash", "tool_input": {"command": "pip install x"}}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert guard.main() == 0
    output = json.loads(capsys.readouterr().out)["hookSpecificOutput"]
    assert output["permissionDecision"] == "deny"
    assert "uv add" in output["permissionDecisionReason"]


def test_guard_allows_on_bad_input(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert guard.main() == 0
    assert capsys.readouterr().out == ""


def test_status_reports_branch_plan_and_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    plans = tmp_path / "docs" / "plans"
    plans.mkdir(parents=True)
    (plans / "2026-10-01-feature.md").write_text("- [ ] one\n- [x] two\n")
    runs = tmp_path / ".git" / "agent-runs" / "dr-1"
    runs.mkdir(parents=True)
    (runs / "state.json").write_text(json.dumps({"token": "dr-1", "plan": "docs/plans/x.md", "status": "active"}))
    monkeypatch.chdir(tmp_path)

    assert status.main() == 0

    out = capsys.readouterr().out
    assert "Branch: main" in out
    assert "2026-10-01-feature.md (1 open items)" in out
    assert "dr-1 for docs/plans/x.md" in out
