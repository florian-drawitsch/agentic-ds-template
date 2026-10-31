"""Verify reviewer executable selection and CLI compatibility checks."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "plugins/project/skills/cross-review/scripts/cross_review.py"
)
SPEC = importlib.util.spec_from_file_location("cross_review", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(review)

DELIVER_SCRIPT = (
    Path(__file__).resolve().parents[1] / "plugins/project/skills/deliver/scripts/deliver.py"
)
DELIVER_SPEC = importlib.util.spec_from_file_location("deliver_for_review", DELIVER_SCRIPT)
assert DELIVER_SPEC is not None
assert DELIVER_SPEC.loader is not None
deliver = importlib.util.module_from_spec(DELIVER_SPEC)
sys.modules[DELIVER_SPEC.name] = deliver
DELIVER_SPEC.loader.exec_module(deliver)


@pytest.mark.parametrize("version", ["0.162.0-alpha.17.2", "0.163.0"])
def test_explicit_binary_overrides_environment(monkeypatch, version: str) -> None:
    monkeypatch.setenv("CODEX_BIN", "/old/codex")

    def fake_run(command, **kwargs):
        assert command == ["/current/codex", "--version"]
        return subprocess.CompletedProcess(command, 0, f"codex-cli {version}", "")

    monkeypatch.setattr(review.subprocess, "run", fake_run)
    assert review.codex_binary("/current/codex") == "/current/codex"


def test_old_codex_fails_before_model_request(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_BIN", "/old/codex")

    def fake_run(command, **kwargs):
        assert command == ["/old/codex", "--version"]
        return subprocess.CompletedProcess(command, 0, "codex-cli 0.153.0", "")

    monkeypatch.setattr(review.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match=r"too old.*reviewer"):
        review.codex_binary()


def test_codex_review_passes_model_and_preserves_read_only_boundary() -> None:
    command = review.codex_command(
        "Review",
        "gpt-6.1-sol",
        Path("schema.json"),
        Path("result.json"),
        "/current/codex",
    )
    assert command[:2] == ["/current/codex", "exec"]
    assert command[command.index("-m") + 1] == "gpt-6.1-sol"
    assert 'sandbox_mode="read-only"' in command
    assert 'approval_policy="never"' in command


def test_claude_review_is_cost_and_turn_capped() -> None:
    command = review.claude_command("Review", "opus", 0.75)
    assert command[command.index("--max-budget-usd") + 1] == "0.75"
    assert command[command.index("--max-turns") + 1] == "60"
    assert "--exclude-dynamic-system-prompt-sections" in command


def test_reviewer_prompt_covers_shared_library_rules_without_cosmetic_findings() -> None:
    prompt = review.build_prompt("base", "docs/plans/example.md")
    assert "public contract changes" in prompt
    assert "accepted ADRs" in prompt
    assert "blocking review rules in AGENTS.md" in prompt
    assert "Do not request cosmetic changes" in prompt
    assert "PoC" not in prompt


def test_claude_reviewer_has_only_read_tools() -> None:
    command = review.claude_command("Review", "sonnet")
    assert command[command.index("--tools") + 1] == "Read,Grep,Glob"
    assert not any("Bash(" in item for item in command)


def test_review_inputs_are_written_outside_worktree(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    source = tmp_path / "module.py"
    source.write_text("before = 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "module.py"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "initial",
        ],
        check=True,
    )
    base = subprocess.run(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    source.write_text("after = 2\n")
    input_dir = review.write_review_inputs(tmp_path, base, "run-token")
    assert input_dir.is_relative_to(tmp_path / ".git")
    assert "before = 1" in (input_dir / "diff.patch").read_text()
    assert "after = 2" in (input_dir / "diff.patch").read_text()


def test_delivery_and_cross_review_state_signatures_agree(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("initial\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.txt"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(tmp_path),
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "initial",
        ],
        check=True,
    )
    tracked.write_text("staged\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.txt"], check=True)
    tracked.write_text("staged and unstaged\n")
    (tmp_path / "untracked.txt").write_text("untracked\n")
    assert deliver.state_signature(tmp_path) == review.state_signature(tmp_path)


@pytest.mark.parametrize("vendor", ["codex", "claude"])
def test_delivery_review_cannot_repeat_after_findings(tmp_path, monkeypatch, vendor: str) -> None:
    directory = tmp_path / "run" / "reviews"
    directory.mkdir(parents=True)
    model = review.DEFAULT_CODEX_MODEL if vendor == "codex" else None
    (directory / "review.json").write_text(
        json.dumps({"reviewer": vendor, "model": model, "verdict": "changes_requested"})
    )
    monkeypatch.setattr(review, "git", lambda *args: str(tmp_path))
    monkeypatch.setattr(review, "base_ref", lambda *args: "main")

    def unexpected_review(*args, **kwargs):
        pytest.fail("A second reviewer session must not start")

    monkeypatch.setattr(review, "run_review", unexpected_review)
    assert review.main(["--reviewer", vendor, "--token", "run"]) == 1


def test_explicit_review_budget_allows_one_more_pass(tmp_path, monkeypatch) -> None:
    directory = tmp_path / "run" / "reviews"
    directory.mkdir(parents=True)
    (directory.parent / "state.json").write_text(json.dumps({"review_rounds": 2}))
    (directory / "old.json").write_text(
        json.dumps({"verdict": "changes_requested", "resolution": {"signature": "current"}})
    )
    monkeypatch.setattr(review, "git", lambda *args: str(tmp_path))
    monkeypatch.setattr(review, "base_ref", lambda *args: "main")
    monkeypatch.setattr(review, "state_signature", lambda *args: "current")
    inputs = tmp_path / "review-inputs"
    inputs.mkdir()
    monkeypatch.setattr(review, "write_review_inputs", lambda *args: inputs)
    calls = []

    def fake_review(*args):
        calls.append(args)
        return {"verdict": "pass", "findings": [], "summary": "No required findings"}

    monkeypatch.setattr(review, "run_review", fake_review)
    (directory / "old.json").write_text(json.dumps({"verdict": "changes_requested"}))
    assert review.main(["--reviewer", "claude", "--token", "run"]) == 1
    assert calls == []
    (directory / "old.json").write_text(
        json.dumps({"verdict": "changes_requested", "resolution": {"signature": "current"}})
    )
    assert review.main(["--reviewer", "claude", "--token", "run"]) == 0
    assert review.main(["--reviewer", "codex", "--token", "run"]) == 1
    assert len(calls) == 1


def _delivery_review_run(tmp_path, monkeypatch, state: dict, ledger: list[dict]) -> Path:
    directory = tmp_path / "run"
    (directory / "reviews").mkdir(parents=True)
    (directory / "state.json").write_text(json.dumps(state))
    (directory / "usage.jsonl").write_text("".join(json.dumps(row) + "\n" for row in ledger))
    monkeypatch.setattr(review, "git", lambda *args: str(tmp_path))
    monkeypatch.setattr(review, "base_ref", lambda *args: "main")
    monkeypatch.setattr(review, "state_signature", lambda *args: "current")

    def inputs(*args):
        path = tmp_path / "review-input"
        path.mkdir(exist_ok=True)
        return path

    monkeypatch.setattr(review, "write_review_inputs", inputs)
    return directory


def test_zero_max_cost_keeps_review_budget_uncapped(tmp_path, monkeypatch) -> None:
    _delivery_review_run(
        tmp_path, monkeypatch, {"max_cost": 0, "review_cost": 1.5}, [{"cost_usd": 12.0}]
    )
    budgets = []

    def fake_review(*args):
        budgets.append(args[-1])
        return {"verdict": "pass", "findings": [], "summary": "No required findings"}

    monkeypatch.setattr(review, "run_review", fake_review)
    assert review.main(["--reviewer", "claude", "--token", "run"]) == 0
    assert budgets == [1.5]


def test_review_without_model_usage_records_unknown_tokens(tmp_path, monkeypatch) -> None:
    directory = _delivery_review_run(tmp_path, monkeypatch, {"max_cost": 0}, [])

    def fake_review(*args):
        usage = {"cost_usd": 0.4, "modelUsage": None, "num_turns": 3}
        return {"verdict": "pass", "findings": [], "summary": "", "_usage": usage}

    monkeypatch.setattr(review, "run_review", fake_review)
    assert review.main(["--reviewer", "claude", "--token", "run"]) == 0
    row = json.loads((directory / "usage.jsonl").read_text().splitlines()[-1])
    keys = ("input_tokens", "cached_input_tokens", "cache_creation_tokens", "output_tokens")
    assert all(row[key] is None for key in keys)
    assert row["cost_usd"] == 0.4


def test_codex_review_prices_cached_input_once(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(review, "codex_binary", lambda *args: "codex")

    def fake_run(command, **kwargs):
        Path(command[command.index("-o") + 1]).write_text(
            json.dumps({"verdict": "pass", "summary": "ok", "findings": []})
        )
        event = {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 1_000_000,
                "cached_input_tokens": 400_000,
                "output_tokens": 0,
            },
        }
        return subprocess.CompletedProcess(command, 0, json.dumps(event) + "\n", "")

    monkeypatch.setattr(review.subprocess, "run", fake_run)
    result = review.run_review(tmp_path, "codex", "Review", "gpt-6.1-sol", 60)
    assert result["_usage"]["cost_usd"] == pytest.approx(0.6 * 2.0 + 0.4 * 0.10)


def test_review_effort_is_always_high() -> None:
    claude = review.claude_command("Review", None)
    assert claude[claude.index("--model") + 1] == "opus"
    assert claude[claude.index("--effort") + 1] == "high"
    codex = review.codex_command("Review", None, Path("schema"), Path("output"))
    assert 'model_reasoning_effort="high"' in codex
