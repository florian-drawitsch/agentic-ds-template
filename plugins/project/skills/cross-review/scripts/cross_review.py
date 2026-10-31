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
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

RUNS_DIRNAME = "agent-runs"
NESTED_ENV = "AGENTIC_NESTED"
BLOCKING = {"blocking", "major"}
DEFAULT_CODEX_MODEL = "gpt-6.1-sol"
MIN_CODEX_VERSION = (0, 162, 0)
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
CLAUDE_READ_TOOLS = ["Read", "Grep", "Glob"]


def prices_file() -> Path:
    """Locate the Codex price table: beside this script in a project copy, else in the plugin."""
    here = Path(__file__).resolve()
    flat = here.parent / "prices.toml"
    return flat if flat.is_file() else here.parents[2] / "deliver/scripts/prices.toml"


def git(cwd: Path, *args: str) -> str:
    """Run a git command and return its stripped standard output."""
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def state_signature(root: Path) -> str:
    """Fingerprint the worktree state. Must match deliver.py's state_signature."""
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


def build_prompt(base: str, plan: str | None, input_dir: Path | None = None) -> str:
    """Return the review instructions."""
    spec = (
        f"The change implements the plan in {plan}. Read it and check spec compliance first. "
        "Ticked plan checkboxes and a rulings section in the plan are expected delivery "
        "bookkeeping, not scope changes; do review whether each ruling is sound."
        if plan
        else "Infer the intent from the commits and the diff."
    )
    inputs = (
        f"The controller prepared read-only review inputs at {input_dir}. Read `context.md` and "
        "`diff.patch` there with the Read tool, then inspect every untracked file listed in the "
        "context and any referenced source files with Read, Grep, and Glob. Do not run shell "
        "commands."
        if input_dir
        else "The controller supplies the change through read-only input files."
    )
    return f"""You are reviewing a change written by another coding agent. You are read-only:
do not modify files and do not run commands that change anything.

Scope: the difference from commit {base} and the current working tree.
{inputs}

{spec}

Review issues that matter for this shared library:
1. Critical design or correctness problems: wrong results, broken boundaries, data leakage,
   unsafe operations, public contract changes, and contradictions with accepted ADRs, the plan,
   domain contracts, or the blocking review rules in AGENTS.md.
2. Messy code that materially harms maintenance: needless abstraction, duplicated policy,
   tangled responsibilities, or control flow that can be made substantially shorter and clearer.

Prefer pragmatic, concise code. Report concrete blocking or major findings with the smallest
useful correction. Do not request cosmetic changes, speculative extensibility, extra test layers,
process artifacts, or unrelated cleanup. Formatting and type details belong to automated checks.
Do one focused review. The implementation agent will fix required findings autonomously and
verify them with focused tests. A second model review is not required for routine fixes.
Return verdict "pass" when no blocking or major finding remains.
Answer with JSON matching the given schema only."""


def claude_command(prompt: str, model: str | None, budget: float = 2.0) -> list[str]:
    """Return a read-only Claude Code review invocation. The prompt must follow -p directly."""
    command = ["claude", "-p", prompt, "--output-format", "json"]
    command += ["--json-schema", json.dumps(SCHEMA), "--permission-mode", "dontAsk"]
    command += [
        "--max-budget-usd",
        str(budget),
        "--max-turns",
        "60",
        "--exclude-dynamic-system-prompt-sections",
    ]
    command += ["--model", model or "opus", "--effort", "high"]
    command += ["--disallowedTools", "Edit", "Write", "NotebookEdit"]
    return [*command, "--tools", ",".join(CLAUDE_READ_TOOLS)]


def write_review_inputs(root: Path, base: str, token: str | None) -> Path:
    """Write review diff and context files outside the tracked worktree."""
    runs = Path(git(root, "rev-parse", "--path-format=absolute", "--git-path", RUNS_DIRNAME))
    parent = runs / token if token else runs
    parent.mkdir(parents=True, exist_ok=True)
    input_dir = Path(tempfile.mkdtemp(prefix="review-input-", dir=parent))
    diff = subprocess.run(
        ["git", "diff", "--binary", base], cwd=root, capture_output=True, check=True
    ).stdout
    status = git(root, "status", "--short")
    log = git(root, "log", "--oneline", f"{base}..HEAD")
    untracked = git(root, "ls-files", "--others", "--exclude-standard")
    (input_dir / "diff.patch").write_bytes(diff)
    (input_dir / "context.md").write_text(
        f"Base: {base}\n\nCommits:\n{log}\n\nStatus:\n{status}\n\nUntracked files:\n{untracked}\n",
        encoding="utf-8",
    )
    return input_dir


def codex_command(
    prompt: str, model: str | None, schema: Path, output: Path, binary: str = "codex"
) -> list[str]:
    """Return a Codex review invocation in the read-only sandbox."""
    command = [
        binary,
        "exec",
        "--json",
        "-c",
        'sandbox_mode="read-only"',
        "-c",
        'approval_policy="never"',
    ]
    command += ["-c", 'model_reasoning_effort="high"']
    command += ["--ephemeral", "--output-schema", str(schema), "-o", str(output)]
    command += ["-m", model] if model else []
    return [*command, prompt]


def codex_binary(explicit: str | None = None) -> str:
    """Resolve the reviewer executable and reject versions with incompatible model support."""
    binary = explicit or os.environ.get("CODEX_BIN") or shutil.which("codex")
    if not binary:
        raise RuntimeError("Codex is not installed; install a current CLI or set CODEX_BIN")
    result = subprocess.run(
        [binary, "--version"],
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    version = re.search(r"(\d+)\.(\d+)\.(\d+)", result.stdout)
    if result.returncode != 0 or version is None:
        raise RuntimeError(f"Cannot read Codex version from {binary}; set --codex-bin or CODEX_BIN")
    if tuple(map(int, version.groups())) < MIN_CODEX_VERSION:
        raise RuntimeError(
            f"Codex {version.group()} at {binary} is too old for this reviewer. "
            "Use Codex >=0.162.0: run 'brew upgrade --cask codex', or set --codex-bin/CODEX_BIN "
            "to a current executable. Keep the model gpt-6.1-sol; do not guess aliases."
        )
    return binary


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
    root: Path,
    reviewer: str,
    prompt: str,
    model: str | None,
    timeout: int,
    codex_bin: str | None = None,
    budget: float = 2.0,
) -> dict[str, Any]:
    """Run the reviewer CLI and return its normalized review."""
    env = os.environ | {NESTED_ENV: "1"}
    if reviewer == "claude":
        result = subprocess.run(
            claude_command(prompt, model, budget),
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f"claude exited with {result.returncode}: {result.stderr[-2000:]}")
        raw = json.loads(result.stdout)
        review = normalize(parse_claude_output(result.stdout))
        review["_usage"] = {
            "cost_usd": raw.get("total_cost_usd"),
            "modelUsage": raw.get("modelUsage"),
            "num_turns": raw.get("num_turns"),
        }
        return review

    binary = codex_binary(codex_bin)
    with tempfile.TemporaryDirectory() as tmp:
        schema, output = Path(tmp) / "schema.json", Path(tmp) / "review.json"
        schema.write_text(json.dumps(SCHEMA), encoding="utf-8")
        result = subprocess.run(
            codex_command(prompt, model or DEFAULT_CODEX_MODEL, schema, output, binary),
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
        if result.returncode != 0 or not output.is_file():
            raise RuntimeError(f"codex exited with {result.returncode}: {result.stderr[-2000:]}")
        review = normalize(parse_json_text(output.read_text(encoding="utf-8")))
        input_tokens = cached_tokens = cache_creation_tokens = output_tokens = turns = 0
        for line in result.stdout.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "turn.completed":
                turns += 1
                usage = event.get("usage") or {}
                input_tokens += int(usage.get("input_tokens", 0) or 0)
                cached_tokens += int(usage.get("cached_input_tokens", 0) or 0)
                cache_creation_tokens += int(usage.get("cache_creation_input_tokens", 0) or 0)
                output_tokens += int(usage.get("output_tokens", 0) or 0)
        model_name = model or DEFAULT_CODEX_MODEL
        cost = None
        try:
            table = tomllib.loads(prices_file().read_text())["prices"][model_name]
            # Codex reports cached input as a subset of input_tokens.
            uncached = max(0, input_tokens - cached_tokens)
            cost = (
                uncached * table.get("input", 0)
                + cached_tokens * table.get("cached_input", 0)
                + cache_creation_tokens * table.get("cache_creation", 0)
                + output_tokens * table.get("output", 0)
            ) / 1_000_000
        except (OSError, KeyError, tomllib.TOMLDecodeError):
            pass
        review["_usage"] = {
            "cost_usd": cost,
            "num_turns": turns,
            "input_tokens": input_tokens,
            "cached_input_tokens": cached_tokens,
            "cache_creation_tokens": cache_creation_tokens,
            "output_tokens": output_tokens,
        }
        return review


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
    parser.add_argument("--model", help="reviewer model (Codex default: gpt-6.1-sol)")
    parser.add_argument("--codex-bin", help="Codex executable (default: CODEX_BIN or PATH)")
    parser.add_argument("--preflight", action="store_true", help="check reviewer installation only")
    parser.add_argument("--timeout", type=int, default=1800, help="seconds")
    parser.add_argument("--dry-run", action="store_true", help="print the command only")
    args = parser.parse_args(argv)

    if args.reviewer == "codex" and args.model is None:
        args.model = DEFAULT_CODEX_MODEL
    if args.preflight:
        try:
            binary = (
                codex_binary(args.codex_bin) if args.reviewer == "codex" else shutil.which("claude")
            )
            if not binary:
                print("Reviewer preflight failed: Claude CLI is not installed", file=sys.stderr)
                return 1
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            print(f"Reviewer preflight failed: {exc}", file=sys.stderr)
            return 1
        print(f"Reviewer ready: {binary}; model {args.model or 'CLI default'}")
        return 0

    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel"))
    base = base_ref(root, args.base)
    prompt = build_prompt(base, args.plan)
    run_dir = None
    review_budget = 2.0
    remaining_budget = float("inf")
    if args.token:
        runs = Path(git(root, "rev-parse", "--path-format=absolute", "--git-path", RUNS_DIRNAME))
        run_dir = runs / args.token
        state_path = run_dir / "state.json"
        if state_path.is_file():
            state = json.loads(state_path.read_text())
            review_budget = float(state.get("review_cost", 2.0))
            max_cost = float(state.get("max_cost", 10.0))
            ledger = run_dir / "usage.jsonl"
            if max_cost and ledger.is_file():  # max_cost 0 keeps the review uncapped by the run
                remaining_budget = max_cost - sum(
                    json.loads(line).get("cost_usd") or 0
                    for line in ledger.read_text().splitlines()
                    if line
                )
            if max_cost and remaining_budget < 0.25:
                print(
                    "Run cost budget exhausted before review; raise --max-cost to continue.",
                    file=sys.stderr,
                )
                return 1
            review_budget = min(review_budget, remaining_budget)
    if args.dry_run:
        if args.reviewer == "claude":
            command = claude_command(prompt, args.model, review_budget)
        else:
            command = codex_command(
                prompt,
                args.model,
                Path("schema.json"),
                Path("review.json"),
                args.codex_bin or os.environ.get("CODEX_BIN") or "codex",
            )
        print(json.dumps(command, indent=2))
        return 0

    if args.token:
        runs = Path(git(root, "rev-parse", "--path-format=absolute", "--git-path", RUNS_DIRNAME))
        directory = runs / args.token
        state = directory / "state.json"
        budget = json.loads(state.read_text()).get("review_rounds", 1) if state.is_file() else 1
        used = len(list((directory / "reviews").glob("*.json")))
        if used >= budget:
            print(
                "Review budget exhausted. Implement required fixes autonomously, run focused "
                "checks, and use deliver.py resolve-review. Do not repeat the review.",
                file=sys.stderr,
            )
            return 1

        previous_paths = sorted((directory / "reviews").glob("*.json"))
        if previous_paths:
            previous = json.loads(previous_paths[-1].read_text())
            if previous.get("verdict") == "changes_requested" and previous.get(
                "resolution", {}
            ).get("signature") != state_signature(root):
                print(
                    "Verify required fixes with deliver.py resolve-review before another pass.",
                    file=sys.stderr,
                )
                return 1

    if run_dir:
        diff = subprocess.run(
            ["git", "diff", base], cwd=root, capture_output=True, text=True, check=False
        ).stdout
        diffstat = subprocess.run(
            ["git", "diff", "--stat", base], cwd=root, capture_output=True, text=True, check=False
        ).stdout
        (run_dir / "review.diff").write_text(diff, encoding="utf-8")
        (run_dir / "review.diffstat").write_text(diffstat, encoding="utf-8")
        prompt += (
            f"\n\nFocused diff: {run_dir / 'review.diff'}\nDiff summary: "
            f"{run_dir / 'review.diffstat'}\nRead other files only when a finding requires "
            "context."
        )

    signature = state_signature(root)
    input_dir = write_review_inputs(root, base, args.token)
    prompt = build_prompt(base, args.plan, input_dir)
    review_started = time.monotonic()
    review_started_at = datetime.now(UTC).isoformat(timespec="seconds")
    try:
        review = run_review(
            root, args.reviewer, prompt, args.model, args.timeout, args.codex_bin, review_budget
        )
    except (
        OSError,
        RuntimeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(f"Cross-review failed: {exc}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(input_dir, ignore_errors=True)

    usage = review.pop("_usage", {})
    usage["seconds"] = time.monotonic() - review_started
    usage["started"] = review_started_at
    record = review | {
        "reviewer": args.reviewer,
        "model": args.model,
        "base": base,
        "signature": signature,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    path = save_review(root, args.token, record)
    if run_dir:
        ledger = run_dir / "usage.jsonl"
        old = ledger.read_text(encoding="utf-8") if ledger.is_file() else ""
        details = next(iter((usage.get("modelUsage") or {}).values()), usage)
        row = {
            "round": len([line for line in old.splitlines() if line]) + 1,
            "kind": "review",
            "vendor": args.reviewer,
            "model": args.model or "CLI default",
            "started": usage.get("started", record["created"]),
            "seconds": usage.get("seconds", 0),
            "turns": usage.get("num_turns", 0),
            "input_tokens": details.get("inputTokens", usage.get("input_tokens")),
            "cached_input_tokens": details.get(
                "cacheReadInputTokens", usage.get("cached_input_tokens")
            ),
            "cache_creation_tokens": details.get(
                "cacheCreationInputTokens", usage.get("cache_creation_tokens")
            ),
            "output_tokens": details.get("outputTokens", usage.get("output_tokens")),
            "cost_usd": usage.get("cost_usd"),
            "cost_basis": "cli-estimate" if usage.get("cost_usd") is not None else "unpriced",
            "outcome": "success",
        }
        with ledger.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row) + "\n")
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
