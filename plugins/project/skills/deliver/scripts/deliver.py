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
    resolve-review  Record verified fixes for the final review's required findings.
    publish   Push the verified branch and create or update its pull request.
    install   Add the guard hook and the implementer and reviewer roles to a repository.
    doctor    Check that the guard hook and the roles are installed.
    headless  Run a plan unattended, by default in a new git worktree.
    resume    Choose a saved delivery run and continue its vendor session.

A run is delivered when every plan checkbox is ticked, the check command passes, a requested
cross-vendor review passed or its required fixes were verified and, with --publish-pr, a pull
request exists for the verified branch. Headless runs survive usage and
rate limits: a round cut off by a limit is not counted, and the run waits until the limit resets
(or backs off exponentially when no reset time is known), then resumes the same session.

Run state lives in the git directory of the worktree, so it is never committed and each worktree
has its own runs.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import functools
import hashlib
import json
import os
import random
import re
import secrets
import select
import selectors
import shlex
import shutil
import signal
import subprocess
import sys
import termios
import time
import tomllib
import tty
import urllib.parse
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

VERSION = "0.1.0"
PROJECT_COPY = Path("scripts/agent/deliver.py")
# Files the project copy needs at run time, by their path below the plugin's skills directory.
# `install` copies them flat into scripts/agent/, so a project runs without the plugin.
COMPANIONS = [
    "cross-review/scripts/cross_review.py",
    "github-create-pr/scripts/create_pr.py",
    "deliver/scripts/prices.toml",
]
RUNS_DIRNAME = "agent-runs"
WORKTREES_DIRNAME = ".worktrees"
NESTED_ENV = "AGENTIC_NESTED"
TOKEN_LINE = "deliver-run:"
HOOK_MARKER = "scripts/agent/deliver.py"
HOOK_TIMEOUT = 1800
CLAUDE_HOOK_COMMAND = 'uv run --script "${CLAUDE_PROJECT_DIR}/scripts/agent/deliver.py" hook'
CODEX_HOOK_COMMAND = (
    'uv run --script "$(git rev-parse --show-toplevel)/scripts/agent/deliver.py" hook'
)

PACKAGE_HOSTS = ["pypi.org", "files.pythonhosted.org", "registry.npmjs.org"]
CREDENTIAL_PATHS = [
    "~/.ssh",
    "~/.aws",
    "~/.azure",
    "~/.config/gcloud",
    "~/.config/gh",
    "~/.netrc",
    "~/.git-credentials",
    "~/.docker/config.json",
    "~/.codex/auth.json",
    "~/.claude/.credentials.json",
]
CREDENTIAL_ENV = ["GH_TOKEN", "GITHUB_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"]
MIN_CODEX_PROFILE_VERSION = (0, 163, 0)
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

# Pull-request helpers by remote host: the forge name and the helper below the skills directory.
# Each helper accepts --preflight, --execute, --push and --result-file and writes the forge's
# own pull-request JSON, which pr_receipt() normalizes.
PR_HELPERS = {
    "github.com": ("GitHub", "github-create-pr/scripts/create_pr.py"),
}


# --- git helpers -----------------------------------------------------------------------------


def git(cwd: Path, *args: str) -> str:
    """Run Git and return its output, or exit with the command and failure details."""
    command = ["git", *args]
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        details = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
        raise SystemExit(f"{shlex.join(command)} failed (exit {result.returncode}):\n{details}")
    return result.stdout.strip()


def repo_root(cwd: Path) -> Path:
    """Return the top-level directory of the worktree that contains cwd."""
    return Path(git(cwd, "rev-parse", "--show-toplevel"))


def main_root(cwd: Path) -> Path:
    """Return the top-level directory of the main worktree, even from a linked worktree."""
    return Path(git(cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")).parent


def worktrees_ignored(root: Path) -> bool:
    """Tell whether git ignores the directory that holds the delivery worktrees."""
    probe = subprocess.run(
        ["git", "check-ignore", "-q", f"{WORKTREES_DIRNAME}/probe"], cwd=root, check=False
    )
    return probe.returncode == 0


def runs_dir(root: Path) -> Path:
    """Return the per-worktree directory that stores delivery runs."""
    return Path(git(root, "rev-parse", "--path-format=absolute", "--git-path", RUNS_DIRNAME))


def state_signature(root: Path) -> str:
    """Fingerprint the committed and uncommitted state of the worktree.

    cross_review.py computes the same signature, so a review can be matched to the exact
    state it reviewed.
    """
    head = git(root, "rev-parse", "HEAD")
    diff = subprocess.run(
        ["git", "diff", "--binary", "--ignore-submodules=none", "--submodule=diff", "HEAD"],
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout
    untracked = git(root, "ls-files", "--others", "--exclude-standard")
    digest = hashlib.sha256()
    for part in (head.encode(), diff, untracked.encode()):
        digest.update(part)
        digest.update(b"\0")
    for relative in untracked.splitlines():
        digest.update((root / relative).read_bytes())
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
    mode: str = "single"
    cross_review: str = "off"
    reviewer: str | None = None
    reviewer_model: str | None = None
    focused_review: bool = False
    verified_signature: str | None = None
    verified_check: str | None = None
    codex_bin: str | None = None
    publish_pr: bool = False
    pr_remote: str = "origin"
    pr_destination: str = "main"
    pr_url: str | None = None
    pr_head: str | None = None
    pr_branch: str | None = None
    headless: bool = False
    pr_title: str | None = None
    pr_description_file: str | None = None
    max_rounds: int = 40
    round_cost: float = 2.0
    round_turns: int = 150
    round_minutes: float = 90.0
    max_cost: float = 10.0
    review_cost: float = 2.0
    stall_limit: int = 3
    check_timeout: int = 1200
    status: str = ACTIVE
    session_id: str | None = None
    harness: str | None = None
    model: str | None = None
    effort: str | None = None
    setup: str | None = None
    claude_permission_mode: str = "acceptEdits"
    max_wait: float = 48
    review_rounds: int = 1
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


def run_check(root: Path, command: str, timeout: int, *, outer: bool = False) -> tuple[int, str]:
    """Run the check command and return its exit code and the tail of its output."""
    env = os.environ | {NESTED_ENV: "1"}
    if outer:
        keep = {"PATH", "HOME", "LANG", "TERM", "NO_COLOR", NESTED_ENV}
        keep.update(
            {
                "UV_CACHE_DIR",
                "UV_PYTHON",
                "UV_PYTHON_DOWNLOADS",
                "UV_OFFLINE",
                "UV_NO_PROGRESS",
                "UV_LINK_MODE",
            }
        )
        env = {key: value for key, value in env.items() if key in keep}
    sandbox = delivery_config(root)
    if outer and sandbox.get("gate", "env-scrub") == "srt":
        srt = shutil.which("srt")
        if not srt:
            return 127, "[sandbox].gate is 'srt' but srt is not installed"
        command = shlex.join([srt, "--", *shlex.split(command)])
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

    action: str  # continue: repair, release: stop, handoff: outer verification
    message: str


def protocol(run: Run) -> str:
    """Return the reminder appended to every continuation prompt."""
    return (
        f"End every turn with the line `{TOKEN_LINE} {run.token}`. Tick a plan checkbox only "
        "after its step is verified. If only a human can decide how to proceed (an "
        "irreversible or security-sensitive action, publication outside this run, "
        "a merge, or a plan too broken to continue), run "
        f"`uv run --no-project python {shlex.quote(str(Path(__file__).resolve()))} block "
        f'--token {run.token} --reason "<why>"` and stop.'
    )


def continuation_prompt(run: Run, message: str) -> str:
    """Keep follow-up prompts short while preserving the completion token."""
    message = message.replace(protocol(run), "").strip()
    return f"{message}\n\nEnd every turn with the line `{TOKEN_LINE} {run.token}`."


def full_protocol_prompt(run: Run, message: str) -> str:
    """Append the full guard protocol to a message."""
    return f"{message.rstrip()}\n\n{protocol(run)}"


def companion(skill_path: str) -> Path:
    """Locate a file of another skill: beside this script in a project copy, else in the plugin."""
    here = Path(__file__).resolve()
    flat = here.parent / Path(skill_path).name
    return flat if flat.is_file() else here.parents[2] / skill_path


def review_command(run: Run) -> list[str]:
    """Return the configured final-review command without relying on session model defaults."""
    command = [
        "uv",
        "run",
        "--no-project",
        "python",
        str(companion("cross-review/scripts/cross_review.py")),
        "--token",
        run.token,
        "--reviewer",
        run.reviewer or "codex",
        "--plan",
        run.plan,
    ]
    if run.reviewer_model:
        command += ["--model", run.reviewer_model]
    if run.codex_bin:
        command += ["--codex-bin", run.codex_bin]
    return command


def pr_forge(root: Path, remote: str) -> tuple[str, Path]:
    """Return the forge name and the pull-request helper for the remote's host."""
    url = git(root, "remote", "get-url", remote)
    host = urllib.parse.urlparse(url).hostname if "://" in url else url.split("@")[-1].split(":")[0]
    if host not in PR_HELPERS:
        supported = ", ".join(sorted(PR_HELPERS))
        raise SystemExit(f"Remote {remote} ({url}) is not on a supported forge: {supported}")
    name, helper = PR_HELPERS[host]
    path = companion(helper)
    if not path.is_file():
        raise SystemExit(f"{name} publication needs the {path.parents[1].name} skill at {path}")
    return name, path


def pr_receipt(forge: str, response: dict[str, Any]) -> dict[str, str]:
    """Normalize a forge's pull-request JSON to url, state, source, commit and destination."""
    return {
        "url": response.get("url", ""),
        "state": response.get("state", ""),
        "source": response.get("headRefName", ""),
        "commit": response.get("headRefOid", ""),
        "destination": response.get("baseRefName", ""),
    }


def publication_environment(root: Path, command: list[str]) -> list[str]:
    """Load local credentials only in publication subprocesses, never in preview."""
    # Worktree copies may contain a token that was rotated in the main checkout.
    base = main_root(root) if (root / ".git").is_file() else root
    files = [base / ".env", base / ".env.local"]
    options = [item for path in files if path.is_file() for item in ("--env-file", str(path))]
    return ["uv", "run", *options, "--no-project", *command] if options else command


def preflight(root: Path, args: argparse.Namespace) -> None:
    """Check reviewer installation and publication access before creating a delivery worktree."""
    model_defaults(args)
    commands: list[list[str]] = []
    if args.cross_review == "final":
        if args.reviewer is None:
            args.reviewer = "codex" if getattr(args, "harness", "claude") == "claude" else "claude"
        args.reviewer_model = reviewer_model(args.reviewer, args.reviewer_model)
        if args.reviewer == "codex":
            args.codex_bin = args.codex_bin or os.environ.get("CODEX_BIN") or shutil.which("codex")
            args.reviewer_model = args.reviewer_model or "gpt-6.1-sol"
        command = [
            sys.executable,
            str(companion("cross-review/scripts/cross_review.py")),
            "--reviewer",
            args.reviewer,
            "--preflight",
        ]
        if args.reviewer_model:
            command += ["--model", args.reviewer_model]
        if args.codex_bin:
            command += ["--codex-bin", args.codex_bin]
        commands.append(command)
    if args.publish_pr:
        _, helper = pr_forge(root, args.pr_remote)
        commands.append([sys.executable, str(helper), "--preflight", "--remote", args.pr_remote])
    for command in commands:
        if "--remote" in command:
            command = publication_environment(root, command)
        result = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise SystemExit(f"Delivery preflight failed:\n{result.stdout}{result.stderr}")
        print(result.stdout.strip())


def evaluate(root: Path, run: Run, *, outer: bool = False) -> Verdict:
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
    review_text = review and json.dumps(review, sort_keys=True)
    state = f"{state_signature(root)}|{done}|{len(open_items)}|{review_text}"
    fingerprint = hashlib.sha256(f"{state}|{run.pr_url}|{run.pr_head}".encode()).hexdigest()
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

    selected_review = (
        review is not None
        and review.get("reviewer") == run.reviewer
        and (not run.reviewer_model or review.get("model") == run.reviewer_model)
    )
    signature = state_signature(root)
    if run.cross_review == "final" and not selected_review and run.headless and outer:
        run.status = "reviewing"
        return Verdict(
            "handoff",
            "Implementation is ready for the configured final review. End this turn; the outer "
            "controller will run the reviewer and resume this session with its findings.",
        )
    if run.cross_review == "final":
        reviewed = (
            selected_review
            and review.get("verdict") == "pass"
            and review.get("signature") == signature
        )
        resolved = (
            selected_review
            and review is not None
            and review.get("resolution", {}).get("signature") == signature
        )
        used = len(list((run.directory(root) / "reviews").glob("*.json")))
        if resolved and review and review.get("verdict") != "pass" and used < run.review_rounds:
            return Verdict(
                "continue",
                f"{prefix} Fixes verified. Run the next explicitly requested review "
                f"({used + 1}/{run.review_rounds}): `{shlex.join(review_command(run))}`. "
                "Use focused checks between reviews, not broad suites.",
            )
        if not reviewed and not resolved:
            if selected_review:
                return Verdict(
                    "continue",
                    f"{prefix} Implement every blocking or major review finding autonomously. "
                    "Keep fixes small, add or run focused tests, and commit them. "
                    "Record the fixes with "
                    "`uv run --no-project python "
                    f"{shlex.quote(str(Path(__file__).resolve()))} resolve-review --token "
                    f"{run.token} "
                    "--fixes-file /path/to/fixes.json --check '<focused test command>'`. "
                    "The JSON contains a summary and fixes with each finding index and change. "
                    "Do not start another model review for routine fixes. "
                    f"Ask only for a material design or scope decision.\n\n{protocol(run)}",
                )
            return Verdict(
                "continue",
                f"{prefix} Run the final review with `{shlex.join(review_command(run))}`.\n\n"
                f"{protocol(run)}",
            )

    if run.verified_signature != signature or run.verified_check != run.check:
        if run.headless and not outer:
            run.status = "verifying"
            return Verdict(
                "handoff",
                "Implementation and review are ready. End this turn; the outer controller runs "
                "the final gate outside the agent sandbox. Do not run or block on the gate here.",
            )
        code, tail = run_check(root, run.check, run.check_timeout, outer=outer)
        if code != 0:
            return Verdict(
                "continue",
                f"{prefix} `{run.check}` failed with exit code {code}:\n\n{tail}\n\n"
                f"Fix the failures autonomously, then continue.\n\n{protocol(run)}",
            )
        if state_signature(root) != signature:
            return Verdict("continue", "Checks changed files. Commit the changes and continue.")
        run.verified_signature, run.verified_check = signature, run.check

    if run.publish_pr and (
        not run.pr_url
        or run.pr_head != git(root, "rev-parse", "HEAD")
        or run.pr_branch != git(root, "branch", "--show-current")
        or git(root, "status", "--porcelain")
    ):
        return Verdict(
            "continue",
            f"{prefix} Verification passed. Publish the committed branch with "
            f"`uv run --no-project python {shlex.quote(str(Path(__file__).resolve()))} "
            f"publish --token {run.token} "
            "--title 'type(scope): summary' --description-file /path/to/description.md`. "
            "Fill every section of the repository's pull-request description format. "
            "This run authorizes a normal push and PR creation. "
            f"A copy-ready description alone is not completion.\n\n{protocol(run)}",
        )
    run.status = "completed"
    extra = ""
    if run.cross_review == "final":
        extra = (
            " and required review fixes were verified"
            if resolved
            else " and the cross-review passed"
        )
    return Verdict(
        "release",
        f"Delivery run {run.token} is complete: all plan items are ticked, "
        f"`{run.check}` passes{extra}." + (f" PR: {run.pr_url}" if run.pr_url else ""),
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
        evidence = payload.get("last_assistant_message") or ""
        if not any(r.token in evidence for r in unbound):
            evidence += transcript_text(payload.get("transcript_path"), limit=256_000)
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
    if verdict.action == "release":
        vendor_usage = "/cost in Claude Code" if run.harness == "claude" else "/status in Codex"
        verdict.message += (
            f"\n\n{usage_summary(run, root)}\nInteractive hooks cannot read session usage; check "
            f"{vendor_usage} for vendor-reported usage."
        )
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
        if run.status in {ACTIVE, "reviewing", "verifying", "publishing"} and run.plan == relative:
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
        reviewer_model=getattr(args, "reviewer_model", None),
        focused_review=getattr(args, "focused_review", False),
        codex_bin=getattr(args, "codex_bin", None),
        publish_pr=getattr(args, "publish_pr", False),
        headless=getattr(args, "headless", False),
        pr_remote=getattr(args, "pr_remote", "origin"),
        pr_destination=getattr(args, "pr_destination", "main"),
        max_rounds=args.max_rounds,
        round_cost=args.round_cost,
        round_turns=args.round_turns,
        round_minutes=args.round_minutes,
        max_cost=args.max_cost,
        review_cost=args.review_cost,
        stall_limit=args.stall_limit,
        check_timeout=args.check_timeout,
        session_id=getattr(args, "session_id", None),
        harness=getattr(args, "harness", None),
        model=getattr(args, "model", None),
        effort=getattr(args, "effort", None),
        setup=getattr(args, "setup", None),
        claude_permission_mode=getattr(args, "claude_permission_mode", "acceptEdits"),
        max_wait=getattr(args, "max_wait", 48),
        review_rounds=1 if getattr(args, "review_rounds", None) is None else args.review_rounds,
    )
    run.save(root)
    return run


def cmd_start(args: argparse.Namespace) -> int:
    """Start a run and print the token the session must echo."""
    root = repo_root(Path.cwd())
    preflight(root, args)
    run = create_run(root, args)
    open_items, done = plan_items((root / run.plan).read_text(encoding="utf-8"))
    print(f"Delivery run {run.token} started for {run.plan}")
    print(f"  open items: {len(open_items)}, done: {done}, check: {run.check}")
    print(f"  mode: {run.mode}, cross-review: {run.cross_review}, reviewer: {run.reviewer}")
    print("The guard binds to this session at the end of the current turn.")
    print(f"{TOKEN_LINE} {run.token}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Print runs of this worktree, or all registered worktrees when requested."""
    root = repo_root(Path.cwd())
    all_worktrees = getattr(args, "all_worktrees", False)
    runs = (
        worktree_runs(root, include_completed=True)
        if all_worktrees
        else [(root, run) for run in load_runs(root)]
    )
    if not runs:
        print(
            "No delivery runs in registered worktrees."
            if all_worktrees
            else "No delivery runs in this worktree."
        )
    for workdir, run in runs:
        if all_worktrees:
            print(f"Worktree: {workdir}")
        plan = workdir / run.plan
        open_items, done = (
            plan_items(plan.read_text(encoding="utf-8")) if plan.is_file() else ([], 0)
        )
        print(
            f"{run.token}  {run.status:<9}  {run.plan}  done {done}, open {len(open_items)}, "
            f"rounds {run.rounds}/{run.max_rounds}, session {run.session_id or 'unbound'}"
        )
        print(usage_summary(run, root))
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

    Claude roles carry a `model:` line; Codex reviewers default to Sol, while
    implementers inherit the session unless an explicit model is configured.
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
    for item in COMPANIONS:
        origin = source.parents[2] / item
        destination = copy.parent / Path(item).name
        if origin.is_file() and (
            not destination.is_file() or destination.read_bytes() != origin.read_bytes()
        ):
            shutil.copy2(origin, destination)
            changes.append(str(PROJECT_COPY.parent / Path(item).name))
    if not worktrees_ignored(target):
        gitignore = target / ".gitignore"
        text = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
        separator = "" if not text or text.endswith("\n") else "\n"
        gitignore.write_text(f"{text}{separator}{WORKTREES_DIRNAME}/\n", encoding="utf-8")
        changes.append(f".gitignore: {WORKTREES_DIRNAME}/")

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
    for item in COMPANIONS:
        if not (target / PROJECT_COPY.parent / Path(item).name).is_file():
            problems.append(f"{PROJECT_COPY.parent / Path(item).name} is missing; reinstall")
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
    if not worktrees_ignored(target):
        problems.append(f"{WORKTREES_DIRNAME}/ is not git-ignored; run install")
    if shutil.which("uv") is None:
        problems.append("uv is not on PATH; the hooks run the guard with uv")
    if shutil.which("srt") is None:
        print("Optional gate sandbox: srt not installed (env-scrub remains available).")
    else:
        print("Optional gate sandbox: srt available.")
    if not codex_profile_mode():
        print(
            "Codex sandbox mode: network_access=false fallback (requires "
            f"{'.'.join(map(str, MIN_CODEX_PROFILE_VERSION))} for profiles)."
        )
    else:
        print("Codex sandbox mode: deliver permission profile.")
    risky = worktreeinclude_credentials(target)
    if risky:
        print("WARNING: .worktreeinclude may copy credential files: " + ", ".join(risky))
    problems += unlinked_skills(target)
    return problems


def unlinked_skills(root: Path) -> list[str]:
    """List project skills in .agents/skills/ that Claude Code cannot see.

    Codex reads project skills from .agents/skills/. Claude Code reads .claude/skills/, so each
    skill needs a relative symlink there. Only checked when the project uses Claude Code.
    """
    if not (root / ".claude").is_dir():
        return []
    problems = []
    for skill in sorted((root / ".agents" / "skills").glob("*/SKILL.md")):
        name = skill.parent.name
        link = root / ".claude" / "skills" / name
        if not link.exists() or link.resolve() != skill.parent.resolve():
            problems.append(
                f".claude/skills/{name} must link to .agents/skills/{name}: "
                f"ln -s ../../.agents/skills/{name} .claude/skills/{name}"
            )
    return problems


def worktreeinclude_credentials(root: Path) -> list[str]:
    """List .worktreeinclude entries that may copy secrets into delivery worktrees."""
    include = root / ".worktreeinclude"
    if not include.is_file():
        return []
    credential_names = {Path(item).name for item in CREDENTIAL_PATHS}
    return [
        line.strip()
        for line in include.read_text(encoding="utf-8").splitlines()
        if line.strip()
        and (
            any(part.startswith(".env") for part in Path(line).parts)
            or any(part in credential_names for part in Path(line).parts)
        )
    ]


def secret_scan(root: Path, base: str) -> None:
    """Run gitleaks against the commits that would be published."""
    executable = shutil.which("gitleaks")
    if executable is None:
        prek_home = Path(os.environ.get("PREK_HOME") or Path.home() / ".cache" / "prek")
        candidates = sorted(prek_home.glob("hooks/golang-*/bin/gitleaks"))
        executable = str(candidates[-1]) if candidates else None
    if executable is None:
        raise SystemExit(
            "Publication requires gitleaks; install the pinned version from .pre-commit-config.yaml"
        )
    report = runs_dir(root) / "gitleaks-report.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    command = [
        executable,
        "git",
        f"--log-opts={base}..HEAD",
        "--report-path",
        str(report),
        "--redact",
    ]
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(
            f"Secret scan blocked publication; report: {report}\n{result.stdout}{result.stderr}"
        )


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


def default_branch(plan: str) -> str:
    """Name feature branches after the plan topic, without its filing date."""
    topic = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", Path(plan).stem)
    return f"feat/{slug(topic)}"


def create_worktree(root: Path, plan: str, branch: str | None) -> Path:
    """Create a worktree in .worktrees/ on a new branch and copy files listed in .worktreeinclude.

    Worktrees always go into the main worktree's .worktrees/, also when started from a linked
    worktree, so they never nest.
    """
    name = slug(Path(plan).stem)
    branch = branch or default_branch(plan)
    main = main_root(root)
    if not worktrees_ignored(main):
        raise SystemExit(f"Add {WORKTREES_DIRNAME}/ to {main / '.gitignore'} first")
    path = main / WORKTREES_DIRNAME / name
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


def headless_prompt(run: Run, plan: str, root: Path | None = None) -> str:
    """Return the first prompt of an unattended run."""
    review = (
        f" Finish with `{shlex.join(review_command(run))}`."
        if run.cross_review == "final" and not latest_review(root or Path.cwd(), run)
        else ""
    )
    publication = ""
    if run.publish_pr:
        forge, helper = pr_forge(root or Path.cwd(), run.pr_remote)
        publication = (
            f" This run explicitly authorizes a normal branch push and {forge} PR creation "
            f"through the deliver publish command after verification. Follow "
            f"{helper.parents[1].name} and its description format. Report the actual PR URL; "
            "do not stop at a copy-ready handoff."
        )
    return (
        f"Use the deliver skill to deliver the plan in {plan} end to end in {run.mode} mode. "
        f"The delivery run {run.token} is already started; do not start "
        f"another.{review}{publication} "
        f"The review budget is {run.review_rounds}; implement required fixes autonomously. "
        "The outer controller owns the final broad gate and publication. Run focused checks only "
        "inside the agent sandbox, then stage the PR with publish; do not repeat the full suite. "
        f"Use the current controller at {Path(__file__).resolve()} for all delivery commands, "
        "including resolve-review, block, and publish; do not use an older worktree copy. "
        "You are running unattended: make rulings instead of asking, and record them. "
        f"{protocol(run)}"
    )


def writable_paths(workdir: Path) -> list[str]:
    """Return explicit writable roots needed by a sandboxed implementation agent.

    - uv's cache, for `uv sync`, `uv add` and `uv run`.
    - The repository's shared git directory: a linked worktree keeps its refs and objects
      there, so without it every `git add` and `git commit` fails.
    - The worktree's own git directory, which holds its index and the run state. Codex marks
      the gitdir a `.git` file points to read-only, also inside the shared directory, unless it
      is a writable root of its own.
    - prek's cache, which the git hooks behind `task lint` and every commit use.
    - npm's cache, for `npm ci` and `npm install` in frontend projects.
    - Local agent skills and Codex configuration: Codex otherwise protects these even
      inside the worktree. Never grant a linked directory outside this worktree.
    """
    try:
        cache = subprocess.run(
            ["uv", "cache", "dir"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        cache = str(Path.home() / ".cache" / "uv")
    common_dir = git(workdir, "rev-parse", "--path-format=absolute", "--git-common-dir")
    git_dir = git(workdir, "rev-parse", "--absolute-git-dir")
    git_dirs = [common_dir] if git_dir == common_dir else [common_dir, git_dir]
    prek = os.environ.get("PREK_HOME") or str(Path.home() / ".cache" / "prek")
    npm = os.environ.get("npm_config_cache") or str(Path.home() / ".npm")  # noqa: SIM112
    root = workdir.resolve()
    harness_paths = []
    for name in (".agents", ".codex"):
        path = root / name
        resolved = path.resolve()
        if path.is_dir() and resolved.is_relative_to(root):
            harness_paths.append(str(resolved))
    return list(dict.fromkeys([cache, *git_dirs, prek, npm, *harness_paths]))


def delivery_config(root: Path) -> dict[str, Any]:
    """Read optional repository-specific delivery sandbox settings."""
    path = root / "deliver.toml"
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise SystemExit(f"Invalid {path}: {exc}") from exc
    sandbox = data.get("sandbox", {})
    if not isinstance(sandbox, dict):
        raise SystemExit(f"[sandbox] in {path} must be a table")
    return sandbox


def allowed_hosts(root: Path) -> list[str]:
    """Return package registries plus documented repository-specific hosts."""
    extras = delivery_config(root).get("extra_hosts", [])
    if not isinstance(extras, list) or any(not isinstance(host, str) for host in extras):
        raise SystemExit("[sandbox].extra_hosts must be an array of host strings")
    return list(dict.fromkeys([*PACKAGE_HOSTS, *extras]))


def protected_git_paths(root: Path) -> list[str]:
    """Return shared Git hook/config paths that sandboxed rounds must not edit."""
    common = Path(git(root, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    own = Path(git(root, "rev-parse", "--absolute-git-dir"))
    paths = [common / "hooks", common / "config", own / "config.worktree"]
    return [str(path) for path in paths if path.exists()]


def protected_git_hashes(root: Path) -> dict[str, str]:
    """Hash protected Git configuration files/directories for round integrity checks."""
    hashes: dict[str, str] = {}
    for raw in protected_git_paths(root):
        path = Path(raw)
        digest = hashlib.sha256()
        files = (
            sorted(item for item in path.rglob("*") if item.is_file()) if path.is_dir() else [path]
        )
        for item in files:
            digest.update(str(item.relative_to(path) if path.is_dir() else item.name).encode())
            digest.update(item.read_bytes())
        hashes[raw] = digest.hexdigest()
    return hashes


def sandbox_environment() -> dict[str, str]:
    """Return an environment for model-controlled processes without common credentials."""
    env = {key: value for key, value in os.environ.items() if key not in CREDENTIAL_ENV}
    env["CLAUDE_CODE_SUBPROCESS_ENV_SCRUB"] = "1"
    return env


def credential_file_paths(root: Path) -> list[str]:
    """Return shared credential paths plus env files in the active and main checkouts."""
    paths = [str(Path(path).expanduser()) for path in CREDENTIAL_PATHS]
    roots = {root.resolve(), main_root(root).resolve()}
    paths.extend(str(path / ".env*") for path in roots)
    return list(dict.fromkeys(paths))


def codex_profile_mode() -> bool:
    """Tell whether this Codex CLI meets the minimum version for permission profiles."""
    version = cli_version("codex")
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", version)
    return bool(match and tuple(map(int, match.groups())) >= MIN_CODEX_PROFILE_VERSION)


def claude_sandbox_settings(writable: list[str], root: Path | None = None) -> str:
    """Return session settings that confine Claude's shell commands to the worktree.

    Shell commands run in the operating-system sandbox and are approved automatically; they can
    write to the worktree, the temp directory and the given paths, and reach package hosts only.
    File edits are approved by the acceptEdits permission mode.
    """
    sandbox = {
        "enabled": True,
        "autoAllowBashIfSandboxed": True,
        "allowUnsandboxedCommands": False,
        "filesystem": {
            "allowWrite": writable,
            "denyWrite": protected_git_paths(root or Path.cwd()),
        },
        "network": {"allowedDomains": allowed_hosts(root or Path.cwd())},
        "credentials": {
            "files": {"mode": "deny", "paths": credential_file_paths(root or Path.cwd())},
            "envVars": {"mode": "deny", "names": CREDENTIAL_ENV},
        },
        "failIfUnavailable": True,
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
    round_cost: float | None = None,
    round_turns: int | None = None,
    root: Path | None = None,
) -> list[str]:
    """Return the CLI invocation for one headless round.

    Claude Code gets a preset session id so the run is bound from the start. Codex reports its
    thread id in the JSON event stream, and later rounds resume that thread. Both are confined
    to the worktree: Claude by its Bash sandbox, Codex by its workspace-write sandbox.
    """
    root = root or Path.cwd()
    if harness == "claude":
        command = ["claude", "-p", "--permission-mode", mode]
        if cli_supports_argument("claude", "--permission-prompts"):
            command += ["--permission-prompts", "none"]
        command += ["--session-id" if first else "--resume", str(session)]
        command += ["--settings", claude_sandbox_settings(writable or [], root)]
        command += ["--output-format", "stream-json", "--verbose"]
        command += ["--model", model] if model else []
        command += ["--effort", effort] if effort else []
        if round_cost is not None:
            command += ["--max-budget-usd", str(round_cost)]
        if round_turns is not None:
            command += ["--max-turns", str(round_turns)]
        return [*command, prompt]
    command = ["codex", "exec"] if first else ["codex", "exec", "resume", str(session)]
    command += ["--json", "-c", 'approval_policy="never"']
    if codex_profile_mode():
        command += ["-c", 'default_permissions="deliver"', "-c", "features.network_proxy=true"]
        for path in writable or []:
            command += ["-c", f"permissions.deliver.workspace_roots.{json.dumps(path)}=true"]
        for path in credential_file_paths(root):
            command += ["-c", f'permissions.deliver.filesystem.{json.dumps(path)}="deny"']
        for path in protected_git_paths(root):
            command += ["-c", f'permissions.deliver.filesystem.{json.dumps(path)}="read"']
        for host in allowed_hosts(root):
            command += ["-c", f'permissions.deliver.network.domains.{json.dumps(host)}="allow"']
    else:
        command += ["-c", 'sandbox_mode="workspace-write"']
        if writable:
            command += ["-c", f"sandbox_workspace_write.writable_roots={json.dumps(writable)}"]
        command += ["-c", "sandbox_workspace_write.network_access=false"]
    command += ["-m", model] if model else []
    command += ["-c", f'model_reasoning_effort="{effort}"'] if effort else []
    return [*command, prompt]


def configured_codex_model(root: Path) -> str | None:
    """Return the configured Codex model when events omit model metadata."""
    model, _ = first_setting(
        "model", [root / ".codex/config.toml", Path.home() / ".codex/config.toml"], read_toml
    )
    return model


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
    turns: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_creation_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    cost_basis: str = "unpriced"
    subtype: str | None = None
    model: str | None = None
    seconds: float = 0.0
    started_at: str | None = None
    compacted: bool = False
    session_id: str | None = None


def absorb(result: RoundResult, event: dict[str, Any]) -> None:
    """Record the session and limit signals of one Claude or Codex JSON event."""
    kind = event.get("type")
    if kind == "system" and event.get("subtype") == "compact_boundary":
        result.compacted = True
    result.model = result.model or event.get("model")
    if kind == "turn.completed":
        result.turns += 1
        usage = event.get("usage") or {}
        result.input_tokens += int(usage.get("input_tokens", 0) or 0)
        result.cached_input_tokens += int(
            usage.get("cached_input_tokens", usage.get("cache_read_input_tokens", 0)) or 0
        )
        result.cache_creation_tokens += int(usage.get("cache_creation_input_tokens", 0) or 0)
        result.output_tokens += int(usage.get("output_tokens", 0) or 0)
    if kind == "result":
        result.subtype = event.get("subtype")
        result.turns = int(event.get("num_turns", result.turns) or result.turns)
        usage = event.get("modelUsage") or {}
        for model, details in usage.items():
            result.model = result.model or model
            result.input_tokens += int(details.get("inputTokens", 0) or 0)
            result.cached_input_tokens += int(details.get("cacheReadInputTokens", 0) or 0)
            result.cache_creation_tokens += int(details.get("cacheCreationInputTokens", 0) or 0)
            result.output_tokens += int(details.get("outputTokens", 0) or 0)
        if isinstance(event.get("total_cost_usd"), int | float):
            result.cost_usd = float(event["total_cost_usd"])
            result.cost_basis = "cli-estimate"
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


def run_round(command: list[str], cwd: Path, log: Path, timeout: float = 5400) -> RoundResult:
    """Run one agent round, log its output, and return what it reported."""
    started_at = time.monotonic()
    started_iso = now()
    with (
        log.open("a", encoding="utf-8", buffering=1) as sink,
        subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=sandbox_environment() | {NESTED_ENV: "1"},
        ) as process,
    ):
        assert process.stdout is not None
        result = RoundResult(code=0)
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)

        def consume(line: str) -> None:
            sink.write(line)
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                if line.strip():
                    result.errors.append(line.strip())
                    say(paint(line.rstrip(), "dim"))
                return
            if not isinstance(event, dict):
                return
            absorb(result, event)
            if event.get("thread_id"):
                active = [run for run in load_runs(cwd) if run.status == ACTIVE]
                if len(active) == 1 and active[0].session_id != event["thread_id"]:
                    active[0].session_id = event["thread_id"]
                    active[0].save(cwd)
            say(*render_event(event))

        limited = False
        while process.poll() is None:
            remaining = timeout - (time.monotonic() - started_at)
            if remaining <= 0:
                limited = True
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait()
                break
            for key, _ in selector.select(min(remaining, 0.2)):
                line = key.fileobj.readline()  # ty: ignore[unresolved-attribute]
                if line:
                    consume(line)
        for line in process.stdout:
            consume(line)
        result.code = process.returncode
        if limited:
            result.subtype = "wall_clock_limit"
        result.seconds = time.monotonic() - started_at
        result.started_at = started_iso
        selector.close()
    return result


# --- usage and rate limits -------------------------------------------------------------------


def usage_path(run: Run, root: Path) -> Path:
    """Return the usage ledger of a run."""
    return run.directory(root) / "usage.jsonl"


def usage_rows(run: Run, root: Path) -> list[dict[str, Any]]:
    """Read the usage ledger rows of a run."""
    path = usage_path(run, root)
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def append_usage(run: Run, root: Path, result: RoundResult, kind: str, outcome: str) -> None:
    """Append one round or review to the usage ledger."""
    path = usage_path(run, root)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "round": len(usage_rows(run, root)) + 1,
        "kind": kind,
        "vendor": run.harness if kind == "implement" else run.reviewer,
        "session_id": run.session_id if kind == "implement" else None,
        "model": result.model or (run.model if kind == "implement" else run.reviewer_model),
        "started": result.started_at or now(),
        "seconds": round(result.seconds, 3),
        "turns": result.turns,
        "input_tokens": result.input_tokens,
        "cached_input_tokens": result.cached_input_tokens,
        "cache_creation_tokens": result.cache_creation_tokens,
        "output_tokens": result.output_tokens,
        "cost_usd": result.cost_usd,
        "cost_basis": result.cost_basis,
        "outcome": outcome,
    }
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row) + "\n")


def usage_summary(run: Run, root: Path) -> str:
    """Summarize tokens, estimated cost, wall time and waits of a run."""
    rows = usage_rows(run, root)
    total = sum(row.get("cost_usd") or 0 for row in rows)
    seconds = sum(row.get("seconds") or 0 for row in rows)
    unknown = sum(row.get("cost_usd") is None for row in rows)
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(
            (row.get("vendor") or "unknown", row.get("model") or "unknown"), []
        ).append(row)
    details = []
    for (vendor, model), items in sorted(groups.items()):
        keys = ("input_tokens", "cached_input_tokens", "cache_creation_tokens", "output_tokens")
        totals = {key: sum(item.get(key) or 0 for item in items) for key in keys}
        cost = sum(item.get("cost_usd") or 0 for item in items)
        label = (
            "USD unknown" if any(item.get("cost_usd") is None for item in items) else f"${cost:.2f}"
        )
        tokens = (
            f"in {totals['input_tokens']:,}, cached {totals['cached_input_tokens']:,}, "
            f"cache-write {totals['cache_creation_tokens']:,}, out "
            f"{totals['output_tokens']:,}"
            if all(item.get(key) is not None for item in items for key in keys)
            else "token counts unknown"
        )
        details.append(f"  {vendor}/{model}: {tokens}; {label}")
    cap = "disabled" if not run.max_cost else f"${run.max_cost:.2f}"
    header = (
        f"Usage: {sum(r['kind'] == 'implement' for r in rows)} rounds, "
        f"{sum(r['kind'] == 'review' for r in rows)} reviews; ${total:.2f} estimated / {cap} cap; "
        f"{seconds / 60:.1f} min; {run.waited_seconds / 60:.1f} min waiting"
    )
    if unknown:
        header += f"; {unknown} unpriced entries"
    return "\n".join([header, *details])


def ledger_cost(run: Run, root: Path) -> float:
    """Return the estimated USD spent by a run so far."""
    return sum(row.get("cost_usd") or 0 for row in usage_rows(run, root))


def remaining_budget(run: Run, root: Path) -> float:
    """Return the USD left in the run cost budget."""
    return float("inf") if run.max_cost == 0 else run.max_cost - ledger_cost(run, root)


def clipped_round_cost(run: Run, root: Path) -> float:
    """Return the round cost cap, clipped to the remaining run budget."""
    return min(run.round_cost, remaining_budget(run, root))


def cost_budget_exhausted(run: Run, root: Path) -> bool:
    """Tell whether too little run budget remains for another round."""
    return run.max_cost > 0 and remaining_budget(run, root) < 0.25


def round_exhausted_run_budget(run: Run, root: Path) -> bool:
    """Whether the ledger, including reviews started inside the round, reached the run cap."""
    return run.max_cost > 0 and ledger_cost(run, root) >= run.max_cost


def cost_resume_allowed(run: Run, requested_max: float | None) -> bool:
    """Tell whether a cost-exhausted run may resume with the requested cap."""
    return not (run.status == "exhausted" and run.note and "max_cost" in run.note) or (
        requested_max is not None and requested_max > run.max_cost
    )


def claude_round_delta(result: RoundResult, rows: list[dict[str, Any]]) -> float | None:
    """Return a resumed Claude session's cost for this round only."""
    if result.cost_usd is None:
        return None
    previous = sum(
        row.get("cost_usd") or 0
        for row in rows
        if row.get("kind") == "implement"
        and row.get("vendor") == "claude"
        and row.get("session_id") == result.session_id
    )
    return max(0.0, result.cost_usd - previous)


def claude_token_delta(result: RoundResult, rows: list[dict[str, Any]]) -> None:
    """Reduce a resumed Claude session's cumulative tokens to this round's."""
    for key in ("input_tokens", "cached_input_tokens", "cache_creation_tokens", "output_tokens"):
        previous = sum(
            row.get(key) or 0
            for row in rows
            if row.get("vendor") == "claude" and row.get("session_id") == result.session_id
        )
        setattr(result, key, max(0, getattr(result, key) - previous))


def claude_budget_requires_fresh_session(subtype: str | None) -> bool:
    """Tell whether a Claude result subtype means the session budget is spent."""
    return subtype == "error_max_budget_usd"


def price_codex(result: RoundResult, fallback_model: str | None = None) -> None:
    """Estimate Codex cost from the maintained adjacent TOML price table."""
    result.model = result.model or fallback_model
    if not result.model:
        return
    try:
        prices = tomllib.loads(companion("deliver/scripts/prices.toml").read_text())["prices"][
            result.model
        ]
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return
    # Codex reports cached input as a subset of input_tokens, so only the remainder is uncached.
    uncached = max(0, result.input_tokens - result.cached_input_tokens)
    result.cost_usd = (
        uncached * prices.get("input", 0)
        + result.cached_input_tokens * prices.get("cached_input", 0)
        + result.cache_creation_tokens * prices.get("cache_creation", 0)
        + result.output_tokens * prices.get("output", 0)
    ) / 1_000_000
    result.cost_basis = "list-price"


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
    if args.harness == "claude" and not args.effort and os.environ.get("CLAUDE_CODE_EFFORT_LEVEL"):
        effort, effort_origin = os.environ["CLAUDE_CODE_EFFORT_LEVEL"], "CLAUDE_CODE_EFFORT_LEVEL"
    rows = {
        "vendor": f"{'Claude Code' if args.harness == 'claude' else 'Codex'} "
        f"{cli_version(args.harness)}",
        "model": f"{model or 'CLI default'}" + (f"  ({origin})" if origin else ""),
        "effort": f"{effort or 'CLI default'}" + (f"  ({effort_origin})" if effort_origin else ""),
        "mode": args.mode,
    }
    if args.cross_review == "final":
        rows["final review"] = (
            f"{args.reviewer or 'other vendor'}; model "
            f"{args.reviewer_model or 'CLI default'}; effort high"
        )
        if args.codex_bin:
            rows["Codex reviewer"] = args.codex_bin
    rows["publication"] = (
        f"{pr_forge(root, args.pr_remote)[0]} PR to {args.pr_remote}/{args.pr_destination}"
        if args.publish_pr
        else "local completion"
    )
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


@functools.cache
def cli_supports_argument(harness: str, argument: str) -> bool:
    """Tell whether a CLI's help output advertises an argument, caching the result."""
    try:
        result = subprocess.run(
            [harness, "--help"], capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return argument in f"{result.stdout}\n{result.stderr}"


def print_header(args: argparse.Namespace, root: Path, plan: str) -> None:
    """Print what the run will use before it starts."""
    say(paint("● deliver ", "yellow", "bold") + paint(plan, "bold"))
    rows = session_settings(args, root)
    rows["caps"] = (
        f"${args.round_cost:.2f}/round, {args.round_turns} turns, {args.round_minutes:g} min, "
        f"${args.max_cost:.2f} run, ${args.review_cost:.2f} review"
    )
    rows["limits"] = f"waits up to {args.max_wait:g} h for usage limits"
    width = max(len(label) for label in rows)
    for label, value in rows.items():
        say(f"  {paint(label.ljust(width), 'dim')} {value}")


def arrow_selection(choices: list[tuple[str, Any]], default: int) -> Any:
    """Read a terminal menu in cbreak mode and always restore terminal settings."""
    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    selected, digits = default, ""
    columns, rows = shutil.get_terminal_size()
    visible = max(1, min(len(choices), rows - 6))

    def render() -> None:
        start = min(max(selected - visible // 2, 0), len(choices) - visible)
        for index in range(start, start + visible):
            title = choices[index][0]
            marker = paint("❯", "cyan", "bold") if index == selected else " "  # noqa: RUF001
            hint = " (default)" if index == default else ""
            title = title[: max(1, columns - len(str(index + 1)) - len(hint) - 7)]
            say(f"\r\033[2K  {marker} {index + 1}. {title}{hint}")
        say("\r\033[2K  ↑/↓ to move, Enter to select, number + Enter, q to cancel")

    try:
        tty.setcbreak(fd)
        render()
        while True:
            key = os.read(fd, 1)
            if key in {b"", b"q", b"Q", b"\x03", b"\x04"}:
                raise SystemExit(0)
            if key in {b"\r", b"\n"}:
                if not digits:
                    return choices[selected][1]
                if 1 <= int(digits) <= len(choices):
                    return choices[int(digits) - 1][1]
                digits = ""
            elif key == b"\x1b":
                sequence = b""
                while len(sequence) < 2 and select.select([fd], [], [], 0.1)[0]:
                    sequence += os.read(fd, 1)
                if sequence in {b"[A", b"OA"}:
                    selected = (selected - 1) % len(choices)
                elif sequence in {b"[B", b"OB"}:
                    selected = (selected + 1) % len(choices)
                digits = ""
            elif key.isdigit():
                digits += key.decode()
                if 1 <= int(digits) <= len(choices):
                    selected = int(digits) - 1
            elif key in {b"\x7f", b"\x08"}:
                digits = digits[:-1]
            sys.stdout.write(f"\033[{visible + 1}A")
            render()
    except KeyboardInterrupt:
        raise SystemExit(0) from None
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)


def supports_arrow_selection() -> bool:
    """Use cursor navigation only with real input/output terminals."""
    try:
        return (
            os.isatty(sys.stdin.fileno())
            and sys.stdout.isatty()
            and os.environ.get("TERM") != "dumb"
        )
    except (OSError, ValueError):
        return False


def select_option(label: str, choices: list[tuple[str, Any]], default: int = 0) -> Any:
    """Choose with arrows or numbered input, without a prompt dependency."""
    if not choices:
        raise SystemExit(f"No {label.lower()} available.")
    if not sys.stdin.isatty():
        raise SystemExit(
            "A terminal is required for selection. Pass --plan/--harness or "
            "--worktree/--token and explicit options."
        )
    say("", paint("● deliver", "yellow", "bold") + " · " + paint(label, "bold"))
    if supports_arrow_selection():
        return arrow_selection(choices, default)
    for i, (title, _) in enumerate(choices, 1):
        selected = i == default + 1
        marker = paint("❯", "cyan", "bold") if selected else " "  # noqa: RUF001
        number = paint(f"{i}.", "dim")
        title = paint(title, "bold") if selected else title
        hint = paint(" (default)", "dim") if selected else ""
        say(f"  {marker} {number} {title}{hint}")
    while True:
        try:
            value = input(paint(f"  Choose [{default + 1}], q to cancel: ", "cyan")).strip()
        except (EOFError, KeyboardInterrupt):
            raise SystemExit(0) from None
        if value.lower() == "q":
            raise SystemExit(0)
        if not value:
            return choices[default][1]
        if value.isdigit() and 1 <= int(value) <= len(choices):
            return choices[int(value) - 1][1]
        note("Enter one of the listed numbers.")


def worktree_runs(root: Path, *, include_completed: bool = False) -> list[tuple[Path, Run]]:
    """Find runs across existing worktrees, most recently updated first."""
    found = []
    for line in git(root, "worktree", "list", "--porcelain").splitlines():
        if not line.startswith("worktree "):
            continue
        path = Path(line[9:])
        if not path.is_dir():
            continue
        try:
            found.extend(
                (path, run)
                for run in load_runs(path)
                if include_completed or run.status != "completed"
            )
        except (OSError, ValueError, TypeError) as exc:
            say(f"Cannot read runs in {path}: {exc}")
    return sorted(found, key=lambda item: item[1].updated, reverse=True)


def run_title(root: Path, run: Run) -> str:
    """Describe a run with branch, plan progress, and saved-session availability."""
    plan = root / run.plan
    opened, done = plan_items(plan.read_text()) if plan.is_file() else ([], 0)
    branch = git(root, "branch", "--show-current") or "detached"
    return (
        f"{branch} / {Path(run.plan).name} / {done}/{done + len(opened)} done / "
        f"{run.token} / {run.status} / {run.updated} / session {run.session_id or 'unavailable'}"
    )


def implementation_harness(root: Path, run: Run) -> str:
    """Recover the vendor of a legacy run from its recorded event stream."""
    if run.harness:
        return run.harness
    log = run.directory(root) / "headless.jsonl"
    if log.is_file():
        with log.open() as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if event.get("type") == "thread.started":
                    return "codex"
                if event.get("type") == "system" and event.get("subtype") == "init":
                    return "claude"
    return "claude" if run.reviewer == "codex" else "codex"


def select_model(label: str, harness: str, *, review: bool = False) -> str:
    """Choose a model interactively, retaining defaults for explicit CLI workflows."""
    choices = (
        [("GPT-6.1 Sol", "gpt-6.1-sol"), ("GPT-6 Luna", "gpt-6-luna")]
        if harness == "codex"
        else [("Claude Sonnet", "sonnet"), ("Claude Opus", "opus"), ("Claude Haiku", "haiku")]
    )
    if review:
        choices = [choices[0]] if harness == "codex" else [choices[1]]
    default = 0
    if not sys.stdin.isatty():
        return choices[default][1]
    if not review:
        choices = [(f"{title} · effort {default_effort(model)}", model) for title, model in choices]
    return select_option(label, choices, default)


def default_effort(model: str | None) -> str:
    """Use high effort for small implementers and medium for frontier models."""
    return "high" if any(name in (model or "").lower() for name in ("luna", "haiku")) else "medium"


def reviewer_model(harness: str, model: str | None) -> str:
    """Require a frontier reviewer, including for explicit flags and saved runs."""
    model = model or ("gpt-6.1-sol" if harness == "codex" else "opus")
    if any(name in model.lower() for name in ("luna", "haiku")):
        raise SystemExit(
            "Review requires a frontier model (Codex Sol or Claude Opus), at high effort."
        )
    return model


def model_defaults(args: argparse.Namespace) -> None:
    """Apply the same role defaults to terminal and fully specified CLI runs."""
    if getattr(args, "harness", None) and not args.model:
        args.model = "gpt-6.1-sol" if args.harness == "codex" else "sonnet"
    if hasattr(args, "effort"):
        args.effort = args.effort or default_effort(args.model)
    if args.cross_review == "final" and args.reviewer:
        args.reviewer_model = reviewer_model(args.reviewer, args.reviewer_model)


def configure_dialog(args: argparse.Namespace, saved: Run | None = None) -> None:
    """Resolve explicit settings first, then saved settings and terminal defaults."""
    harness = getattr(args, "harness", None)
    default_harness = saved.harness if saved else "codex"
    args.harness = harness or select_option(
        "Implementer",
        [("Codex", "codex"), ("Claude", "claude")],
        1 if default_harness == "claude" else 0,
    )
    same_harness = saved is not None and args.harness == saved.harness
    args.model = getattr(args, "model", None) or (
        saved.model
        if same_harness and saved.model
        else select_model("Implementation model", args.harness)
    )
    effort = getattr(args, "effort", None)
    if not effort and same_harness and saved.model == args.model:
        effort = saved.effort
    if not effort:
        default = default_effort(args.model)
        levels = ["low", "medium", "high"]
        effort = (
            select_option(
                "Implementation effort",
                [(level.title(), level) for level in levels],
                levels.index(default),
            )
            if sys.stdin.isatty()
            else default
        )
    args.effort = effort
    other = "claude" if args.harness == "codex" else "codex"
    default_reviewer = saved.reviewer if saved and saved.reviewer != args.harness else other
    reviewer = getattr(args, "reviewer", None)
    budget = getattr(args, "review_rounds", None)
    if reviewer is None and budget != 0:
        reviewer = select_option(
            "Reviewer",
            [
                (other.title(), other),
                ("No review", "off"),
            ],
            1 if saved and saved.cross_review == "off" else 0,
        )
    args.reviewer = None if reviewer in {None, "off"} else reviewer
    if budget is None:
        default_budget = saved.review_rounds if saved else 1
        if reviewer == "off":
            default_budget = 0
        budget = select_option("Review rounds", [(str(n), n) for n in range(4)], default_budget)
    args.review_rounds = budget
    args.cross_review = "final" if budget else "off"
    if budget and not args.reviewer:
        args.reviewer = default_reviewer
    if budget and args.reviewer == args.harness:
        raise SystemExit("Choose the other vendor for cross-review, or zero review rounds.")
    args.reviewer_model = getattr(args, "reviewer_model", None) or (
        saved.reviewer_model
        if saved and saved.reviewer == args.reviewer and saved.reviewer_model
        else select_model("Review model", str(args.reviewer), review=True)
        if budget
        else None
    )
    model_defaults(args)


def prerequisite_branches(root: Path, plan: str) -> list[str]:
    """Find completed local prerequisite plans that are absent from this checkout."""
    text = (root / plan).read_text()
    links = re.findall(r"Depends on \[[^\]]+\]\(([^)]+\.md)\)", text)
    needed = []
    for link in links:
        path = (root / plan).parent / link
        relative = path.resolve().relative_to(root).as_posix()
        if path.is_file() and not plan_items(path.read_text())[0]:
            continue
        matches = []
        for branch in git(
            root, "for-each-ref", "--format=%(refname:short)", "refs/heads"
        ).splitlines():
            result = subprocess.run(
                ["git", "show", f"{branch}:{relative}"],
                cwd=root,
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                open_items, done = plan_items(result.stdout)
                if done and not open_items:
                    matches.append(branch)
        if not matches:
            raise SystemExit(
                f"Prerequisite {relative} is unfinished or unavailable locally. Complete it "
                "before resuming."
            )
        branch = next((name for name in matches if name.startswith("feat/")), matches[0])
        if branch not in needed:
            needed.append(branch)
    return needed


def preview_selection(
    root: Path, args: argparse.Namespace, run: Run | None, merges: list[str]
) -> None:
    """Print public configuration only, without credentials or vendor calls."""
    say(
        json.dumps(
            {
                "action": "resume" if run else "start",
                "plan": args.plan,
                "worktree": str(root),
                "token": run.token if run else None,
                "session": run.session_id if run else None,
                "harness": args.harness,
                "model": args.model,
                "effort": args.effort,
                "branch": git(root, "branch", "--show-current")
                if run or args.in_place
                else args.branch or default_branch(args.plan),
                "reviewer_effort": "high" if args.review_rounds else None,
                "reviewer": args.reviewer,
                "reviewer_model": args.reviewer_model if args.review_rounds else None,
                "review_rounds": args.review_rounds,
                "caps": {
                    key: getattr(args, key)
                    for key in (
                        "round_cost",
                        "round_turns",
                        "round_minutes",
                        "max_cost",
                        "review_cost",
                    )
                    if hasattr(args, key)
                },
                "reviews_completed": len(list((run.directory(root) / "reviews").glob("*.json")))
                if run
                else 0,
                "prerequisite_merges": merges,
                "publish_pr": args.publish_pr,
                "writable_paths": writable_paths(root),
            },
            indent=2,
        )
    )


@contextmanager
def controller_lock(root: Path):
    """Allow one controller per worktree; kernel locks recover after process death."""
    path = Path(git(root, "rev-parse", "--absolute-git-dir")) / "deliver-controller.lock"
    with path.open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(f"A delivery controller is already running in {root}.") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def launch_dialog(args: argparse.Namespace, root: Path) -> int:
    """Choose a plan and launch it, or redirect an existing plan to its saved run."""
    if not args.plan:
        plans = []
        for path in sorted((root / "docs/plans").glob("*.md"), reverse=True):
            opened, done = plan_items(path.read_text())
            if opened:
                relative = path.relative_to(root).as_posix()
                title = path.read_text().splitlines()[0].lstrip("# ")
                plans.append((f"{title} ({done}/{done + len(opened)}) [{path.name}]", relative))
        resumable = {run.plan for _, run in worktree_runs(root)}
        default = next((i for i, (_, plan) in enumerate(plans) if plan in resumable), 0)
        args.plan = select_option("Plan", plans, default)
    existing = [(path, run) for path, run in worktree_runs(root) if run.plan == args.plan]
    if existing and not args.in_place:
        path, run = select_option(
            "Existing delivery: resume",
            [(run_title(path, run), (path, run)) for path, run in existing],
        )
        resume = parser().parse_args(["resume", "--worktree", str(path), "--token", run.token])
        for name in (
            "harness",
            "model",
            "effort",
            "reviewer",
            "reviewer_model",
            "review_rounds",
            "preview",
        ):
            setattr(resume, name, getattr(args, name))
        return cmd_resume(resume)
    # Setup may have failed before a run was created. Reuse its registered worktree.
    target = main_root(root) / WORKTREES_DIRNAME / slug(Path(args.plan).stem)
    registered = [
        Path(line[9:])
        for line in git(root, "worktree", "list", "--porcelain").splitlines()
        if line.startswith("worktree ")
    ]
    if not args.in_place and target in registered:
        if not select_option(
            "Existing worktree without a saved run",
            [("Continue in this worktree", True), ("Cancel", False)],
        ):
            return 0
        root = target
        args.in_place = True
        args.launch_root = str(root)
    configure_dialog(args)
    args.prerequisite_merges = prerequisite_branches(root, args.plan)
    if getattr(args, "review_rounds", 1) == 0:
        args.cross_review = "off"
    args.publish_pr = True
    args.check = args.check or default_check(root)
    preview_selection(root, args, None, args.prerequisite_merges)
    if args.preview:
        return 0
    if select_option("Start delivery?", [("Start", True), ("Cancel", False)]):
        # All required choices are resolved; avoid entering the dialog again.
        return cmd_headless(args)
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    """Resume a saved run from any project worktree using the current controller."""
    source = repo_root(Path.cwd()).resolve()
    candidates = [
        (path, run)
        for path, run in worktree_runs(source)
        if (not args.worktree or path.resolve() == args.worktree.resolve())
        and (not args.token or run.token == args.token)
    ]
    if args.worktree and args.token and len(candidates) == 1:
        workdir, run = candidates[0]
    else:
        workdir, run = select_option(
            "Delivery to resume",
            [
                (
                    run_title(path, run),
                    (path, run),
                )
                for path, run in candidates
            ],
        )
    run.harness = implementation_harness(workdir, run)
    original_harness = run.harness
    overrides = {key: value for key, value in vars(args).items() if value is not None}
    options = argparse.Namespace(**asdict(run))
    # Prompt defaults must come from the run, not count as explicit choices.
    for name in ("harness", "model", "reviewer", "reviewer_model", "review_rounds"):
        setattr(options, name, getattr(args, name, None))
    options.effort = args.effort
    options.plan = run.plan
    configure_dialog(options, run)
    for name in (
        "max_rounds",
        "max_wait",
        "round_cost",
        "round_turns",
        "round_minutes",
        "max_cost",
        "review_cost",
    ):
        if name in overrides:
            setattr(options, name, overrides[name])
    if not cost_resume_allowed(run, args.max_cost):
        raise SystemExit(
            "This run exhausted --max-cost. Explicitly raise it with --max-cost to resume."
        )
    merges = prerequisite_branches(workdir, run.plan)
    fresh = args.fresh_session or options.harness != original_harness
    if (
        not fresh
        and run.note
        and re.search(
            r"session.*(?:not found|unavailable)|no (?:conversation|session)", run.note, re.I
        )
    ):
        fresh = select_option(
            "Saved session unavailable",
            [("Start a fresh session in this run", True), ("Cancel", False)],
        )
        if not fresh:
            return 0
    if fresh:
        say("Keep this delivery run and files; start a fresh vendor session with a handoff.")
    preview_selection(workdir, options, run, merges)
    if args.preview:
        return 0
    if (not args.worktree or not args.token or sys.stdin.isatty()) and not select_option(
        "Resume with the displayed settings and prerequisite merges?",
        [("Resume", True), ("Cancel", False)],
    ):
        return 0
    if run.rounds >= options.max_rounds or run.waited_seconds >= options.max_wait * 3600:
        raise SystemExit(
            "Saved budget is exhausted. Explicitly increase --max-rounds or --max-wait."
        )
    options.dry_run = False
    with controller_lock(workdir):
        preflight(source, options)
        for branch in merges:
            result = subprocess.run(["git", "merge", "--no-edit", branch], cwd=workdir, check=False)
            if result.returncode:
                raise SystemExit(
                    "Prerequisite merge needs attention; existing edits are preserved. "
                    "Resolve it before resuming."
                )
        setup = run.setup or default_setup(workdir) or "true"
        if subprocess.run(
            setup, shell=True, cwd=workdir, env=os.environ | {"VIRTUAL_ENV": ""}, check=False
        ).returncode:
            raise SystemExit(f"Setup failed: {setup}. The saved run and session are preserved.")
        for name in (
            "harness",
            "model",
            "effort",
            "reviewer",
            "reviewer_model",
            "review_rounds",
            "cross_review",
            "codex_bin",
            "max_rounds",
            "max_wait",
            "round_cost",
            "round_turns",
            "round_minutes",
            "max_cost",
            "review_cost",
        ):
            setattr(run, name, getattr(options, name))
        if fresh:
            run.session_id = str(uuid.uuid4()) if run.harness == "claude" else None
        run.stalls, run.fingerprint = 0, None
        run.headless = True
        run.status = (
            run.status if run.status in {"publishing", "verifying", "reviewing"} else ACTIVE
        )
        run.save(workdir)
        with (run.directory(workdir) / "headless.jsonl").open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "type": "delivery.resumed",
                        "token": run.token,
                        "session": run.session_id,
                        "at": now(),
                    }
                )
                + "\n"
            )
        options.claude_permission_mode = run.claude_permission_mode
        return continue_run(workdir, run, options, first=True if fresh else None)


def cmd_headless(args: argparse.Namespace) -> int:
    """Deliver a plan unattended, resuming the session until the guard releases it."""
    source_root = repo_root(Path.cwd()).resolve()
    if (
        not args.plan
        or not args.harness
        or args.preview
        or (
            not args.in_place
            and any(run.plan == args.plan for _, run in worktree_runs(source_root))
        )
    ):
        return launch_dialog(args, source_root)
    if args.review_rounds == 0:
        args.cross_review = "off"
    plan = (source_root / args.plan).resolve().relative_to(source_root).as_posix()
    if not args.dry_run:
        preflight(source_root, args)
    model_defaults(args)
    print_header(args, source_root, plan)
    if not args.in_place:
        tracked = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--", plan], cwd=source_root, check=False
        )
        listed = git(source_root, "ls-files", plan)
        if not listed or tracked.returncode != 0:
            raise SystemExit(f"Commit {plan} first: the new worktree starts from HEAD")
    merges = prerequisite_branches(source_root, plan)
    if any(branch not in getattr(args, "prerequisite_merges", []) for branch in merges):
        raise SystemExit(
            "Missing prerequisite branches. Start the run from the interactive menu to "
            "approve their integration."
        )
    workdir = source_root if args.in_place else create_worktree(source_root, plan, args.branch)
    say(f"  {paint('worktree', 'dim')} {workdir}", "")
    with controller_lock(workdir):
        for branch in merges:
            git(workdir, "merge", "--no-edit", branch)
        session = str(uuid.uuid4()) if args.harness == "claude" else None
        args.plan = plan
        args.session_id = session
        setup = args.setup if args.setup is not None else default_setup(workdir)
        if setup and not args.dry_run:
            note(f"Preparing the worktree: {setup}")
            if subprocess.run(setup, shell=True, cwd=workdir, check=False).returncode != 0:
                raise SystemExit(f"Setup command failed: {setup}")
        args.headless = True
        run = create_run(workdir, args)
        writable = writable_paths(workdir)
        prompt = headless_prompt(run, plan, workdir)
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
                root=workdir,
            )
            say(" ".join(command))
            return 0

        return continue_run(workdir, run, args, first=True)


def advance_handoff(root: Path, run: Run) -> tuple[Run, Verdict]:
    """Verify or publish outside the child sandbox, returning repair instructions on failure."""
    if run.status == "publishing":
        run = publish_pending(root, run)
        if run.status == ACTIVE and (
            run.verified_signature != state_signature(root) or run.verified_check != run.check
        ):
            return run, Verdict("continue", run.note or "Fix the failed final gate.")
    elif run.status == "reviewing":
        command = review_command(run)
        result = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
        if result.returncode not in {0, 2}:
            run.status = ACTIVE
            run.note = (
                "Outer cross-review failed with exit code "
                f"{result.returncode}:\n{result.stdout}{result.stderr}"
            )
            run.save(root)
            return run, Verdict("continue", run.note)
        run.status = ACTIVE
        run.note = (result.stdout or "Outer cross-review finished.").strip()
    elif run.status == "verifying":
        run.status = ACTIVE
    if run.status != ACTIVE:
        return run, Verdict("release", run.note or f"Delivery is {run.status}.")
    verdict = evaluate(root, run, outer=True)
    run.save(root)
    return run, verdict


def continue_run(
    workdir: Path, run: Run, args: argparse.Namespace, *, first: bool | None = None
) -> int:
    """Continue one saved delivery run through the original vendor session."""
    session = run.session_id
    first = not bool(session) if first is None else first
    budget, backoff = args.max_wait * 3600, Backoff()
    writable = writable_paths(workdir)
    log = run.directory(workdir) / "headless.jsonl"
    prompt = headless_prompt(run, run.plan, workdir)
    if run.status in {"publishing", "verifying", "reviewing"}:
        run, verdict = advance_handoff(workdir, run)
        if verdict.action == "release":
            note(verdict.message)
            note(usage_summary(run, workdir))
            return 0 if run.status == "completed" else 1
        prompt = (
            full_protocol_prompt(run, verdict.message)
            if first
            else continuation_prompt(run, verdict.message)
        )
    if run.resume_at:
        resume = datetime.fromisoformat(run.resume_at)
        if resume.timestamp() > time.time():
            note(f"Continuing the saved usage-limit wait until {run.resume_at}.")
            wait_until(resume)
        run.resume_at = None
        run.save(workdir)
    try:
        while True:
            if cost_budget_exhausted(run, workdir):
                run.status, run.note = (
                    "exhausted",
                    "Run cost budget exhausted: remaining budget is below USD 0.25 "
                    f"(max_cost={run.max_cost:.2f}).",
                )
                run.save(workdir)
                note(run.note, "red")
                return 1
            round_cap = clipped_round_cost(run, workdir)
            command = agent_command(
                args.harness,
                prompt,
                session,
                first,
                args.model,
                args.claude_permission_mode,
                args.effort,
                writable,
                round_cap,
                run.round_turns,
                root=workdir,
            )
            protected_before = protected_git_hashes(workdir)
            result = run_round(command, workdir, log, run.round_minutes * 60)
            protected_after = protected_git_hashes(workdir)
            changed_protected = sorted(
                path
                for path in set(protected_before) | set(protected_after)
                if protected_before.get(path) != protected_after.get(path)
            )
            if changed_protected:
                run = find_run(workdir, run.token)
                run.status = "blocked"
                run.note = (
                    "Security integrity check found modified Git hook/config paths: "
                    + ", ".join(changed_protected)
                )
                run.save(workdir)
                note(run.note, "red")
                return 1

            session = session or result.thread
            run = find_run(workdir, run.token)
            if session and run.session_id != session:
                run.session_id = session
                run.save(workdir)
            result.session_id = run.session_id or session
            if args.harness == "codex":
                price_codex(result, run.model or configured_codex_model(workdir))
            else:
                result.model = result.model or run.model
                rows = usage_rows(run, workdir)
                claude_token_delta(result, rows)
                result.cost_usd = claude_round_delta(result, rows)
            if first and (result.started or SESSION_IN_USE.search("\n".join(result.errors))):
                first = False  # the session exists now; later attempts resume it
            run = find_run(workdir, run.token)
            limit = classify_limit(result, datetime.now(UTC))
            if limit is not None and run.status == ACTIVE:
                append_usage(run, workdir, result, "implement", "limited")
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
                    message = (
                        f"Your previous turn hit a {limit.kind} limit ({limit.detail}). Check the "
                        "plan and working tree, then continue."
                    )
                    prompt = (
                        full_protocol_prompt(run, message)
                        if result.compacted
                        else continuation_prompt(run, message)
                    )
                continue
            if result.subtype in {"error_max_budget_usd", "error_max_turns", "wall_clock_limit"}:
                append_usage(run, workdir, result, "implement", "limited")
                run.rounds += 1
                run.save(workdir)
                if session is None:
                    run.status, run.note = (
                        "stopped",
                        "The capped CLI round did not create a resumable session.",
                    )
                    run.save(workdir)
                    note(run.note, "red")
                    return 1
                message = (
                    "Your last turn hit its round limit. Commit what works, then continue with "
                    "the next open item."
                )
                if args.harness == "claude" and claude_budget_requires_fresh_session(
                    result.subtype
                ):
                    session = str(uuid.uuid4())
                    run.session_id = session
                    run.save(workdir)
                    first = True
                    prompt = full_protocol_prompt(run, message)
                else:
                    first = False
                    prompt = (
                        full_protocol_prompt(run, message)
                        if result.compacted
                        else continuation_prompt(run, message)
                    )
                continue
            append_usage(
                run, workdir, result, "implement", "success" if result.code == 0 else "error"
            )
            if round_exhausted_run_budget(run, workdir):
                run.status, run.note = (
                    "exhausted",
                    f"Run reached max_cost USD {run.max_cost:.2f} after a {args.harness} "
                    "round. Raise --max-cost to resume.",
                )
                run.save(workdir)
                note(run.note, "red")
                return 1
            backoff.reset()
            if (
                session is None
                or (result.code != 0 and first)
                or (result.code != 0 and not result.started and result.errors)
            ):
                run.status = "stopped"
                run.note = (
                    f"The {args.harness} CLI exited with code {result.code}; see {log}. "
                    + "\n".join(result.errors)[-1200:]
                )
                run.save(workdir)
                note(run.note, "red")
                return 1
            if run.status in {"publishing", "verifying", "reviewing"}:
                run, verdict = advance_handoff(workdir, run)
                if verdict.action == "release":
                    note(verdict.message)
                    return 0 if run.status == "completed" else 1
                prompt = (
                    full_protocol_prompt(run, verdict.message)
                    if result.compacted
                    else continuation_prompt(run, verdict.message)
                )
                first = False
                continue
            if run.status != ACTIVE:
                tone = "green" if run.status == "completed" else "red"
                note(
                    f"Run {run.token} ended inside the session: {run.status}. {run.note or ''}",
                    tone,
                )
                return 0 if run.status == "completed" else 1
            run.session_id = session
            verdict = evaluate(workdir, run, outer=True)
            run.note = verdict.message if verdict.action == "release" else run.note
            run.save(workdir)
            if verdict.action == "release":
                note(verdict.message, "green" if run.status == "completed" else "red")
                return 0 if run.status == "completed" else 1
            note(verdict.message.splitlines()[0])
            prompt = (
                full_protocol_prompt(run, verdict.message)
                if result.compacted
                else continuation_prompt(run, verdict.message)
            )
            first = False
    except KeyboardInterrupt:
        run = find_run(workdir, run.token)
        run.status, run.resume_at = "stopped", None
        run.note = f"Interrupted by the user. Plan progress is kept in {workdir}."
        run.save(workdir)
        say("")
        note(run.note, "red")
        return 130
    finally:
        with contextlib.suppress(OSError, SystemExit, ValueError):
            note(usage_summary(find_run(workdir, run.token), workdir))


def cmd_resolve_review(args: argparse.Namespace) -> int:
    """Verify and record each required review fix without starting another reviewer session."""
    root = repo_root(Path.cwd())
    run = find_run(root, args.token)
    if run.status != ACTIVE:
        raise SystemExit("Review resolution requires an active run")
    review = latest_review(root, run)
    if (
        not review
        or review.get("reviewer") != run.reviewer
        or (run.reviewer_model and review.get("model") != run.reviewer_model)
    ):
        raise SystemExit("No review from the configured reviewer is available")
    report = json.loads(args.fixes_file.read_text(encoding="utf-8"))
    fixes = report.get("fixes", [])
    if not isinstance(report.get("summary"), str) or not report["summary"].strip():
        raise SystemExit("The fix report needs a nonempty summary")
    findings = review.get("findings", [])
    required = {
        i for i, finding in enumerate(findings) if finding.get("severity") in {"blocking", "major"}
    }
    resolved: set[int] = set()
    for fix in fixes:
        index, change = fix.get("finding"), fix.get("change")
        if (
            type(index) is not int
            or index < 0
            or index >= len(findings)
            or index in resolved
            or not isinstance(change, str)
            or not change.strip()
        ):
            raise SystemExit("Each fix needs a unique finding index and a concrete change")
        resolved.add(index)
    if not required.issubset(resolved):
        raise SystemExit("Implement and document every blocking or major finding before continuing")
    if git(root, "status", "--porcelain"):
        raise SystemExit("Commit review fixes before recording their verification")
    code, tail = run_check(root, args.check, run.check_timeout, outer=True)
    if code != 0:
        raise SystemExit(f"Review fixes are not verified:\n{tail}")
    if git(root, "status", "--porcelain"):
        raise SystemExit("Verification changed files; commit them and verify again")
    record = {key: value for key, value in review.items() if key != "path"}
    record["resolution"] = {
        "signature": state_signature(root),
        "summary": report["summary"],
        "fixes": fixes,
        "check": args.check,
        "verified_at": now(),
    }
    path = Path(review["path"])
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    print(
        "Required review fixes verified. Continue to the final gate and PR; no new review is "
        "needed."
    )
    return 0


def publish_pending(root: Path, run: Run) -> Run:
    """Execute publication in the outer task, independently of the coding agent sandbox."""
    run.status = ACTIVE
    probe = replace(run, publish_pr=False, rounds=0, stalls=0, fingerprint=None)
    verdict = evaluate(root, probe, outer=True)
    run.verified_signature, run.verified_check = probe.verified_signature, probe.verified_check
    if probe.status != "completed":
        run.status = probe.status
        run.note = verdict.message
        run.save(root)
        return run
    run.save(root)
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        "publish",
        "--execute",
        "--token",
        run.token,
        "--title",
        run.pr_title or "",
        "--description-file",
        run.pr_description_file or "",
    ]
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        run.status = "blocked"
        run.note = (
            f"PR publication failed; local work is preserved:\n{result.stdout}{result.stderr}"
        )
        run.save(root)
        return run
    print(result.stdout.strip())
    return find_run(root, run.token)


def cmd_publish(args: argparse.Namespace) -> int:
    """Publish only verified, committed changes and record the forge's actual response."""
    root = repo_root(Path.cwd())
    run = find_run(root, args.token)
    if not run.publish_pr:
        raise SystemExit("This run does not authorize PR publication; start it with --publish-pr")
    if run.status != ACTIVE:
        raise SystemExit(f"Run {run.token} is {run.status}; publication requires an active run")
    if git(root, "status", "--porcelain"):
        raise SystemExit("Commit all changes before publication and repeat final review")
    head = git(root, "rev-parse", "HEAD")
    branch = git(root, "branch", "--show-current")
    if not branch or branch == run.pr_destination:
        raise SystemExit("Publication requires a feature branch distinct from the destination")
    probe = replace(run, publish_pr=False, rounds=0, stalls=0, fingerprint=None)
    verdict = evaluate(root, probe, outer=getattr(args, "execute", False))
    staged_gate = (
        run.headless and not getattr(args, "execute", False) and verdict.action == "handoff"
    )
    if probe.status != "completed" and not staged_gate:
        raise SystemExit(f"Publication blocked: {verdict.message}")
    secret_scan(root, f"{run.pr_remote}/{run.pr_destination}")
    run.verified_signature, run.verified_check = probe.verified_signature, probe.verified_check
    if run.headless and not args.execute:
        description = run.directory(root) / "pr-description.md"
        description.write_text(args.description_file.read_text(encoding="utf-8"), encoding="utf-8")
        run.pr_title, run.pr_description_file = args.title, str(description)
        run.status = "publishing"
        run.save(root)
        print("PR publication prepared. End this turn so the outer task can verify and publish.")
        return 0
    response_path = run.directory(root) / "pull-request.json"
    forge, helper = pr_forge(root, run.pr_remote)
    command = [
        sys.executable,
        str(helper),
        "--title",
        args.title,
        "--description-file",
        str(args.description_file.resolve()),
        "--remote",
        run.pr_remote,
        "--destination",
        run.pr_destination,
        "--source",
        branch,
        "--execute",
        "--push",
        "--result-file",
        str(response_path),
    ]
    command = publication_environment(root, command)
    result = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(
            f"PR publication failed; local work is preserved:\n{result.stdout}{result.stderr}"
        )
    receipt = pr_receipt(forge, json.loads(response_path.read_text(encoding="utf-8")))
    source_hash = receipt["commit"]
    matching_commit = bool(re.fullmatch(r"[0-9a-f]{12,40}", source_hash)) and (
        git(root, "rev-parse", "--verify", f"{source_hash}^{{commit}}") == head
    )
    if (
        receipt["state"] != "OPEN"
        or receipt["source"] != branch
        or not matching_commit
        or receipt["destination"] != run.pr_destination
        or not receipt["url"]
        or git(root, "rev-parse", "HEAD") != head
        or git(root, "status", "--porcelain")
    ):
        raise SystemExit("PR receipt does not match the verified branch; repeat publication")
    run.pr_url = receipt["url"]
    run.pr_head, run.pr_branch = head, branch
    run.save(root)
    print(f"Published: {run.pr_url}")
    return 0


# --- command line ----------------------------------------------------------------------------


def add_run_options(parser: argparse.ArgumentParser, *, require_plan: bool = True) -> None:
    """Add the options shared by start and headless."""
    parser.add_argument("--plan", required=require_plan, help="plan file with `- [ ]` items")
    parser.add_argument("--check", help="merge gate command (default: task check)")
    parser.add_argument("--mode", choices=["subagents", "single"], default="single")
    parser.add_argument(
        "--focused-review",
        action="store_true",
        help="one review followed by verified autonomous fixes",
    )
    parser.add_argument("--cross-review", choices=["off", "final"], default="off")
    parser.add_argument("--reviewer", choices=["claude", "codex"])
    parser.add_argument(
        "--reviewer-model", help="final reviewer model (Codex default: gpt-6.1-sol)"
    )
    parser.add_argument(
        "--codex-bin", help="Codex reviewer executable (default: CODEX_BIN or PATH)"
    )
    parser.add_argument("--publish-pr", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--pr-remote", default="origin")
    parser.add_argument("--pr-destination", default="main")
    parser.add_argument("--max-rounds", type=int, default=40)
    parser.add_argument("--round-cost", type=float, default=2.0)
    parser.add_argument("--round-turns", type=int, default=150)
    parser.add_argument("--round-minutes", type=float, default=90.0)
    parser.add_argument("--max-cost", type=float, default=10.0)
    parser.add_argument("--review-cost", type=float, default=2.0)
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

    status_command = commands.add_parser("status", help="show delivery runs")
    status_command.add_argument(
        "--all-worktrees", action="store_true", help="include runs in all registered worktrees"
    )
    status_command.set_defaults(func=cmd_status)

    for name, status in (("block", "blocked"), ("stop", "stopped")):
        command = commands.add_parser(name, help=f"mark a run {status}")
        command.add_argument("--token")
        command.add_argument("--reason", required=name == "block")
        command.set_defaults(func=lambda args, status=status: finish(args, status))

    commands.add_parser("hook", help="Stop-hook entry point").set_defaults(func=cmd_hook)

    resolve = commands.add_parser(
        "resolve-review", help="record tested fixes without another model review"
    )
    resolve.add_argument("--token", required=True)
    resolve.add_argument("--fixes-file", type=Path, required=True)
    resolve.add_argument("--check", required=True, help="focused regression test command")
    resolve.set_defaults(func=cmd_resolve_review)

    publish = commands.add_parser("publish", help="verify, push, and create the delivery PR")
    publish.add_argument("--token", required=True)
    publish.add_argument("--title", required=True)
    publish.add_argument("--description-file", type=Path, required=True)
    publish.add_argument("--execute", action="store_true", help="publish outside the agent sandbox")
    publish.set_defaults(func=cmd_publish)

    inst = commands.add_parser("install", help="install the guard hook and roles")
    inst.add_argument("--target", default=".")
    inst.add_argument(
        "--harness", nargs="+", choices=["claude", "codex"], default=["claude", "codex"]
    )
    inst.add_argument("--claude-implementer-model", help="default: sonnet, from the role file")
    inst.add_argument("--claude-reviewer-model", help="default: opus, from the role file")
    inst.add_argument("--codex-implementer-model", help="default: inherit the session model")
    inst.add_argument("--codex-reviewer-model", help="default: gpt-6.1-sol at high effort")
    inst.add_argument("--force", action="store_true", help="overwrite existing role files")
    inst.set_defaults(func=cmd_install)

    doc = commands.add_parser("doctor", help="check the installation")
    doc.add_argument("--target", default=".")
    doc.set_defaults(func=cmd_doctor)

    head = commands.add_parser("headless", help="deliver a plan unattended")
    add_run_options(head, require_plan=False)
    head.add_argument("--review-rounds", type=int, choices=range(4))
    head.add_argument(
        "--preview", action="store_true", help="choose options without changing anything"
    )
    head.add_argument("--harness", choices=["claude", "codex"])
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

    resume = commands.add_parser("resume", help="choose and resume an existing delivery run")
    resume.add_argument("--worktree", type=Path)
    resume.add_argument("--token")
    resume.add_argument("--harness", choices=["codex", "claude"])
    resume.add_argument("--model")
    resume.add_argument("--effort", choices=["low", "medium", "high"])
    resume.add_argument("--reviewer", choices=["codex", "claude"])
    resume.add_argument("--reviewer-model")
    resume.add_argument("--review-rounds", type=int, choices=range(4))
    resume.add_argument("--max-rounds", type=int)
    resume.add_argument("--max-wait", type=float)
    resume.add_argument("--round-cost", type=float)
    resume.add_argument("--round-turns", type=int)
    resume.add_argument("--round-minutes", type=float)
    resume.add_argument("--max-cost", type=float)
    resume.add_argument("--review-cost", type=float)
    resume.add_argument("--fresh-session", action="store_true")
    resume.add_argument("--preview", action="store_true")
    resume.set_defaults(func=cmd_resume)
    return root


def main(argv: list[str] | None = None) -> int:
    """Run the command line interface."""
    args = parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
