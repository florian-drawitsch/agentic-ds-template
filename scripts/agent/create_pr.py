#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Preview or create an idempotent GitHub pull request through the GitHub CLI.

The description must contain the level-two headings of the repository's
`.github/pull_request_template.md` in order, each with content. Without a template, the five
standard sections apply. `--result-file` saves the pull request as `gh pr view --json` reports it.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

TEMPLATE = Path(".github/pull_request_template.md")
DEFAULT_HEADINGS = (
    "## Summary",
    "## Changes",
    "## Decisions",
    "## Verification",
    "## Risks and follow-ups",
)
HEADING = re.compile(r"^## .+$", re.MULTILINE)
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
UNFINISHED_PATTERN = re.compile(r"\b(?:TODO|TBD)\b|<[^>]+>", re.IGNORECASE)
RECEIPT_FIELDS = "number,url,state,title,headRefName,headRefOid,baseRefName"


def _run(*args: str) -> str:
    result = subprocess.run(list(args), check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _git(*args: str) -> str:
    return _run("git", *args)


def _gh(*args: str) -> str:
    return _run("gh", *args)


def _parse_remote(remote_url: str) -> tuple[str, str]:
    scp_match = re.fullmatch(r"git@github\.com:([^/]+)/(.+)", remote_url)
    if scp_match:
        owner, repository = scp_match.groups()
    else:
        parsed = urllib.parse.urlparse(remote_url)
        if parsed.hostname != "github.com":
            raise ValueError(f"remote is not GitHub: {remote_url}")
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) != 2:
            raise ValueError(f"cannot resolve owner and repository from: {remote_url}")
        owner, repository = parts
    return owner, repository.removesuffix(".git")


def required_headings(root: Path) -> tuple[str, ...]:
    """Return the level-two headings of the repository's PR template, or the default five."""
    template = root / TEMPLATE
    if not template.is_file():
        return DEFAULT_HEADINGS
    text = COMMENT.sub("", template.read_text(encoding="utf-8"))
    return tuple(match.group().strip() for match in HEADING.finditer(text)) or DEFAULT_HEADINGS


def _description(path: Path, headings: tuple[str, ...]) -> str:
    description = path.read_text(encoding="utf-8").strip()
    found = list(HEADING.finditer(description))
    if tuple(match.group().strip() for match in found) != headings:
        raise ValueError(
            "description must contain exactly these sections in order: " + ", ".join(headings)
        )
    for index, heading in enumerate(found):
        end = found[index + 1].start() if index + 1 < len(found) else len(description)
        if not description[heading.end() : end].strip():
            raise ValueError(f"description section is empty: {heading.group()}")
    if UNFINISHED_PATTERN.search(description):
        raise ValueError(
            "description contains an unfinished TODO, TBD, or angle-bracket placeholder"
        )
    _reject_attribution(description)
    return description


def _reject_attribution(text: str) -> None:
    if re.search(
        r"co-authored-by:|(?:created|generated|written) (?:by|with) "
        r"(?:codex|claude|chatgpt|openai|ai agent)",
        text,
        re.IGNORECASE,
    ):
        raise ValueError("coding-agent attribution is not allowed")


def _title(value: str) -> str:
    if not re.fullmatch(
        r"(?:feat|fix|docs|test|refactor|perf|build|ci|chore|style|revert)"
        r"(?:\([^\n)]+\))?!?: \S[^\n]*",
        value,
    ):
        raise ValueError("title must use Conventional Commit format")
    _reject_attribution(value)
    return value


def _existing_pull_request(repository: str, source: str, destination: str) -> dict | None:
    listed = _gh(
        "pr",
        "list",
        "--repo",
        repository,
        "--head",
        source,
        "--base",
        destination,
        "--state",
        "open",
        "--json",
        "number,title,body",
    )
    matches = json.loads(listed or "[]")
    return matches[0] if matches else None


def _save_result(path: Path | None, repository: str, number: int) -> str:
    response = json.loads(
        _gh("pr", "view", str(number), "--repo", repository, "--json", RECEIPT_FIELDS)
    )
    url = response.get("url", "")
    if urllib.parse.urlparse(url).hostname != "github.com":
        raise RuntimeError("GitHub returned no valid pull-request URL")
    if path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(response, indent=2) + "\n", encoding="utf-8")
    return url


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title")
    parser.add_argument("--description-file", type=Path)
    parser.add_argument("--destination", default="main")
    parser.add_argument("--source")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--draft", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--push", action="store_true", help="push the checked-out source before publication"
    )
    parser.add_argument("--result-file", type=Path, help="save the actual GitHub response")
    parser.add_argument("--preflight", action="store_true", help="check remote and CLI access only")
    return parser.parse_args()


def main() -> int:
    """Validate the PR and preview, check access, or publish the requested source branch."""
    args = _arguments()
    owner, name = _parse_remote(_git("remote", "get-url", args.remote))
    repository = f"{owner}/{name}"
    if args.preflight:
        _gh("auth", "status", "--hostname", "github.com")
        _gh("repo", "view", repository, "--json", "name")
        _gh("pr", "list", "--repo", repository, "--state", "open", "--limit", "1")
        print(f"GitHub CLI access verified for {repository}")
        return 0
    if not args.title or args.description_file is None:
        raise ValueError("--title and --description-file are required for publication")
    title = _title(args.title)
    source = args.source or _git("branch", "--show-current")
    if not source:
        raise ValueError("HEAD is detached; pass --source explicitly")
    if source == args.destination:
        raise ValueError("source and destination branches must differ")
    headings = required_headings(Path(_git("rev-parse", "--show-toplevel")))
    description = _description(args.description_file, headings)

    summary = {
        "repository": repository,
        "source": source,
        "destination": args.destination,
        "title": title,
        "draft": args.draft,
        "description_headings": list(headings),
    }
    if not args.execute:
        print(json.dumps(summary, indent=2))
        print("Dry run only. Re-run with --execute to create the pull request.")
        return 0

    _gh("auth", "status", "--hostname", "github.com")
    if args.push:
        if source != _git("branch", "--show-current"):
            raise ValueError("--push requires the checked-out source branch")
        if _git("status", "--porcelain"):
            raise ValueError("commit all changes before pushing")
        base = f"{args.remote}/{args.destination}"
        if _git("rev-list", "--count", f"{base}..HEAD") == "0":
            raise ValueError("source has no commits beyond the destination")
        _reject_attribution(_git("log", "--format=%B", f"{base}..HEAD"))
        _git("push", "--set-upstream", args.remote, f"HEAD:refs/heads/{source}")

    existing = _existing_pull_request(repository, source, args.destination)
    if existing:
        number = int(existing["number"])
        if existing.get("title") != title or (existing.get("body") or "").strip() != description:
            _gh(
                "pr",
                "edit",
                str(number),
                "--repo",
                repository,
                "--title",
                title,
                "--body-file",
                str(args.description_file),
            )
    else:
        command = [
            "pr",
            "create",
            "--repo",
            repository,
            "--base",
            args.destination,
            "--head",
            source,
            "--title",
            title,
            "--body-file",
            str(args.description_file),
        ]
        if args.draft:
            command.append("--draft")
        created = _gh(*command).splitlines()[-1]
        number = int(created.rstrip("/").rsplit("/", 1)[-1])
    print(_save_result(args.result_file, repository, number))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, subprocess.CalledProcessError, RuntimeError, ValueError) as error:
        detail = getattr(error, "stderr", None)
        print(f"error: {error}" + (f"\n{detail.strip()}" if detail else ""), file=sys.stderr)
        sys.exit(1)
