import json
import subprocess
from pathlib import Path

import pytest

from tests.helpers import load_script
from tests.test_deliver import PLAN, deliver, sh, start

ROOT = Path(__file__).resolve().parents[1]
cross = load_script(
    ROOT / "plugins/project/skills/cross-review/scripts/cross_review.py", "cross_review"
)

PASS = {"verdict": "pass", "summary": "fine", "findings": []}


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "repo"
    (root / "docs" / "plans").mkdir(parents=True)
    (root / "docs" / "plans" / "feature.md").write_text(PLAN)
    sh(root, "git", "init", "-q", "-b", "main")
    sh(root, "git", "-c", "user.name=t", "-c", "user.email=t@example.com", "add", "-A")
    sh(root, "git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "init")
    monkeypatch.chdir(root)
    return root.resolve()


REAL_RUN = subprocess.run


def fake_cli(monkeypatch: pytest.MonkeyPatch, review: dict, calls: list) -> None:
    """Replace the reviewer CLIs with canned answers; git keeps running for real."""

    def run(command, **kwargs):
        if command[0] not in {"claude", "codex"}:
            return REAL_RUN(command, **kwargs)
        calls.append((command, kwargs))
        if command[0] == "claude":
            out = json.dumps({"result": "", "structured_output": review})
            return subprocess.CompletedProcess(command, 0, out, "")
        output = Path(command[command.index("-o") + 1])
        output.write_text(json.dumps(review))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)


def test_signature_matches_the_completion_guard(repo: Path) -> None:
    (repo / "new.py").write_text("x = 1\n")
    assert cross.state_signature(repo) == deliver.state_signature(repo)


def test_claude_command_keeps_the_prompt_ahead_of_variadic_flags() -> None:
    command = cross.claude_command("review it", None)
    assert command[:3] == ["claude", "-p", "review it"]
    assert command[command.index("--permission-mode") + 1] == "dontAsk"
    assert "Edit" in command[command.index("--disallowedTools") + 1 :]
    assert command[-1] == "Bash(git ls-files:*)"


def test_codex_command_uses_the_read_only_sandbox(tmp_path: Path) -> None:
    command = cross.codex_command("review it", "m", tmp_path / "s.json", tmp_path / "o.json")
    assert 'sandbox_mode="read-only"' in command
    assert command[-1] == "review it"
    assert command[command.index("-m") + 1] == "m"


@pytest.mark.parametrize(
    "stdout",
    [
        json.dumps({"structured_output": PASS}),
        json.dumps({"result": "```json\n" + json.dumps(PASS) + "\n```"}),
    ],
)
def test_claude_output_is_parsed(stdout: str) -> None:
    assert cross.parse_claude_output(stdout) == PASS


def test_blocking_findings_force_changes_requested() -> None:
    finding = {"severity": "major", "file": "a.py", "line": 3, "issue": "bug", "suggestion": "fix"}
    review = cross.normalize({"verdict": "pass", "summary": "", "findings": [finding]})
    assert review["verdict"] == "changes_requested"


def test_malformed_review_is_rejected() -> None:
    with pytest.raises(ValueError, match="schema"):
        cross.normalize({"verdict": "maybe"})


@pytest.mark.parametrize("reviewer", ["claude", "codex"])
def test_passing_review_completes_a_delivery_run(
    repo: Path, monkeypatch: pytest.MonkeyPatch, reviewer: str
) -> None:
    run = start(repo, cross_review="final", reviewer=reviewer)
    plan = repo / "docs/plans/feature.md"
    plan.write_text(
        plan.read_text().replace("- [ ] **Step", "- [x] **Step").replace("- [ ] Step", "- [x] Step")
    )
    calls: list = []
    fake_cli(monkeypatch, PASS, calls)

    assert cross.main(["--reviewer", reviewer, "--token", run.token, "--plan", run.plan]) == 0

    command, kwargs = calls[0]
    assert command[0] == reviewer
    assert kwargs["env"][cross.NESTED_ENV] == "1"
    assert (
        deliver.stop_decision(
            {"session_id": "s", "cwd": str(repo), "last_assistant_message": run.token}
        )
        == {}
    )
    assert deliver.find_run(repo, run.token).status == "completed"


def test_changes_requested_exits_with_two(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    finding = {
        "severity": "blocking",
        "file": "a.py",
        "line": None,
        "issue": "x",
        "suggestion": "y",
    }
    fake_cli(
        monkeypatch, {"verdict": "changes_requested", "summary": "", "findings": [finding]}, []
    )
    assert cross.main(["--reviewer", "codex"]) == 2


def test_failing_reviewer_exits_with_one(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def run(command, **kwargs):
        if command[0] == "claude":
            return subprocess.CompletedProcess(command, 1, "", "auth")
        return REAL_RUN(command, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    assert cross.main(["--reviewer", "claude"]) == 1


def test_prompt_treats_plan_bookkeeping_as_expected() -> None:
    prompt = cross.build_prompt("abc123", "docs/plans/feature.md")
    assert "git diff abc123" in prompt
    assert "expected delivery bookkeeping" in prompt
