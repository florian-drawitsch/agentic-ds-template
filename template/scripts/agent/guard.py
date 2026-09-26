#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""PreToolUse guard for Claude Code and Codex.

Blocks a small set of actions that break project rules, and tells the agent what to do instead.
Everything else is allowed. The guard never blocks because of its own errors.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import PurePosixPath
from typing import Any

ENV_FILE = re.compile(r"^\.env(\..+)?$")
PATCH_PATH = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$", re.MULTILINE)
SEPARATORS = {"&&", "||", ";", "|", "&"}
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def is_env_file(path: str) -> bool:
    """Return whether a path names a dotenv file other than the committed example."""
    name = PurePosixPath(path.strip("'\"")).name
    return bool(ENV_FILE.match(name)) and name != ".env.example"


def segments(command: str) -> list[list[str]]:
    """Split a shell command into simple commands made of tokens."""
    try:
        tokens = shlex.split(command, posix=True)
    except ValueError:
        tokens = command.split()
    parts: list[list[str]] = [[]]
    for token in tokens:
        if token in SEPARATORS:
            parts.append([])
        else:
            parts[-1].append(token)
    return [part for part in parts if part]


def check_command(command: str) -> str | None:
    """Return why a shell command is blocked, or None."""
    for words in segments(command):
        if any(is_env_file(word.split("=")[-1]) for word in words):
            return "Do not read or write .env files. Ask the user for the values you need."
        program = PurePosixPath(words[0]).name
        rest = words[1:]
        if program in {"pip", "pip3"} and "install" in rest:
            return "Use `uv add <package>` (or `uv add --group <group>`) instead of pip."
        if program in {"python", "python3"} and rest[:2] == ["-m", "pip"]:
            return "Use `uv add <package>` instead of pip."
        if program == "uv" and rest[:2] == ["pip", "install"]:
            return "Use `uv add <package>` so the dependency is recorded in pyproject.toml."
        forced = any(w in {"--force", "-f", "--force-with-lease"} or w.startswith("+") for w in rest)
        if program == "git" and rest[:1] == ["push"] and forced:
            return "Force pushes are not allowed. Ask the user."
        if program == "git" and rest[:1] == ["commit"] and ({"--no-verify", "-n"} & set(rest)):
            return "Do not skip git hooks. Fix what they report."
    return None


def check_path(path: str, editing: bool) -> str | None:
    """Return why reading or editing a path is blocked, or None."""
    if is_env_file(path):
        return "Do not read or write .env files. Ask the user for the values you need."
    if editing and PurePosixPath(path).name == "uv.lock":
        return "Change dependencies with `uv add` or `uv remove`; never edit uv.lock by hand."
    if editing and ".git" in PurePosixPath(path).parts:
        return "Do not edit git internals."
    return None


def decide(payload: dict[str, Any]) -> str | None:
    """Return the reason to block a tool call, or None to allow it."""
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    if tool == "Bash":
        return check_command(str(tool_input.get("command", "")))
    if tool == "apply_patch":
        for path in PATCH_PATH.findall(str(tool_input.get("command", ""))):
            if reason := check_path(path.strip(), editing=True):
                return reason
        return None
    path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if path and tool in EDIT_TOOLS:
        return check_path(str(path), editing=True)
    if path and tool == "Read":
        return check_path(str(path), editing=False)
    return None


def main() -> int:
    """Read the hook payload and print a deny decision when needed."""
    try:
        reason = decide(json.loads(sys.stdin.read() or "{}"))
    except Exception as exc:  # a guard bug must never block the agent
        print(f"guard error, allowing the call: {exc}", file=sys.stderr)
        return 0
    if reason:
        decision = {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
        print(json.dumps({"hookSpecificOutput": decision}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
