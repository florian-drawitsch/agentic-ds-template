"""Verify the delivery guard, its installation, and the repository's agent wiring."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "plugins/project/skills/deliver/scripts/deliver.py"
SPEC = importlib.util.spec_from_file_location("deliver", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
deliver = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = deliver
SPEC.loader.exec_module(deliver)


@pytest.fixture
def delivery_repo(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    root.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--allow-empty",
            "-qm",
            "initial",
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "remote", "add", "origin", "git@github.com:example/project.git"],
        check=True,
    )
    return root


@pytest.mark.parametrize(
    ("plan", "check", "action", "status"),
    [
        ("- [ ] Implement\n", "exit 0", "continue", "active"),
        ("- [x] Implement\n", "exit 3", "continue", "active"),
        ("- [x] Implement\n", "exit 0", "release", "completed"),
    ],
)
def test_completion_requires_plan_and_check(
    delivery_repo: Path, plan: str, check: str, action: str, status: str
) -> None:
    (delivery_repo / "plan.md").write_text(plan)
    run = deliver.Run(token="test-run", plan="plan.md", check=check)
    assert deliver.evaluate(delivery_repo, run).action == action
    assert run.status == status


def test_install_preserves_hooks_and_repeats_safely(delivery_repo: Path) -> None:
    hooks = delivery_repo / ".codex/hooks.json"
    hooks.parent.mkdir()
    existing = {"hooks": [{"type": "command", "command": "existing-hook"}]}
    hooks.write_text(json.dumps({"hooks": {"Stop": [existing]}}))
    models = {
        f"{harness}_{role}": None
        for harness in ("claude", "codex")
        for role in ("implementer", "reviewer")
    }
    deliver.install(delivery_repo, ["claude", "codex"], models, False)
    assert json.loads(hooks.read_text())["hooks"]["Stop"][0] == existing
    assert deliver.doctor(delivery_repo) == []
    assert deliver.install(delivery_repo, ["claude", "codex"], models, False) == []


def test_git_failure_reports_stderr(delivery_repo: Path) -> None:
    with pytest.raises(SystemExit, match=r"fatal:.*missing-revision"):
        deliver.git(delivery_repo, "show-ref", "--verify", "refs/heads/missing-revision")


def test_linked_worktree_git_dir_is_writable(delivery_repo: Path) -> None:
    worktree = delivery_repo.parent / "linked"
    subprocess.run(
        ["git", "-C", str(delivery_repo), "worktree", "add", "-q", str(worktree)], check=True
    )
    common = str((delivery_repo / ".git").resolve())
    own = str((delivery_repo / ".git/worktrees/linked").resolve())

    assert {common, own} <= set(deliver.writable_paths(worktree))
    assert deliver.writable_paths(delivery_repo).count(common) == 1


@pytest.mark.parametrize("first", [True, False])
def test_codex_rounds_can_edit_local_harness(
    delivery_repo: Path, first: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deliver, "cli_version", lambda _: "0.162.9")
    for name in (".agents", ".codex"):
        (delivery_repo / name).mkdir()
    writable = deliver.writable_paths(delivery_repo)
    command = deliver.agent_command(
        "codex", "Implement", "saved-session", first, None, "acceptEdits", writable=writable
    )
    settings = [command[i + 1] for i, arg in enumerate(command) if arg == "-c"]
    roots = next(s for s in settings if s.startswith("sandbox_workspace_write.writable_roots="))
    allowed = json.loads(roots.split("=", 1)[1])
    assert {str(delivery_repo / name) for name in (".agents", ".codex")} <= set(allowed)
    assert 'sandbox_mode="workspace-write"' in settings
    assert 'approval_policy="never"' in settings
    assert "--dangerously-bypass-approvals-and-sandbox" not in command


def test_harness_grants_stay_inside_worktree(delivery_repo: Path, tmp_path: Path) -> None:
    outside = tmp_path / "personal-config"
    outside.mkdir()
    (delivery_repo / ".codex").symlink_to(outside, target_is_directory=True)
    assert str(outside) not in deliver.writable_paths(delivery_repo)
    assert str(delivery_repo / ".agents") not in deliver.writable_paths(delivery_repo)


@pytest.mark.parametrize(
    ("fixture", "vendor", "turns", "tokens"),
    [
        ("claude-stream.jsonl", "claude", 1, 29968),
        ("codex-stream.jsonl", "codex", 1, 1300),
    ],
)
def test_vendor_usage_fixtures_parse(fixture: str, vendor: str, turns: int, tokens: int) -> None:
    result = deliver.RoundResult(code=0)
    for line in (ROOT / "tests/fixtures" / fixture).read_text().splitlines():
        deliver.absorb(result, json.loads(line))
    assert result.turns == turns
    assert (
        result.input_tokens
        + result.cached_input_tokens
        + result.cache_creation_tokens
        + result.output_tokens
        == tokens
    )
    if vendor == "claude":
        result.session_id = "session-fixture"
        assert result.cost_usd == pytest.approx(0.0782322)
        # Resumed Claude sessions report a cumulative total; the controller stores its delta.
        prior = [
            {"kind": "implement", "vendor": "claude", "session_id": "old-session", "cost_usd": 3.0},
            {
                "kind": "implement",
                "vendor": "claude",
                "session_id": "session-fixture",
                "cost_usd": 0.03,
            },
        ]
        assert deliver.claude_round_delta(result, prior) == pytest.approx(0.0482322)


def test_codex_prices_known_model_and_leaves_unknown_unpriced() -> None:
    known = deliver.RoundResult(code=0, input_tokens=1_000_000)
    deliver.price_codex(known, "gpt-6.1-sol")
    assert known.cost_usd == 2.0
    # Cached input is a subset of input_tokens and is charged only at the cached rate.
    cached = deliver.RoundResult(code=0, input_tokens=1_000_000, cached_input_tokens=400_000)
    deliver.price_codex(cached, "gpt-6.1-sol")
    assert cached.cost_usd == pytest.approx(0.6 * 2.0 + 0.4 * 0.10)
    unknown = deliver.RoundResult(code=0, model="future-model", input_tokens=1_000_000)
    deliver.price_codex(unknown)
    assert unknown.cost_usd is None


def test_claude_budget_exhaustion_rotates_to_fresh_session() -> None:
    assert deliver.claude_budget_requires_fresh_session("error_max_budget_usd")
    assert not deliver.claude_budget_requires_fresh_session("error_max_turns")


def test_claude_token_delta_is_scoped_to_session() -> None:
    result = deliver.RoundResult(code=0, session_id="current", input_tokens=400, output_tokens=60)
    rows = [
        {"vendor": "claude", "session_id": "old", "input_tokens": 300, "output_tokens": 10},
        {"vendor": "claude", "session_id": "current", "input_tokens": 100, "output_tokens": 20},
    ]
    deliver.claude_token_delta(result, rows)
    assert result.input_tokens == 300
    assert result.output_tokens == 40


def test_codex_model_falls_back_to_repository_config(delivery_repo: Path) -> None:
    (delivery_repo / ".codex").mkdir()
    (delivery_repo / ".codex/config.toml").write_text('model = "gpt-6.1-sol"\n')
    assert deliver.configured_codex_model(delivery_repo) == "gpt-6.1-sol"


def test_compaction_is_detected_and_full_protocol_can_be_restored() -> None:
    run = deliver.Run("compact", "plan.md", "exit 0")
    result = deliver.RoundResult(code=0)
    deliver.absorb(result, {"type": "system", "subtype": "compact_boundary"})
    prompt = deliver.full_protocol_prompt(run, "Continue")
    assert result.compacted
    assert "irreversible or security-sensitive action" in prompt
    assert ' block --token compact --reason "<why>"` and stop.' in prompt


def test_claude_round_caps_are_passed() -> None:
    command = deliver.agent_command(
        "claude", "prompt", "session", False, None, "acceptEdits", round_cost=0.5, round_turns=9
    )
    assert command[command.index("--max-budget-usd") + 1] == "0.5"
    assert command[command.index("--max-turns") + 1] == "9"


def test_round_timeout_sends_sigint_and_is_recorded(tmp_path: Path) -> None:
    script = "import signal,time; signal.signal(signal.SIGINT, lambda *_: exit(0)); time.sleep(5)"
    result = deliver.run_round(
        [sys.executable, "-c", script], tmp_path, tmp_path / "round.log", timeout=0.1
    )
    assert result.subtype == "wall_clock_limit"
    assert result.seconds < 2


def test_usage_ledger_records_limited_round_and_summary(delivery_repo: Path) -> None:
    run = deliver.Run("ledger", "plan.md", "exit 0", max_cost=1.0)
    deliver.append_usage(
        run, delivery_repo, deliver.RoundResult(code=1, input_tokens=4), "implement", "limited"
    )
    rows = deliver.usage_rows(run, delivery_repo)
    assert rows[0]["outcome"] == "limited"
    assert "in 4" in deliver.usage_summary(run, delivery_repo)
    assert rows[0]["cost_usd"] is None


def test_usage_summary_marks_missing_review_usage_unknown(delivery_repo: Path) -> None:
    run = deliver.Run(
        "unknown-review", "plan.md", "exit 0", reviewer="claude", reviewer_model="opus"
    )
    result = deliver.RoundResult(code=0, model="opus", seconds=2)
    result.input_tokens = result.cached_input_tokens = result.cache_creation_tokens = (
        result.output_tokens
    ) = None
    deliver.append_usage(run, delivery_repo, result, "review", "success")
    summary = deliver.usage_summary(run, delivery_repo)
    assert "claude/opus: token counts unknown; USD unknown" in summary


def test_round_budget_clips_to_remaining_and_zero_disables(delivery_repo: Path) -> None:
    run = deliver.Run("clip", "plan.md", "exit 0", max_cost=0.5, round_cost=2.0)
    deliver.append_usage(
        run, delivery_repo, deliver.RoundResult(code=0, cost_usd=0.3), "implement", "success"
    )
    assert deliver.remaining_budget(run, delivery_repo) == pytest.approx(0.2)
    assert deliver.clipped_round_cost(run, delivery_repo) == pytest.approx(0.2)
    assert deliver.remaining_budget(
        deliver.Run("none", "plan.md", "exit 0", max_cost=0), delivery_repo
    ) == float("inf")


def test_run_cost_budget_exhaustion_threshold(delivery_repo: Path) -> None:
    run = deliver.Run("threshold", "plan.md", "exit 0", max_cost=0.5)
    deliver.append_usage(
        run, delivery_repo, deliver.RoundResult(code=0, cost_usd=0.25), "implement", "success"
    )
    assert not deliver.cost_budget_exhausted(run, delivery_repo)
    deliver.append_usage(
        run, delivery_repo, deliver.RoundResult(code=0, cost_usd=0.01), "implement", "success"
    )
    assert deliver.cost_budget_exhausted(run, delivery_repo)


@pytest.mark.parametrize("harness", ["codex", "claude"])
def test_post_round_stop_and_explicit_resume_raise(delivery_repo: Path, harness: str) -> None:
    run = deliver.Run(
        f"{harness}-budget", "plan.md", "exit 0", harness=harness, max_cost=0.5, status="active"
    )
    deliver.append_usage(
        run, delivery_repo, deliver.RoundResult(code=0, cost_usd=0.3), "implement", "success"
    )
    assert not deliver.round_exhausted_run_budget(run, delivery_repo)
    # A review started inside the round is clipped against the same pre-round balance.
    deliver.append_usage(
        run, delivery_repo, deliver.RoundResult(code=0, cost_usd=0.2), "review", "success"
    )
    assert deliver.round_exhausted_run_budget(run, delivery_repo)
    assert not deliver.round_exhausted_run_budget(
        deliver.Run("uncapped", "plan.md", "exit 0", harness=harness, max_cost=0), delivery_repo
    )
    run.status, run.note = "exhausted", "Reached max_cost."
    assert not deliver.cost_resume_allowed(run, None)
    assert not deliver.cost_resume_allowed(run, 0.5)
    assert deliver.cost_resume_allowed(run, 1.0)


def test_continuation_prompt_always_carries_token_without_full_protocol() -> None:
    run = deliver.Run("resume-token", "plan.md", "exit 0")
    prompt = deliver.continuation_prompt(run, f"Keep working.\n\n{deliver.protocol(run)}")
    assert prompt.endswith("deliver-run: resume-token`.")
    assert "human can decide" not in prompt


def _commit_plan(root: Path) -> None:
    (root / "plan.md").write_text("- [x] Implement\n")
    subprocess.run(["git", "add", "plan.md"], cwd=root, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "plan",
        ],
        cwd=root,
        check=True,
    )


def test_completion_requires_current_pr(delivery_repo: Path) -> None:
    _commit_plan(delivery_repo)
    run = deliver.Run("publication", plan="plan.md", check="exit 0", publish_pr=True)
    assert deliver.evaluate(delivery_repo, run).action == "continue"
    assert run.status == "active"

    run.pr_url = "https://github.com/example/project/pull/1"
    run.pr_head = deliver.git(delivery_repo, "rev-parse", "HEAD")
    run.pr_branch = "main"
    assert deliver.evaluate(delivery_repo, run).action == "release"
    assert run.status == "completed"

    (delivery_repo / "new.py").write_text("changed\n")
    run.status = "active"
    assert deliver.evaluate(delivery_repo, run).action == "continue"


def test_final_review_requires_configured_vendor_and_model(delivery_repo: Path) -> None:
    _commit_plan(delivery_repo)
    run = deliver.Run(
        "review",
        plan="plan.md",
        check="exit 0",
        cross_review="final",
        reviewer="codex",
        reviewer_model="gpt-6.1-sol",
        codex_bin="/current/codex",
    )
    directory = run.directory(delivery_repo) / "reviews"
    directory.mkdir(parents=True)
    record = {
        "verdict": "pass",
        "signature": deliver.state_signature(delivery_repo),
        "reviewer": "codex",
        "model": "other-model",
    }
    path = directory / "review.json"
    path.write_text(json.dumps(record))
    verdict = deliver.evaluate(delivery_repo, run)
    assert verdict.action == "continue"
    assert "--model gpt-6.1-sol" in verdict.message
    assert "--codex-bin /current/codex" in verdict.message
    record["model"] = "gpt-6.1-sol"
    path.write_text(json.dumps(record))
    assert deliver.evaluate(delivery_repo, run).action == "release"


def test_preflight_failure_prevents_worktree_creation(delivery_repo: Path, monkeypatch) -> None:
    args = deliver.parser().parse_args(
        [
            "headless",
            "--plan",
            "plan.md",
            "--harness",
            "claude",
            "--publish-pr",
        ]
    )
    monkeypatch.chdir(delivery_repo)

    def fail_preflight(root, options):
        raise SystemExit("Missing GitHub access")

    monkeypatch.setattr(deliver, "preflight", fail_preflight)
    with pytest.raises(SystemExit, match="Missing GitHub access"):
        deliver.cmd_headless(args)
    assert not (delivery_repo / ".worktrees").exists()


@pytest.mark.parametrize("failure", ["plan", "check", "review", "dirty"])
def test_publish_never_pushes_unverified_work(
    delivery_repo: Path, monkeypatch, failure: str
) -> None:
    _commit_plan(delivery_repo)
    subprocess.run(["git", "switch", "-qc", "feat/example"], cwd=delivery_repo, check=True)
    run = deliver.Run("publication", plan="plan.md", check="exit 0", publish_pr=True)
    if failure == "plan":
        (delivery_repo / "plan.md").write_text("- [ ] Implement\n")
        subprocess.run(["git", "add", "plan.md"], cwd=delivery_repo, check=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.name=test",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-qm",
                "open plan",
            ],
            cwd=delivery_repo,
            check=True,
        )
    elif failure == "check":
        run.check = "exit 1"
    elif failure == "review":
        run.cross_review, run.reviewer = "final", "codex"
    else:
        (delivery_repo / "uncommitted.py").write_text("change\n")
    run.save(delivery_repo)
    monkeypatch.chdir(delivery_repo)
    with pytest.raises(SystemExit, match=r"Publication blocked|Commit all changes"):
        deliver.cmd_publish(
            argparse.Namespace(
                token=run.token,
                title="feat(example): change",
                description_file=delivery_repo / "body.md",
            )
        )
    assert deliver.find_run(delivery_repo, run.token).pr_url is None


def test_headless_publication_is_staged_for_outer_task(delivery_repo: Path, monkeypatch) -> None:
    _commit_plan(delivery_repo)
    subprocess.run(["git", "switch", "-qc", "feat/example"], cwd=delivery_repo, check=True)
    run = deliver.Run("publication", plan="plan.md", check="exit 0", publish_pr=True, headless=True)
    run.save(delivery_repo)
    body = delivery_repo.parent / "body.md"
    body.write_text("Prepared description.\n")
    monkeypatch.setattr(
        deliver, "run_check", lambda *args, **kwargs: pytest.fail("Gate ran inside child")
    )
    monkeypatch.setattr(deliver, "secret_scan", lambda *args: None)
    monkeypatch.chdir(delivery_repo)
    assert (
        deliver.cmd_publish(
            argparse.Namespace(
                token=run.token,
                title="feat(example): change",
                description_file=body,
                execute=False,
            )
        )
        == 0
    )
    saved = deliver.find_run(delivery_repo, run.token)
    assert saved.status == "publishing"
    assert saved.pr_url is None
    assert Path(saved.pr_description_file).read_text() == body.read_text()


@pytest.mark.parametrize("outcome", ["success", "short-hash", "api-error", "wrong-commit"])
def test_publication_records_only_matching_api_receipt(
    delivery_repo: Path,
    monkeypatch,
    outcome: str,
) -> None:
    _commit_plan(delivery_repo)
    subprocess.run(["git", "switch", "-qc", "feat/example"], cwd=delivery_repo, check=True)
    head = deliver.git(delivery_repo, "rev-parse", "HEAD")
    run = deliver.Run("publication", plan="plan.md", check="exit 0", publish_pr=True)
    run.save(delivery_repo)
    body = delivery_repo.parent / "body.md"
    body.write_text("Prepared description.\n")
    real_run = subprocess.run

    def fake_run(command, **kwargs):
        if isinstance(command, list) and "create_pr.py" in command[1]:
            if outcome == "api-error":
                return subprocess.CompletedProcess(command, 1, "", "No write permission")
            reported_hash = head[:12] if outcome == "short-hash" else head
            response = {
                "number": 1,
                "state": "OPEN",
                "headRefName": "feat/example",
                "headRefOid": "wrong-commit" if outcome == "wrong-commit" else reported_hash,
                "baseRefName": "main",
                "url": "https://github.com/example/project/pull/1",
            }
            Path(command[command.index("--result-file") + 1]).write_text(json.dumps(response))
            return subprocess.CompletedProcess(command, 0, "Published", "")
        return cast(Any, real_run)(command, **kwargs)

    monkeypatch.setattr(deliver.subprocess, "run", fake_run)
    monkeypatch.setattr(deliver, "secret_scan", lambda *args: None)
    monkeypatch.chdir(delivery_repo)
    args = argparse.Namespace(
        token=run.token,
        title="feat(example): change",
        description_file=body,
        execute=True,
    )
    if outcome in {"success", "short-hash"}:
        assert deliver.cmd_publish(args) == 0
        saved = deliver.find_run(delivery_repo, run.token)
        assert saved.pr_head == head
        assert saved.pr_url.endswith("/pull/1")
        assert deliver.evaluate(delivery_repo, saved).action == "release"
    else:
        with pytest.raises(SystemExit, match=r"publication failed|receipt does not match"):
            deliver.cmd_publish(args)
        assert deliver.find_run(delivery_repo, run.token).pr_url is None


def test_outer_publication_failure_blocks_run(delivery_repo: Path, monkeypatch) -> None:
    _commit_plan(delivery_repo)
    monkeypatch.setattr(deliver, "run_check", lambda *args, **kwargs: (0, "passed"))
    real_run = subprocess.run
    run = deliver.Run(
        "publication",
        plan="plan.md",
        check="exit 0",
        publish_pr=True,
        status="publishing",
        pr_title="feat(example): change",
        pr_description_file="/tmp/body.md",
    )

    def fake_run(command, **kwargs):
        if "--execute" in command:
            return subprocess.CompletedProcess(command, 1, "", "No write permission")
        return cast(Any, real_run)(command, **kwargs)

    monkeypatch.setattr(deliver.subprocess, "run", fake_run)
    monkeypatch.setattr(run, "save", lambda root: None)
    saved = deliver.publish_pending(delivery_repo, run)
    assert saved.status == "blocked"
    assert "No write permission" in saved.note


def test_preflight_persists_selected_codex_and_model(delivery_repo: Path, monkeypatch) -> None:
    (delivery_repo / "plan.md").write_text("- [ ] Implement\n")
    args = deliver.parser().parse_args(
        [
            "headless",
            "--plan",
            "plan.md",
            "--harness",
            "claude",
            "--cross-review",
            "final",
            "--check",
            "exit 0",
        ]
    )
    monkeypatch.setenv("CODEX_BIN", "/current/codex")

    def fake_run(command, **kwargs):
        assert command[-1] == "/current/codex"
        assert "--preflight" in command
        return subprocess.CompletedProcess(command, 0, "Reviewer ready", "")

    monkeypatch.setattr(deliver.subprocess, "run", fake_run)
    deliver.preflight(delivery_repo, args)
    assert args.reviewer == "codex"
    assert args.reviewer_model == "gpt-6.1-sol"
    assert args.codex_bin == "/current/codex"


def test_final_gate_is_reused_until_file_contents_change(delivery_repo: Path, monkeypatch) -> None:
    _commit_plan(delivery_repo)
    run = deliver.Run("cached", plan="plan.md", check="final gate")
    commands = []

    def fake_check(root, command, timeout, **kwargs):
        commands.append(command)
        return 0, "passed"

    monkeypatch.setattr(deliver, "run_check", fake_check)
    assert deliver.evaluate(delivery_repo, run).action == "release"
    assert deliver.evaluate(delivery_repo, run).action == "release"
    assert commands == ["final gate"]

    untracked = delivery_repo / "new.py"
    untracked.write_text("first version\n")
    assert deliver.evaluate(delivery_repo, run).action == "release"
    untracked.write_text("second version\n")
    assert deliver.evaluate(delivery_repo, run).action == "release"
    assert commands == ["final gate"] * 3


def test_focused_review_fixes_continue_without_second_model_review(
    delivery_repo: Path,
    monkeypatch,
) -> None:
    _commit_plan(delivery_repo)
    run = deliver.Run(
        "focused",
        plan="plan.md",
        check="final gate",
        cross_review="final",
        reviewer="codex",
        reviewer_model="gpt-6.1-sol",
        focused_review=False,  # Existing runs also avoid review loops.
    )
    run.save(delivery_repo)
    directory = run.directory(delivery_repo) / "reviews"
    directory.mkdir()
    path = directory / "review.json"
    original_signature = deliver.state_signature(delivery_repo)
    path.write_text(
        json.dumps(
            {
                "reviewer": "codex",
                "model": "gpt-6.1-sol",
                "verdict": "changes_requested",
                "signature": original_signature,
                "findings": [{"severity": "major", "issue": "Duplicated policy"}],
            }
        )
    )
    commands = []

    def fake_check(root, command, timeout, **kwargs):
        commands.append(command)
        return 0, "passed"

    monkeypatch.setattr(deliver, "run_check", fake_check)
    assert "autonomously" in deliver.evaluate(delivery_repo, run).message
    assert commands == []

    fixes = delivery_repo.parent / "fixes.json"
    fixes.write_text(json.dumps({"summary": "Consolidated the policy", "fixes": []}))
    monkeypatch.chdir(delivery_repo)
    args = argparse.Namespace(token=run.token, fixes_file=fixes, check="focused tests")
    with pytest.raises(SystemExit, match="every blocking or major"):
        deliver.cmd_resolve_review(args)

    (delivery_repo / "fixed.py").write_text("policy = 1\n")
    subprocess.run(["git", "add", "fixed.py"], cwd=delivery_repo, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "fix policy",
        ],
        cwd=delivery_repo,
        check=True,
    )
    fixes.write_text(
        json.dumps(
            {
                "summary": "Consolidated the policy",
                "fixes": [{"finding": 0, "change": "Use the shared policy value"}],
            }
        )
    )
    assert deliver.cmd_resolve_review(args) == 0
    assert deliver.evaluate(delivery_repo, run).action == "release"
    record = json.loads(path.read_text())
    assert record["signature"] == original_signature
    assert record["resolution"]["signature"] != original_signature
    assert commands == ["focused tests", "final gate"]


@pytest.mark.parametrize("action", ["headless", "resume"])
def test_dialog_preview_preserves_state_and_never_preflights(
    delivery_repo: Path, monkeypatch, action: str, capsys
) -> None:
    _commit_plan(delivery_repo)
    monkeypatch.chdir(delivery_repo)
    monkeypatch.setattr(deliver, "preflight", lambda *args: pytest.fail("Preview called preflight"))
    (delivery_repo / ".env").write_text("GH_TOKEN=private-value\n")
    if action == "resume":
        run = deliver.Run(
            "saved", "plan.md", "exit 0", harness="codex", session_id="thread-1", status="stalled"
        )
        run.save(delivery_repo)
        before = (run.directory(delivery_repo) / "state.json").read_bytes()
        flags = ["--worktree", str(delivery_repo), "--token", run.token]
    else:
        flags = ["--plan", "plan.md"]
    args = deliver.parser().parse_args(
        [
            action,
            *flags,
            "--harness",
            "codex",
            "--reviewer",
            "claude",
            "--review-rounds",
            "1",
            "--preview",
        ]
    )
    assert args.func(args) == 0
    output = capsys.readouterr().out
    assert '"reviewer_model": "opus"' in output
    assert "private-value" not in output
    assert not (delivery_repo / ".git/deliver-controller.lock").exists()
    if action == "resume":
        assert (run.directory(delivery_repo) / "state.json").read_bytes() == before
        assert '"session": "thread-1"' in output
    else:
        assert not deliver.load_runs(delivery_repo)


def test_dialog_defaults_and_cancel_do_not_launch(monkeypatch) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "")
    args = deliver.parser().parse_args(["headless"])
    deliver.configure_dialog(args)
    assert (args.harness, args.model, args.reviewer, args.reviewer_model, args.review_rounds) == (
        "codex",
        "gpt-6.1-sol",
        "claude",
        "opus",
        1,
    )
    monkeypatch.setattr("builtins.input", lambda _: "q")
    with pytest.raises(SystemExit) as exc:
        deliver.select_option("Plan", [("test", "plan.md")])
    assert exc.value.code == 0


def test_resume_keeps_session_progress_and_dirty_files(delivery_repo: Path, monkeypatch) -> None:
    _commit_plan(delivery_repo)
    monkeypatch.chdir(delivery_repo)
    run = deliver.Run(
        "resume",
        "plan.md",
        "exit 0",
        harness="codex",
        model="gpt-6.1-sol",
        reviewer="claude",
        reviewer_model="opus",
        session_id="thread-1",
        status="stalled",
        rounds=7,
        stalls=3,
        waits=2,
        waited_seconds=60,
        setup="exit 0",
        publish_pr=True,
    )
    run.save(delivery_repo)
    (delivery_repo / "unfinished.py").write_text("work = 1\n")
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(deliver, "preflight", lambda *args: None)
    calls = []

    def fake_round(command, cwd, log, timeout=5400):
        calls.append(command)
        saved = deliver.find_run(cwd, run.token)
        saved.status = "blocked"
        saved.save(cwd)
        return deliver.RoundResult(code=0, thread="thread-1", started=True)

    monkeypatch.setattr(deliver, "run_round", fake_round)
    args = deliver.parser().parse_args(
        [
            "resume",
            "--worktree",
            str(delivery_repo),
            "--token",
            run.token,
            "--harness",
            "codex",
            "--reviewer",
            "claude",
            "--review-rounds",
            "1",
        ]
    )
    assert deliver.cmd_resume(args) == 1
    assert calls[0][:4] == ["codex", "exec", "resume", "thread-1"]
    saved = deliver.find_run(delivery_repo, run.token)
    assert (saved.session_id, saved.rounds, saved.waits, saved.waited_seconds) == (
        "thread-1",
        7,
        2,
        60,
    )
    assert saved.stalls == 0
    assert (delivery_repo / "unfinished.py").read_text() == "work = 1\n"
    assert len(deliver.load_runs(delivery_repo)) == 1
    assert (
        '"type": "delivery.resumed"'
        in (run.directory(delivery_repo) / "headless.jsonl").read_text()
    )


def test_controller_lock_excludes_duplicates_and_recovers(delivery_repo: Path) -> None:
    with (
        deliver.controller_lock(delivery_repo),
        pytest.raises(SystemExit, match="already running"),
        deliver.controller_lock(delivery_repo),
    ):
        pytest.fail("Duplicate controller entered")
    with deliver.controller_lock(delivery_repo):
        pass


def test_discovery_across_worktrees_and_completed_prerequisite(delivery_repo: Path) -> None:
    root = delivery_repo
    (root / "docs/plans").mkdir(parents=True)
    (root / "docs/plans/phase1.md").write_text("- [ ] Contracts\n")
    (root / "docs/plans/phase2.md").write_text("Depends on [phase 1](phase1.md).\n- [ ] Context\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=t@example.com", "commit", "-qm", "plans"],
        cwd=root,
        check=True,
    )
    linked = root.parent / "worktree with spaces"
    subprocess.run(
        ["git", "worktree", "add", "-qb", "codex/deliver/phase1", str(linked)], cwd=root, check=True
    )
    (linked / "docs/plans/phase1.md").write_text("- [x] Contracts\n")
    subprocess.run(["git", "add", "."], cwd=linked, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=test",
            "-c",
            "user.email=t@example.com",
            "commit",
            "-qm",
            "contracts",
        ],
        cwd=linked,
        check=True,
    )
    deliver.Run("complete", "docs/plans/phase1.md", "exit 0", status="completed").save(linked)
    deliver.Run("pending", "docs/plans/phase2.md", "exit 0", status="stalled").save(root)
    assert [(path, run.token) for path, run in deliver.worktree_runs(linked)] == [(root, "pending")]
    assert deliver.prerequisite_branches(root, "docs/plans/phase2.md") == ["codex/deliver/phase1"]


def test_status_across_worktrees_includes_completed_runs(
    delivery_repo: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    linked = delivery_repo.parent / "app worktree"
    subprocess.run(
        ["git", "worktree", "add", "--detach", str(linked)], cwd=delivery_repo, check=True
    )
    (delivery_repo / "plan.md").write_text("- [x] Done\n")
    deliver.Run("completed-run", "plan.md", "exit 0", status="completed").save(delivery_repo)
    monkeypatch.chdir(linked)

    args = deliver.parser().parse_args(["status"])
    args.func(args)
    assert capsys.readouterr().out == "No delivery runs in this worktree.\n"

    args = deliver.parser().parse_args(["status", "--all-worktrees"])
    assert args.func(args) == 0
    output = capsys.readouterr().out
    assert f"Worktree: {delivery_repo}" in output
    assert "completed-run  completed" in output
    assert "done 1, open 0" in output
    assert deliver.worktree_runs(linked) == []


def test_delivery_from_detached_app_worktree_does_not_nest(delivery_repo: Path) -> None:
    (delivery_repo / ".gitignore").write_text(".worktrees/\n")
    (delivery_repo / "plan.md").write_text("- [ ] Implement\n")
    subprocess.run(["git", "add", "."], cwd=delivery_repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=t@example.com", "commit", "-qm", "plan"],
        cwd=delivery_repo,
        check=True,
    )
    app = delivery_repo.parent / "app worktree"
    subprocess.run(["git", "worktree", "add", "--detach", str(app)], cwd=delivery_repo, check=True)
    (app / "uncommitted.txt").write_text("App-only edit\n")

    result = deliver.create_worktree(app, "plan.md", None)

    assert result == delivery_repo / ".worktrees/plan"
    assert deliver.git(result, "rev-parse", "HEAD") == deliver.git(app, "rev-parse", "HEAD")
    assert deliver.git(result, "branch", "--show-current") == "feat/plan"
    assert deliver.git(app, "branch", "--show-current") == ""
    assert not (result / "uncommitted.txt").exists()
    assert not (app / ".worktrees").exists()


def test_review_budget_requires_verified_fixes_before_another_pass(
    delivery_repo: Path, monkeypatch
) -> None:
    _commit_plan(delivery_repo)
    run = deliver.Run(
        "budget", "plan.md", "final gate", cross_review="final", reviewer="claude", review_rounds=2
    )
    directory = run.directory(delivery_repo) / "reviews"
    directory.mkdir(parents=True)
    record = {
        "reviewer": "claude",
        "verdict": "changes_requested",
        "signature": "old",
        "findings": [{"severity": "major"}],
    }
    (directory / "1.json").write_text(json.dumps(record))
    monkeypatch.setattr(deliver, "run_check", lambda *args, **kwargs: (0, "passed"))
    assert "resolve-review" in deliver.evaluate(delivery_repo, run).message
    record["resolution"] = {"signature": deliver.state_signature(delivery_repo)}
    (directory / "1.json").write_text(json.dumps(record))
    assert "(2/2)" in deliver.evaluate(delivery_repo, run).message
    (directory / "2.json").write_text(json.dumps(record))
    assert deliver.evaluate(delivery_repo, run).action == "release"


def test_run_round_saves_session_before_stream_finishes(delivery_repo: Path, monkeypatch) -> None:
    run = deliver.Run("stream", "plan.md", "exit 0")
    run.save(delivery_repo)

    command = [
        sys.executable,
        "-c",
        "import json,time; "
        "print(json.dumps({'type':'thread.started','thread_id':'thread-new'}), flush=True); "
        "time.sleep(.2)",
    ]
    result = deliver.run_round(
        command, delivery_repo, run.directory(delivery_repo) / "headless.jsonl"
    )
    assert result.thread == "thread-new"


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_fresh_vendor_session_uses_launch_not_resume(
    delivery_repo: Path, monkeypatch, harness: str
) -> None:
    args = deliver.parser().parse_args(["headless", "--plan", "plan.md", "--harness", harness])
    run = deliver.Run(
        "fresh",
        "plan.md",
        "exit 0",
        harness=harness,
        session_id="new-claude" if harness == "claude" else None,
    )
    run.save(delivery_repo)
    commands = []

    def fake_round(command, cwd, log, timeout=5400):
        commands.append(command)
        saved = deliver.find_run(cwd, run.token)
        saved.status = "blocked"
        saved.save(cwd)
        return deliver.RoundResult(code=0, started=True, thread="new-thread")

    monkeypatch.setattr(deliver, "run_round", fake_round)
    assert deliver.continue_run(delivery_repo, run, args, first=True) == 1
    assert "--resume" not in commands[0]
    assert "resume" not in commands[0]
    if harness == "claude":
        assert "--session-id" in commands[0]


def test_resume_refuses_exhausted_budget_without_reset(delivery_repo: Path, monkeypatch) -> None:
    _commit_plan(delivery_repo)
    monkeypatch.chdir(delivery_repo)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    run = deliver.Run(
        "exhausted", "plan.md", "exit 0", harness="codex", rounds=40, status="exhausted"
    )
    run.save(delivery_repo)
    before = (run.directory(delivery_repo) / "state.json").read_bytes()
    args = deliver.parser().parse_args(
        [
            "resume",
            "--worktree",
            str(delivery_repo),
            "--token",
            run.token,
            "--harness",
            "codex",
            "--reviewer",
            "claude",
            "--review-rounds",
            "1",
        ]
    )
    with pytest.raises(SystemExit, match="budget is exhausted"):
        deliver.cmd_resume(args)
    assert (run.directory(delivery_repo) / "state.json").read_bytes() == before


def test_pending_publication_resumes_without_an_agent(delivery_repo: Path, monkeypatch) -> None:
    run = deliver.Run("publish-resume", "plan.md", "exit 0", status="publishing")
    run.save(delivery_repo)
    args = deliver.parser().parse_args(["headless", "--harness", "codex", "--plan", "plan.md"])

    def publish(root, saved):
        saved.status = "active"
        saved.verified_signature = deliver.state_signature(root)
        saved.verified_check = saved.check
        return saved

    def evaluate(root, saved, **kwargs):
        saved.status = "completed"
        return deliver.Verdict("release", "Published")

    monkeypatch.setattr(deliver, "publish_pending", publish)
    monkeypatch.setattr(deliver, "evaluate", evaluate)
    monkeypatch.setattr(
        deliver, "run_round", lambda *args: pytest.fail("Unnecessary vendor session")
    )
    assert deliver.continue_run(delivery_repo, run, args) == 0
    assert deliver.find_run(delivery_repo, run.token).status == "completed"


def test_headless_hook_queues_final_gate_for_outer_controller(
    delivery_repo: Path, monkeypatch
) -> None:
    _commit_plan(delivery_repo)
    run = deliver.Run("gate-handoff", "plan.md", "final gate", headless=True, session_id="session")
    run.save(delivery_repo)
    monkeypatch.delenv(deliver.NESTED_ENV, raising=False)
    calls = []
    monkeypatch.setattr(
        deliver, "run_check", lambda *args, **kwargs: calls.append(args) or (0, "passed")
    )
    assert deliver.stop_decision({"cwd": str(delivery_repo), "session_id": "session"}) == {}
    saved = deliver.find_run(delivery_repo, run.token)
    assert saved.status == "verifying"
    assert calls == []
    saved, verdict = deliver.advance_handoff(delivery_repo, saved)
    assert verdict.action == "release"
    assert saved.status == "completed"
    assert len(calls) == 1


@pytest.mark.parametrize("evidence_source", ["message", "transcript_tail"])
def test_unbound_run_binds_from_assistant_message_or_transcript_tail(
    delivery_repo: Path, monkeypatch, evidence_source: str
) -> None:
    monkeypatch.delenv(deliver.NESTED_ENV, raising=False)
    run = deliver.Run("bind-run", "plan.md", "exit 0")
    run.save(delivery_repo)
    transcript = delivery_repo / "transcript.jsonl"
    message = "bind-run" if evidence_source == "message" else ""
    if evidence_source == "transcript_tail":
        transcript.write_text('{"message":"bind-run"}\n')
    deliver.stop_decision(
        {
            "cwd": str(delivery_repo),
            "session_id": "bound-session",
            "last_assistant_message": message,
            "transcript_path": str(transcript),
        }
    )
    assert deliver.find_run(delivery_repo, run.token).session_id == "bound-session"


def test_outer_gate_failure_returns_to_agent_without_publication(
    delivery_repo: Path, monkeypatch
) -> None:
    _commit_plan(delivery_repo)
    run = deliver.Run(
        "failed-gate", "plan.md", "final gate", headless=True, publish_pr=True, status="publishing"
    )
    run.save(delivery_repo)
    calls = []
    monkeypatch.setattr(
        deliver,
        "run_check",
        lambda *args, **kwargs: calls.append(args) or (1, "contract regression"),
    )
    monkeypatch.setattr(
        deliver, "publication_environment", lambda *args: pytest.fail("Failed gate published")
    )
    saved, verdict = deliver.advance_handoff(delivery_repo, run)
    assert saved.status == "active"
    assert verdict.action == "continue"
    assert "contract regression" in verdict.message
    assert saved.pr_url is None
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("url", "forge", "helper"),
    [
        ("git@github.com:example/project.git", "GitHub", "github-create-pr"),
        ("https://github.com/example/project.git", "GitHub", "github-create-pr"),
    ],
)
def test_publication_uses_the_remote_forge_helper(
    delivery_repo: Path, url: str, forge: str, helper: str
) -> None:
    subprocess.run(["git", "remote", "set-url", "origin", url], cwd=delivery_repo, check=True)
    name, path = deliver.pr_forge(delivery_repo, "origin")
    assert name == forge
    assert path.parents[1].name == helper
    assert path.is_file()


@pytest.mark.parametrize("host", ["gitlab.com", "bitbucket.org"])
def test_unsupported_forge_blocks_publication(delivery_repo: Path, host: str) -> None:
    subprocess.run(
        ["git", "remote", "set-url", "origin", f"git@{host}:example/project.git"],
        cwd=delivery_repo,
        check=True,
    )
    with pytest.raises(SystemExit, match="not on a supported forge"):
        deliver.pr_forge(delivery_repo, "origin")
    assert "bitbucket.org" not in deliver.PACKAGE_HOSTS
    assert "api.bitbucket.org" not in deliver.PACKAGE_HOSTS


def test_receipts_normalize_across_forges() -> None:
    expected = {
        "url": "https://example.test/pr/1",
        "state": "OPEN",
        "source": "feat/example",
        "commit": "abc123",
        "destination": "main",
    }
    github = {
        "url": expected["url"],
        "state": "OPEN",
        "headRefName": "feat/example",
        "headRefOid": "abc123",
        "baseRefName": "main",
    }
    assert deliver.pr_receipt("GitHub", github) == expected


def test_sandbox_hosts_are_registry_only_with_documented_overrides(tmp_path: Path) -> None:
    assert deliver.PACKAGE_HOSTS == ["pypi.org", "files.pythonhosted.org", "registry.npmjs.org"]
    (tmp_path / "deliver.toml").write_text(
        '[sandbox]\nextra_hosts = ["packages.example.test"]\ngate = "env-scrub"\n'
    )
    assert deliver.allowed_hosts(tmp_path) == [*deliver.PACKAGE_HOSTS, "packages.example.test"]


def test_claude_settings_deny_credentials_and_fail_closed(delivery_repo: Path) -> None:
    settings = json.loads(deliver.claude_sandbox_settings([], delivery_repo))["sandbox"]
    assert settings["credentials"]["files"]["mode"] == "deny"
    assert settings["credentials"]["envVars"]["names"] == deliver.CREDENTIAL_ENV
    assert settings["failIfUnavailable"] is True
    assert settings["network"]["allowedDomains"] == deliver.PACKAGE_HOSTS
    assert settings["filesystem"]["denyWrite"]


def test_codex_profile_and_legacy_network_fallback(
    delivery_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deliver, "cli_version", lambda _: "0.163.0")
    command = deliver.agent_command(
        "codex", "Implement", None, True, None, "acceptEdits", writable=[]
    )
    assert 'default_permissions="deliver"' in command
    assert "features.network_proxy=true" in command
    assert any("network.domains" in part and "pypi.org" in part for part in command)
    assert any("filesystem" in part and ".ssh" in part and "deny" in part for part in command)
    monkeypatch.setattr(deliver, "cli_version", lambda _: "0.162.9")
    command = deliver.agent_command(
        "codex", "Implement", None, True, None, "acceptEdits", writable=[]
    )
    assert "--profile" not in command
    assert "sandbox_workspace_write.network_access=false" in command


def test_outer_gate_receives_scrubbed_environment(
    delivery_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GH_TOKEN", "secret")
    monkeypatch.setenv("UV_PUBLISH_TOKEN", "secret")
    monkeypatch.setenv("UV_INDEX_PRIVATE_PASSWORD", "secret")
    monkeypatch.setenv("UV_INDEX_URL", "https://user:secret@example.test/simple")
    monkeypatch.setenv("UV_CACHE_DIR", "/tmp/uv-cache")
    monkeypatch.setenv("CUSTOM_SECRET", "preserved-for-local-check")
    monkeypatch.delenv("UV_PROJECT_ENVIRONMENT", raising=False)
    captured: dict[str, str] = {}

    def fake_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.update(cast(Mapping[str, str], kwargs["env"]))
        return subprocess.CompletedProcess(cast(Sequence[str], args[0]), 0, "ok", "")

    monkeypatch.setattr(deliver.subprocess, "run", fake_run)
    assert deliver.run_check(delivery_repo, "true", 10, outer=True) == (0, "ok")
    assert "GH_TOKEN" not in captured
    assert "UV_PUBLISH_TOKEN" not in captured
    assert "UV_INDEX_PRIVATE_PASSWORD" not in captured
    assert "UV_INDEX_URL" not in captured
    assert captured["UV_CACHE_DIR"] == "/tmp/uv-cache"
    assert "CUSTOM_SECRET" not in captured
    assert "PATH" in captured
    assert "UV_PROJECT_ENVIRONMENT" not in captured


def test_fake_round_editing_git_hook_blocks_run(
    delivery_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan = delivery_repo / "plan.md"
    plan.write_text("- [ ] Implement\n")
    hook = delivery_repo / ".git/hooks/pre-commit"
    hook.write_text("#!/bin/sh\nexit 0\n")
    run = deliver.Run(token="test-run", plan="plan.md", check="exit 0", session_id="session")
    run.save(delivery_repo)

    def fake_round(
        command: list[str], cwd: Path, log: Path, timeout: float = 5400
    ) -> deliver.RoundResult:
        hook.write_text("#!/bin/sh\necho modified\n")
        return deliver.RoundResult(code=0, thread="session", started=True)

    monkeypatch.setattr(deliver, "run_round", fake_round)
    monkeypatch.setattr(deliver, "agent_command", lambda *args, **kwargs: ["fake"])
    args = argparse.Namespace(
        max_wait=1, harness="codex", model=None, claude_permission_mode="acceptEdits", effort=None
    )
    assert deliver.continue_run(delivery_repo, run, args) == 1
    blocked = deliver.find_run(delivery_repo, run.token)
    assert blocked.status == "blocked"
    assert "hooks" in blocked.note


@pytest.mark.parametrize("harness", ["claude", "codex"])
def test_one_item_headless_round_completes_for_each_vendor(
    delivery_repo: Path, monkeypatch: pytest.MonkeyPatch, harness: str
) -> None:
    plan = delivery_repo / "plan.md"
    plan.write_text("- [ ] Implement\n")
    run = deliver.Run(
        token=f"{harness}-run",
        plan="plan.md",
        check="exit 0",
        harness=harness,
        headless=True,
        session_id="fake-session",
        publish_pr=False,
    )
    run.save(delivery_repo)
    commands: list[list[str]] = []

    def fake_round(
        command: list[str], cwd: Path, log: Path, timeout: float = 5400
    ) -> deliver.RoundResult:
        commands.append(command)
        plan.write_text("- [x] Implement\n")
        return deliver.RoundResult(code=0, thread="fake-session", started=True)

    monkeypatch.setattr(deliver, "run_round", fake_round)
    monkeypatch.setattr(deliver, "run_check", lambda *args, **kwargs: (0, "passed"))
    args = argparse.Namespace(
        max_wait=1, harness=harness, model=None, claude_permission_mode="acceptEdits", effort=None
    )
    assert deliver.continue_run(delivery_repo, run, args) == 0
    assert commands[0][0] == harness
    assert deliver.find_run(delivery_repo, run.token).status == "completed"


def test_reviewing_handoff_runs_review_outside_agent_round(
    delivery_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _commit_plan(delivery_repo)
    run = deliver.Run(
        token="review-run",
        plan="plan.md",
        check="exit 0",
        cross_review="final",
        reviewer="codex",
        reviewer_model="gpt-6.1-sol",
        review_rounds=1,
        headless=True,
        status="reviewing",
    )
    run.save(delivery_repo)
    real_run = subprocess.run

    def fake_review(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if "cross_review.py" in " ".join(command):
            review_dir = run.directory(delivery_repo) / "reviews"
            review_dir.mkdir(parents=True, exist_ok=True)
            record = {
                "reviewer": "codex",
                "model": "gpt-6.1-sol",
                "verdict": "pass",
                "summary": "No required changes.",
                "findings": [],
                "signature": deliver.state_signature(delivery_repo),
            }
            (review_dir / "fake.json").write_text(json.dumps(record))
            return subprocess.CompletedProcess(command, 0, "Review passed", "")
        return cast(Any, real_run)(command, **kwargs)

    monkeypatch.setattr(deliver.subprocess, "run", fake_review)
    monkeypatch.setattr(deliver, "run_check", lambda *args, **kwargs: (0, "passed"))
    saved, verdict = deliver.advance_handoff(delivery_repo, run)
    assert saved.status == "completed"
    assert verdict.action == "release"
    assert "cross_review.py" in " ".join(deliver.review_command(run))


def test_doctor_warns_on_worktree_credentials(delivery_repo: Path) -> None:
    (delivery_repo / ".worktreeinclude").write_text("data/.env.local\nsecrets/.ssh/id_ed25519\n")
    assert deliver.worktreeinclude_credentials(delivery_repo) == [
        "data/.env.local",
        "secrets/.ssh/id_ed25519",
    ]


def test_secret_scan_invokes_gitleaks_and_records_report(
    delivery_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(deliver.shutil, "which", lambda _: "/usr/bin/gitleaks")
    calls: list[list[str]] = []
    real_run = subprocess.run

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if command[0] == "/usr/bin/gitleaks":
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")
        return cast(Any, real_run)(command, **kwargs)

    monkeypatch.setattr(deliver.subprocess, "run", fake_run)
    deliver.secret_scan(delivery_repo, "origin/main")
    assert "--log-opts=origin/main..HEAD" in calls[0]
    assert str(deliver.runs_dir(delivery_repo) / "gitleaks-report.json") in calls[0]


@pytest.mark.parametrize(
    ("harness", "answer", "model", "effort"),
    [
        ("codex", "2", "gpt-6-luna", "high"),
        ("claude", "3", "haiku", "high"),
        ("codex", "1", "gpt-6.1-sol", "medium"),
        ("claude", "1", "sonnet", "medium"),
    ],
)
def test_dialog_model_effort_defaults(monkeypatch, harness, answer, model, effort) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    answers = iter([answer, ""])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    args = deliver.parser().parse_args(["headless", "--harness", harness, "--review-rounds", "0"])
    deliver.configure_dialog(args)
    assert (args.model, args.effort) == (model, effort)


@pytest.mark.parametrize(("harness", "model"), [("codex", "gpt-6.1-sol"), ("claude", "opus")])
def test_reviewer_menu_only_offers_frontier(monkeypatch, harness, model) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "")
    assert deliver.select_model("Review model", harness, review=True) == model


@pytest.mark.parametrize("model", ["gpt-6-luna", "haiku", "claude-haiku-4-5"])
def test_small_reviewer_is_rejected(model) -> None:
    with pytest.raises(SystemExit, match="frontier"):
        deliver.reviewer_model("codex", model)


@pytest.mark.parametrize("supported", [True, False])
def test_claude_permission_prompts_flag_is_capability_gated(monkeypatch, supported: bool) -> None:
    monkeypatch.setattr(deliver, "cli_supports_argument", lambda harness, argument: supported)
    command = deliver.agent_command("claude", "Run", "session", True, None, "acceptEdits")
    flag_present = "--permission-prompts" in command
    assert flag_present is supported
    if supported:
        assert command[command.index("--permission-prompts") + 1] == "none"


@pytest.mark.parametrize("advertised", [True, False])
def test_cli_argument_capability_uses_cached_help(monkeypatch, advertised: bool) -> None:
    calls = []
    option = "--permission-prompts"

    def fake_run(command, **kwargs):
        calls.append(command)
        help_text = f"{option} Who answers prompts" if advertised else "--permission-mode"
        return subprocess.CompletedProcess(command, 0, help_text, "")

    monkeypatch.setattr(deliver.subprocess, "run", fake_run)
    deliver.cli_supports_argument.cache_clear()
    try:
        assert deliver.cli_supports_argument("claude", option) is advertised
        assert deliver.cli_supports_argument("claude", option) is advertised
        assert calls == [["claude", "--help"]]
    finally:
        deliver.cli_supports_argument.cache_clear()


def test_dialog_keeps_saved_and_explicit_models_without_prompting(monkeypatch) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("Unexpected model prompt"))
    saved = deliver.Run(
        "saved-models",
        "plan.md",
        "exit 0",
        harness="codex",
        model="gpt-6-luna",
        effort="high",
        reviewer="claude",
        reviewer_model="opus",
    )
    args = deliver.parser().parse_args(
        ["headless", "--harness", "codex", "--reviewer", "claude", "--review-rounds", "1"]
    )
    deliver.configure_dialog(args, saved)
    assert args.model == "gpt-6-luna"
    args.model = "custom-model"
    args.effort = "low"
    args.reviewer_model = "custom-review-model"
    deliver.configure_dialog(args, saved)
    assert (args.model, args.reviewer_model) == ("custom-model", "custom-review-model")


@pytest.mark.parametrize("no_color", [False, True])
def test_dialog_styling_and_invalid_input(monkeypatch, capsys, no_color: bool) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setenv("FORCE_COLOR", "1")
    if no_color:
        monkeypatch.setenv("NO_COLOR", "1")
    else:
        monkeypatch.delenv("NO_COLOR", raising=False)
    answers = iter(["invalid", ""])
    prompts = []

    def answer(prompt):
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", answer)
    assert deliver.select_option("Plan", [("Example plan", "plan.md")]) == "plan.md"
    output = capsys.readouterr().out
    assert "● deliver" in output
    assert "\u276f" in output
    assert "Enter one of the listed numbers." in output
    assert ("\033[" in output + "".join(prompts)) is not no_color


@pytest.mark.parametrize(
    ("plan", "branch"),
    [
        ("docs/plans/2026-10-20-harness-s1-cost-caps.md", "feat/harness-s1-cost-caps"),
        ("docs/plans/New Feature.md", "feat/new-feature"),
    ],
)
def test_feature_branch_names(plan, branch) -> None:
    assert deliver.default_branch(plan) == branch


@pytest.mark.parametrize(
    ("model", "expected"), [("gpt-6-luna", "high"), ("haiku", "high"), ("opus", "medium")]
)
def test_explicit_cli_receives_effort_defaults(model, expected) -> None:
    args = deliver.parser().parse_args(["headless", "--model", model])
    deliver.model_defaults(args)
    assert args.effort == expected
    args.effort = "low"
    deliver.model_defaults(args)
    assert args.effort == "low"


def test_arrow_menu_restores_terminal(monkeypatch) -> None:
    keys = iter([b"\x1b", b"[", b"B", b"\n"])
    restored = []
    monkeypatch.setattr(sys.stdin, "fileno", lambda: 42)
    monkeypatch.setattr(deliver.termios, "tcgetattr", lambda _: ["original"])
    monkeypatch.setattr(deliver.tty, "setcbreak", lambda _: None)
    monkeypatch.setattr(deliver.termios, "tcsetattr", lambda *args: restored.append(args))
    monkeypatch.setattr(deliver.os, "read", lambda *args: next(keys))
    monkeypatch.setattr(deliver.select, "select", lambda *args: ([42], [], []))
    assert deliver.arrow_selection([("First", 1), ("Second", 2)], 0) == 2
    assert restored == [(42, deliver.termios.TCSADRAIN, ["original"])]


def test_arrow_menu_cancel_restores_terminal(monkeypatch) -> None:
    restored = []
    monkeypatch.setattr(sys.stdin, "fileno", lambda: 42)
    monkeypatch.setattr(deliver.termios, "tcgetattr", lambda _: ["original"])
    monkeypatch.setattr(deliver.tty, "setcbreak", lambda _: None)
    monkeypatch.setattr(deliver.termios, "tcsetattr", lambda *args: restored.append(args))
    monkeypatch.setattr(deliver.os, "read", lambda *args: b"q")
    with pytest.raises(SystemExit):
        deliver.arrow_selection([("First", 1)], 0)
    assert restored


def test_noninteractive_implementer_defaults_are_explicit() -> None:
    args = deliver.parser().parse_args(["headless", "--harness", "codex"])
    deliver.model_defaults(args)
    assert (args.model, args.effort) == ("gpt-6.1-sol", "medium")


def test_resume_vendor_change_does_not_inherit_effort(monkeypatch) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    saved = deliver.Run(
        "run", "plan.md", "true", harness="codex", model="gpt-6-luna", effort="high"
    )
    args = deliver.parser().parse_args(
        ["headless", "--harness", "claude", "--model", "sonnet", "--review-rounds", "0"]
    )
    deliver.configure_dialog(args, saved)
    assert args.effort == "medium"


def test_outer_evaluation_requests_headless_review_handoff(delivery_repo: Path) -> None:
    _commit_plan(delivery_repo)
    (delivery_repo / "plan.md").write_text("- [x] Implement\n")
    run = deliver.Run(
        token="handoff-run",
        plan="plan.md",
        check="exit 0",
        headless=True,
        cross_review="final",
        reviewer="codex",
    )
    verdict = deliver.evaluate(delivery_repo, run, outer=True)
    assert run.status == "reviewing"
    assert verdict.action == "handoff"


def test_agent_credential_policy_covers_active_linked_worktree(
    delivery_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    worktree = delivery_repo.parent / "linked"
    subprocess.run(
        ["git", "-C", str(delivery_repo), "worktree", "add", "-q", str(worktree)], check=True
    )
    monkeypatch.chdir(delivery_repo)
    command = deliver.agent_command(
        "claude", "Implement", "session", True, None, "acceptEdits", root=worktree
    )
    settings = json.loads(command[command.index("--settings") + 1])["sandbox"]
    paths = settings["credentials"]["files"]["paths"]
    assert str(worktree / ".env*") in paths
    assert str(delivery_repo / ".env*") in paths


def test_doctor_reports_project_skills_claude_cannot_see(tmp_path: Path) -> None:
    for name in ("linked", "missing"):
        (tmp_path / ".agents/skills" / name).mkdir(parents=True)
        (tmp_path / ".agents/skills" / name / "SKILL.md").write_text("---\nname: x\n---\n")
    (tmp_path / ".claude/skills").mkdir(parents=True)
    (tmp_path / ".claude/skills/linked").symlink_to("../../.agents/skills/linked")
    problems = deliver.unlinked_skills(tmp_path)
    assert len(problems) == 1
    assert problems[0].startswith(".claude/skills/missing must link to .agents/skills/missing")


def test_codex_only_projects_need_no_skill_links(tmp_path: Path) -> None:
    (tmp_path / ".agents/skills/solo").mkdir(parents=True)
    (tmp_path / ".agents/skills/solo/SKILL.md").write_text("---\nname: solo\n---\n")
    assert deliver.unlinked_skills(tmp_path) == []
