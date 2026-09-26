#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""SessionStart hook for Claude Code and Codex: print the state a session starts from.

Both harnesses add this output to the agent's context, so it stays short.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

OPEN_ITEM = re.compile(r"^\s*[-*+] \[ \]", re.MULTILINE)


def git(*args: str) -> str:
    """Run a git command and return its output, or an empty string on failure."""
    result = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    return result.stdout.strip() if result.returncode == 0 else ""


def latest_plan(root: Path) -> str | None:
    """Describe the newest plan in docs/plans and its open items."""
    plans = sorted(p for p in (root / "docs" / "plans").glob("*.md") if p.name != "README.md")
    if not plans:
        return None
    plan = plans[-1]
    open_items = len(OPEN_ITEM.findall(plan.read_text(encoding="utf-8")))
    return f"{plan.relative_to(root)} ({open_items} open items)"


def active_runs() -> list[str]:
    """List active delivery runs of this worktree."""
    runs_dir = git("rev-parse", "--path-format=absolute", "--git-path", "agent-runs")
    if not runs_dir:
        return []
    runs = []
    for state in sorted(Path(runs_dir).glob("*/state.json")):
        data = json.loads(state.read_text(encoding="utf-8"))
        if data.get("status") == "active":
            runs.append(f"{data['token']} for {data['plan']}")
    return runs


def main() -> int:
    """Print branch, working-tree state, latest plan and active delivery runs."""
    try:
        root = Path(git("rev-parse", "--show-toplevel") or ".")
        branch = git("branch", "--show-current") or "detached HEAD"
        changes = git("status", "--short").splitlines()
        lines = [f"Branch: {branch}. Uncommitted changes: {len(changes)} files."]
        lines += [f"  {change}" for change in changes[:10]]
        if recent := git("log", "--oneline", "-3"):
            lines.append("Recent commits:")
            lines += [f"  {line}" for line in recent.splitlines()]
        if plan := latest_plan(root):
            lines.append(f"Latest plan: {plan}")
        for run in active_runs():
            lines.append(f"Active delivery run: {run}")
        lines.append("Rules: AGENTS.md. Context: docs/context/. Decisions: docs/adr/.")
        print("\n".join(lines))
    except Exception as exc:  # a status error must never block a session
        print(f"status unavailable: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
