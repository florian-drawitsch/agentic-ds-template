import json
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from tests.factory_fakes import CONTROLLER, FakeGitHub
from tests.helpers import load_script

SCRIPT = Path("plugins/factory/skills/factory/scripts/factory.py")
factory = load_script(SCRIPT, "factory")
State = factory.State
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
    for name in ("status", "tick", "labels"):
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
    assert config.sprint_start is None


def test_config_overrides(tmp_path):
    (tmp_path / "factory.toml").write_text(
        'max_verified = 5\nsprint_start = 2026-11-01\ntrusted_bots = ["renovate[bot]"]\n'
        'daily_cap_usd = 7\n[deliver]\nharness = "codex"\nmax_cost = 4\n'
    )
    config = factory.load_config(tmp_path)
    assert config.max_verified == 5
    assert config.sprint_start == date(2026, 11, 1)
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
    "text", ["max_verified = 'x'", "max_in_progress = true", "sprint_start = 5"]
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
    assert len(gh.created) == 7
    assert run_main(["labels"], gh) == 0
    assert len(gh.created) == 7


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


def test_repair_stops_at_cycle_limit():
    snap = build(
        {"in_review": [issue(1)]}, pulls={1: pull(11, changes=True, passed=False)}, repairs={1: 2}
    )
    assert act(snap).kind == "idle"


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


@pytest.fixture
def clone(tmp_path):
    def git(cwd, *args):
        subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)

    bare = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(bare)], check=True, capture_output=True
    )
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", str(bare), str(seed)], check=True, capture_output=True)
    git(seed, "config", "user.email", "a@b.c")
    git(seed, "config", "user.name", "a")
    (seed / "f").write_text("x")
    git(seed, "add", "f")
    git(seed, "commit", "-m", "init")
    git(seed, "push", "origin", "HEAD:main")
    clones = []
    for name in ("one", "two"):
        path = tmp_path / name
        subprocess.run(["git", "clone", str(bare), str(path)], check=True, capture_output=True)
        clones.append(path)
    return clones


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
    assert factory.spend(snap, NOW, config).sprint == 12.0


def test_sprint_boundary():
    config = cfg(sprint_start=date(2026, 11, 4), sprint_days=14, sprint_cap_usd=50.0)
    before = datetime(2026, 11, 17, 23, 59, tzinfo=UTC)
    after = datetime(2026, 11, 18, 0, 0, tzinfo=UTC)
    snap = build({}, costs=[(before, 40.0), (after, 5.0)])
    assert factory.spend(snap, NOW, config).sprint == 5.0
    assert factory.spend(snap, before, config).sprint == 40.0


def test_cap_reached_is_idle_with_cap_name():
    snap = build({"accepted": [issue(1)]}, costs=[(NOW, 25.0)])
    assert "daily_cap_usd" in act(snap).reason
    old = NOW - timedelta(days=3)
    snap = build({"accepted": [issue(1)]}, costs=[(old, 150.0)])
    result = act(snap)
    assert result.kind == "idle"
    assert "sprint_cap_usd" in result.reason


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


def test_tick_agent_action_exits_3(capsys):
    assert run_main(["tick"], seeded()) == 3
    assert "agent stages arrive in S2" in capsys.readouterr().err


def test_tick_idle_exits_0(capsys):
    assert run_main(["tick"], FakeGitHub()) == 0
    assert "idle" in capsys.readouterr().out


def test_tick_implement_claims_and_relabels(clone, capsys):
    gh = FakeGitHub()
    gh.add_issue(7, ["factory:accepted"], title="Add a thing")
    assert run_main(["tick"], gh, root=clone[0]) == 0
    assert gh.issues[7]["labels"] == ["factory:in-progress"]
    assert run_main(["tick", "--dry-run"], gh, root=clone[0]) == 0


def test_lost_claim_changes_no_labels(clone, capsys):
    assert factory.claim(clone[1], 7, "Add a thing")
    gh = FakeGitHub()
    gh.add_issue(7, ["factory:accepted"], title="Add a thing")
    assert run_main(["tick"], gh, root=clone[0]) == 0
    assert gh.issues[7]["labels"] == ["factory:accepted"]
    assert "claim lost" in capsys.readouterr().err


def test_github_error_exits_1(capsys):
    class Broken(FakeGitHub):
        def viewer(self):
            raise factory.GhError("no auth")

    assert run_main(["status"], Broken()) == 1
    assert "no auth" in capsys.readouterr().err
