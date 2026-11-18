#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Deterministic controller core of the ticket factory (ADR-0033).

Commands:
    status    Show counts per state, unadmitted issues, spend, caps and the next action.
    tick      Print (--dry-run) or take the next action.
    labels    Create the factory labels that are missing.
    init      Create the labels and the board, record the board in factory.toml, mirror issues.
    sync      Mirror the label state of open issues into the board's Status field.

All state lives in GitHub issue labels and comments. No model chooses work, and issue text
never reaches a shell.
"""

from __future__ import annotations

import argparse
import enum
import fnmatch
import json
import re
import subprocess
import sys
import tomllib
import unicodedata
import urllib.parse
import uuid
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

VERSION = "0.1.0"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_IMPLEMENTED = 3

REPAIR_MARKER = "<!-- factory:repair -->"
COST_MARKER = re.compile(r"<!-- factory:cost (\{.*?\}) -->")
WRITE_PERMISSIONS = frozenset({"admin", "maintain", "write"})
PRIORITY_RANK = {"priority:high": 0, "priority:medium": 1, "priority:low": 2}

DEFAULT_PROTECTED = (
    "AGENTS.md",
    "CLAUDE.md",
    "factory.toml",
    "deliver.toml",
    ".claude/",
    ".codex/",
    "scripts/agent/",
    ".github/",
    "infra/",
)


# --- configuration -----------------------------------------------------------------------


@dataclass(frozen=True)
class DeliverConfig:
    """The `[deliver]` table: settings passed to the delivery runs."""

    harness: str | None = None
    reviewer: str | None = None
    max_cost: float | None = None


@dataclass(frozen=True)
class Config:
    """Controller settings from `factory.toml`."""

    max_in_progress: int = 1
    max_verified: int = 3
    max_repair_cycles: int = 2
    review_wait_minutes: int = 30
    daily_cap_usd: float = 20.0
    sprint_cap_usd: float = 100.0
    sprint_start: date | None = None
    sprint_days: int = 14
    protected_paths: tuple[str, ...] = DEFAULT_PROTECTED
    trusted_bots: tuple[str, ...] = ()
    controller: str | None = None
    required_checks: tuple[str, ...] = ()
    project: str | None = None
    holdout: str | None = None
    deliver: DeliverConfig = field(default_factory=DeliverConfig)


def _check(key: str, value: Any, kind: str) -> Any:
    """Return the value converted to the declared kind or exit naming the key."""
    ok = False
    if kind == "int":
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif kind == "float":
        ok = isinstance(value, int | float) and not isinstance(value, bool)
        value = float(value) if ok else value
    elif kind == "str":
        ok = isinstance(value, str)
    elif kind == "strs":
        ok = isinstance(value, list) and all(isinstance(v, str) for v in value)
        value = tuple(value) if ok else value
    elif kind == "date":
        if isinstance(value, datetime):
            ok = False
        elif isinstance(value, date):
            ok = True
        elif isinstance(value, str):
            try:
                value, ok = date.fromisoformat(value), True
            except ValueError:
                ok = False
    if not ok:
        raise SystemExit(f"factory.toml: wrong type for '{key}' (expected {kind})")
    return value


_KINDS = {
    "max_in_progress": "int",
    "max_verified": "int",
    "max_repair_cycles": "int",
    "review_wait_minutes": "int",
    "daily_cap_usd": "float",
    "sprint_cap_usd": "float",
    "sprint_start": "date",
    "sprint_days": "int",
    "protected_paths": "strs",
    "trusted_bots": "strs",
    "controller": "str",
    "required_checks": "strs",
    "project": "str",
    "holdout": "str",
}
_DELIVER_KINDS = {"harness": "str", "reviewer": "str", "max_cost": "float"}


def load_config(root: Path) -> Config:
    """Read `factory.toml` under root; a missing file gives the defaults."""
    path = root / "factory.toml"
    if not path.is_file():
        return Config()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise SystemExit(f"factory.toml: {exc}") from exc
    values: dict[str, Any] = {}
    for key, value in raw.items():
        if key == "deliver":
            if not isinstance(value, dict):
                raise SystemExit("factory.toml: wrong type for 'deliver' (expected table)")
            values["deliver"] = _deliver_config(value)
        elif key in _KINDS:
            values[key] = _check(key, value, _KINDS[key])
        else:
            raise SystemExit(f"factory.toml: unknown key '{key}'")
    return Config(**values)


def _deliver_config(table: dict[str, Any]) -> DeliverConfig:
    values = {}
    for key, value in table.items():
        if key not in _DELIVER_KINDS:
            raise SystemExit(f"factory.toml: unknown key 'deliver.{key}'")
        values[key] = _check(f"deliver.{key}", value, _DELIVER_KINDS[key])
    return DeliverConfig(**values)


# --- label states ------------------------------------------------------------------------


class State(enum.Enum):
    """The seven factory states; the value is the issue label."""

    READY = "factory:ready"
    ACCEPTED = "factory:accepted"
    NEEDS_INFO = "factory:needs-info"
    IN_PROGRESS = "factory:in-progress"
    IN_REVIEW = "factory:in-review"
    VERIFIED = "factory:verified"
    NEEDS_HUMAN = "factory:needs-human"


TRANSITIONS: dict[State, frozenset[State]] = {
    State.READY: frozenset({State.ACCEPTED, State.NEEDS_INFO}),
    State.NEEDS_INFO: frozenset({State.READY}),
    State.ACCEPTED: frozenset({State.IN_PROGRESS}),
    State.IN_PROGRESS: frozenset({State.IN_REVIEW, State.NEEDS_HUMAN}),
    State.IN_REVIEW: frozenset({State.IN_REVIEW, State.VERIFIED, State.NEEDS_HUMAN}),
    State.VERIFIED: frozenset(),
    State.NEEDS_HUMAN: frozenset(),
}

LABEL_STYLE: dict[State, tuple[str, str]] = {
    State.READY: ("2f81f7", "Factory: a maintainer released this ticket"),
    State.ACCEPTED: ("8b949e", "Factory: triaged and queued for implementation"),
    State.NEEDS_INFO: ("e5534b", "Factory: triage needs more information"),
    State.IN_PROGRESS: ("8b949e", "Factory: claimed, implementation running"),
    State.IN_REVIEW: ("8b949e", "Factory: pull request open and under review"),
    State.VERIFIED: ("2f81f7", "Factory: all checks passed, waiting for a human merge"),
    State.NEEDS_HUMAN: ("e5534b", "Factory: stopped, a human must decide"),
}

_BY_LABEL = {s.value: s for s in State}


def state_of(labels: Sequence[str]) -> State | None:
    """Return the single factory state in labels, None if there is none."""
    found = {_BY_LABEL[name] for name in labels if name in _BY_LABEL}
    if len(found) > 1:
        names = ", ".join(sorted(s.value for s in found))
        raise ValueError(f"more than one factory label: {names}")
    return next(iter(found), None)


def transition(current: State | None, target: State) -> State:
    """Return target if the state diagram allows it, otherwise raise ValueError."""
    if current is None:
        if target is State.READY:
            return target
    elif target in TRANSITIONS[current]:
        return target
    raise ValueError(
        f"transition not allowed: {current.value if current else None} -> {target.value}"
    )


def ensure_labels(github: GitHub) -> list[str]:
    """Create the factory labels that are missing and return their names."""
    existing = set(github.labels())
    created = []
    for state in State:
        if state.value not in existing:
            color, description = LABEL_STYLE[state]
            github.create_label(state.value, color, description)
            created.append(state.value)
    return created


# --- GitHub access -----------------------------------------------------------------------


class GhError(RuntimeError):
    """A `gh` call failed."""


class GitHub:
    """Thin wrapper over `gh api`. Arguments go to `gh` as a list, never through a shell."""

    def _call(self, args: Sequence[str], input: str | None = None) -> str:
        try:
            proc = subprocess.run(
                ["gh", *args],
                input=input,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            raise GhError(f"cannot run gh: {exc}") from exc
        if proc.returncode != 0:
            raise GhError(proc.stderr.strip() or f"gh exited with {proc.returncode}")
        return proc.stdout

    def _json(self, args: Sequence[str], input: str | None = None) -> Any:
        out = self._call(args, input)
        return json.loads(out) if out.strip() else None

    def _pages(self, path: str) -> list[Any]:
        sep = "&" if "?" in path else "?"
        pages = self._json(["api", "--paginate", "--slurp", f"{path}{sep}per_page=100"]) or []
        return [item for page in pages for item in (page if isinstance(page, list) else [page])]

    def _send(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        args = ["api", "--method", method, path]
        if body is None:
            return self._json(args)
        return self._json([*args, "--input", "-"], input=json.dumps(body))

    def graphql(self, query: str, **variables: str) -> Any:
        """Run a GraphQL query; variables are passed as fields, not interpolated."""
        args = ["api", "graphql", "-f", f"query={query}"]
        for key, value in variables.items():
            args += ["-f", f"{key}={value}"]
        return self._json(args)

    def viewer(self) -> str:
        """Return the login the controller acts as."""
        return self._json(["api", "user"])["login"]

    def labels(self) -> list[str]:
        """Return the names of the repository labels."""
        return [item["name"] for item in self._pages("repos/{owner}/{repo}/labels")]

    def create_label(self, name: str, color: str, description: str) -> None:
        """Create one label."""
        body = {"name": name, "color": color, "description": description}
        self._send("POST", "repos/{owner}/{repo}/labels", body)

    def issues_with_label(self, label: str) -> list[dict[str, Any]]:
        """Return issues (any state, no pull requests) that carry the label."""
        query = urllib.parse.urlencode({"labels": label, "state": "all"})
        items = self._pages(f"repos/{{owner}}/{{repo}}/issues?{query}")
        return [_issue(i) for i in items if "pull_request" not in i]

    def issue(self, number: int) -> dict[str, Any]:
        """Return one issue."""
        return _issue(self._json(["api", f"repos/{{owner}}/{{repo}}/issues/{int(number)}"]))

    def timeline(self, number: int) -> list[dict[str, Any]]:
        """Return the issue timeline events."""
        return self._pages(f"repos/{{owner}}/{{repo}}/issues/{int(number)}/timeline")

    def comments(self, number: int) -> list[dict[str, Any]]:
        """Return the issue comments."""
        return self._pages(f"repos/{{owner}}/{{repo}}/issues/{int(number)}/comments")

    def permission(self, user: str) -> str:
        """Return the repository permission of user, `none` for a non-collaborator."""
        path = f"repos/{{owner}}/{{repo}}/collaborators/{urllib.parse.quote(user)}/permission"
        try:
            return self._json(["api", path])["permission"]
        except GhError as exc:
            if "404" in str(exc):
                return "none"
            raise

    def pull_for_branch(self, prefix: str) -> dict[str, Any] | None:
        """Return the open same-repository pull request whose head branch starts with prefix."""
        for pull in self._pages("repos/{owner}/{repo}/pulls?state=open"):
            head_repo = (pull["head"].get("repo") or {}).get("full_name")
            base_repo = (pull["base"].get("repo") or {}).get("full_name")
            if head_repo and head_repo == base_repo and pull["head"]["ref"].startswith(prefix):
                return {
                    "number": pull["number"],
                    "branch": pull["head"]["ref"],
                    "sha": pull["head"]["sha"],
                }
        return None

    def reviews(self, pr: int) -> list[dict[str, Any]]:
        """Return the reviews of a pull request."""
        return self._pages(f"repos/{{owner}}/{{repo}}/pulls/{int(pr)}/reviews")

    def check_runs(self, sha: str) -> list[dict[str, Any]]:
        """Return the check runs of a commit."""
        path = f"repos/{{owner}}/{{repo}}/commits/{urllib.parse.quote(sha)}/check-runs"
        runs = self._json(["api", f"{path}?per_page=100"]) or {}
        return runs.get("check_runs", [])

    def changed_files(self, pr: int) -> list[str]:
        """Return paths touched by the pull request; a rename lists both paths."""
        paths = []
        for item in self._pages(f"repos/{{owner}}/{{repo}}/pulls/{int(pr)}/files"):
            paths.append(item["filename"])
            if item.get("previous_filename"):
                paths.append(item["previous_filename"])
        return paths

    def add_labels(self, number: int, labels: Sequence[str]) -> None:
        """Add labels to an issue."""
        self._send(
            "POST",
            f"repos/{{owner}}/{{repo}}/issues/{int(number)}/labels",
            {"labels": list(labels)},
        )

    def remove_label(self, number: int, label: str) -> None:
        """Remove one label from an issue."""
        name = urllib.parse.quote(label, safe="")
        self._send("DELETE", f"repos/{{owner}}/{{repo}}/issues/{int(number)}/labels/{name}")

    def comment(self, number: int, body: str) -> None:
        """Post a comment; the body goes through stdin, not argv."""
        self._send(
            "POST", f"repos/{{owner}}/{{repo}}/issues/{int(number)}/comments", {"body": body}
        )

    # Projects (the board). `gh project` needs the token scope `project`.

    def repository(self) -> dict[str, str]:
        """Return the owner login and the full name of the current repository."""
        raw = self._json(["api", "repos/{owner}/{repo}"])
        return {"owner": raw["owner"]["login"], "name": raw["full_name"]}

    def projects(self, owner: str) -> list[dict[str, Any]]:
        """Return the owner's projects with `id`, `number`, `title` and `url`."""
        data = self._json(["project", "list", "--owner", owner, "--format", "json"])
        return (data or {}).get("projects", [])

    def create_project(self, owner: str, title: str) -> dict[str, Any]:
        """Create a project and return it with `id`, `number`, `title` and `url`."""
        args = ["project", "create", "--owner", owner, "--title", title, "--format", "json"]
        return self._json(args)

    def link_project(self, owner: str, number: int, repo: str) -> None:
        """Link the project to the repository, so it shows in the repository's Projects tab."""
        self._call(["project", "link", str(int(number)), "--owner", owner, "--repo", repo])

    def project_fields(self, owner: str, number: int) -> list[dict[str, Any]]:
        """Return the project fields; single-select fields carry `options`."""
        args = ["project", "field-list", str(int(number)), "--owner", owner, "--format", "json"]
        return (self._json(args) or {}).get("fields", [])

    def set_field_options(self, field_id: str, options: Sequence[dict[str, str]]) -> None:
        """Replace the options of a single-select field."""
        body = {
            "query": UPDATE_FIELD_MUTATION,
            "variables": {"field": field_id, "options": list(options)},
        }
        self._json(["api", "graphql", "--input", "-"], input=json.dumps(body))

    def add_item(self, owner: str, number: int, url: str) -> str:
        """Add an issue to the project (a no-op when present) and return the item id."""
        args = ["project", "item-add", str(int(number)), "--owner", owner, "--url", url]
        return self._json([*args, "--format", "json"])["id"]

    def set_item_option(self, project_id: str, item_id: str, field_id: str, option: str) -> None:
        """Set a single-select field of one project item."""
        self._call(
            [
                "project",
                "item-edit",
                "--id",
                item_id,
                "--project-id",
                project_id,
                "--field-id",
                field_id,
                "--single-select-option-id",
                option,
            ]
        )


UPDATE_FIELD_MUTATION = """
mutation($field: ID!, $options: [ProjectV2SingleSelectFieldOptionInput!]) {
  updateProjectV2Field(input: {fieldId: $field, singleSelectOptions: $options}) {
    projectV2Field { ... on ProjectV2SingleSelectField { id } }
  }
}
""".strip()


def _issue(raw: dict[str, Any]) -> dict[str, Any]:
    labels = [x["name"] if isinstance(x, dict) else x for x in raw.get("labels", [])]
    return {
        "number": raw["number"],
        "title": raw.get("title", ""),
        "state": raw.get("state", "open"),
        "labels": labels,
        "url": raw.get("html_url", ""),
    }


# --- admission ---------------------------------------------------------------------------


def admitted(github: GitHub, issue: dict[str, Any], config: Config) -> bool:
    """Return True when the latest `factory:ready` label came from a trusted actor."""
    ready = [
        e
        for e in github.timeline(issue["number"])
        if e.get("event") == "labeled" and (e.get("label") or {}).get("name") == State.READY.value
    ]
    if not ready:
        return False
    actor = ready[-1].get("actor") or {}
    login = actor.get("login")
    if not login:
        return False
    if actor.get("type") == "Bot":
        return login in config.trusted_bots
    return github.permission(login) in WRITE_PERMISSIONS


# --- snapshot and dispatch ---------------------------------------------------------------


@dataclass(frozen=True)
class Pull:
    """A pull request linked to an issue, with the facts dispatch needs."""

    number: int
    branch: str
    changes_requested: bool
    checks_passed: bool


@dataclass(frozen=True)
class Snapshot:
    """Everything `next_action` reads, collected once."""

    states: dict[State, list[dict[str, Any]]]
    unadmitted: list[int]
    conflicts: list[int]
    pulls: dict[int, Pull]
    repairs: dict[int, int]
    costs: list[tuple[datetime, float]]


@dataclass(frozen=True)
class Action:
    """What the controller would do next."""

    kind: str
    issue: int | None
    reason: str


def Idle(reason: str) -> Action:
    """Return the action that means nothing to do."""
    return Action("idle", None, reason)


def _parse_costs(
    comments: Sequence[dict[str, Any]], controller: str
) -> list[tuple[datetime, float]]:
    costs = []
    for comment in comments:
        if (comment.get("user") or {}).get("login") != controller:
            continue
        for match in COST_MARKER.finditer(comment.get("body") or ""):
            try:
                data = json.loads(match.group(1))
                usd = data["usd"]
                at = datetime.fromisoformat(data["at"])
                if not isinstance(data["stage"], str) or isinstance(usd, bool):
                    continue
                if not isinstance(usd, int | float) or usd < 0:
                    continue
            except (ValueError, KeyError, TypeError):
                continue
            costs.append((at if at.tzinfo else at.replace(tzinfo=UTC), float(usd)))
    return costs


def _pull_facts(github: GitHub, pr: dict[str, Any], required_checks: Sequence[str]) -> Pull:
    latest: dict[str, str] = {}
    for review in sorted(github.reviews(pr["number"]), key=lambda r: r.get("submitted_at") or ""):
        if review.get("state") in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
            latest[(review.get("user") or {}).get("login", "")] = review["state"]
    runs = github.check_runs(pr["sha"])
    failed = any(
        r.get("conclusion") in {"failure", "timed_out", "cancelled", "action_required"}
        for r in runs
    )
    done = {r.get("name"): r.get("conclusion") == "success" for r in runs}
    if required_checks:
        passed = all(done.get(name) is True for name in required_checks)
    else:
        passed = bool(runs) and all(done.values()) and len(done) == len(runs)
    return Pull(
        number=pr["number"],
        branch=pr["branch"],
        changes_requested="CHANGES_REQUESTED" in latest.values() or failed,
        checks_passed=passed,
    )


def snapshot(github: GitHub, config: Config) -> Snapshot:
    """Collect factory issues per state, linked pull requests, repair counts and spend."""
    controller = config.controller or github.viewer()
    seen: dict[int, dict[str, Any]] = {}
    for state in State:
        for issue in github.issues_with_label(state.value):
            seen[issue["number"]] = issue
    states: dict[State, list[dict[str, Any]]] = {s: [] for s in State}
    unadmitted: list[int] = []
    conflicts: list[int] = []
    pulls: dict[int, Pull] = {}
    repairs: dict[int, int] = {}
    costs: list[tuple[datetime, float]] = []
    for number, issue in sorted(seen.items()):
        comments = github.comments(number)
        costs += _parse_costs(comments, controller)
        if issue["state"] != "open":
            continue
        try:
            state = state_of(issue["labels"])
        except ValueError:
            conflicts.append(number)
            continue
        if state is None:
            continue
        if state is State.READY and not admitted(github, issue, config):
            unadmitted.append(number)
            continue
        states[state].append(issue)
        repairs[number] = sum(
            REPAIR_MARKER in (c.get("body") or "")
            for c in comments
            if (c.get("user") or {}).get("login") == controller
        )
        if state is State.IN_REVIEW:
            pr = github.pull_for_branch(f"feat/{number}-")
            if pr:
                pulls[number] = _pull_facts(github, pr, config.required_checks)
    return Snapshot(states, unadmitted, conflicts, pulls, repairs, costs)


@dataclass(frozen=True)
class Spend:
    """Spend in US dollars for the current UTC day and sprint."""

    day: float
    sprint: float


def sprint_window(config: Config, now: datetime) -> tuple[datetime, datetime]:
    """Return the sprint containing now; without `sprint_start` the last `sprint_days` days."""
    now = now.astimezone(UTC)
    if config.sprint_start is None:
        return now - timedelta(days=config.sprint_days), now + timedelta(microseconds=1)
    start = datetime.combine(config.sprint_start, datetime.min.time(), UTC)
    length = timedelta(days=config.sprint_days)
    index = max((now - start) // length, 0)
    return start + index * length, start + (index + 1) * length


def spend(snap: Snapshot, now: datetime, config: Config) -> Spend:
    """Return today's and the current sprint's totals in UTC."""
    now = now.astimezone(UTC)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    low, high = sprint_window(config, now)
    day = sum(usd for at, usd in snap.costs if day_start <= at < day_start + timedelta(days=1))
    sprint = sum(usd for at, usd in snap.costs if low <= at < high)
    return Spend(day, sprint)


def record_cost(
    github: GitHub, issue: int, stage: str, usd: float, now: datetime | None = None
) -> None:
    """Write one cost marker comment on the ticket."""
    at = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
    payload = json.dumps({"stage": stage, "usd": usd, "at": at})
    github.comment(issue, f"<!-- factory:cost {payload} -->")


def _order(issues: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(issue: dict[str, Any]) -> tuple[int, int]:
        rank = min((PRIORITY_RANK[x] for x in issue["labels"] if x in PRIORITY_RANK), default=3)
        return rank, issue["number"]

    return sorted(issues, key=key)


def next_action(snap: Snapshot, config: Config, now: datetime) -> Action:
    """Choose one action: repair, verify, implement, triage, or Idle."""
    used = spend(snap, now, config)
    if used.day >= config.daily_cap_usd:
        return Idle(f"daily_cap_usd reached ({used.day:.2f} of {config.daily_cap_usd:.2f})")
    if used.sprint >= config.sprint_cap_usd:
        return Idle(f"sprint_cap_usd reached ({used.sprint:.2f} of {config.sprint_cap_usd:.2f})")

    in_review = _order(snap.states[State.IN_REVIEW])
    for issue in in_review:
        pull = snap.pulls.get(issue["number"])
        if (
            pull
            and pull.changes_requested
            and snap.repairs.get(issue["number"], 0) < config.max_repair_cycles
        ):
            return Action("repair", issue["number"], "review findings or failing checks")
    for issue in in_review:
        pull = snap.pulls.get(issue["number"])
        if pull and pull.checks_passed and not pull.changes_requested:
            return Action("verify", issue["number"], "all required checks passed")

    in_progress = len(snap.states[State.IN_PROGRESS])
    verified = len(snap.states[State.VERIFIED])
    accepted = _order(snap.states[State.ACCEPTED])
    if accepted and in_progress < config.max_in_progress and verified < config.max_verified:
        return Action("implement", accepted[0]["number"], "accepted, within limits")
    ready = _order(snap.states[State.READY])
    if ready:
        return Action("triage", ready[0]["number"], "admitted and ready")
    if accepted:
        limit = (
            f"max_in_progress ({config.max_in_progress})"
            if in_progress >= config.max_in_progress
            else f"max_verified ({config.max_verified})"
        )
        return Idle(f"{limit} reached")
    return Idle("no open work")


# --- claim -------------------------------------------------------------------------------


def slug(title: str) -> str:
    """Return lowercase ASCII words joined by hyphens, at most 40 characters."""
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    words = re.findall(r"[a-z0-9]+", ascii_title.lower())
    return "-".join(words)[:40].strip("-") or "issue"


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def claim(root: Path, issue: int, title: str) -> bool:
    """Push `feat/<issue>-<slug>` at the default branch without force; False if it lost."""
    branch = f"feat/{int(issue)}-{slug(title)}"
    if _git(root, "fetch", "origin").returncode != 0:
        return False
    head = _git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    base = head.stdout.strip() if head.returncode == 0 else "origin/main"
    taken = _git(root, "ls-remote", "--heads", "origin", f"refs/heads/feat/{int(issue)}-*")
    if taken.returncode != 0 or taken.stdout.strip():
        return False
    # The lock commit is unique per caller, so a create-only push of it cannot succeed as a no-op
    # when the lock exists. An empty expected value makes the server refuse an existing ref.
    tree = _git(root, "rev-parse", f"{base}^{{tree}}")
    if tree.returncode != 0:
        return False
    nonce = f"factory claim #{int(issue)} {uuid.uuid4()}"
    identity = ["-c", "user.name=factory", "-c", "user.email=factory@localhost"]
    made = _git(root, *identity, "commit-tree", tree.stdout.strip(), "-p", base, "-m", nonce)
    if made.returncode != 0:
        return False
    lock = f"refs/factory/claims/{int(issue)}"
    locked = _git(
        root, "push", f"--force-with-lease={lock}:", "origin", f"{made.stdout.strip()}:{lock}"
    )
    if locked.returncode != 0:
        return False
    return _git(root, "push", "origin", f"{base}:refs/heads/{branch}").returncode == 0


# --- protected paths ---------------------------------------------------------------------


def touches_protected(files: Sequence[str], patterns: Sequence[str]) -> list[str]:
    """Return the files that match a protected pattern."""
    hits = []
    for name in files:
        path = name.replace("\\", "/")
        for pattern in patterns:
            if pattern.endswith("/"):
                match = path.startswith(pattern)
            elif pattern == "AGENTS.md":
                match = path == pattern or path.endswith("/AGENTS.md")
            else:
                match = fnmatch.fnmatchcase(path, pattern)
            if match:
                hits.append(name)
                break
    return hits


# --- board -------------------------------------------------------------------------------

STATUS_FIELD = "Status"
GITHUB_DEFAULT_STATUS = ["Todo", "In Progress", "Done"]
# Board columns in workflow order: option name, color, description and the mirrored state.
# "Done" has no label: GitHub's built-in "Item closed" workflow moves closed issues there.
BOARD_COLUMNS: tuple[tuple[str, str, str, State | None], ...] = (
    ("Ready", "BLUE", "A maintainer released the ticket", State.READY),
    ("Needs info", "RED", "Triage needs more information", State.NEEDS_INFO),
    ("Accepted", "GRAY", "Triaged and queued for implementation", State.ACCEPTED),
    ("In progress", "YELLOW", "Claimed, implementation running", State.IN_PROGRESS),
    ("In review", "PURPLE", "Pull request open and under review", State.IN_REVIEW),
    ("Verified", "GREEN", "All checks passed, waiting for a human merge", State.VERIFIED),
    ("Needs human", "ORANGE", "Stopped, a human must decide", State.NEEDS_HUMAN),
    ("Done", "GREEN", "Closed", None),
)
COLUMN_OF = {state: name for name, _, _, state in BOARD_COLUMNS if state is not None}
PROJECT_URL = re.compile(r"https://github\.com/(?:orgs|users)/([^/\s]+)/projects/(\d+)/?")


class Unavailable(RuntimeError):
    """GitHub cannot serve this step yet; the message says what to fix."""


@dataclass(frozen=True)
class Board:
    """A GitHub project that mirrors the factory states in its Status field."""

    owner: str
    number: int
    id: str
    url: str


def board_title(repo: str) -> str:
    """Return the title of the board init creates for `owner/name`."""
    return f"{repo.split('/')[-1]} factory"


def _status_field(github: GitHub, board: Board) -> dict[str, Any]:
    for item in github.project_fields(board.owner, board.number):
        if item.get("name") == STATUS_FIELD and "options" in item:
            return item
    raise ValueError(f"project {board.url} has no single-select field '{STATUS_FIELD}'")


def find_board(
    github: GitHub, config: Config, repo: dict[str, str], create: bool = False
) -> tuple[Board | None, bool]:
    """Return the configured board, else the one titled after the repository.

    With create, a missing board is created and linked to the repository. The second value
    tells whether it was created.
    """
    owner = repo["owner"]
    if config.project:
        match = PROJECT_URL.fullmatch(config.project)
        if not match:
            raise ValueError("factory.toml: 'project' must be a GitHub project URL")
        owner, number = match.group(1), int(match.group(2))
        found = next((p for p in github.projects(owner) if p["number"] == number), None)
        if found is None:
            raise ValueError(f"project {config.project} does not exist or is not accessible")
    else:
        title = board_title(repo["name"])
        found = next((p for p in github.projects(owner) if p["title"] == title), None)
        if found is None and create:
            found = github.create_project(owner, title)
            github.link_project(owner, found["number"], repo["name"])
            board = Board(owner, int(found["number"]), found["id"], found["url"])
            return board, True
    if found is None:
        return None, False
    return Board(owner, int(found["number"]), found["id"], found["url"]), False


def ensure_columns(github: GitHub, board: Board, created: bool) -> bool:
    """Give the Status field the factory columns; return True when it changed.

    Options are replaced only on a new board or one with GitHub's default options, so a
    board someone already arranged is never rewritten.
    """
    field = _status_field(github, board)
    names = [o["name"] for o in field["options"]]
    wanted = [name for name, _, _, _ in BOARD_COLUMNS]
    missing = [name for name in wanted if name not in names]
    if not missing:
        return False
    if not created and names != GITHUB_DEFAULT_STATUS:
        raise ValueError(
            f"Status field of {board.url} lacks the columns {', '.join(missing)}; add them"
        )
    options = [{"name": n, "color": c, "description": d} for n, c, d, _ in BOARD_COLUMNS]
    github.set_field_options(field["id"], options)
    return True


def factory_issues(github: GitHub) -> list[dict[str, Any]]:
    """Return every issue with a factory label, once each, by number."""
    seen: dict[int, dict[str, Any]] = {}
    for state in State:
        for issue in github.issues_with_label(state.value):
            seen[issue["number"]] = issue
    return [seen[number] for number in sorted(seen)]


def sync_board(github: GitHub, board: Board, issues: Sequence[dict[str, Any]]) -> int:
    """Mirror the label state of open issues into the board's Status field."""
    field = _status_field(github, board)
    options = {o["name"]: o["id"] for o in field["options"]}
    mirrored = 0
    for issue in issues:
        try:
            state = state_of(issue["labels"])
        except ValueError:
            continue
        if state is None or issue["state"] != "open" or not issue.get("url"):
            continue
        column = COLUMN_OF[state]
        if column not in options:
            raise ValueError(f"Status field of {board.url} lacks the column {column}")
        item = github.add_item(board.owner, board.number, issue["url"])
        github.set_item_option(board.id, item, field["id"], options[column])
        mirrored += 1
    return mirrored


def write_project(root: Path, url: str) -> None:
    """Set the top-level `project` key of `factory.toml`, creating the file if needed."""
    path = root / "factory.toml"
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    line = f'project = "{url}"'
    header = re.search(r"^\[", text, re.MULTILINE)
    top, rest = (text[: header.start()], text[header.start() :]) if header else (text, "")
    key = re.compile(r"^project\s*=.*$", re.MULTILINE)
    if key.search(top):
        top = key.sub(lambda _: line, top, count=1)
    else:
        top = top + ("\n" if top and not top.endswith("\n") else "") + line + "\n"
        top += "\n" if rest else ""
    path.write_text(top + rest, encoding="utf-8")
    load_config(root)


# --- commands ----------------------------------------------------------------------------


def _describe(action: Action) -> str:
    target = f" #{action.issue}" if action.issue else ""
    return f"{action.kind}{target}: {action.reason}"


def _status(github: GitHub, config: Config, now: datetime, as_json: bool) -> int:
    snap = snapshot(github, config)
    used = spend(snap, now, config)
    action = next_action(snap, config, now)
    counts = {s.value: len(snap.states[s]) for s in State}
    data = {
        "counts": counts,
        "unadmitted": snap.unadmitted,
        "conflicts": snap.conflicts,
        "spend": {
            "day": used.day,
            "daily_cap_usd": config.daily_cap_usd,
            "sprint": used.sprint,
            "sprint_cap_usd": config.sprint_cap_usd,
        },
        "next_action": asdict(action),
    }
    if as_json:
        print(json.dumps(data, indent=2))
        return EXIT_OK
    for name, count in counts.items():
        print(f"{name}: {count}")
    print(f"unadmitted: {snap.unadmitted or 'none'}")
    if snap.conflicts:
        print(f"conflicting labels: {snap.conflicts}")
    print(f"spend today: {used.day:.2f} of {config.daily_cap_usd:.2f} USD")
    print(f"spend sprint: {used.sprint:.2f} of {config.sprint_cap_usd:.2f} USD")
    print(f"next: {_describe(action)}")
    return EXIT_OK


def _tick(
    github: GitHub, config: Config, root: Path, now: datetime, dry_run: bool, as_json: bool
) -> int:
    action = next_action(snapshot(github, config), config, now)
    if as_json:
        print(json.dumps(asdict(action)))
    else:
        print(_describe(action))
    if dry_run or action.kind == "idle":
        return EXIT_OK
    if action.kind != "implement":
        print("agent stages arrive in S2", file=sys.stderr)
        return EXIT_NOT_IMPLEMENTED
    assert action.issue is not None
    title = github.issue(action.issue)["title"]
    if not claim(root, action.issue, title):
        print(f"claim lost for #{action.issue}", file=sys.stderr)
        return EXIT_OK
    print(f"claimed #{action.issue}; labels and agent stages arrive in S2", file=sys.stderr)
    return EXIT_OK


def _repository(github: GitHub) -> dict[str, str]:
    try:
        return github.repository()
    except GhError as exc:
        reason = str(exc).splitlines()[0] if str(exc) else "gh failed"
        raise Unavailable(f"no GitHub repository for this checkout yet ({reason})") from exc


def _board_step(exc: GhError) -> Exception:
    """Turn a missing token scope into an actionable Unavailable, keep other errors."""
    if "scope" in str(exc).lower():
        return Unavailable("the gh token lacks the project scope; run `gh auth refresh -s project`")
    return exc


def _init(github: GitHub, config: Config, root: Path, board: bool) -> int:
    repo = _repository(github)
    created = ensure_labels(github)
    print(f"labels: {repo['name']}, created " + (", ".join(created) or "none"))
    if not board:
        return EXIT_OK
    try:
        found, new = find_board(github, config, repo, create=True)
        assert found is not None
        changed = ensure_columns(github, found, new)
        mirrored = sync_board(github, found, factory_issues(github))
    except GhError as exc:
        raise _board_step(exc) from exc
    if config.project != found.url:
        write_project(root, found.url)
        print(f"factory.toml: project = {found.url}")
    columns = "columns set" if changed else "columns present"
    print(f"board: {found.url} ({'created' if new else 'found'}, {columns})")
    print(f"mirrored: {mirrored} issues")
    return EXIT_OK


def _sync(github: GitHub, config: Config) -> int:
    repo = _repository(github)
    try:
        found, _ = find_board(github, config, repo)
        if found is None:
            print("no factory board; run init first", file=sys.stderr)
            return EXIT_ERROR
        mirrored = sync_board(github, found, factory_issues(github))
    except GhError as exc:
        raise _board_step(exc) from exc
    print(f"mirrored: {mirrored} issues to {found.url}")
    return EXIT_OK


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="factory.py", description=__doc__.split("\n\n")[0])
    parser.add_argument("--version", action="version", version=VERSION)
    sub = parser.add_subparsers(dest="command", required=True)
    status = sub.add_parser("status", help="show counts, spend, caps and the next action")
    status.add_argument("--json", action="store_true")
    tick = sub.add_parser("tick", help="take or print the next action")
    tick.add_argument("--dry-run", action="store_true", help="print the action, change nothing")
    tick.add_argument("--json", action="store_true")
    sub.add_parser("labels", help="create the missing factory labels")
    init = sub.add_parser(
        "init", help="create the labels and the board, record it in factory.toml, mirror issues"
    )
    init.add_argument("--no-board", action="store_true", help="create the labels only")
    init.add_argument(
        "--skip-unavailable",
        action="store_true",
        help="exit 0 with a note when GitHub refuses a step (no repository, scope or access)",
    )
    sub.add_parser("sync", help="mirror the label state of open issues into the board")
    return parser


def main(
    argv: Sequence[str] | None = None,
    github: GitHub | None = None,
    root: Path | None = None,
    now: datetime | None = None,
) -> int:
    """Run the command line; github, root and now are injected by tests."""
    args = _parser().parse_args(argv)
    root = root or Path.cwd()
    now = now or datetime.now(UTC)
    try:
        config = load_config(root)
        github = github or GitHub()
        if args.command == "labels":
            created = ensure_labels(github)
            print("created: " + (", ".join(created) or "none"))
            return EXIT_OK
        if args.command == "init":
            return _init(github, config, root, not args.no_board)
        if args.command == "sync":
            return _sync(github, config)
        if args.command == "status":
            return _status(github, config, now, args.json)
        return _tick(github, config, root, now, args.dry_run, args.json)
    except (Unavailable, GhError) as exc:
        # Setup and `copier copy` pass --skip-unavailable: GitHub must never fail them.
        if getattr(args, "skip_unavailable", False):
            print(f"factory init skipped: {exc}. Rerun init once that is fixed.", file=sys.stderr)
            return EXIT_OK
        print(f"factory: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except ValueError as exc:
        print(f"factory: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
