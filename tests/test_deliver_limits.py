"""Headless delivery runs wait out usage and rate limits instead of failing or stalling."""

from __future__ import annotations

import importlib.util
import json
import random
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[1] / "plugins/project/skills/deliver/scripts/deliver.py"
NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def deliver() -> ModuleType:
    spec = importlib.util.spec_from_file_location("deliver", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class NoJitter(random.Random):
    def uniform(self, a: float, b: float) -> float:
        return 0.0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Claude AI usage limit reached|1760112000", datetime.fromtimestamp(1760112000, UTC)),
        (
            "You've hit your usage limit. Try again in 2 hours 13 minutes.",
            NOW + timedelta(minutes=133),
        ),
        ("429 Too Many Requests, retry after 60 seconds", NOW + timedelta(seconds=60)),
        (
            "5-hour limit reached - resets 3pm (Europe/Berlin)",
            datetime(2026, 10, 10, 13, 0, tzinfo=UTC),
        ),
        ("Limit reached, resets 9am (Europe/Berlin)", datetime(2026, 10, 11, 7, 0, tzinfo=UTC)),
        ("usage limit reached", None),
    ],
)
def test_reset_times_are_parsed_from_limit_messages(
    deliver: ModuleType, text: str, expected: datetime | None
) -> None:
    assert deliver.parse_reset(text, NOW) == expected


def test_rejected_rate_limit_event_is_a_usage_limit_with_its_reset(deliver: ModuleType) -> None:
    result = deliver.RoundResult(code=1)
    reset = NOW.timestamp() + 3600
    deliver.absorb(
        result,
        {
            "type": "rate_limit_event",
            "rate_limit_info": {"status": "rejected", "resetsAt": int(reset)},
        },
    )

    limit = deliver.classify_limit(result, NOW)

    assert limit.kind == "usage"
    assert limit.reset_at == datetime.fromtimestamp(int(reset), UTC)


@pytest.mark.parametrize(
    ("events", "code", "kind"),
    [
        (
            [{"type": "error", "message": "You've hit your usage limit. Try again in 3 hours."}],
            1,
            "usage",
        ),
        (
            [{"type": "turn.failed", "error": {"message": "stream error: 429 Too Many Requests"}}],
            1,
            "transient",
        ),
        (
            [{"type": "result", "is_error": True, "result": "API Error: 529 overloaded"}],
            1,
            "transient",
        ),
        ([{"type": "result", "is_error": True, "result": "Tool failed: file not found"}], 1, None),
        (
            [{"type": "result", "is_error": False, "result": "I added rate limiting (429)."}],
            0,
            None,
        ),
    ],
)
def test_only_error_signals_classify_a_round_as_limited(
    deliver: ModuleType, events: list[dict[str, object]], code: int, kind: str | None
) -> None:
    result = deliver.RoundResult(code=code)
    for event in events:
        deliver.absorb(result, event)

    limit = deliver.classify_limit(result, NOW)

    assert (limit.kind if limit else None) == kind


def test_backoff_waits_for_a_known_reset_plus_margin(deliver: ModuleType) -> None:
    limit = deliver.Limit("usage", NOW + timedelta(minutes=30), "limit")
    assert deliver.Backoff(rng=NoJitter()).delay(limit, NOW) == 1800 + deliver.RESET_MARGIN


def test_backoff_grows_exponentially_up_to_a_cap_and_restarts(deliver: ModuleType) -> None:
    backoff = deliver.Backoff(rng=NoJitter())
    usage = deliver.Limit("usage", None, "limit")
    transient = deliver.Limit("transient", None, "429")

    assert [backoff.delay(usage, NOW) for _ in range(4)] == [900, 1800, 3600, 3600]
    backoff.reset()
    assert [backoff.delay(transient, NOW) for _ in range(3)] == [30, 60, 120]


def test_stop_hook_does_not_count_a_turn_ended_by_an_api_error(
    deliver: ModuleType, tmp_path: Path
) -> None:
    transcript = tmp_path / "t.jsonl"
    lines = [
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Working on it"}]}},
        {
            "type": "assistant",
            "isApiErrorMessage": True,
            "message": {"content": [{"type": "text", "text": "limit"}]},
        },
    ]
    transcript.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")

    assert deliver.turn_hit_limit({"transcript_path": str(transcript)})
    transcript.write_text(json.dumps(lines[0]), encoding="utf-8")
    assert not deliver.turn_hit_limit({"transcript_path": str(transcript)})
    assert deliver.turn_hit_limit({"last_assistant_message": "You've hit your usage limit."})


def init_repo(path: Path) -> None:
    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            cwd=path,
            check=True,
            capture_output=True,
        )

    git("init", "-q")
    (path / "plan.md").write_text("# Plan\n\n- [ ] Do the thing\n", encoding="utf-8")
    git("add", "plan.md")
    git("commit", "-qm", "plan")


def test_headless_run_waits_out_a_limit_and_resumes_the_session(
    deliver: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)
    commands: list[list[str]] = []
    waits: list[datetime] = []
    reset = datetime.now(UTC) + timedelta(minutes=10)

    def fake_round(command: list[str], cwd: Path, log: Path) -> object:
        commands.append(command)
        if len(commands) == 1:
            result = deliver.RoundResult(code=1, thread="thread-1", started=True)
            deliver.absorb(
                result,
                {"type": "error", "message": f"Usage limit reached|{int(reset.timestamp())}"},
            )
            return result
        (cwd / "plan.md").write_text("# Plan\n\n- [x] Do the thing\n", encoding="utf-8")
        return deliver.RoundResult(code=0, thread="thread-1", started=True)

    monkeypatch.setattr(deliver, "run_round", fake_round)
    monkeypatch.setattr(deliver, "wait_until", waits.append)

    code = deliver.main(
        [
            "headless",
            "--plan",
            "plan.md",
            "--harness",
            "codex",
            "--in-place",
            "--setup",
            "",
            "--check",
            "true",
        ]
    )

    run = deliver.load_runs(tmp_path)[-1]
    assert code == 0
    assert run.status == "completed"
    assert run.rounds == 1  # the limited round is not counted
    assert run.waits == 1
    assert waits[0] >= reset
    assert commands[1][:4] == ["codex", "exec", "resume", "thread-1"]
    assert "cut off by a usage limit" in commands[1][-1]


def test_headless_run_stops_when_the_wait_exceeds_the_budget(
    deliver: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_repo(tmp_path)
    monkeypatch.chdir(tmp_path)

    def fake_round(command: list[str], cwd: Path, log: Path) -> object:
        result = deliver.RoundResult(code=1, thread="thread-1", started=True)
        deliver.absorb(
            result,
            {
                "type": "error",
                "message": "You've hit your usage limit. Try again in 4 days 2 hours.",
            },
        )
        return result

    monkeypatch.setattr(deliver, "run_round", fake_round)
    monkeypatch.setattr(deliver, "wait_until", lambda moment: pytest.fail("must not wait"))

    code = deliver.main(
        [
            "headless",
            "--plan",
            "plan.md",
            "--harness",
            "codex",
            "--in-place",
            "--setup",
            "",
            "--check",
            "true",
            "--max-wait",
            "24",
        ]
    )

    run = deliver.load_runs(tmp_path)[-1]
    assert code == 1
    assert run.status == "exhausted"
    assert "--max-wait 24 h" in run.note


def test_sandboxed_agents_may_write_the_shared_git_dir_and_package_caches(
    deliver: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    init_repo(tmp_path)
    monkeypatch.setenv("PREK_HOME", str(tmp_path / "prek"))
    monkeypatch.setenv("npm_config_cache", str(tmp_path / "npm"))

    writable = deliver.writable_paths(tmp_path)

    assert str((tmp_path / ".git").resolve()) in [str(Path(p).resolve()) for p in writable]
    assert str(tmp_path / "prek") in writable
    assert str(tmp_path / "npm") in writable
    claude = deliver.agent_command("claude", "go", "s", True, None, "acceptEdits", None, writable)
    settings = json.loads(claude[claude.index("--settings") + 1])
    assert settings["sandbox"]["filesystem"]["allowWrite"] == writable
    hosts = set(settings["sandbox"]["network"]["allowedDomains"])
    assert {"pypi.org", "registry.npmjs.org"} <= hosts
    codex = deliver.agent_command("codex", "go", None, True, None, "acceptEdits", None, writable)
    assert f"sandbox_workspace_write.writable_roots={json.dumps(writable)}" in codex


def test_progress_renders_messages_tools_results_and_subagents_like_claude_code(
    deliver: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    subagent = {
        "type": "assistant",
        "parent_tool_use_id": "toolu_1",
        "message": {
            "content": [
                {"type": "thinking", "thinking": ""},
                {"type": "text", "text": "Starting task 2"},
                {"type": "tool_use", "name": "Bash", "input": {"command": "task check"}},
            ]
        },
    }
    dispatch = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Agent",
                    "input": {"subagent_type": "implementer", "description": "Task 2"},
                }
            ]
        },
    }
    result = {
        "type": "user",
        "message": {
            "content": [
                {"type": "tool_result", "content": "first line\nsecond\nthird", "is_error": True}
            ]
        },
    }
    command = {
        "type": "item.completed",
        "item": {"type": "command_execution", "exit_code": 0, "aggregated_output": "a\n9 passed"},
    }

    assert deliver.render_event(subagent) == [
        "  │ ✻ Thinking…",
        "  │ ⏺ Starting task 2",
        "  │ ⏺ Bash(task check)",
    ]
    assert deliver.render_event(dispatch) == ["", "⏺ Agent(implementer: Task 2)"]
    assert deliver.render_event(result) == ["  ⎿  first line (+2 lines)"]
    assert deliver.render_event(command) == ["  ⎿  exit 0 · 9 passed"]


def test_shell_results_show_the_last_line_and_codex_commands_are_unwrapped(
    deliver: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    call = {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "id": "t9", "name": "Bash", "input": {}}]},
    }
    output = {
        "type": "user",
        "message": {
            "content": [
                {"type": "tool_result", "tool_use_id": "t9", "content": "ruff ok\n50 passed"}
            ]
        },
    }
    started = {
        "type": "item.started",
        "item": {
            "type": "command_execution",
            "command": "/bin/zsh -lc \"git commit -m 'test: probe'\"",
        },
    }

    deliver.render_event(call)

    assert deliver.render_event(output) == ["  ⎿  50 passed (+1 line)"]
    assert deliver.render_event(started) == ["", "⏺ Shell(git commit -m 'test: probe')"]


def test_colors_follow_no_color_and_force_color(
    deliver: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.setenv("FORCE_COLOR", "1")
    assert deliver.paint("Bash", "green", "bold") == "\033[32;1mBash\033[0m"
    monkeypatch.setenv("NO_COLOR", "1")
    assert deliver.paint("Bash", "green", "bold") == "Bash"


def test_header_names_the_model_and_effort_and_where_they_come_from(
    deliver: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deliver.Path, "home", lambda: tmp_path / "home")
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.setattr(deliver, "cli_version", lambda harness: "1.2.3")
    (tmp_path / "home/.codex").mkdir(parents=True)
    (tmp_path / "home/.codex/config.toml").write_text(
        'model = "gpt-x"\nmodel_reasoning_effort = "high"\n', encoding="utf-8"
    )
    (tmp_path / ".claude/agents").mkdir(parents=True)
    (tmp_path / ".claude/settings.json").write_text('{"model": "opus"}', encoding="utf-8")
    (tmp_path / ".claude/agents/implementer.md").write_text("---\nmodel: sonnet\n---\n")
    (tmp_path / ".claude/agents/reviewer.md").write_text("---\nmodel: opus\n---\n")
    args = deliver.parser().parse_args(["headless", "--plan", "p.md", "--harness", "codex"])

    codex = deliver.session_settings(args, tmp_path)
    args.harness, args.effort = "claude", "low"
    claude = deliver.session_settings(args, tmp_path)

    assert codex["vendor"] == "Codex 1.2.3"
    assert codex["model"] == "gpt-x  (~/.codex/config.toml)"
    assert codex["effort"] == "high  (~/.codex/config.toml)"
    assert claude["model"].startswith("opus  (")
    assert claude["effort"] == "low  (--effort)"
    assert claude["implementer"] == "sonnet · effort low  (session)"
    assert claude["reviewer"] == "opus · effort low  (session)"
    assert codex["implementer"] == "session model · effort high  (session)"


def test_role_effort_from_the_role_file_and_the_model_each_subagent_runs(
    deliver: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.delenv("CLAUDE_CODE_EFFORT_LEVEL", raising=False)
    (tmp_path / ".claude/agents").mkdir(parents=True)
    (tmp_path / ".claude/agents/implementer.md").write_text(
        "---\nmodel: sonnet\neffort: low\n---\n"
    )
    first = {
        "type": "assistant",
        "parent_tool_use_id": "dispatch-7",
        "message": {"model": "claude-sonnet-5-5", "content": [{"type": "text", "text": "On it"}]},
    }

    role = deliver.role_settings("claude", tmp_path, "implementer", "high", "--effort")

    assert role == "sonnet · effort low  (.claude/agents/implementer.md)"
    assert deliver.render_event(first) == [
        "  │   ⎿  subagent model claude-sonnet-5-5",
        "  │ ⏺ On it",
    ]
    assert deliver.render_event(first) == ["  │ ⏺ On it"]  # shown once per dispatch


def test_an_earlier_delivery_of_the_same_plan_is_reported_not_crashed_on(
    deliver: ModuleType, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    (repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    deliver.create_worktree(repo, "plan.md", None)

    with pytest.raises(SystemExit, match="Continue it:"):
        deliver.create_worktree(repo, "plan.md", None)


def test_worktrees_go_into_the_main_repository_and_never_nest(
    deliver: ModuleType, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)
    (repo / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")

    first = deliver.create_worktree(repo, "docs/plans/first.md", None)
    second = deliver.create_worktree(first, "docs/plans/second.md", None)

    assert first == repo.resolve() / ".worktrees" / "first"
    assert second == repo.resolve() / ".worktrees" / "second"
    status = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
    )
    assert ".worktrees" not in status.stdout


def test_a_worktree_is_refused_while_the_directory_is_not_ignored(
    deliver: ModuleType, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    init_repo(repo)

    with pytest.raises(SystemExit, match=r"Add \.worktrees/"):
        deliver.create_worktree(repo, "plan.md", None)
    assert not (repo / ".worktrees").exists()
