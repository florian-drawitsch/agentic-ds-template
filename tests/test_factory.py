import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.factory_fakes import CONTROLLER, FakeGitHub
from tests.helpers import load_script

SCRIPT = Path("plugins/factory/skills/factory/scripts/factory.py")
factory = load_script(SCRIPT, "factory")
State = factory.State
ALL_LABELS = {s.value for s in State} | set(factory.PRIORITY_STYLE)
NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def cfg(**kw):
    return factory.Config(**kw)


def run_main(argv, gh, root=None, now=NOW):
    return factory.main(argv, github=gh, root=root or Path.cwd(), now=now)


# --- skeleton ---


def test_help_lists_subcommands():
    out = subprocess.run(
        ["uv", "run", "--script", str(SCRIPT), "--help"], capture_output=True, text=True, check=True
    ).stdout
    for name in ("status", "tick", "labels", "init", "sync"):
        assert name in out


def test_build_check_passes():
    result = subprocess.run(
        ["uv", "run", "--no-project", "python", "scripts/build.py", "--check"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# --- configuration ---


def test_config_defaults_without_file(tmp_path):
    config = factory.load_config(tmp_path)
    assert config.max_in_progress == 1
    assert config.max_verified == 3
    assert config.daily_cap_usd == 20.0
    assert "AGENTS.md" in config.protected_paths
    assert config.weekly_cap_usd == 100.0


def test_config_overrides(tmp_path):
    (tmp_path / "factory.toml").write_text(
        'max_verified = 5\nweekly_cap_usd = 60\ntrusted_bots = ["renovate[bot]"]\n'
        'daily_cap_usd = 7\n[deliver]\nharness = "codex"\nmax_cost = 4\n'
    )
    config = factory.load_config(tmp_path)
    assert config.max_verified == 5
    assert config.weekly_cap_usd == 60.0
    assert config.trusted_bots == ("renovate[bot]",)
    assert config.daily_cap_usd == 7.0
    assert config.deliver.harness == "codex"
    assert config.deliver.max_cost == 4.0


def test_config_unknown_key(tmp_path):
    (tmp_path / "factory.toml").write_text("bogus = 1\n")
    with pytest.raises(SystemExit, match="bogus"):
        factory.load_config(tmp_path)


def test_config_unknown_deliver_key(tmp_path):
    (tmp_path / "factory.toml").write_text("[deliver]\nbogus = 1\n")
    with pytest.raises(SystemExit, match=r"deliver\.bogus"):
        factory.load_config(tmp_path)


@pytest.mark.parametrize(
    "text", ["max_verified = 'x'", "max_in_progress = true", "weekly_cap_usd = 'x'"]
)
def test_config_wrong_type(tmp_path, text):
    (tmp_path / "factory.toml").write_text(text + "\n")
    with pytest.raises(SystemExit, match=text.split()[0]):
        factory.load_config(tmp_path)


# --- label states ---


def test_every_allowed_transition():
    for current, targets in factory.TRANSITIONS.items():
        for target in targets:
            assert factory.transition(current, target) is target
    assert factory.transition(None, State.READY) is State.READY


def test_rejected_transitions():
    with pytest.raises(ValueError, match="not allowed"):
        factory.transition(State.READY, State.VERIFIED)
    with pytest.raises(ValueError, match="not allowed"):
        factory.transition(None, State.ACCEPTED)


def test_state_of():
    assert factory.state_of(["bug"]) is None
    assert factory.state_of(["bug", "factory:ready"]) is State.READY
    with pytest.raises(ValueError, match="more than one"):
        factory.state_of(["factory:ready", "factory:verified"])


def test_labels_command_is_idempotent():
    gh = FakeGitHub()
    assert run_main(["labels"], gh) == 0
    assert set(gh.created) == ALL_LABELS
    assert run_main(["labels"], gh) == 0
    assert len(gh.created) == len(ALL_LABELS)


# --- github access ---


class RecordingGitHub(factory.GitHub):
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def _call(self, args, input=None):
        self.calls.append((list(args), input))
        return self.replies.pop(0)


def test_changed_files_lists_both_rename_paths():
    pages = json.dumps([[{"filename": "new.py", "previous_filename": "old.py"}, {"filename": "b"}]])
    gh = RecordingGitHub([pages])
    assert gh.changed_files(3) == ["new.py", "old.py", "b"]


def test_issue_text_goes_to_input_not_args():
    gh = RecordingGitHub(["null"])
    body = "$(rm -rf /); `x`"
    gh.comment(4, body)
    args, sent = gh.calls[0]
    assert body not in " ".join(args)
    assert json.loads(sent) == {"body": body}


def test_graphql_passes_variables_as_fields():
    gh = RecordingGitHub(['{"data": 1}'])
    assert gh.graphql("query($a: String!){x}", a="v; ls") == {"data": 1}
    assert "a=v; ls" in gh.calls[0][0]


def test_permission_404_is_none():
    class Missing(factory.GitHub):
        def _call(self, args, input=None):
            raise factory.GhError("HTTP 404: Not Found")

    assert Missing().permission("someone") == "none"


# --- admission ---


def ready_issue(gh, number, login, bot=False):
    gh.add_issue(number, ["factory:ready"])
    gh.label_event(number, "factory:ready", login, bot)
    return gh.issues[number]


@pytest.mark.parametrize("perm", ["admin", "maintain", "write"])
def test_maintainer_admits(perm):
    gh = FakeGitHub()
    gh.permissions["mia"] = perm
    assert factory.admitted(gh, ready_issue(gh, 1, "mia"), cfg())


def test_read_user_not_admitted():
    gh = FakeGitHub()
    gh.permissions["rex"] = "read"
    assert not factory.admitted(gh, ready_issue(gh, 1, "rex"), cfg())


def test_outsider_relabel_after_maintainer_not_admitted():
    gh = FakeGitHub()
    gh.permissions["mia"] = "write"
    issue = ready_issue(gh, 1, "mia")
    gh.label_event(1, "factory:ready", "eve")
    assert not factory.admitted(gh, issue, cfg())


def test_bot_needs_trust():
    gh = FakeGitHub()
    issue = ready_issue(gh, 1, "ci[bot]", bot=True)
    assert not factory.admitted(gh, issue, cfg())
    assert factory.admitted(gh, issue, cfg(trusted_bots=("ci[bot]",)))


def test_no_ready_event_not_admitted():
    gh = FakeGitHub()
    gh.add_issue(1, ["factory:ready"])
    assert not factory.admitted(gh, gh.issues[1], cfg())


def test_status_reports_unadmitted():
    gh = FakeGitHub()
    ready_issue(gh, 1, "rex")
    snap = factory.snapshot(gh, cfg())
    assert snap.unadmitted == [1]
    assert factory.next_action(snap, cfg(), NOW).kind == "idle"


# --- dispatch ---


def issue(number, *labels):
    return {"number": number, "title": "t", "state": "open", "labels": list(labels)}


def build(states, pulls=None, repairs=None, costs=None):
    full = {s: [] for s in State}
    for name, issues in states.items():
        full[State[name.upper()]] = issues
    return factory.Snapshot(full, [], [], pulls or {}, repairs or {}, costs or [])


def pull(n, changes=False, passed=True):
    return factory.Pull(n, f"feat/{n}-x", changes, passed)


def act(snap, config=None):
    return factory.next_action(snap, config or cfg(), NOW)


def test_order_repair_before_verify_before_implement_before_triage():
    snap = build(
        {
            "in_review": [issue(1), issue(2)],
            "accepted": [issue(3)],
            "ready": [issue(4)],
        },
        pulls={1: pull(11), 2: pull(12, changes=True, passed=False)},
    )
    assert act(snap) == factory.Action("repair", 2, "review findings or failing checks")
    snap = build(
        {"in_review": [issue(1)], "accepted": [issue(3)], "ready": [issue(4)]}, pulls={1: pull(11)}
    )
    assert act(snap).kind == "verify"
    snap = build({"accepted": [issue(3)], "ready": [issue(4)]})
    assert act(snap) == factory.Action("implement", 3, "accepted, within limits")
    assert act(build({"ready": [issue(4)]})).kind == "triage"
    assert act(build({})).kind == "idle"


def test_repair_limit_escalates():
    snap = build(
        {"in_review": [issue(1)]}, pulls={1: pull(11, changes=True, passed=False)}, repairs={1: 2}
    )
    assert act(snap).kind == "escalate"


def test_pending_checks_do_not_verify():
    snap = build({"in_review": [issue(1)]}, pulls={1: pull(11, passed=False)})
    assert act(snap).kind == "idle"


def test_priority_orders_before_number():
    snap = build(
        {
            "accepted": [
                issue(1),
                issue(2, "priority:low"),
                issue(3, "priority:high"),
                issue(4, "priority:medium"),
            ]
        }
    )
    assert act(snap).issue == 3
    snap = build({"accepted": [issue(5, "priority:low"), issue(2, "priority:low"), issue(9)]})
    assert act(snap).issue == 2


def test_ties_break_on_issue_number():
    snap = build({"ready": [issue(8, "priority:high"), issue(6, "priority:high")]})
    assert act(snap).issue == 6


def test_implement_refused_at_in_progress_limit():
    snap = build({"accepted": [issue(3)], "in_progress": [issue(1)]})
    assert act(snap) == factory.Action("idle", None, "max_in_progress (1) reached")


def test_implement_refused_at_verified_limit_falls_back_to_triage():
    snap = build(
        {"accepted": [issue(3)], "verified": [issue(i) for i in (1, 2, 4)], "ready": [issue(9)]}
    )
    assert act(snap).kind == "triage"
    snap = build({"accepted": [issue(3)], "verified": [issue(i) for i in (1, 2, 4)]})
    assert "max_verified" in act(snap).reason


def test_limits_never_violated():
    for in_progress in range(4):
        for verified in range(5):
            config = cfg(max_in_progress=2, max_verified=3)
            snap = build(
                {
                    "accepted": [issue(100)],
                    "in_progress": [issue(i) for i in range(in_progress)],
                    "verified": [issue(50 + i) for i in range(verified)],
                }
            )
            result = act(snap, config)
            if result.kind == "implement":
                assert in_progress < 2
                assert verified < 3


# --- claim ---


def test_first_claim_wins_second_loses(clone):
    first, second = clone
    assert factory.claim(first, 7, "Add a thing") is True
    assert factory.claim(second, 7, "Add a thing") is False
    out = subprocess.run(
        ["git", "-C", str(first), "ls-remote", "--heads", "origin"], capture_output=True, text=True
    ).stdout
    assert "refs/heads/feat/7-add-a-thing" in out


def test_claim_loses_even_with_different_title(clone):
    first, second = clone
    assert factory.claim(first, 7, "Add a thing")
    assert not factory.claim(second, 7, "Renamed")


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Fix: the Bug!", "fix-the-bug"),
        ("Zürich café", "zurich-cafe"),
        ("日本語", "issue"),
        ("", "issue"),
        ("!!!", "issue"),
        ("a" * 60, "a" * 40),
        ("word " * 20, "word-word-word-word-word-word-word-word"),
    ],
)
def test_slug(title, expected):
    assert factory.slug(title) == expected


def test_slug_never_ends_with_hyphen():
    assert not factory.slug("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa bbb").endswith("-")


# --- protected paths ---


@pytest.mark.parametrize(
    "path",
    [
        "AGENTS.md",
        "pkg/AGENTS.md",
        "CLAUDE.md",
        "factory.toml",
        "deliver.toml",
        ".claude/settings.json",
        ".codex/config.toml",
        "scripts/agent/deliver.py",
        ".github/workflows/ci.yml",
        "infra/main.tf",
    ],
)
def test_each_default_pattern(path):
    assert factory.touches_protected([path], factory.DEFAULT_PROTECTED) == [path]


def test_rename_counts_both_paths():
    files = ["src/new.py", ".github/old.yml"]
    assert factory.touches_protected(files, factory.DEFAULT_PROTECTED) == [".github/old.yml"]
    assert factory.touches_protected(["docs/x.md", "AGENTS.md"], factory.DEFAULT_PROTECTED) == [
        "AGENTS.md"
    ]


def test_no_false_match():
    paths = ["docs/AGENTS.md.bak", "src/github/x.py", "scripts/agent_tools.py", "my-factory.toml"]
    assert factory.touches_protected(paths, factory.DEFAULT_PROTECTED) == []


def test_glob_pattern():
    assert factory.touches_protected(["a/b.lock", "c.txt"], ["*.lock"]) == ["a/b.lock"]


# --- spend ledger ---


def cost_comment(usd, at, login=CONTROLLER):
    payload = json.dumps({"stage": "implement", "usd": usd, "at": at.isoformat()})
    return {"body": f"<!-- factory:cost {payload} -->", "user": {"login": login}}


def test_record_cost_writes_marker():
    gh = FakeGitHub()
    gh.add_issue(1, ["factory:in-progress"])
    factory.record_cost(gh, 1, "implement", 1.5, NOW)
    parsed = factory._parse_costs(gh.comments(1), CONTROLLER)
    assert parsed == [(NOW, 1.5)]


def test_day_boundary():
    config = cfg(daily_cap_usd=10.0)
    yesterday = NOW.replace(hour=0, minute=0) - timedelta(seconds=1)
    snap = build({}, costs=[(yesterday, 9.0), (NOW, 3.0)])
    assert factory.spend(snap, NOW, config).day == 3.0
    assert factory.spend(snap, NOW, config).week == 12.0


def test_week_boundary_is_monday_utc():
    config = cfg()
    sunday = datetime(2026, 11, 15, 23, 59, tzinfo=UTC)
    monday = datetime(2026, 11, 16, 0, 0, tzinfo=UTC)
    snap = build({}, costs=[(sunday, 40.0), (monday, 5.0)])
    assert factory.spend(snap, NOW, config).week == 5.0  # NOW is Wednesday 2026-11-18
    assert factory.spend(snap, sunday, config).week == 40.0
    assert factory.week_window(NOW) == (monday, monday + timedelta(days=7))


def test_sprint_keys_are_gone(tmp_path):
    for key in ("sprint_cap_usd = 1", "sprint_start = 2026-11-02", "sprint_days = 7"):
        (tmp_path / "factory.toml").write_text(key + "\n")
        with pytest.raises(SystemExit, match="unknown key"):
            factory.load_config(tmp_path)


def test_cap_reached_is_idle_with_cap_name():
    snap = build({"accepted": [issue(1)]}, costs=[(NOW, 25.0)])
    assert "daily_cap_usd" in act(snap).reason
    earlier_this_week = NOW - timedelta(days=1)
    snap = build({"accepted": [issue(1)]}, costs=[(earlier_this_week, 150.0)])
    result = act(snap)
    assert result.kind == "idle"
    assert "weekly_cap_usd" in result.reason


def test_malformed_and_foreign_markers_ignored():
    good = cost_comment(2.0, NOW)
    comments = [
        good,
        {"body": "<!-- factory:cost {nope} -->", "user": {"login": CONTROLLER}},
        {
            "body": '<!-- factory:cost {"stage": "x", "usd": "9", "at": "2026-11-18T00:00Z"} -->',
            "user": {"login": CONTROLLER},
        },
        {
            "body": '<!-- factory:cost {"stage": "x", "usd": -1, "at": "2026-11-18T00:00:00Z"} -->',
            "user": {"login": CONTROLLER},
        },
        cost_comment(99.0, NOW, login="mallory"),
    ]
    assert factory._parse_costs(comments, CONTROLLER) == [(NOW, 2.0)]


def test_snapshot_reads_costs_from_closed_issues_and_counts_repairs():
    gh = FakeGitHub()
    gh.add_issue(1, ["factory:verified"], state="closed")
    gh.add_comment(1, cost_comment(4.0, NOW)["body"])
    gh.add_issue(2, ["factory:in-review"])
    gh.add_comment(2, factory.REPAIR_MARKER)
    gh.add_comment(2, factory.REPAIR_MARKER, login="mallory")
    gh.add_pull(20, 2, checks="failure")
    snap = factory.snapshot(gh, cfg())
    assert factory.spend(snap, NOW, cfg()).day == 4.0
    assert snap.repairs[2] == 1
    assert snap.pulls[2].changes_requested
    assert snap.states[State.VERIFIED] == []


def test_changes_requested_review_needs_repair():
    gh = FakeGitHub()
    gh.add_issue(2, ["factory:in-review"])
    gh.add_pull(20, 2)
    gh.review_log[20] = [
        {
            "user": {"login": "r"},
            "state": "CHANGES_REQUESTED",
            "submitted_at": "2026-11-18T01:00:00Z",
        },
    ]
    assert factory.snapshot(gh, cfg()).pulls[2].changes_requested
    gh.review_log[20].append(
        {"user": {"login": "r"}, "state": "APPROVED", "submitted_at": "2026-11-18T02:00:00Z"}
    )
    assert not factory.snapshot(gh, cfg()).pulls[2].changes_requested


def test_conflicting_labels_reported():
    gh = FakeGitHub()
    gh.add_issue(5, ["factory:accepted", "factory:in-review"])
    assert factory.snapshot(gh, cfg()).conflicts == [5]


# --- commands ---


def seeded():
    gh = FakeGitHub()
    gh.permissions["mia"] = "write"
    gh.add_issue(1, ["factory:ready"], title="Do it")
    gh.label_event(1, "factory:ready", "mia")
    gh.add_issue(2, ["factory:ready"])
    gh.label_event(2, "factory:ready", "rex")
    return gh


def test_status_json(capsys):
    assert run_main(["status", "--json"], seeded()) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["unadmitted"] == [2]
    assert data["counts"]["factory:ready"] == 1
    assert data["next_action"]["kind"] == "triage"


def test_status_text(capsys):
    assert run_main(["status"], seeded()) == 0
    out = capsys.readouterr().out
    assert "unadmitted: [2]" in out
    assert "next: triage #1" in out


def test_tick_dry_run_json_prints_one_action_and_changes_nothing(capsys):
    gh = seeded()
    assert run_main(["tick", "--dry-run", "--json"], gh) == 0
    assert json.loads(capsys.readouterr().out) == {
        "kind": "triage",
        "issue": 1,
        "reason": "admitted and ready",
    }
    assert gh.issues[1]["labels"] == ["factory:ready"]


def test_tick_idle_exits_0(capsys):
    assert run_main(["tick"], FakeGitHub()) == 0
    assert "idle" in capsys.readouterr().out


def test_lost_claim_changes_no_labels(clone, capsys):
    assert factory.claim(clone[1], 7, "Add a thing")
    gh = FakeGitHub()
    gh.add_issue(7, ["factory:accepted"], title="Add a thing")
    gh.add_comment(7, '<!-- factory:triage {"acceptance": ["a"]} -->')
    assert run_main(["tick"], gh, root=clone[0]) == 0
    assert gh.issues[7]["labels"] == ["factory:accepted"]
    assert "claim lost" in capsys.readouterr().err


def test_github_error_exits_1(capsys):
    class Broken(FakeGitHub):
        def viewer(self):
            raise factory.GhError("no auth")

    assert run_main(["status"], Broken()) == 1
    assert "no auth" in capsys.readouterr().err


def test_claim_lock_blocks_a_different_slug_and_a_deleted_branch(clone):
    first, second = clone
    assert factory.claim(first, 7, "Add a thing")
    subprocess.run(
        ["git", "-C", str(first), "push", "origin", ":refs/heads/feat/7-add-a-thing"], check=True
    )
    assert not factory.claim(second, 7, "Other title")


def test_skipped_check_does_not_verify():
    gh = FakeGitHub()
    gh.add_issue(2, ["factory:in-review"])
    gh.add_pull(20, 2, checks="skipped")
    assert not factory.snapshot(gh, cfg()).pulls[2].checks_passed


def test_missing_required_check_fails_closed():
    gh = FakeGitHub()
    gh.add_issue(2, ["factory:in-review"])
    gh.add_pull(20, 2)
    assert factory.snapshot(gh, cfg()).pulls[2].checks_passed
    assert not factory.snapshot(gh, cfg(required_checks=("ci", "tests"))).pulls[2].checks_passed
    assert factory.snapshot(gh, cfg(required_checks=("ci",))).pulls[2].checks_passed


def test_one_failing_run_among_successes_does_not_verify():
    gh = FakeGitHub()
    gh.add_issue(2, ["factory:in-review"])
    gh.add_pull(20, 2)
    gh.runs["sha20"].append({"name": "lint", "conclusion": None})
    assert not factory.snapshot(gh, cfg()).pulls[2].checks_passed


def test_fork_pull_request_is_not_linked():
    def pull(head_repo):
        return {
            "number": 1,
            "head": {"ref": "feat/5-x", "sha": "s", "repo": {"full_name": head_repo}},
            "base": {"repo": {"full_name": "o/r"}},
        }

    gh = RecordingGitHub([json.dumps([[pull("evil/r")]])])
    assert gh.pull_for_branch("feat/5-") is None
    gh = RecordingGitHub([json.dumps([[pull("evil/r"), pull("o/r")]])])
    assert gh.pull_for_branch("feat/5-")["number"] == 1


def test_configured_controller_avoids_user_endpoint(tmp_path):
    (tmp_path / "factory.toml").write_text('controller = "factory-app[bot]"\n')
    config = factory.load_config(tmp_path)

    class NoUser(FakeGitHub):
        def viewer(self):
            raise AssertionError("GET /user must not be called")

    gh = NoUser()
    gh.add_issue(1, ["factory:verified"], state="closed")
    gh.add_comment(1, cost_comment(4.0, NOW)["body"], login="factory-app[bot]")
    snap = factory.snapshot(gh, config)
    assert factory.spend(snap, NOW, config).day == 4.0


# --- board ---

COLUMNS = [name for name, _, _, _ in factory.BOARD_COLUMNS]


def board_gh():
    gh = FakeGitHub()
    gh.error_type = factory.GhError
    return gh


def test_init_creates_labels_and_board_and_mirrors_issues(tmp_path, capsys):
    gh = board_gh()
    gh.add_issue(1, ["factory:ready"])
    gh.add_issue(2, ["factory:in-review"])
    gh.add_issue(3, ["factory:verified"], state="closed")
    gh.add_issue(4, ["bug"])
    assert run_main(["init"], gh, root=tmp_path) == 0
    assert set(gh.created) == ALL_LABELS
    board = gh.boards["acme"][0]
    assert board["title"] == "widgets factory"
    assert gh.links == [("acme", board["number"], "acme/widgets")]
    assert [o["name"] for o in gh.option_updates[0]] == COLUMNS
    assert {o["color"] for o in gh.option_updates[0]} <= {
        "GRAY", "BLUE", "GREEN", "YELLOW", "ORANGE", "RED", "PINK", "PURPLE"
    }  # fmt: skip
    assert not (tmp_path / "factory.toml").exists(), "no account-specific URL is written"
    url = "https://github.com/acme/widgets/issues/{}"
    assert gh.item_values == {
        gh.items[url.format(1)]: "O-Ready",
        gh.items[url.format(2)]: "O-In review",
    }
    assert "created" in capsys.readouterr().out


def test_init_is_idempotent(tmp_path):
    gh = board_gh()
    assert run_main(["init"], gh, root=tmp_path) == 0
    assert run_main(["init"], gh, root=tmp_path) == 0
    assert len(gh.boards["acme"]) == 1
    assert len(gh.option_updates) == 1
    assert len(gh.links) == 1


def test_init_reuses_a_board_found_by_title_before_the_url_was_saved(tmp_path):
    gh = board_gh()
    gh.add_board("acme", 7, "widgets factory", COLUMNS)
    assert run_main(["init"], gh, root=tmp_path) == 0
    assert gh.option_updates == []
    assert gh.links == []
    assert not (tmp_path / "factory.toml").exists()


def test_init_never_rewrites_an_arranged_board(tmp_path, capsys):
    gh = board_gh()
    gh.add_board("acme", 3, "Roadmap", ["Backlog", "Doing", "Done"])
    (tmp_path / "factory.toml").write_text('project = "https://github.com/users/acme/projects/3"\n')
    assert run_main(["init"], gh, root=tmp_path) == 1
    assert gh.option_updates == []
    assert "lacks the columns" in capsys.readouterr().err


def test_init_skips_without_a_github_repository(tmp_path, capsys):
    gh = board_gh()
    gh.repo_error = "none of the git remotes configured for this repository point to GitHub"
    assert run_main(["init", "--skip-unavailable"], gh, root=tmp_path) == 0
    assert gh.created == []
    assert "skipped" in capsys.readouterr().err
    assert run_main(["init"], gh, root=tmp_path) == 1
    assert not (tmp_path / "factory.toml").exists()


def test_init_names_the_missing_project_scope(tmp_path, capsys):
    gh = board_gh()
    gh.project_error = "your authentication token is missing required scopes [read:project]"
    assert run_main(["init", "--skip-unavailable"], gh, root=tmp_path) == 0
    assert set(gh.created) == ALL_LABELS
    assert "gh auth refresh -s project" in capsys.readouterr().err


def test_init_without_board_creates_labels_only(tmp_path):
    gh = board_gh()
    assert run_main(["init", "--no-board"], gh, root=tmp_path) == 0
    assert gh.boards == {}
    assert set(gh.created) == ALL_LABELS


def test_sync_mirrors_open_issues_and_skips_conflicts(tmp_path):
    gh = board_gh()
    run_main(["init"], gh, root=tmp_path)
    gh.add_issue(5, ["factory:needs-human"])
    gh.add_issue(6, ["factory:ready", "factory:accepted"])
    assert run_main(["sync"], gh, root=tmp_path) == 0
    item = gh.items["https://github.com/acme/widgets/issues/5"]
    assert gh.item_values[item] == "O-Needs human"
    assert "https://github.com/acme/widgets/issues/6" not in gh.items


def test_sync_without_board_fails(tmp_path, capsys):
    assert run_main(["sync"], board_gh(), root=tmp_path) == 1
    assert "run init first" in capsys.readouterr().err


def test_every_state_has_a_board_column():
    assert set(factory.COLUMN_OF) == set(State)
    assert COLUMNS[-1] == "Done"


def test_a_fork_finds_its_own_board_by_title(tmp_path):
    gh = board_gh()
    gh.add_board("upstream", 1, "widgets factory", COLUMNS)
    gh.repo = {"owner": "forker", "name": "forker/widgets"}
    assert run_main(["init"], gh, root=tmp_path) == 0
    assert [b["title"] for b in gh.boards["forker"]] == ["widgets factory"]
    assert gh.links == [("forker", 2, "forker/widgets")]
    assert len(gh.boards["upstream"]) == 1


def test_project_calls_pass_values_as_arguments():
    created = json.dumps({"id": "P1", "number": 4, "title": "t", "url": "u"})
    gh = RecordingGitHub([created, ""])
    gh.create_project("acme", "x; rm -rf /")
    gh.link_project("acme", 4, "acme/widgets")
    assert gh.calls[0][0][-3:] == ["x; rm -rf /", "--format", "json"]
    assert gh.calls[1][0] == ["project", "link", "4", "--owner", "acme", "--repo", "acme/widgets"]


def test_field_options_go_through_graphql_input():
    gh = RecordingGitHub(["{}"])
    gh.set_field_options("F1", [{"name": "Ready", "color": "BLUE", "description": "d"}])
    args, sent = gh.calls[0]
    assert args == ["api", "graphql", "--input", "-"]
    body = json.loads(sent)
    assert "updateProjectV2Field" in body["query"]
    assert body["variables"] == {
        "field": "F1",
        "options": [{"name": "Ready", "color": "BLUE", "description": "d"}],
    }


def test_init_skip_also_covers_refused_writes(tmp_path, capsys):
    class ReadOnly(FakeGitHub):
        def create_label(self, name, color, description):
            raise factory.GhError("HTTP 403: Resource not accessible by integration")

    gh = ReadOnly()
    gh.error_type = factory.GhError
    assert run_main(["init", "--skip-unavailable"], gh, root=tmp_path) == 0
    assert "403" in capsys.readouterr().err
    assert run_main(["init"], gh, root=tmp_path) == 1


# --- board views ---

VIEW_NAMES = [name for name, _, _, _ in factory.BOARD_VIEWS]


def test_init_adds_the_kanban_views(tmp_path, capsys):
    gh = board_gh()
    assert run_main(["init"], gh, root=tmp_path) == 0
    views = gh.views[gh.boards["acme"][0]["id"]]
    assert [v["name"] for v in views] == VIEW_NAMES
    board, backlog, attention = views
    assert board["id"].endswith("-1"), "the default view becomes the board"
    assert board["layout"] == "BOARD_LAYOUT"
    assert {"F-Labels", "F-Linked pull requests"} <= set(board["f"])
    assert backlog["query"] == 'status:Ready,"Needs info",Accepted'
    assert "Needs human" in attention["query"]
    assert "Board, Backlog, Needs attention" in capsys.readouterr().out


def test_kanban_board_has_no_sprint_fields():
    shown = {field for _, _, _, fields in factory.BOARD_VIEWS for field in fields}
    assert not {"Sprint", "Iteration", "Estimate"} & shown


def test_views_are_idempotent_and_never_touch_arranged_views(tmp_path):
    gh = board_gh()
    gh.add_board("acme", 4, "widgets factory", COLUMNS)
    project = gh.boards["acme"][0]["id"]
    mine = {"id": "Vx", "name": "Mine", "layout": "TABLE_LAYOUT", "query": "x", "f": []}
    gh.views[project] = [dict(mine)]
    assert run_main(["init"], gh, root=tmp_path) == 0
    assert gh.views[project][0] == mine
    assert [v["name"] for v in gh.views[project]] == ["Mine", *VIEW_NAMES]
    assert run_main(["init"], gh, root=tmp_path) == 0
    assert len(gh.views[project]) == 1 + len(VIEW_NAMES)


def test_graphql_errors_raise():
    gh = RecordingGitHub([json.dumps({"errors": [{"message": "bad view"}]})])
    with pytest.raises(factory.GhError, match="bad view"):
        gh.project_views("P1")


def test_view_calls_send_typed_variables_on_stdin():
    gh = RecordingGitHub(
        [json.dumps({"data": {"createProjectV2View": {"projectV2View": {"id": "V9"}}}}), "{}"]
    )
    assert gh.create_view("P1", "Board", "BOARD_LAYOUT", ["F1", "F2"]) == "V9"
    gh.update_view("V9", "Board", "BOARD_LAYOUT", "-status:Done", ["F1"])
    for args, _sent in gh.calls:
        assert args == ["api", "graphql", "--input", "-"]
    created = json.loads(gh.calls[0][1])["variables"]
    assert created == {"p": "P1", "n": "Board", "l": "BOARD_LAYOUT", "f": ["F1", "F2"]}
    assert json.loads(gh.calls[1][1])["variables"]["q"] == "-status:Done"
