#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""PostToolUse hook for Claude Code and Codex: format changed Python files with ruff.

Works from git state rather than the tool payload, so it handles single-file edits and multi-file
patches alike. Lint findings stay for `task lint`; this hook only formats and applies safe fixes.
"""

from __future__ import annotations

import subprocess
import sys


def changed_python_files() -> list[str]:
    """Return modified, staged and untracked Python files."""
    tracked = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=d", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.splitlines()
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.splitlines()
    return sorted({path for path in tracked + untracked if path.endswith((".py", ".pyi"))})


def main() -> int:
    """Format changed files; never fail the agent's tool call."""
    try:
        files = changed_python_files()
        if files:
            subprocess.run(["uv", "run", "ruff", "format", "--quiet", *files], check=False)
            subprocess.run(
                ["uv", "run", "ruff", "check", "--fix", "--exit-zero", "--quiet", *files],
                check=False,
            )
    except Exception as exc:  # formatting is a convenience, not a gate
        print(f"format hook skipped: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
