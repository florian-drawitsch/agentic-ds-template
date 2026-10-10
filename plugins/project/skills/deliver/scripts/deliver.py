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
requested, a cross-vendor review of the current state passed. Run state lives in the git
directory of the worktree, so it is never committed and each worktree has its own runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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

    verdict = evaluate(root, run)
    run.note = verdict.message if verdict.action == "release" else run.note
    run.save(root)
    if verdict.action == "continue":
        return {"decision": "block", "reason": verdict.message}
    print(verdict.message, file=sys.stderr)
    return {}


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


def claude_sandbox_settings() -> str:
    """Return session settings that confine Claude's shell commands to the worktree.

    Shell commands run in the operating-system sandbox and are approved automatically; they can
    write to the worktree, the temp directory and uv's cache, and reach package hosts only.
    File edits are approved by the acceptEdits permission mode.
    """
    try:
        cache = subprocess.run(
            ["uv", "cache", "dir"], capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        cache = str(Path.home() / ".cache" / "uv")
    sandbox = {
        "enabled": True,
        "autoAllowBashIfSandboxed": True,
        "allowUnsandboxedCommands": False,
        "filesystem": {"allowWrite": [cache]},
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
) -> list[str]:
    """Return the CLI invocation for one headless round.

    Claude Code gets a preset session id so the run is bound from the start. Codex reports its
    thread id in the JSON event stream, and later rounds resume that thread. Both are confined
    to the worktree: Claude by its Bash sandbox, Codex by its workspace-write sandbox.
    """
    if harness == "claude":
        command = ["claude", "-p", "--permission-mode", mode]
        command += ["--session-id" if first else "--resume", str(session)]
        command += ["--settings", claude_sandbox_settings()]
        command += ["--output-format", "stream-json", "--verbose"]
        command += ["--model", model] if model else []
        command += ["--effort", effort] if effort else []
        return [*command, prompt]
    command = ["codex", "exec"] if first else ["codex", "exec", "resume", str(session)]
    command += ["--json", "-c", 'sandbox_mode="workspace-write"', "-c", 'approval_policy="never"']
    command += ["-m", model] if model else []
    command += ["-c", f'model_reasoning_effort="{effort}"'] if effort else []
    return [*command, prompt]


def run_round(command: list[str], cwd: Path, log: Path) -> tuple[int, str | None]:
    """Run one agent round, log its JSON events, and return the exit code and thread id."""
    thread = None
    with (
        log.open("a", encoding="utf-8") as sink,
        subprocess.Popen(
            command, cwd=cwd, stdout=subprocess.PIPE, text=True, env=os.environ.copy()
        ) as process,
    ):
        assert process.stdout is not None
        for line in process.stdout:
            sink.write(line)
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            thread = thread or event.get("thread_id")
            text = event_text(event)
            if text:
                print(text, flush=True)
    return process.returncode, thread


def event_text(event: dict[str, Any]) -> str | None:
    """Extract assistant text from a Claude or Codex JSON event for console progress."""
    message = event.get("message") or {}
    if event.get("type") == "assistant" and isinstance(message.get("content"), list):
        texts = [part.get("text", "") for part in message["content"] if part.get("type") == "text"]
        return "\n".join(t for t in texts if t) or None
    item = event.get("item") or {}
    if event.get("type") == "item.completed" and item.get("type") == "agent_message":
        return item.get("text")
    return None


def cmd_headless(args: argparse.Namespace) -> int:
    """Deliver a plan unattended, resuming the session until the guard releases it."""
    source_root = repo_root(Path.cwd()).resolve()
    plan = (source_root / args.plan).resolve().relative_to(source_root).as_posix()
    if not args.in_place:
        tracked = subprocess.run(
            ["git", "diff", "--quiet", "HEAD", "--", plan], cwd=source_root, check=False
        )
        listed = git(source_root, "ls-files", plan)
        if not listed or tracked.returncode != 0:
            raise SystemExit(f"Commit {plan} first: the new worktree starts from HEAD")
    workdir = source_root if args.in_place else create_worktree(source_root, plan, args.branch)
    print(f"Working in {workdir}")
    session = str(uuid.uuid4()) if args.harness == "claude" else None
    args.plan = plan
    args.session_id = session
    setup = args.setup if args.setup is not None else default_setup(workdir)
    if setup and not args.dry_run:
        print(f"Preparing the worktree: {setup}")
        if subprocess.run(setup, shell=True, cwd=workdir, check=False).returncode != 0:
            raise SystemExit(f"Setup command failed: {setup}")
    run = create_run(workdir, args)
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
        )
        print(" ".join(command))
        return 0

    first = True
    while True:
        command = agent_command(
            args.harness,
            prompt,
            session,
            first,
            args.model,
            args.claude_permission_mode,
            args.effort,
        )
        code, thread = run_round(command, workdir, log)
        session = session or thread
        if session is None or (code != 0 and first):
            run.status = "stopped"
            run.note = f"The {args.harness} CLI exited with code {code}; see {log}."
            run.save(workdir)
            print(run.note, file=sys.stderr)
            return 1
        run = find_run(workdir, run.token)
        if run.status != ACTIVE:
            print(f"Run {run.token} ended inside the session: {run.status}. {run.note or ''}")
            return 0 if run.status == "completed" else 1
        run.session_id = session
        verdict = evaluate(workdir, run)
        run.note = verdict.message if verdict.action == "release" else run.note
        run.save(workdir)
        if verdict.action == "release":
            print(verdict.message)
            return 0 if run.status == "completed" else 1
        prompt, first = verdict.message, False


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
    head.add_argument("--dry-run", action="store_true", help="print the first command only")
    head.set_defaults(func=cmd_headless)
    return root


def main(argv: list[str] | None = None) -> int:
    """Run the command line interface."""
    args = parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
