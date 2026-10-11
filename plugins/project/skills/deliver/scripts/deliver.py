#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Deliver an implementation plan end to end with Claude Code or Codex.

Commands:
    start     Begin a delivery run for a plan and print its token.
    status    Show the delivery runs of this worktree.
    block     Release the guard because a decision only a human can make blocks progress.
    stop      Cancel a delivery run.
    hook      Stop-hook entry point: keep the agent working until the plan is delivered.
    install   Add the guard hook and the implementer and reviewer roles to a repository.
    doctor    Check that the guard hook and the roles are installed.
    headless  Run a plan unattended, by default in a new git worktree.

A run is delivered when every plan checkbox is ticked, the check command passes and, if
requested, a cross-vendor review of the current state passed. Headless runs survive usage and
rate limits: a round cut off by a limit is not counted, and the run waits until the limit resets
(or backs off exponentially when no reset time is known), then resumes the same session.

Run state lives in the git directory of the worktree, so it is never committed and each worktree
has its own runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

VERSION = "0.1.0"
PROJECT_COPY = Path("scripts/agent/deliver.py")
RUNS_DIRNAME = "agent-runs"
NESTED_ENV = "AGENTIC_NESTED"
TOKEN_LINE = "deliver-run:"
HOOK_MARKER = "scripts/agent/deliver.py"
HOOK_TIMEOUT = 1800
CLAUDE_HOOK_COMMAND = 'uv run --script "${CLAUDE_PROJECT_DIR}/scripts/agent/deliver.py" hook'
CODEX_HOOK_COMMAND = (
    'uv run --script "$(git rev-parse --show-toplevel)/scripts/agent/deliver.py" hook'
)

PACKAGE_HOSTS = ["pypi.org", "files.pythonhosted.org", "github.com", "*.githubusercontent.com"]
OPEN_ITEM = re.compile(r"^\s*[-*+] \[ \]\s+(.*\S)")
DONE_ITEM = re.compile(r"^\s*[-*+] \[[xX]\]\s+")
FENCE = re.compile(r"^\s*(```|~~~)")
ACTIVE = "active"
FINAL_STATES = {"completed", "blocked", "stopped", "stalled", "exhausted"}

# Session and rate limits. Usage limits last until a window resets (hours to days); transient
# limits (HTTP 429, 529, overload, network) clear in seconds to minutes.
USAGE_LIMIT = re.compile(
    r"usage limit|hit your (?:usage )?limit|limit reached|limit exceeded|"
    r"out of (?:extra )?usage|quota exceeded|insufficient_quota",
    re.IGNORECASE,
)
TRANSIENT_LIMIT = re.compile(
    r"\b(?:429|529|503)\b|rate.?limit|overloaded|too many requests|temporarily unavailable|"
    r"connection (?:error|reset|refused)|econnreset|network error",
    re.IGNORECASE,
)
SESSION_IN_USE = re.compile(r"already in use", re.IGNORECASE)
RESET_MARGIN = 90  # seconds after a known reset, so the window has really rolled over
USAGE_BACKOFF = (900, 3600)  # first and maximum wait without a reset time, seconds
TRANSIENT_BACKOFF = (30, 900)
JITTER = 0.1
DEFAULT_MAX_WAIT_HOURS = 48.0


# --- git helpers -----------------------------------------------------------------------------


def git(cwd: Path, *args: str) -> str:
    """Run a git command and return its stripped standard output."""
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return result.stdout.strip()


def repo_root(cwd: Path) -> Path:
    """Return the top-level directory of the worktree that contains cwd."""
    return Path(git(cwd, "rev-parse", "--show-toplevel"))


def runs_dir(root: Path) -> Path:
    """Return the per-worktree directory that stores delivery runs."""
    return Path(git(root, "rev-parse", "--path-format=absolute", "--git-path", RUNS_DIRNAME))


def state_signature(root: Path) -> str:
    """Fingerprint the committed and uncommitted state of the worktree.

    cross_review.py computes the same signature, so a review can be matched to the exact
    state it reviewed.
    """
    head = git(root, "rev-parse", "HEAD")
    diff = subprocess.run(["git", "diff", "HEAD"], cwd=root, capture_output=True, check=True).stdout
    untracked = git(root, "ls-files", "--others", "--exclude-standard")
    digest = hashlib.sha256()
    for part in (head.encode(), diff, untracked.encode()):
        digest.update(part)
        digest.update(b"\0")
    return digest.hexdigest()


# --- run state -------------------------------------------------------------------------------


def now() -> str:
    """Return the current UTC time in ISO format."""
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Run:
    """State of one delivery run."""

    token: str
    plan: str
    check: str
    mode: str = "subagents"
    cross_review: str = "off"
    reviewer: str | None = None
    max_rounds: int = 40
    stall_limit: int = 3
    check_timeout: int = 1200
    status: str = ACTIVE
    session_id: str | None = None
    rounds: int = 0
    stalls: int = 0
    fingerprint: str | None = None
    note: str | None = None
    waits: int = 0
    waited_seconds: float = 0.0
    resume_at: str | None = None
    created: str = field(default_factory=now)
    updated: str = field(default_factory=now)

    def directory(self, root: Path) -> Path:
        """Return the directory that holds this run's state and reviews."""
        return runs_dir(root) / self.token

    def save(self, root: Path) -> None:
        """Write the run state atomically."""
        self.updated = now()
        directory = self.directory(root)
        directory.mkdir(parents=True, exist_ok=True)
        tmp = directory / "state.json.tmp"
        tmp.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        tmp.replace(directory / "state.json")


def load_runs(root: Path) -> list[Run]:
    """Load every run of this worktree, oldest first."""
    base = runs_dir(root)
    if not base.is_dir():
        return []
    runs = []
    for state in sorted(base.glob("*/state.json")):
        data = json.loads(state.read_text(encoding="utf-8"))
        runs.append(Run(**{k: v for k, v in data.items() if k in Run.__dataclass_fields__}))
    return sorted(runs, key=lambda run: run.created)


def find_run(root: Path, token: str | None) -> Run:
    """Return the run with the token, or the only active run when no token is given."""
    runs = load_runs(root)
    if token:
        for run in runs:
            if run.token == token:
                return run
        raise SystemExit(f"No delivery run with token {token}")
    active = [run for run in runs if run.status == ACTIVE]
    if len(active) != 1:
        raise SystemExit("Pass --token: there is not exactly one active delivery run")
    return active[0]


# --- plan and verdict ------------------------------------------------------------------------


def plan_items(text: str) -> tuple[list[str], int]:
    """Return the open checkbox items and the number of ticked ones, ignoring code blocks."""
    open_items: list[str] = []
    done = 0
    in_fence = False
    for line in text.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if match := OPEN_ITEM.match(line):
            open_items.append(match.group(1).replace("**", ""))
        elif DONE_ITEM.match(line):
            done += 1
    return open_items, done


def latest_review(root: Path, run: Run) -> dict[str, Any] | None:
    """Return the newest cross-review result of the run, if any."""
    reviews = sorted((run.directory(root) / "reviews").glob("*.json"))
    if not reviews:
        return None
    return json.loads(reviews[-1].read_text(encoding="utf-8")) | {"path": str(reviews[-1])}


def run_check(root: Path, command: str, timeout: int) -> tuple[int, str]:
    """Run the check command and return its exit code and the tail of its output."""
    env = os.environ | {NESTED_ENV: "1"}
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout} s"
    output = (result.stdout + result.stderr).strip().splitlines()
    return result.returncode, "\n".join(output[-40:])


@dataclass
class Verdict:
    """Outcome of one completion check."""

    action: str  # "continue" keeps the agent working, "release" lets it stop
    message: str


def protocol(run: Run) -> str:
    """Return the reminder appended to every continuation prompt."""
    return (
        f"End every turn with the line `{TOKEN_LINE} {run.token}`. Tick a plan checkbox only "
        "after its step is verified. If only a human can decide how to proceed (an "
        "irreversible or security-sensitive action, an external side effect such as a push "
        "or merge, or a plan too broken to continue), run "
        f'`uv run --script {PROJECT_COPY} block --token {run.token} --reason "<why>"` and stop.'
    )


def evaluate(root: Path, run: Run) -> Verdict:
    """Decide whether the run may stop. Updates round, stall and status fields of the run."""
    run.rounds += 1
    if run.rounds > run.max_rounds:
        run.status = "exhausted"
        return Verdict("release", f"Delivery run {run.token} reached {run.max_rounds} rounds.")

    plan_path = root / run.plan
    if not plan_path.is_file():
        run.status = "stopped"
        return Verdict("release", f"Plan file {run.plan} no longer exists.")
    open_items, done = plan_items(plan_path.read_text(encoding="utf-8"))

    review = latest_review(root, run)
    fingerprint = hashlib.sha256(
        f"{state_signature(root)}|{done}|{len(open_items)}|{review and review['path']}".encode()
    ).hexdigest()
    run.stalls = run.stalls + 1 if fingerprint == run.fingerprint else 0
    run.fingerprint = fingerprint
    if run.stalls >= run.stall_limit:
        run.status = "stalled"
        return Verdict(
            "release",
            f"Delivery run {run.token} made no progress in {run.stall_limit} rounds.",
        )

    prefix = f"[deliver {run.token}, round {run.rounds}/{run.max_rounds}]"
    if open_items:
        return Verdict(
            "continue",
            f"{prefix} {len(open_items)} plan items in {run.plan} are still open. "
            f"Next: {open_items[0]}\nContinue the plan.\n\n{protocol(run)}",
        )

    code, tail = run_check(root, run.check, run.check_timeout)
    if code != 0:
        return Verdict(
            "continue",
            f"{prefix} All plan items are ticked, but `{run.check}` failed with exit code "
            f"{code}:\n\n{tail}\n\nFix the failures, then continue.\n\n{protocol(run)}",
        )

    if run.cross_review == "final":
        reviewer = run.reviewer or "the other vendor"
        if review is None or review.get("signature") != state_signature(root):
            return Verdict(
                "continue",
                f"{prefix} `{run.check}` passes. Run the cross-review skill with "
                f"`--token {run.token} --reviewer {reviewer}` on the current state.\n\n"
                f"{protocol(run)}",
            )
        if review.get("verdict") != "pass":
            count = len(review.get("findings", []))
            return Verdict(
                "continue",
                f"{prefix} The cross-review requested changes ({count} findings in "
                f"{review['path']}). Fix blocking and major findings, or record why a finding "
                f"does not apply, then run the cross-review again.\n\n{protocol(run)}",
            )

    run.status = "completed"
    extra = " and the cross-review passed" if run.cross_review == "final" else ""
    return Verdict(
        "release",
        f"Delivery run {run.token} is complete: all plan items are ticked, "
        f"`{run.check}` passes{extra}.",
    )


# --- hook ------------------------------------------------------------------------------------


def transcript_text(path: str | None, limit: int = 8_000_000) -> str:
    """Return up to the last `limit` bytes of the transcript, or an empty string."""
    if not path:
        return ""
    try:
        with Path(path).open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - limit))
            return handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def stop_decision(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the Stop-hook response for a hook payload from Claude Code or Codex."""
    if os.environ.get(NESTED_ENV):
        return {}
    session = payload.get("session_id")
    if not session:
        return {}
    try:
        root = repo_root(Path(payload.get("cwd") or Path.cwd()))
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {}

    active = [run for run in load_runs(root) if run.status == ACTIVE]
    run = next((r for r in active if r.session_id == session), None)
    if run is None:
        # A run binds to the session in which its token appears, never to a bystander.
        unbound = [r for r in active if r.session_id is None]
        if not unbound:
            return {}
        evidence = (payload.get("last_assistant_message") or "") + transcript_text(
            payload.get("transcript_path")
        )
        run = next((r for r in unbound if r.token in evidence), None)
        if run is None:
            return {}
        run.session_id = session

    if turn_hit_limit(payload):
        # Not a real round: let the turn end without counting it. Headless runs wait and
        # resume; interactive sessions stop until the user continues.
        run.save(root)
        print(
            f"deliver {run.token}: turn ended by a usage or rate limit, not counted.",
            file=sys.stderr,
        )
        return {}
    verdict = evaluate(root, run)
    run.note = verdict.message if verdict.action == "release" else run.note
    run.save(root)
    if verdict.action == "continue":
        return {"decision": "block", "reason": verdict.message}
    print(verdict.message, file=sys.stderr)
    return {}


def turn_hit_limit(payload: dict[str, Any]) -> bool:
    """Return whether the turn that just ended was cut off by a usage or rate limit.

    Looks only at error entries: the last assistant entry of the transcript when it is an API
    error, and a short last assistant message. Agent prose about rate limits does not count.
    """
    last = (payload.get("last_assistant_message") or "").strip()
    if last and len(last) < 400 and (USAGE_LIMIT.search(last) or TRANSIENT_LIMIT.search(last)):
        return True
    for line in reversed(transcript_text(payload.get("transcript_path"), 200_000).splitlines()):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "assistant":
            continue
        return bool(entry.get("isApiErrorMessage") or entry.get("error"))
    return False


def cmd_hook(_: argparse.Namespace) -> int:
    """Read a Stop-hook payload from stdin and answer on stdout. Never traps a session."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        response = stop_decision(payload)
    except Exception as exc:  # a guard bug must never trap a session
        print(f"deliver guard error, letting the agent stop: {exc}", file=sys.stderr)
        response = {}
    print(json.dumps(response))
    return 0


# --- run commands ----------------------------------------------------------------------------


def default_check(root: Path) -> str | None:
    """Return the project's merge gate if it can be inferred."""
    if any((root / name).is_file() for name in ("Taskfile.yml", "Taskfile.yaml")):
        return "task check"
    return None


def create_run(root: Path, args: argparse.Namespace) -> Run:
    """Validate the arguments and create a new active run."""
    root = root.resolve()
    plan = (root / args.plan).resolve()
    if not plan.is_file():
        raise SystemExit(f"Plan not found: {args.plan}")
    relative = plan.relative_to(root).as_posix()
    open_items, _ = plan_items(plan.read_text(encoding="utf-8"))
    if not open_items:
        raise SystemExit(f"{relative} has no open `- [ ]` items to deliver")
    for run in load_runs(root):
        if run.status == ACTIVE and run.plan == relative:
            raise SystemExit(f"Run {run.token} is already delivering {relative}")
    check = args.check or default_check(root)
    if not check:
        raise SystemExit("Pass --check: no Taskfile found to infer the check command")
    if args.cross_review == "final" and args.reviewer not in {"claude", "codex"}:
        raise SystemExit("Pass --reviewer claude or codex together with --cross-review final")
    run = Run(
        token=f"dr-{secrets.token_hex(4)}",
        plan=relative,
        check=check,
        mode=args.mode,
        cross_review=args.cross_review,
        reviewer=args.reviewer,
        max_rounds=args.max_rounds,
        stall_limit=args.stall_limit,
        check_timeout=args.check_timeout,
        session_id=getattr(args, "session_id", None),
    )
    run.save(root)
    return run


def cmd_start(args: argparse.Namespace) -> int:
    """Start a run and print the token the session must echo."""
    root = repo_root(Path.cwd())
    run = create_run(root, args)
    open_items, done = plan_items((root / run.plan).read_text(encoding="utf-8"))
    print(f"Delivery run {run.token} started for {run.plan}")
    print(f"  open items: {len(open_items)}, done: {done}, check: {run.check}")
    print(f"  mode: {run.mode}, cross-review: {run.cross_review}, reviewer: {run.reviewer}")
    print("The guard binds to this session at the end of the current turn.")
    print(f"{TOKEN_LINE} {run.token}")
    return 0


def cmd_status(_: argparse.Namespace) -> int:
    """Print every run of this worktree."""
    root = repo_root(Path.cwd())
    runs = load_runs(root)
    if not runs:
        print("No delivery runs in this worktree.")
    for run in runs:
        plan = root / run.plan
        open_items, done = (
            plan_items(plan.read_text(encoding="utf-8")) if plan.is_file() else ([], 0)
        )
        print(
            f"{run.token}  {run.status:<9}  {run.plan}  done {done}, open {len(open_items)}, "
            f"rounds {run.rounds}/{run.max_rounds}, session {run.session_id or 'unbound'}"
        )
        if run.status == ACTIVE and run.resume_at:
            print(f"    waiting for a usage or rate limit until {run.resume_at}")
        if run.waits:
            hours = run.waited_seconds / 3600
            print(f"    limit waits: {run.waits}, {hours:.1f} h in total")
        if run.note:
            print(f"    {run.note}")
    return 0


def finish(args: argparse.Namespace, status: str) -> int:
    """Move a run into a final state."""
    root = repo_root(Path.cwd())
    run = find_run(root, args.token)
    run.status = status
    run.note = args.reason
    run.save(root)
    print(f"Delivery run {run.token} is now {status}.")
    return 0


# --- install and doctor ----------------------------------------------------------------------


def assets_dir() -> Path:
    """Return the role templates shipped next to this script in the plugin."""
    return Path(__file__).resolve().parents[1] / "assets"


def read_json(path: Path) -> dict[str, Any]:
    """Read a JSON object, or return an empty one when the file is missing."""
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Write a JSON object with stable formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def has_stop_hook(config: dict[str, Any]) -> bool:
    """Return whether a hooks config already runs the delivery guard on Stop."""
    for group in config.get("hooks", {}).get("Stop", []):
        for hook in group.get("hooks", []):
            if HOOK_MARKER in hook.get("command", ""):
                return True
    return False


def add_stop_hook(path: Path, command: str, extra: dict[str, Any]) -> bool:
    """Add the guard to a hooks config, keeping everything else. Returns True if changed."""
    config = read_json(path)
    if has_stop_hook(config):
        return False
    hook = {"type": "command", "command": command, "timeout": HOOK_TIMEOUT} | extra
    config.setdefault("hooks", {}).setdefault("Stop", []).append({"hooks": [hook]})
    write_json(path, config)
    return True


def render_role(template: Path, model: str | None, syntax: str) -> str:
    """Return a role file, with its model replaced when a model is given.

    Claude roles carry a `model:` line; Codex roles inherit the session model unless a
    `model = "..."` line is added before the reasoning effort.
    """
    text = template.read_text(encoding="utf-8")
    if model is None:
        return text
    if syntax == "yaml":
        return re.sub(r"^model: .*$", f"model: {model}", text, count=1, flags=re.MULTILINE)
    line = f'model = "{model}"'
    if re.search(r'^model = ".*"$', text, flags=re.MULTILINE):
        return re.sub(r'^model = ".*"$', line, text, count=1, flags=re.MULTILINE)
    return text.replace("model_reasoning_effort", f"{line}\nmodel_reasoning_effort", 1)


def install(
    target: Path, harnesses: list[str], models: dict[str, str | None], force: bool
) -> list[str]:
    """Install the project copy of this script, the Stop hooks and the role files."""
    changes = []
    copy = target / PROJECT_COPY
    source = Path(__file__).resolve()
    if not copy.is_file() or copy.read_bytes() != source.read_bytes():
        copy.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, copy)
        changes.append(f"{PROJECT_COPY} (version {VERSION})")

    roles = assets_dir() / "roles"
    if "claude" in harnesses:
        if add_stop_hook(target / ".claude" / "settings.json", CLAUDE_HOOK_COMMAND, {}):
            changes.append(".claude/settings.json: Stop hook")
        for role in ("implementer", "reviewer"):
            path = target / ".claude" / "agents" / f"{role}.md"
            if force or not path.is_file():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    render_role(roles / "claude" / f"{role}.md", models[f"claude_{role}"], "yaml"),
                    encoding="utf-8",
                )
                changes.append(f".claude/agents/{role}.md")
    if "codex" in harnesses:
        extra = {"statusMessage": "Checking the delivery run"}
        if add_stop_hook(target / ".codex" / "hooks.json", CODEX_HOOK_COMMAND, extra):
            changes.append(".codex/hooks.json: Stop hook")
        for role in ("implementer", "reviewer"):
            path = target / ".codex" / "agents" / f"{role}.toml"
            if force or not path.is_file():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    render_role(roles / "codex" / f"{role}.toml", models[f"codex_{role}"], "toml"),
                    encoding="utf-8",
                )
                changes.append(f".codex/agents/{role}.toml")
    return changes


def cmd_install(args: argparse.Namespace) -> int:
    """Install the guard and roles into the target repository."""
    if not (assets_dir() / "roles").is_dir():
        raise SystemExit("Run install from the plugin copy of deliver.py, not the project copy")
    target = repo_root(Path(args.target).resolve())
    models = {
        "claude_implementer": args.claude_implementer_model,
        "claude_reviewer": args.claude_reviewer_model,
        "codex_implementer": args.codex_implementer_model,
        "codex_reviewer": args.codex_reviewer_model,
    }
    changes = install(target, args.harness, models, args.force)
    if not changes:
        print("Delivery guard and roles are already installed.")
        return 0
    print("Installed:")
    for change in changes:
        print(f"  {change}")
    print("Review and trust the new hooks: `/hooks` in Codex; Claude Code asks on next start.")
    return 0


def doctor(target: Path) -> list[str]:
    """Return the problems that keep the guard or roles from working."""
    problems = []
    copy = target / PROJECT_COPY
    if not copy.is_file():
        problems.append(f"{PROJECT_COPY} is missing")
    elif f'VERSION = "{VERSION}"' not in copy.read_text(encoding="utf-8"):
        problems.append(f"{PROJECT_COPY} differs from version {VERSION}; reinstall")
    if not has_stop_hook(read_json(target / ".claude" / "settings.json")):
        problems.append(".claude/settings.json has no delivery Stop hook")
    if not has_stop_hook(read_json(target / ".codex" / "hooks.json")):
        problems.append(".codex/hooks.json has no delivery Stop hook")
    for path in (
        ".claude/agents/implementer.md",
        ".claude/agents/reviewer.md",
        ".codex/agents/implementer.toml",
        ".codex/agents/reviewer.toml",
    ):
        if not (target / path).is_file():
            problems.append(f"{path} is missing")
    if shutil.which("uv") is None:
        problems.append("uv is not on PATH; the hooks run the guard with uv")
    return problems


def cmd_doctor(args: argparse.Namespace) -> int:
    """Report installation problems."""
    problems = doctor(repo_root(Path(args.target).resolve()))
    if not problems:
        print("Delivery guard and roles are installed.")
        return 0
    print("Problems:", *problems, sep="\n  ")
    return 1


# --- headless --------------------------------------------------------------------------------


def slug(text: str) -> str:
    """Return a branch-safe slug."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "plan"


def create_worktree(root: Path, plan: str, branch: str | None) -> Path:
    """Create a worktree on a new branch and copy files listed in .worktreeinclude."""
    name = slug(Path(plan).stem)
    branch = branch or f"deliver/{name}"
    path = root.parent / f"{root.name}-{name}"
    exists = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=root, check=False
    )
    if path.exists() or exists.returncode == 0:
        raise SystemExit(
            f"An earlier delivery of {plan} exists: worktree {path}, branch {branch}.\n"
            f"Continue it:  cd {path} && uv run --script {PROJECT_COPY} headless --plan {plan} "
            "--harness <claude|codex> --in-place\n"
            f"Or discard it: git worktree remove --force {path} && git branch -D {branch}"
        )
    git(root, "worktree", "add", "-b", branch, str(path))
    include = root / ".worktreeinclude"
    if include.is_file():
        listed = git(
            root, "ls-files", "--others", "--ignored", f"--exclude-from={include}"
        ).splitlines()
        for relative in listed:
            source, destination = root / relative, path / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    return path


def default_setup(workdir: Path) -> str | None:
    """Return the environment setup to run before the agent starts, outside any sandbox."""
    return "uv sync" if (workdir / "pyproject.toml").is_file() else None


def headless_prompt(run: Run, plan: str) -> str:
    """Return the first prompt of an unattended run."""
    review = (
        f" Finish with a cross-review by {run.reviewer}." if run.cross_review == "final" else ""
    )
    return (
        f"Use the deliver skill to deliver the plan in {plan} end to end in {run.mode} mode. "
        f"The delivery run {run.token} is already started; do not start another.{review} "
        "You are running unattended: make rulings instead of asking, and record them. "
        f"{protocol(run)}"
    )


def writable_paths(workdir: Path) -> list[str]:
    """Return the paths outside the worktree that a sandboxed agent must be able to write.

    - uv's cache, for `uv sync`, `uv add` and `uv run`.
    - The repository's shared git directory: a linked worktree keeps its index, refs and objects
      there, so without it every `git add` and `git commit` fails.
    - prek's cache, which the git hooks behind `task lint` and every commit use.
    """
    try:
        cache = subprocess.run(
            ["uv", "cache", "dir"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        cache = str(Path.home() / ".cache" / "uv")
    git_dir = git(workdir, "rev-parse", "--path-format=absolute", "--git-common-dir")
    prek = os.environ.get("PREK_HOME") or str(Path.home() / ".cache" / "prek")
    return [cache, git_dir, prek]


def claude_sandbox_settings(writable: list[str]) -> str:
    """Return session settings that confine Claude's shell commands to the worktree.

    Shell commands run in the operating-system sandbox and are approved automatically; they can
    write to the worktree, the temp directory and the given paths, and reach package hosts only.
    File edits are approved by the acceptEdits permission mode.
    """
    sandbox = {
        "enabled": True,
        "autoAllowBashIfSandboxed": True,
        "allowUnsandboxedCommands": False,
        "filesystem": {"allowWrite": writable},
        "network": {"allowedDomains": PACKAGE_HOSTS},
    }
    return json.dumps({"sandbox": sandbox})


def agent_command(
    harness: str,
    prompt: str,
    session: str | None,
    first: bool,
    model: str | None,
    mode: str,
    effort: str | None = None,
    writable: list[str] | None = None,
) -> list[str]:
    """Return the CLI invocation for one headless round.

    Claude Code gets a preset session id so the run is bound from the start. Codex reports its
    thread id in the JSON event stream, and later rounds resume that thread. Both are confined
    to the worktree: Claude by its Bash sandbox, Codex by its workspace-write sandbox.
    """
    if harness == "claude":
        command = ["claude", "-p", "--permission-mode", mode]
        command += ["--session-id" if first else "--resume", str(session)]
        command += ["--settings", claude_sandbox_settings(writable or [])]
        command += ["--output-format", "stream-json", "--verbose"]
        command += ["--model", model] if model else []
        command += ["--effort", effort] if effort else []
        return [*command, prompt]
    command = ["codex", "exec"] if first else ["codex", "exec", "resume", str(session)]
    command += ["--json", "-c", 'sandbox_mode="workspace-write"', "-c", 'approval_policy="never"']
    if writable:
        command += ["-c", f"sandbox_workspace_write.writable_roots={json.dumps(writable)}"]
    command += ["-c", "sandbox_workspace_write.network_access=true"]
    command += ["-m", model] if model else []
    command += ["-c", f'model_reasoning_effort="{effort}"'] if effort else []
    return [*command, prompt]


@dataclass
class RoundResult:
    """What one headless round reported: exit code, session, and any limit signals."""

    code: int
    thread: str | None = None
    started: bool = False  # the CLI created the session, so later rounds must resume it
    failed: bool = False  # an error event or error result was reported
    rejected: bool = False  # a rate-limit event rejected the request
    resets: list[float] = field(default_factory=list)  # reset times from events, epoch seconds
    errors: list[str] = field(default_factory=list)  # error texts and non-JSON output


def absorb(result: RoundResult, event: dict[str, Any]) -> None:
    """Record the session and limit signals of one Claude or Codex JSON event."""
    kind = event.get("type")
    result.thread = result.thread or event.get("thread_id")
    if (kind == "system" and event.get("subtype") == "init") or kind == "thread.started":
        result.started = True
    if kind == "rate_limit_event":
        info = event.get("rate_limit_info") or event
        if info.get("status") == "rejected":
            result.rejected = True
        for key in ("resetsAt", "resets_at"):
            if isinstance(info.get(key), int | float):
                value = float(info[key])
                result.resets.append(value / 1000 if value > 1e12 else value)
        if isinstance(info.get("retry_after_ms"), int | float):
            result.resets.append(time.time() + info["retry_after_ms"] / 1000)
    if kind == "result" and event.get("is_error"):
        result.failed = True
        result.errors.append(str(event.get("result") or event.get("subtype") or "error"))
    if kind in {"error", "turn.failed"}:
        result.failed = True
        error = event.get("error")
        message = error.get("message") if isinstance(error, dict) else error
        result.errors.append(str(event.get("message") or message or kind))


def run_round(command: list[str], cwd: Path, log: Path) -> RoundResult:
    """Run one agent round, log its output, and return what it reported."""
    with (
        log.open("a", encoding="utf-8", buffering=1) as sink,
        subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=os.environ.copy(),
        ) as process,
    ):
        assert process.stdout is not None
        result = RoundResult(code=0)
        for line in process.stdout:
            sink.write(line)
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                if line.strip():
                    result.errors.append(line.strip())
                    say(paint(line.rstrip(), "dim"))
                continue
            if not isinstance(event, dict):
                continue
            absorb(result, event)
            say(*render_event(event))
    result.code = process.returncode
    return result


# --- usage and rate limits -------------------------------------------------------------------


@dataclass
class Limit:
    """A usage or rate limit that cut a round short."""

    kind: str  # "usage" or "transient"
    reset_at: datetime | None
    detail: str


def classify_limit(result: RoundResult, now: datetime) -> Limit | None:
    """Return the limit that cut the round short, or None if the round was not limited."""
    if not (result.rejected or result.failed or result.code != 0):
        return None
    text = "\n".join(result.errors)
    if result.rejected or USAGE_LIMIT.search(text):
        kind = "usage"
    elif TRANSIENT_LIMIT.search(text):
        kind = "transient"
    else:
        return None
    future = [stamp for stamp in result.resets if stamp > now.timestamp()]
    reset_at = datetime.fromtimestamp(max(future), UTC) if future else parse_reset(text, now)
    match = USAGE_LIMIT.search(text) or TRANSIENT_LIMIT.search(text)
    line = next((line for line in text.splitlines() if match and match.group(0) in line), "")
    return Limit(kind, reset_at, line.strip()[:200] or f"{kind} limit")


UNIT_SECONDS = {"d": 86400, "h": 3600, "m": 60, "s": 1}


def parse_reset(text: str, now: datetime) -> datetime | None:
    """Return the reset time a limit message announces, or None.

    Understands an epoch after a pipe (`limit reached|1760000000`), durations
    (`try again in 2 hours 13 minutes`, `retry after 60 seconds`) and clock times
    (`resets 3pm (Europe/Berlin)`, `try again at 3:05 PM`).
    """
    if match := re.search(r"\|(\d{10,13})\b", text):
        stamp = int(match.group(1))
        return datetime.fromtimestamp(stamp / 1000 if stamp > 1e12 else stamp, UTC)
    duration = re.search(
        r"(?:try again|retry|resets?)(?: after| in)[:\s]+((?:\d+\s*[a-z]+[\s,]*(?:and\s+)?)+)",
        text,
        re.IGNORECASE,
    )
    if duration:
        seconds = 0.0
        for amount, unit in re.findall(r"(\d+)\s*([a-z]+)", duration.group(1), re.IGNORECASE):
            unit = unit.lower()
            if unit.startswith(("ms", "milli")):
                seconds += int(amount) / 1000
            elif unit[0] in UNIT_SECONDS:
                seconds += int(amount) * UNIT_SECONDS[unit[0]]
        if seconds:
            return now + timedelta(seconds=seconds)
    clock = re.search(
        r"(?:resets?|try again)(?:\s+at)?\s+(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?"
        r"(?:\s*\(([^)]+)\))?",
        text,
        re.IGNORECASE,
    )
    if clock:
        hour = int(clock.group(1)) % 12 + (12 if clock.group(3).lower() == "p" else 0)
        minute = int(clock.group(2) or 0)
        try:
            zone = ZoneInfo(clock.group(4)) if clock.group(4) else now.astimezone().tzinfo
        except (ZoneInfoNotFoundError, ValueError):
            zone = now.astimezone().tzinfo
        local = now.astimezone(zone)
        reset = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if reset <= local:
            reset += timedelta(days=1)
        return reset.astimezone(UTC)
    return None


@dataclass
class Backoff:
    """Wait times for limited rounds: until the reset when known, exponential otherwise."""

    rng: random.Random = field(default_factory=random.Random)
    attempts: int = 0

    def delay(self, limit: Limit, now: datetime) -> float:
        """Return the seconds to wait before the next attempt."""
        self.attempts += 1
        if limit.reset_at is not None:
            base = max((limit.reset_at - now).total_seconds(), 0.0) + RESET_MARGIN
        else:
            first, cap = USAGE_BACKOFF if limit.kind == "usage" else TRANSIENT_BACKOFF
            base = min(first * 2 ** (self.attempts - 1), cap)
        return base * (1 + self.rng.uniform(0, JITTER))

    def reset(self) -> None:
        """Start over after a round that was not limited."""
        self.attempts = 0


def wait_until(moment: datetime) -> None:
    """Sleep until the moment, in short steps, so a suspended laptop does not oversleep."""
    while (remaining := (moment - datetime.now(UTC)).total_seconds()) > 0:
        time.sleep(min(remaining, 60))


def retry_prompt(prompt: str, limit: Limit) -> str:
    """Return the prompt that resumes a session after a limit."""
    return (
        f"Your previous turn was cut off by a {limit.kind} limit ({limit.detail}). Check the "
        f"plan and the working tree, then continue where you left off.\n\n{prompt}"
    )


# --- console output --------------------------------------------------------------------------

STYLES = {
    "bold": "1",
    "dim": "2",
    "italic": "3",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "cyan": "36",
}


SHELL_TOOLS = {"Bash", "Shell"}  # their results show the last output line, which says the most
tool_names: dict[str, str] = {}  # tool_use id -> tool name, to style the matching result
announced: set[str] = set()  # subagent dispatches whose model was already shown


def color_enabled() -> bool:
    """Return whether console output may use ANSI colors (NO_COLOR and FORCE_COLOR apply)."""
    if os.environ.get("NO_COLOR"):
        return False
    return bool(os.environ.get("FORCE_COLOR")) or sys.stdout.isatty()


def paint(text: str, *styles: str) -> str:
    """Wrap text in ANSI styles when colors are enabled."""
    if not styles or not color_enabled():
        return text
    return f"\033[{';'.join(STYLES[style] for style in styles)}m{text}\033[0m"


def say(*lines: str) -> None:
    """Print console lines immediately."""
    for line in lines:
        print(line, flush=True)


def note(text: str, tone: str = "yellow") -> None:
    """Print a delivery note: yellow for progress, green for success, red for failure."""
    mark = {"green": "✓", "red": "✗"}.get(tone, "●")
    say(paint(f"{mark} ", tone, "bold") + text)


def clip(text: str, width: int = 110, last: bool = False) -> tuple[str, int]:
    """Return the first (or last) non-empty line, shortened, and the number of other lines."""
    lines = [line.strip() for line in text.strip().splitlines() if line.strip()]
    if not lines:
        return "", 0
    line = lines[-1] if last else lines[0]
    return (line if len(line) <= width else line[: width - 1] + "…"), len(lines) - 1


def tool_argument(name: str, data: dict[str, Any]) -> str:
    """Return the argument shown in a tool line, such as the command or the file."""
    agent = data.get("subagent_type")
    if isinstance(agent, str):
        overrides = ", ".join(f"{key} {data[key]}" for key in ("model", "effort") if data.get(key))
        text = f"{agent}: {data.get('description') or ''}".strip()
        return f"{text} · {overrides}" if overrides else text
    for key in ("command", "file_path", "path", "pattern", "url", "query", "description"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return clip(value, 100)[0]
    return ""


def tool_line(name: str, argument: str = "") -> str:
    """Return a tool call line in the style of the Claude Code CLI."""
    return paint("⏺ ", "green") + paint(name, "bold") + (f"({argument})" if argument else "")


def result_line(text: str, error: bool = False, last: bool = False) -> str:
    """Return a tool result line: one line of output and how many lines were left out."""
    line, more = clip(text, last=last)
    plural = "line" if more == 1 else "lines"
    summary = (line or "(no output)") + (f" (+{more} {plural})" if more else "")
    return paint("  ⎿  ", "dim") + paint(summary, "red" if error else "dim")


def message_lines(text: str) -> list[str]:
    """Return an assistant message, its first line marked like the Claude Code CLI."""
    rows = text.strip().splitlines() or [""]
    return [paint("⏺ ", "bold") + rows[0], *(f"  {row}" for row in rows[1:])]


def thinking_line(text: str) -> str:
    """Return a dim thinking line; models that hide their reasoning show a placeholder."""
    line, more = clip(text, 100)
    shown = f"{line}{' …' if more and not line.endswith('…') else ''}" if line else "Thinking…"
    return paint(f"✻ {shown}", "dim", "italic")


def result_text(content: Any) -> str:
    """Return the text of a Claude tool result, which is a string or a list of parts."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def render_event(event: dict[str, Any]) -> list[str]:
    """Return the console lines for one Claude or Codex JSON event."""
    kind = event.get("type")
    message = event.get("message") or {}
    lines: list[str] = []
    if kind == "system" and event.get("subtype") == "init":
        lines.append(
            paint(
                f"  ⎿  session {str(event.get('session_id'))[:8]} · model "
                f"{event.get('model', '?')}",
                "dim",
            )
        )
    elif kind == "assistant" and isinstance(message.get("content"), list):
        spaced = not event.get("parent_tool_use_id")  # blank line between main-level steps
        for part in message["content"]:
            if part.get("type") == "text" and part.get("text", "").strip():
                lines += [""] * spaced + message_lines(part["text"])
            elif part.get("type") == "thinking":
                lines += [""] * spaced + [thinking_line(part.get("thinking", ""))]
            elif part.get("type") == "tool_use":
                name = part.get("name", "tool")
                tool_names[str(part.get("id"))] = name
                lines += [""] * spaced + [
                    tool_line(name, tool_argument(name, part.get("input") or {}))
                ]
    elif kind == "user" and isinstance(message.get("content"), list):
        for part in message["content"]:
            if isinstance(part, dict) and part.get("type") == "tool_result":
                error = bool(part.get("is_error"))
                shell = tool_names.get(str(part.get("tool_use_id"))) in SHELL_TOOLS
                text = result_text(part.get("content"))
                lines.append(result_line(text, error=error, last=shell and not error))
    elif kind == "result" and event.get("is_error"):
        lines.append(paint(f"✗ {clip(str(event.get('result') or 'error'))[0]}", "red"))
    elif kind in {"error", "turn.failed"}:
        error = event.get("error")
        text = event.get("message") or (error.get("message") if isinstance(error, dict) else error)
        lines.append(paint(f"✗ {clip(str(text or kind))[0]}", "red"))
    else:
        lines += render_codex_item(event)
    parent = event.get("parent_tool_use_id")
    if parent:
        model = message.get("model") if kind == "assistant" else None
        if model and parent not in announced:
            announced.add(parent)
            lines.insert(0, paint(f"  ⎿  subagent model {model}", "dim"))
        bar = paint("  │ ", "cyan", "dim")
        lines = [bar + line for line in lines]
    return lines


def render_codex_item(event: dict[str, Any]) -> list[str]:
    """Return the console lines for a Codex item event."""
    item = event.get("item") or {}
    kind, done = item.get("type"), event.get("type") == "item.completed"
    if done and kind == "agent_message":
        return ["", *message_lines(item.get("text") or "")]
    if done and kind == "reasoning":
        return ["", thinking_line(item.get("text") or "")]
    if event.get("type") == "item.started" and kind == "command_execution":
        command = str(item.get("command") or "")
        try:
            argv = shlex.split(command)
        except ValueError:
            argv = []
        if len(argv) == 3 and argv[0].endswith("sh") and argv[1] in {"-c", "-lc"}:
            command = argv[2]  # unwrap `/bin/zsh -lc "<command>"`
        return ["", tool_line("Shell", clip(command, 100)[0])]
    if done and kind == "command_execution":
        code = item.get("exit_code")
        output = str(item.get("aggregated_output") or "")
        line, _ = clip(output, last=True)
        return [result_line(f"exit {code}" + (f" · {line}" if line else ""), error=code != 0)]
    if done and kind == "file_change":
        paths = ", ".join(change.get("path", "?") for change in item.get("changes") or [])
        return ["", tool_line("Edit", clip(paths, 100)[0])]
    if event.get("type") == "item.started" and kind == "mcp_tool_call":
        return ["", tool_line(f"{item.get('server', 'mcp')}.{item.get('tool', 'tool')}")]
    return []


# --- run header ------------------------------------------------------------------------------


def home_relative(path: Path) -> str:
    """Return the path with the home directory shortened to ~."""
    try:
        return "~/" + path.relative_to(Path.home()).as_posix()
    except ValueError:
        return str(path)


def read_toml(path: Path) -> dict[str, Any]:
    """Return a TOML file as a dict, or an empty dict when it is missing or invalid."""
    import tomllib

    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def first_setting(key: str, sources: list[Path], loader: Any) -> tuple[str | None, str]:
    """Return the first value of key in the sources and where it came from."""
    for path in sources:
        value = loader(path).get(key)
        if isinstance(value, str) and value:
            return value, home_relative(path)
    return None, ""


def json_file(path: Path) -> dict[str, Any]:
    """Return a JSON object file as a dict, or an empty dict."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def session_settings(args: argparse.Namespace, root: Path) -> dict[str, str]:
    """Return the vendor, model and effort a headless run will use, with their sources."""
    home = Path.home()
    if args.harness == "claude":
        sources = [
            root / ".claude/settings.local.json",
            root / ".claude/settings.json",
            home / ".claude/settings.json",
        ]
        model, origin = first_setting("model", sources, json_file)
        if os.environ.get("ANTHROPIC_MODEL"):
            model, origin = os.environ["ANTHROPIC_MODEL"], "ANTHROPIC_MODEL"
        effort, effort_origin = None, ""
    else:
        sources = [root / ".codex/config.toml", home / ".codex/config.toml"]
        model, origin = first_setting("model", sources, read_toml)
        effort, effort_origin = first_setting("model_reasoning_effort", sources, read_toml)
    if args.model:
        model, origin = args.model, "--model"
    if args.effort:
        effort, effort_origin = args.effort, "--effort"
    if args.harness == "claude" and os.environ.get("CLAUDE_CODE_EFFORT_LEVEL"):
        effort, effort_origin = os.environ["CLAUDE_CODE_EFFORT_LEVEL"], "CLAUDE_CODE_EFFORT_LEVEL"
    rows = {
        "vendor": f"{'Claude Code' if args.harness == 'claude' else 'Codex'} "
        f"{cli_version(args.harness)}",
        "model": f"{model or 'CLI default'}" + (f"  ({origin})" if origin else ""),
        "effort": f"{effort or 'CLI default'}" + (f"  ({effort_origin})" if effort_origin else ""),
        "mode": args.mode,
    }
    if args.mode == "subagents":
        for name in ("implementer", "reviewer"):
            rows[name] = role_settings(args.harness, root, name, effort, effort_origin)
    return rows


def role_settings(
    harness: str, root: Path, name: str, session_effort: str | None, session_origin: str
) -> str:
    """Return the model and effort a subagent role runs with, and where each comes from."""
    if harness == "claude":
        path = root / f".claude/agents/{name}.md"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            return "role file missing"
        found = {
            key: match.group(1)
            for key in ("model", "effort")
            if (match := re.search(rf"^{key}:\s*(\S+)", text, re.MULTILINE))
        }
        model = found.get("model")
        if model in {None, "inherit"}:
            model = os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL") or "session model"
        effort = found.get("effort")
    else:
        data = read_toml(root / f".codex/agents/{name}.toml")
        model = str(data.get("model") or "session model")
        effort = data.get("model_reasoning_effort")
    if harness == "claude" and session_origin == "CLAUDE_CODE_EFFORT_LEVEL":
        effort_text = f"effort {session_effort}  (CLAUDE_CODE_EFFORT_LEVEL)"
    elif effort:
        source = (
            f".claude/agents/{name}.md" if harness == "claude" else f".codex/agents/{name}.toml"
        )
        effort_text = f"effort {effort}  ({source})"
    else:
        effort_text = f"effort {session_effort or 'CLI default'}  (session)"
    return f"{model} · {effort_text}"


def cli_version(harness: str) -> str:
    """Return the installed version of the vendor CLI."""
    try:
        output = subprocess.run(
            [harness, "--version"], capture_output=True, text=True, timeout=15, check=False
        ).stdout
    except (OSError, subprocess.TimeoutExpired):
        return "(not found)"
    match = re.search(r"\d+\.\d+\.\d+", output)
    return match.group(0) if match else output.strip()[:40]


def print_header(args: argparse.Namespace, root: Path, plan: str) -> None:
    """Print what the run will use before it starts."""
    say(paint("● deliver ", "yellow", "bold") + paint(plan, "bold"))
    rows = session_settings(args, root)
    rows["limits"] = f"waits up to {args.max_wait:g} h for usage limits"
    width = max(len(label) for label in rows)
    for label, value in rows.items():
        say(f"  {paint(label.ljust(width), 'dim')} {value}")


def cmd_headless(args: argparse.Namespace) -> int:
    """Deliver a plan unattended, resuming the session until the guard releases it."""
    source_root = repo_root(Path.cwd()).resolve()
    plan = (source_root / args.plan).resolve().relative_to(source_root).as_posix()
    print_header(args, source_root, plan)
    if not args.in_place:
        tracked = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--", plan], cwd=source_root, check=False
        )
        listed = git(source_root, "ls-files", plan)
        if not listed or tracked.returncode != 0:
            raise SystemExit(f"Commit {plan} first: the new worktree starts from HEAD")
    workdir = source_root if args.in_place else create_worktree(source_root, plan, args.branch)
    say(f"  {paint('worktree', 'dim')} {workdir}", "")
    session = str(uuid.uuid4()) if args.harness == "claude" else None
    args.plan = plan
    args.session_id = session
    setup = args.setup if args.setup is not None else default_setup(workdir)
    if setup and not args.dry_run:
        note(f"Preparing the worktree: {setup}")
        if subprocess.run(setup, shell=True, cwd=workdir, check=False).returncode != 0:
            raise SystemExit(f"Setup command failed: {setup}")
    run = create_run(workdir, args)
    writable = writable_paths(workdir)
    log = run.directory(workdir) / "headless.jsonl"
    prompt = headless_prompt(run, plan)
    if args.dry_run:
        command = agent_command(
            args.harness,
            prompt,
            session,
            True,
            args.model,
            args.claude_permission_mode,
            args.effort,
            writable,
        )
        say(" ".join(command))
        return 0

    first, budget, backoff = True, args.max_wait * 3600, Backoff()
    try:
        while True:
            command = agent_command(
                args.harness,
                prompt,
                session,
                first,
                args.model,
                args.claude_permission_mode,
                args.effort,
                writable,
            )
            result = run_round(command, workdir, log)
            session = session or result.thread
            if first and (result.started or SESSION_IN_USE.search("\n".join(result.errors))):
                first = False  # the session exists now; later attempts resume it
            run = find_run(workdir, run.token)
            limit = classify_limit(result, datetime.now(UTC))
            if limit is not None and run.status == ACTIVE:
                delay = backoff.delay(limit, datetime.now(UTC))
                resume = datetime.now(UTC) + timedelta(seconds=delay)
                if run.waited_seconds + delay > budget:
                    run.status = "exhausted"
                    run.note = (
                        f"A {limit.kind} limit needs a wait until {resume:%Y-%m-%d %H:%M} UTC, "
                        f"beyond --max-wait {args.max_wait:g} h. Plan progress is kept in "
                        f"{workdir}; continue there later with `--in-place`."
                    )
                    run.resume_at = None
                    run.save(workdir)
                    note(run.note, "red")
                    return 1
                run.waits += 1
                run.waited_seconds += delay
                run.resume_at = resume.isoformat(timespec="seconds")
                run.save(workdir)
                local = resume.astimezone()
                note(
                    f"{limit.kind.capitalize()} limit: {limit.detail}. Waiting until "
                    f"{local:%Y-%m-%d %H:%M %Z} ({delay / 60:.0f} min), then resuming."
                )
                wait_until(resume)
                run = find_run(workdir, run.token)
                run.resume_at = None
                run.save(workdir)
                if session is not None and not first:
                    prompt = retry_prompt(prompt, limit)
                continue
            backoff.reset()
            if session is None or (result.code != 0 and first):
                run.status = "stopped"
                run.note = f"The {args.harness} CLI exited with code {result.code}; see {log}."
                run.save(workdir)
                note(run.note, "red")
                return 1
            if run.status != ACTIVE:
                tone = "green" if run.status == "completed" else "red"
                note(
                    f"Run {run.token} ended inside the session: {run.status}. {run.note or ''}",
                    tone,
                )
                return 0 if run.status == "completed" else 1
            run.session_id = session
            verdict = evaluate(workdir, run)
            run.note = verdict.message if verdict.action == "release" else run.note
            run.save(workdir)
            if verdict.action == "release":
                note(verdict.message, "green" if run.status == "completed" else "red")
                return 0 if run.status == "completed" else 1
            note(verdict.message.splitlines()[0])
            prompt, first = verdict.message, False
    except KeyboardInterrupt:
        run = find_run(workdir, run.token)
        run.status, run.resume_at = "stopped", None
        run.note = f"Interrupted by the user. Plan progress is kept in {workdir}."
        run.save(workdir)
        say("")
        note(run.note, "red")
        return 130


# --- command line ----------------------------------------------------------------------------


def add_run_options(parser: argparse.ArgumentParser) -> None:
    """Add the options shared by start and headless."""
    parser.add_argument("--plan", required=True, help="plan file with `- [ ]` items")
    parser.add_argument("--check", help="merge gate command (default: task check)")
    parser.add_argument("--mode", choices=["subagents", "single"], default="subagents")
    parser.add_argument("--cross-review", choices=["off", "final"], default="off")
    parser.add_argument("--reviewer", choices=["claude", "codex"])
    parser.add_argument("--max-rounds", type=int, default=40)
    parser.add_argument("--stall-limit", type=int, default=3)
    parser.add_argument("--check-timeout", type=int, default=1200, help="seconds")


def parser() -> argparse.ArgumentParser:
    """Build the command line parser."""
    root = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    root.add_argument("--version", action="version", version=VERSION)
    commands = root.add_subparsers(dest="command", required=True)

    start = commands.add_parser("start", help="begin a delivery run")
    add_run_options(start)
    start.set_defaults(func=cmd_start)

    commands.add_parser("status", help="show delivery runs").set_defaults(func=cmd_status)

    for name, status in (("block", "blocked"), ("stop", "stopped")):
        command = commands.add_parser(name, help=f"mark a run {status}")
        command.add_argument("--token")
        command.add_argument("--reason", required=name == "block")
        command.set_defaults(func=lambda args, status=status: finish(args, status))

    commands.add_parser("hook", help="Stop-hook entry point").set_defaults(func=cmd_hook)

    inst = commands.add_parser("install", help="install the guard hook and roles")
    inst.add_argument("--target", default=".")
    inst.add_argument(
        "--harness", nargs="+", choices=["claude", "codex"], default=["claude", "codex"]
    )
    inst.add_argument("--claude-implementer-model", help="default: sonnet, from the role file")
    inst.add_argument("--claude-reviewer-model", help="default: opus, from the role file")
    inst.add_argument("--codex-implementer-model", help="default: inherit the session model")
    inst.add_argument("--codex-reviewer-model", help="default: inherit the session model")
    inst.add_argument("--force", action="store_true", help="overwrite existing role files")
    inst.set_defaults(func=cmd_install)

    doc = commands.add_parser("doctor", help="check the installation")
    doc.add_argument("--target", default=".")
    doc.set_defaults(func=cmd_doctor)

    head = commands.add_parser("headless", help="deliver a plan unattended")
    add_run_options(head)
    head.add_argument("--harness", choices=["claude", "codex"], required=True)
    head.add_argument("--model")
    head.add_argument("--effort", choices=["low", "medium", "high"], help="reasoning effort")
    head.add_argument("--in-place", action="store_true", help="use this worktree")
    head.add_argument("--branch", help="branch for the new worktree")
    head.add_argument(
        "--claude-permission-mode",
        default="acceptEdits",
        help="file edits are approved by this mode; shell commands by the sandbox",
    )
    head.add_argument("--setup", help="command run before the agent starts (default: uv sync)")
    head.add_argument(
        "--max-wait",
        type=float,
        default=DEFAULT_MAX_WAIT_HOURS,
        help="hours the run may spend waiting for usage and rate limits in total",
    )
    head.add_argument("--dry-run", action="store_true", help="print the first command only")
    head.set_defaults(func=cmd_headless)
    return root


def main(argv: list[str] | None = None) -> int:
    """Run the command line interface."""
    args = parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
