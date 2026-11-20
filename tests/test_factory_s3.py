"""Factory S3: holdout checks, reports, retrospective, board mirror and board intake."""

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.factory_fakes import CONTROLLER, FakeGitHub
from tests.helpers import load_script

factory = load_script(Path("plugins/factory/skills/factory/scripts/factory.py"), "factory_s3")
State = factory.State
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)  # a Friday
MARKER = '<!-- factory:triage {"acceptance": ["It works."]} -->'


class Fake:
    """Command runner: answers by substring, can raise, records the calls."""

    def __init__(self, answers=None, raises=None):
        self.calls, self.envs, self.answers, self.raises = [], [], answers or {}, raises

    def __call__(self, args, cwd, timeout=None, env=None):
        self.calls.append(list(args))
        self.envs.append(env)
        if self.raises:
            raise self.raises
        text = " ".join(args)
        for key, (code, out) in self.answers.items():
            if key in text:
                return subprocess.CompletedProcess(args, code, out, "")
        return subprocess.CompletedProcess(args, 0, "", "")


def env(gh, run=None, root=Path(), **kw):
    config = kw.pop("config", factory.Config())
    return factory.Env(gh, config, root, NOW, run or Fake(), lambda r, n: [], **kw)


def cost(usd, at):
    data = {"stage": "implement", "usd": usd, "at": at}
    return f"<!-- factory:cost {json.dumps(data)} -->"


def lines(*items):
    return "\n".join(json.dumps(i) for i in items)


# --- holdout configuration ---


def write_toml(root, text):
    (root / "factory.toml").write_text(text)
    return factory.load_config(root)


def test_holdout_local_path(tmp_path):
    repo, data = tmp_path / "repo", tmp_path / "data"
    repo.mkdir()
    config = write_toml(repo, f'[holdout]\nsource = "{data}"\ncommand = "pytest -q"\n')
    assert config.holdout.source == str(data.resolve())
    assert config.holdout.timeout_minutes == 30
    assert config.holdout.local_path == str(data.resolve())


def test_holdout_git_source(tmp_path):
    toml = (
        '[holdout]\nsource = "https://example.test/h.git#v1"\ncommand = "x"\ntimeout_minutes = 5\n'
    )
    config = write_toml(tmp_path, toml)
    assert config.holdout.is_git
    assert config.holdout.local_path is None
    assert config.holdout.timeout_minutes == 5


def test_holdout_git_source_needs_a_ref(tmp_path):
    with pytest.raises(SystemExit, match="ref"):
        write_toml(tmp_path, '[holdout]\nsource = "https://example.test/h.git"\ncommand = "x"\n')


@pytest.mark.parametrize("source", ["data", "."])
def test_holdout_inside_repository_is_rejected(tmp_path, source):
    with pytest.raises(SystemExit, match="outside the repository"):
        write_toml(tmp_path, f'[holdout]\nsource = "{source}"\ncommand = "x"\n')


def test_holdout_needs_a_command(tmp_path):
    with pytest.raises(SystemExit, match=r"holdout\.command"):
        write_toml(tmp_path, '[holdout]\nsource = "/elsewhere"\n')


def test_retro_every_default_and_override(tmp_path):
    assert factory.load_config(tmp_path).retro_every == 10
    assert write_toml(tmp_path, "retro_every = 3\n").retro_every == 3
    with pytest.raises(SystemExit):
        write_toml(tmp_path, "retro_every = 0\n")


# --- holdout run ---


def holdout_env(tmp_path, run, source=None):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "secret.csv").write_text("x")
    cfg = factory.Config(
        holdout=factory.HoldoutConfig(source=source or str(data), command="pytest -q")
    )
    return env(FakeGitHub(), run, tmp_path / "wt", config=cfg)


def test_holdout_skipped_without_configuration(tmp_path):
    run = Fake()
    assert factory.run_holdout(env(FakeGitHub(), run), tmp_path) is None
    assert not run.calls


def test_holdout_pass_scrubs_environment_and_removes_the_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    seen = {}

    def run(args, cwd, timeout=None, env=None):
        assert env is not None
        seen["dir"] = Path(env["FACTORY_HOLDOUT_DIR"])
        seen["exists"] = (seen["dir"] / "secret.csv").is_file()
        seen["env"], seen["timeout"] = env, timeout
        return subprocess.CompletedProcess(args, 0, lines({"check": "a", "passed": True}), "")

    result = factory.run_holdout(holdout_env(tmp_path, run), tmp_path)
    assert result.passed
    assert seen["exists"]
    assert "GITHUB_TOKEN" not in seen["env"]
    assert seen["timeout"] == 1800
    assert not seen["dir"].exists()
    assert not seen["dir"].parent.exists()


def test_holdout_git_source_is_cloned_outside_worktrees(tmp_path):
    run = Fake()
    e = holdout_env(tmp_path, run, source="https://example.test/h.git#v1")
    assert factory.run_holdout(e, tmp_path).passed
    clone = run.calls[0]
    assert clone[:2] == ["git", "clone"]
    assert "v1" in clone
    assert "https://example.test/h.git" in clone
    assert not Path(clone[-1]).is_relative_to(tmp_path)


def test_holdout_fail_keeps_only_check_names(tmp_path):
    out = lines(
        {"check": "auc_at_least_0.8", "passed": False},
        {"check": "calibration", "passed": True},
        {"check": "rows 1,2,3 leaked", "passed": False, "detail": "row 7: 0.91"},
        {"check": "ok", "passed": "yes"},
    )
    out += "\nrow 7 is wrong: patient 4411\nnot json {"
    result = factory.run_holdout(holdout_env(tmp_path, Fake({"pytest": (1, out)})), tmp_path)
    assert not result.passed
    assert result.failed == ("auc_at_least_0.8",)
    assert result.exit_code == 1
    assert "4411" not in repr(result)


def test_holdout_timeout(tmp_path):
    raises = subprocess.TimeoutExpired("pytest", 1)
    result = factory.run_holdout(holdout_env(tmp_path, Fake(raises=raises)), tmp_path)
    assert not result.passed
    assert "timed out" in result.reason
    assert result.exit_code is None


def test_holdout_nonzero_exit_without_checks_fails(tmp_path):
    result = factory.run_holdout(holdout_env(tmp_path, Fake({"pytest": (3, "noise")})), tmp_path)
    assert not result.passed
    assert result.reason == "exit code 3"


def review_setup():
    gh = FakeGitHub()
    gh.add_issue(3, ["factory:in-review"])
    gh.add_comment(3, MARKER)
    gh.add_pull(30, 3)
    gh.review_log[30] = [
        {"user": {"login": factory.CODEX_BOT}, "commit_id": "sha30", "state": "COMMENTED"}
    ]
    return gh


def verify(gh, tmp_path, answers, monkeypatch, holdout=True):
    run = Fake(answers)
    cfg = factory.Config()
    if holdout:
        (tmp_path / "data").mkdir(exist_ok=True)
        hold = factory.HoldoutConfig(source=str(tmp_path / "data"), command="pytest -q")
        cfg = factory.Config(holdout=hold)
    (tmp_path / "scripts/agent").mkdir(parents=True, exist_ok=True)
    (tmp_path / "scripts/agent/cross_review.py").write_text("")
    subprocess.run(["git", "-C", str(tmp_path), "init", "-q"], check=True)
    monkeypatch.setattr(factory, "issue_worktree", lambda *a: tmp_path)
    factory.do_verify(env(gh, run, tmp_path, config=cfg), 3)
    return run


def test_failed_holdout_comments_check_names_only_and_skips_the_verifier(tmp_path, monkeypatch):
    out = lines({"check": "auc", "passed": False}) + "\nsecret row 9"
    gh = review_setup()
    run = verify(gh, tmp_path, {"pytest": (1, out)}, monkeypatch)
    assert gh.issues[3]["labels"] == ["factory:needs-human"]
    body = gh.comment_log[3][-1]["body"]
    assert "holdout checks failed: auc" in body
    assert "secret" not in body
    assert not any("cross_review.py" in " ".join(c) for c in run.calls)


def test_passed_holdout_continues_to_the_verifier(tmp_path, monkeypatch):
    gh = review_setup()
    verify(gh, tmp_path, {"cross_review": (0, "pass")}, monkeypatch)
    assert gh.issues[3]["labels"] == ["factory:verified"]


def test_without_holdout_the_verifier_runs_directly(tmp_path, monkeypatch):
    gh = review_setup()
    verify(gh, tmp_path, {"cross_review": (0, "pass")}, monkeypatch, holdout=False)
    assert gh.issues[3]["labels"] == ["factory:verified"]


def test_delivery_denies_the_local_holdout_path(tmp_path):
    run = Fake({"status": (0, json.dumps([{"stage": "completed", "pr": "u"}]))})
    e = holdout_env(tmp_path, run)
    factory.run_delivery(e, tmp_path, "docs/plans/p.md")
    call = next(c for c in run.calls if "headless" in c)
    assert call[call.index("--deny-path") + 1] == str(tmp_path / "data")


# --- tracking issue ---


def test_tracking_issue_created_once_then_reused():
    gh = FakeGitHub()
    first = factory.ensure_tracking_issue(gh)
    assert gh.issues[first]["title"] == "Factory status"
    assert gh.issues[first]["labels"] == ["factory:tracking"]
    assert gh.pinned == [f"N{first}"]
    assert factory.ensure_tracking_issue(gh) == first
    assert len(gh.issues) == 1


def test_tracking_issue_tolerates_pin_failure():
    gh = FakeGitHub()
    gh.error_type = factory.GhError
    gh.pin_error = True
    assert factory.ensure_tracking_issue(gh) in gh.issues


# --- reports ---


def report_setup():
    gh = FakeGitHub()
    gh.add_issue(1, ["factory:needs-human"], title="a")
    gh.add_comment(1, "Needs a human: protected paths changed: AGENTS.md")
    gh.add_issue(2, ["factory:in-progress"], title="b")
    gh.label_event(2, "factory:in-progress", CONTROLLER, at="2026-11-19T12:00:00+00:00")
    gh.add_comment(
        2,
        cost(1.5, "2026-11-20T01:00:00+00:00"),
    )
    gh.add_issue(3, ["factory:verified"], title="c", state="closed")
    gh.label_event(3, "factory:accepted", CONTROLLER, at="2026-11-17T12:00:00+00:00")
    gh.add_comment(
        3,
        cost(4, "2026-11-18T01:00:00+00:00"),
    )
    gh.merged = [
        {"number": 30, "branch": "feat/3-c", "merged_at": "2026-11-19T12:00:00Z"},
        {"number": 99, "branch": "feat/9-old", "merged_at": "2026-11-01T12:00:00Z"},
    ]
    return gh


def test_daily_report_is_posted_once_and_edited_on_rerun():
    gh = report_setup()
    cfg = factory.Config()
    assert factory.main(["report", "daily"], github=gh, root=Path("/nonexistent"), now=NOW) == 0
    tracking = next(n for n, i in gh.issues.items() if i["title"] == "Factory status")
    body = gh.comment_log[tracking][0]["body"]
    assert "<!-- factory:report daily 2026-11-20 -->" in body
    assert "factory:needs-human | 1" in body
    assert "#1: protected paths changed: AGENTS.md" in body
    assert "Spend today: 1.50 of 20.00 USD" in body
    assert cfg.daily_cap_usd == 20.0
    factory.main(["report", "daily"], github=gh, root=Path("/nonexistent"), now=NOW)
    assert len(gh.comment_log[tracking]) == 1
    factory.main(
        ["report", "daily"], github=gh, root=Path("/nonexistent"), now=NOW + timedelta(days=1)
    )
    assert len(gh.comment_log[tracking]) == 2


def test_weekly_report_figures():
    gh = report_setup()
    marker, body = factory.weekly_report(gh, factory.Config(), NOW)
    assert marker == "<!-- factory:report weekly 2026-11-16 -->"
    assert "Throughput: 1 merged pull requests" in body
    assert "#3 (PR #30): 48.0 h" in body
    assert "#2: 24.0 h" in body
    assert "#2: 1.50 USD" in body
    assert "#3: 4.00 USD" in body
    assert "protected paths changed: 1" in body


def test_weekly_report_rerun_edits_in_place():
    gh = report_setup()
    for _ in range(2):
        factory.main(["report", "weekly"], github=gh, root=Path("/nonexistent"), now=NOW)
    tracking = next(n for n, i in gh.issues.items() if i["title"] == "Factory status")
    assert len(gh.comment_log[tracking]) == 1


# --- retrospective ---


def retro_gh(merged_count):
    gh = FakeGitHub()
    for n in range(1, merged_count + 1):
        gh.add_issue(n, ["factory:verified"], state="closed")
        gh.add_comment(n, "Needs a human: Codex review missing")
        gh.merged.append(
            {
                "number": 100 + n,
                "branch": f"feat/{n}-x",
                "merged_at": f"2026-11-1{n % 10}T00:00:00Z",
            }
        )
    return gh


def retro_env(gh, run, root, every=3):
    return env(gh, run, root, config=factory.Config(retro_every=every))


def git_root(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q", "-b", "main"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "init",
        ],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "update-ref", "refs/remotes/origin/HEAD", "HEAD"], check=True
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
            "refs/remotes/origin/main",
        ],
        check=False,
    )
    return root


def test_retro_below_the_count_runs_nothing(tmp_path):
    run = Fake()
    gh = retro_gh(2)
    message = factory.do_retro(retro_env(gh, run, tmp_path))
    assert "2 of 3" in message
    assert not run.calls


def test_retro_at_the_count_runs_once_and_the_marker_resets_it(tmp_path, monkeypatch):
    gh = retro_gh(3)
    run = Fake(
        {
            "status": (
                0,
                json.dumps([{"stage": "completed", "pr": "https://x/pull/77", "cost_usd": 0.5}]),
            )
        }
    )
    seen = []

    def fake_git(root, *args):
        seen.append(args)
        if args[0] == "worktree":
            Path(args[4]).mkdir(parents=True)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(factory, "_git", fake_git)
    gh.add_issue(77, [], title="the retro pull request")
    e = retro_env(gh, run, tmp_path)
    assert factory.do_retro(e).endswith("https://x/pull/77")
    assert sum("headless" in c for c in run.calls) == 1
    plan = (
        tmp_path / ".worktrees/retro-2026-11-20/docs/plans/2026-11-20-factory-retro.md"
    ).read_text()
    assert "agents-md" in plan
    evidence = (
        tmp_path / ".worktrees/retro-2026-11-20/docs/factory/retro-2026-11-20-evidence.md"
    ).read_text()
    assert "Codex review missing" in evidence
    assert gh.issues[77]["labels"] == ["factory:needs-human"]
    tracking = next(n for n, i in gh.issues.items() if i["title"] == "Factory status")
    assert "<!-- factory:retro " in gh.comment_log[tracking][-1]["body"]
    run.calls.clear()
    assert "0 of 3" in factory.do_retro(e)
    assert not run.calls


def test_retro_evidence_lists_cost_outliers_and_declined_findings(tmp_path):
    gh = retro_gh(3)
    for n, usd in ((1, 1), (2, 1), (3, 9)):
        gh.add_comment(
            n,
            cost(usd, "2026-11-18T00:00:00+00:00"),
        )
    merged = factory._merged(gh, datetime.min.replace(tzinfo=UTC))
    e = env(gh, declined=lambda n: ["false positive about X"] if n == 1 else [])
    text = factory.retro_evidence(e, merged)
    assert "#3: 9.00 USD" in text
    assert "#1: 1.00 USD" not in text
    assert "#1: false positive about X" in text


# --- board mirror ---


def test_mirror_runs_once_per_label_change_and_tolerates_errors(tmp_path):
    gh = FakeGitHub()
    gh.add_issue(4, ["factory:ready"])
    mirrored = []
    e = env(gh, root=tmp_path, mirror=mirrored.append)
    factory.move(e, 4, State.ACCEPTED)
    assert mirrored == [4]

    def broken(number):
        raise RuntimeError("gh exploded")

    e = env(gh, root=tmp_path, mirror=broken)
    factory.move(e, 4, State.IN_PROGRESS)
    assert gh.issues[4]["labels"] == ["factory:in-progress"]
    assert "gh exploded" in (tmp_path / ".factory/run.log").read_text()


def test_make_mirror_syncs_one_issue():
    gh = FakeGitHub()
    gh.add_board("acme", 1, "widgets factory", options=[n for n, *_ in factory.BOARD_COLUMNS])
    gh.add_issue(4, ["factory:verified"])
    factory.make_mirror(gh, factory.Config())(4)
    assert list(gh.item_values.values()) == ["O-Verified"]


# --- board intake ---

BOARD = None


def board_gh(card_status="Ready"):
    gh = FakeGitHub()
    gh.add_board("acme", 1, "widgets factory", options=[n for n, *_ in factory.BOARD_COLUMNS])
    gh.permissions |= {"maint": "maintain", "reader": "read"}
    gh.cards = []
    return gh, factory.Board("acme", 1, "P1", "u")


def card(gh, number, labels, status="Ready", state="open"):
    gh.add_issue(number, labels, state=state)
    gh.cards.append({"number": number, "state": state, "status": status})


def intake(gh, board):
    return factory.intake(gh, board, factory.Config())


def test_admitted_move_sets_the_label():
    gh, board = board_gh()
    card(gh, 1, [])
    gh.move(1, "maint")
    assert intake(gh, board) == [1]
    assert gh.issues[1]["labels"] == ["factory:ready"]


@pytest.mark.parametrize(
    "move",
    [
        {"login": "reader"},
        {"login": "stranger"},
        {"login": "maint", "automated": True},
        {"login": "maint", "project": "OTHER"},
    ],
)
def test_other_moves_do_not_release(move):
    gh, board = board_gh()
    card(gh, 1, [])
    gh.move(1, **move)
    assert intake(gh, board) == []
    assert gh.issues[1]["labels"] == []


def test_needs_info_moved_to_ready_becomes_ready():
    gh, board = board_gh()
    card(gh, 1, ["factory:needs-info"])
    gh.move(1, "maint")
    assert intake(gh, board) == [1]
    assert gh.issues[1]["labels"] == ["factory:ready"]


def test_accepted_issue_is_left_alone():
    gh, board = board_gh()
    card(gh, 1, ["factory:accepted"])
    gh.move(1, "maint")
    assert intake(gh, board) == []
    assert gh.issues[1]["labels"] == ["factory:accepted"]


def test_closed_issue_and_other_columns_are_ignored():
    gh, board = board_gh()
    card(gh, 1, [], state="closed")
    card(gh, 2, [], status="Verified")
    gh.move(1, "maint")
    gh.move(2, "maint", status="Verified")
    assert intake(gh, board) == []


def test_later_outsider_label_after_an_admitted_move_is_rejected():
    gh, board = board_gh()
    gh.add_issue(1, ["factory:ready"])
    gh.move(1, "maint")
    gh.label_event(1, "factory:ready", "stranger")
    assert not factory.admitted(gh, gh.issues[1], factory.Config(), board)
    assert not factory.admitted(gh, gh.issues[1], factory.Config())


def test_the_controllers_own_label_passes_admission():
    gh = FakeGitHub()
    gh.add_issue(1, ["factory:ready"])
    gh.label_event(1, "factory:ready", CONTROLLER)
    assert factory.admitted(gh, gh.issues[1], factory.Config())


def test_admitted_move_after_an_outsider_label_counts():
    gh, board = board_gh()
    gh.add_issue(1, ["factory:ready"])
    gh.label_event(1, "factory:ready", "stranger")
    gh.move(1, "maint")
    assert factory.admitted(gh, gh.issues[1], factory.Config(), board)


def test_tick_runs_intake_before_the_snapshot_and_scheduled_sync(tmp_path):
    gh, _ = board_gh()
    card(gh, 1, [])
    gh.move(1, "maint")
    card(gh, 2, ["factory:verified"], status="Verified")
    out = []
    e = env(gh, root=tmp_path)
    factory._tick(e, False, False, out.append, scheduled=True)
    assert out[0].startswith("triage #1")  # intake released #1 before the snapshot
    assert gh.item_values  # the full sync mirrored the cards


def test_card_dragged_to_verified_returns_to_its_label_column():
    gh, board = board_gh()
    card(gh, 1, ["factory:in-review"], status="Verified")
    factory.sync_board(gh, board, factory.factory_issues(gh))
    assert list(gh.item_values.values()) == ["O-In review"]


def test_intake_errors_never_fail_the_tick(tmp_path):
    gh, _ = board_gh()
    gh.add_issue(1, [])

    def broken(project_id):
        raise RuntimeError("graphql down")

    gh.board_items = broken
    out = []
    assert factory._tick(env(gh, root=tmp_path), False, False, out.append) == 0
    assert "graphql down" in (tmp_path / ".factory/run.log").read_text()
