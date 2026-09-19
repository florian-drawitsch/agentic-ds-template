#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Have the other vendor's coding agent review the current change, read-only.

Claude Code reviews with only read and git-read tools allowed. Codex reviews in its read-only
sandbox. Both return structured findings, which are saved next to the delivery run so the
completion guard can match the review to the exact state it reviewed.

Exit codes: 0 the review passed, 2 changes were requested, 1 the review could not run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RUNS_DIRNAME = "agent-runs"
NESTED_ENV = "AGENTIC_NESTED"
BLOCKING = {"blocking", "major"}
SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["verdict", "summary", "findings"],
    "properties": {
        "verdict": {"type": "string", "enum": ["pass", "changes_requested"]},
        "summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["severity", "file", "line", "issue", "suggestion"],
                "properties": {
                    "severity": {"type": "string", "enum": ["blocking", "major", "minor"]},
                    "file": {"type": "string"},
                    "line": {"type": ["integer", "null"]},
                    "issue": {"type": "string"},
                    "suggestion": {"type": "string"},
                },
            },
        },
    },
}
CLAUDE_READ_TOOLS = [
    "Read",
    "Grep",
    "Glob",
    "Bash(git diff:*)",
    "Bash(git log:*)",
    "Bash(git show:*)",
    "Bash(git status:*)",
    "Bash(git ls-files:*)",
]


def git(cwd: Path, *args: str) -> str:
    """Run a git command and return its stripped standard output."""
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def state_signature(root: Path) -> str:
    """Fingerprint the worktree state. Must match deliver.py's state_signature."""
    head = git(root, "rev-parse", "HEAD")
    diff = subprocess.run(["git", "diff", "HEAD"], cwd=root, capture_output=True, check=True).stdout
    untracked = git(root, "ls-files", "--others", "--exclude-standard")
    digest = hashlib.sha256()
    for part in (head.encode(), diff, untracked.encode()):
        digest.update(part)
        digest.update(b"\0")
    return digest.hexdigest()


def base_ref(root: Path, explicit: str | None) -> str:
    """Return the commit the change is compared against."""
    if explicit:
        return explicit
    for candidate in ("origin/main", "main", "origin/master", "master"):
        try:
            base = git(root, "merge-base", "HEAD", candidate)
        except subprocess.CalledProcessError:
            continue
        if base != git(root, "rev-parse", "HEAD"):
            return base
    return "HEAD"


def build_prompt(base: str, plan: str | None) -> str:
    """Return the review instructions."""
    spec = (
        f"The change implements the plan in {plan}. Read it and check spec compliance first. "
        "Ticked plan checkboxes and a rulings section in the plan are expected delivery "
        "bookkeeping, not scope changes; do review whether each ruling is sound."
        if plan
        else "Infer the intent from the commits and the diff."
    )
    return f"""You are reviewing a change written by another coding agent. You are read-only:
do not modify files and do not run commands that change anything.

Scope: the difference between commit {base} and the current working tree. Inspect it with
`git diff {base}`, `git log {base}..HEAD`, `git status` and `git ls-files --others
--exclude-standard` for new files, and read whatever code you need.

{spec}

Check, in this order:
1. Spec compliance: nothing missing, nothing extra.
2. Correctness: logic errors, edge cases, data leakage, error handling.
3. Tests: the changed behavior is covered and the tests would fail without the change.
4. Project rules: accepted ADRs under docs/adr and the AGENTS.md files are respected.

Severity: blocking (wrong results, data loss, security), major (a real defect or a missing test
for changed behavior), minor (clarity, naming, small improvements). Report only findings you
can point to in the code. Return verdict "pass" when there is no blocking or major finding.
Answer with JSON matching the given schema only."""


def claude_command(prompt: str, model: str | None) -> list[str]:
    """Return a read-only Claude Code review invocation. The prompt must follow -p directly."""
    command = ["claude", "-p", prompt, "--output-format", "json"]
    command += ["--json-schema", json.dumps(SCHEMA), "--permission-mode", "dontAsk"]
    command += ["--model", model] if model else []
    command += ["--disallowedTools", "Edit", "Write", "NotebookEdit"]
    return [*command, "--allowedTools", *CLAUDE_READ_TOOLS]


def codex_command(prompt: str, model: str | None, schema: Path, output: Path) -> list[str]:
    """Return a Codex review invocation in the read-only sandbox."""
    command = ["codex", "exec", "-c", 'sandbox_mode="read-only"', "-c", 'approval_policy="never"']
    command += ["--ephemeral", "--output-schema", str(schema), "-o", str(output)]
    command += ["-m", model] if model else []
    return [*command, prompt]


def parse_json_text(text: str) -> dict[str, Any]:
    """Parse JSON that may be wrapped in a Markdown code fence."""
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    return json.loads(match.group(1) if match else text)


def parse_claude_output(stdout: str) -> dict[str, Any]:
    """Extract the review from Claude Code's JSON output."""
    data = json.loads(stdout)
    if isinstance(data.get("structured_output"), dict):
        return data["structured_output"]
    return parse_json_text(data.get("result", ""))


def normalize(review: dict[str, Any]) -> dict[str, Any]:
    """Validate the review and make the verdict consistent with the findings."""
    findings = review.get("findings")
    if review.get("verdict") not in {"pass", "changes_requested"} or not isinstance(findings, list):
        raise ValueError(f"Review does not match the schema: {review}")
    if any(finding.get("severity") in BLOCKING for finding in findings):
        review["verdict"] = "changes_requested"
    return review


def run_review(
    root: Path, reviewer: str, prompt: str, model: str | None, timeout: int
) -> dict[str, Any]:
    """Run the reviewer CLI and return its normalized review."""
    env = os.environ | {NESTED_ENV: "1"}
    if reviewer == "claude":
        result = subprocess.run(
            claude_command(prompt, model),
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"claude exited with {result.returncode}: {result.stderr[-2000:]}")
        return normalize(parse_claude_output(result.stdout))

    with tempfile.TemporaryDirectory() as tmp:
        schema, output = Path(tmp) / "schema.json", Path(tmp) / "review.json"
        schema.write_text(json.dumps(SCHEMA), encoding="utf-8")
        result = subprocess.run(
            codex_command(prompt, model, schema, output),
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
        if result.returncode != 0 or not output.is_file():
            raise RuntimeError(f"codex exited with {result.returncode}: {result.stderr[-2000:]}")
        return normalize(parse_json_text(output.read_text(encoding="utf-8")))


def save_review(root: Path, token: str | None, record: dict[str, Any]) -> Path:
    """Store the review in the run directory, or in a shared reviews directory."""
    runs = Path(git(root, "rev-parse", "--path-format=absolute", "--git-path", RUNS_DIRNAME))
    directory = runs / token / "reviews" if token else runs / "reviews"
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    path = directory / f"{stamp}-{record['reviewer']}.json"
    path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    """Run a cross-vendor review and report the result."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--reviewer", choices=["claude", "codex"], required=True)
    parser.add_argument("--token", help="delivery run that the review belongs to")
    parser.add_argument("--base", help="commit to compare against (default: merge base with main)")
    parser.add_argument("--plan", help="plan the change implements")
    parser.add_argument("--model", help="reviewer model (default: the CLI's configured model)")
    parser.add_argument("--timeout", type=int, default=1800, help="seconds")
    parser.add_argument("--dry-run", action="store_true", help="print the command only")
    args = parser.parse_args(argv)

    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel"))
    base = base_ref(root, args.base)
    prompt = build_prompt(base, args.plan)
    if args.dry_run:
        if args.reviewer == "claude":
            command = claude_command(prompt, args.model)
        else:
            command = codex_command(prompt, args.model, Path("schema.json"), Path("review.json"))
        print(json.dumps(command, indent=2))
        return 0

    signature = state_signature(root)
    try:
        review = run_review(root, args.reviewer, prompt, args.model, args.timeout)
    except (RuntimeError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        print(f"Cross-review failed: {exc}", file=sys.stderr)
        return 1

    record = review | {
        "reviewer": args.reviewer,
        "base": base,
        "signature": signature,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    path = save_review(root, args.token, record)
    print(f"Cross-review by {args.reviewer}: {review['verdict']}")
    print(review.get("summary", ""))
    for finding in review["findings"]:
        location = (
            f"{finding['file']}:{finding['line']}" if finding.get("line") else finding["file"]
        )
        print(f"  [{finding['severity']}] {location}: {finding['issue']}")
    print(f"Saved to {path}")
    return 0 if review["verdict"] == "pass" else 2


if __name__ == "__main__":
    sys.exit(main())
