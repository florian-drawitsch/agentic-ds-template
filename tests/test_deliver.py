import argparse
import io
import json
import subprocess
from pathlib import Path

import pytest

from tests.helpers import load_script

ROOT = Path(__file__).resolve().parents[1]
deliver = load_script(ROOT / "plugins/project/skills/deliver/scripts/deliver.py", "deliver")

PLAN = """# Plan

- [ ] **Step 1: Write the failing test**
- [ ] Step 2: Implement

```markdown
- [ ] example inside a code block
```
"""


def sh(cwd: Path, *args: str) -> None:
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    (root / "docs" / "plans").mkdir(parents=True)
    (root / "docs" / "plans" / "feature.md").write_text(PLAN)
    sh(root, "git", "init", "-q", "-b", "main")
    sh(root, "git", "-c", "user.name=t", "-c", "user.email=t@example.com", "add", "-A")
    sh(root, "git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "init")
    monkeypatch.chdir(root)
    monkeypatch.delenv(deliver.NESTED_ENV, raising=False)
    return root.resolve()


def start(repo: Path, **overrides: object) -> "deliver.Run":
    options = {
        "plan": "docs/plans/feature.md",
        "check": "exit 0",
        "mode": "subagents",
        "cross_review": "off",
        "reviewer": None,
        "max_rounds": 40,
        "stall_limit": 3,
        "check_timeout": 60,
    } | overrides
    return deliver.create_run(repo, argparse.Namespace(**options))


def tick_all(repo: Path) -> None:
    plan = repo / "docs/plans/feature.md"
    plan.write_text(
        plan.read_text().replace("- [ ] **Step", "- [x] **Step").replace("- [ ] Step", "- [x] Step")
    )


def stop(repo: Path, session: str, message: str = "") -> dict:
    return deliver.stop_decision(
        {"session_id": session, "cwd": str(repo), "last_assistant_message": message}
    )


def test_plan_items_ignore_code_blocks_and_strip_bold() -> None:
    open_items, done = deliver.plan_items(PLAN + "- [x] done\n")
    assert open_items == ["Step 1: Write the failing test", "Step 2: Implement"]
    assert done == 1


def test_run_binds_only_to_the_session_that_shows_its_token(repo: Path) -> None:
    run = start(repo)

    assert stop(repo, "bystander", "unrelated work") == {}
    response = stop(repo, "worker", f"{deliver.TOKEN_LINE} {run.token}")

    assert response["decision"] == "block"
    assert "Next: Step 1: Write the failing test" in response["reason"]
    assert deliver.find_run(repo, run.token).session_id == "worker"
    assert stop(repo, "bystander", f"{deliver.TOKEN_LINE} {run.token}") == {}


def test_failing_check_keeps_the_agent_working(repo: Path) -> None:
    run = start(repo, check="echo broken; exit 3")
    tick_all(repo)

    response = stop(repo, "s", run.token)

    assert response["decision"] == "block"
    assert "exit code 3" in response["reason"]
    assert "broken" in response["reason"]


def test_passing_check_completes_the_run(repo: Path) -> None:
    run = start(repo)
    tick_all(repo)

    assert stop(repo, "s", run.token) == {}
    assert deliver.find_run(repo, run.token).status == "completed"


def test_run_is_released_after_rounds_without_progress(repo: Path) -> None:
    run = start(repo, stall_limit=2)
    stop(repo, "s", run.token)
    assert stop(repo, "s")["decision"] == "block"
    assert stop(repo, "s") == {}
    assert deliver.find_run(repo, run.token).status == "stalled"


def test_progress_resets_the_stall_counter(repo: Path) -> None:
    run = start(repo, stall_limit=2)
    stop(repo, "s", run.token)
    (repo / "new.py").write_text("x = 1\n")
    assert stop(repo, "s")["decision"] == "block"
    assert deliver.find_run(repo, run.token).stalls == 0


def test_round_limit_releases_the_run(repo: Path) -> None:
    run = start(repo, max_rounds=1)
    stop(repo, "s", run.token)
    (repo / "new.py").write_text("x = 1\n")
    assert stop(repo, "s") == {}
    assert deliver.find_run(repo, run.token).status == "exhausted"


def test_cross_review_must_match_the_current_state(repo: Path) -> None:
    run = start(repo, cross_review="final", reviewer="codex")
    tick_all(repo)
    assert "--reviewer codex" in stop(repo, "s", run.token)["reason"]

    reviews = run.directory(repo) / "reviews"
    reviews.mkdir(parents=True)
    result = {"verdict": "changes_requested", "findings": [{"severity": "major"}]}
    (reviews / "001.json").write_text(
        json.dumps(result | {"signature": deliver.state_signature(repo)})
    )
    assert "requested changes (1 findings" in stop(repo, "s")["reason"]

    (reviews / "002.json").write_text(
        json.dumps({"verdict": "pass", "findings": [], "signature": "old"})
    )
    assert "Run the cross-review" in stop(repo, "s")["reason"]

    (reviews / "003.json").write_text(
        json.dumps({"verdict": "pass", "findings": [], "signature": deliver.state_signature(repo)})
    )
    assert stop(repo, "s") == {}
    assert deliver.find_run(repo, run.token).status == "completed"


def test_nested_sessions_are_never_guarded(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = start(repo)
    monkeypatch.setenv(deliver.NESTED_ENV, "1")
    assert stop(repo, "s", run.token) == {}


def test_hook_never_fails_on_bad_input(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert deliver.cmd_hook(argparse.Namespace()) == 0
    assert json.loads(capsys.readouterr().out) == {}


def test_start_rejects_a_second_run_for_the_same_plan(repo: Path) -> None:
    start(repo)
    with pytest.raises(SystemExit, match="already delivering"):
        start(repo)


def test_block_releases_the_guard(repo: Path) -> None:
    run = start(repo)
    stop(repo, "s", run.token)
    deliver.finish(argparse.Namespace(token=run.token, reason="needs a push"), "blocked")
    assert stop(repo, "s") == {}


def test_install_is_idempotent_and_keeps_existing_settings(repo: Path) -> None:
    settings = repo / ".claude" / "settings.json"
    settings.parent.mkdir()
    settings.write_text(json.dumps({"permissions": {"allow": ["Bash(task check)"]}}))
    models = {
        "claude_implementer": None,
        "claude_reviewer": "claude-opus-5-5",
        "codex_implementer": None,
        "codex_reviewer": "some-model",
    }

    first = deliver.install(repo, ["claude", "codex"], models, force=False)
    second = deliver.install(repo, ["claude", "codex"], models, force=False)

    assert len(first) == 7
    assert second == []
    data = json.loads(settings.read_text())
    assert data["permissions"]["allow"] == ["Bash(task check)"]
    assert len(data["hooks"]["Stop"]) == 1
    assert "model: claude-opus-5-5" in (repo / ".claude/agents/reviewer.md").read_text()
    assert "model: sonnet" in (repo / ".claude/agents/implementer.md").read_text()
    assert "model =" not in (repo / ".codex/agents/implementer.toml").read_text()
    assert 'model = "some-model"' in (repo / ".codex/agents/reviewer.toml").read_text()
    assert 'sandbox_mode = "read-only"' in (repo / ".codex/agents/reviewer.toml").read_text()
    assert deliver.doctor(repo) == []


def test_doctor_reports_missing_pieces(repo: Path) -> None:
    problems = deliver.doctor(repo)
    assert any("scripts/agent/deliver.py is missing" in p for p in problems)
    assert any(".codex/hooks.json" in p for p in problems)


@pytest.mark.parametrize(
    ("harness", "first", "expected"),
    [
        (
            "claude",
            True,
            ["claude", "-p", "--permission-mode", "acceptEdits", "--session-id", "sid"],
        ),
        ("claude", False, ["claude", "-p", "--permission-mode", "acceptEdits", "--resume", "sid"]),
        ("codex", True, ["codex", "exec", "--json"]),
        ("codex", False, ["codex", "exec", "resume", "sid", "--json"]),
    ],
)
def test_agent_command_shapes(harness: str, first: bool, expected: list[str]) -> None:
    command = deliver.agent_command(harness, "do it", "sid", first, None, "acceptEdits")
    assert command[: len(expected)] == expected
    assert command[-1] == "do it"
    if harness == "claude":
        sandbox = json.loads(command[command.index("--settings") + 1])["sandbox"]
        assert sandbox["enabled"] is True
        assert sandbox["allowUnsandboxedCommands"] is False


def test_headless_requires_a_committed_plan(repo: Path) -> None:
    (repo / "docs/plans/feature.md").write_text(PLAN + "- [ ] extra\n")
    args = deliver.parser().parse_args(
        ["headless", "--plan", "docs/plans/feature.md", "--harness", "codex", "--check", "exit 0"]
    )
    with pytest.raises(SystemExit, match=r"Commit docs/plans/feature\.md first"):
        deliver.cmd_headless(args)


def test_codex_thread_id_and_messages_are_read_from_events() -> None:
    assert (
        deliver.event_text(
            {"type": "item.completed", "item": {"type": "agent_message", "text": "hi"}}
        )
        == "hi"
    )
    claude_event = {"type": "assistant", "message": {"content": [{"type": "text", "text": "ok"}]}}
    assert deliver.event_text(claude_event) == "ok"
