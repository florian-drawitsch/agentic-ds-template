#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""Check that Mermaid diagrams in Markdown files follow the house style.

The style is defined in the `mermaid` skill of the `project` plugin. This script enforces its
mechanical rules: allowed diagram types, stroke-only colors from the palette, and the style lines
each diagram type needs to render without fills.

Usage:
    check_mermaid.py FILE...   Check the given Markdown files. Exit 1 on findings (git hook).
    check_mermaid.py --hook    Check changed Markdown files. Exit 2 on findings, so Claude Code
                               and Codex show them to the agent after an edit.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

NEUTRAL = "#868e96"
NEUTRAL_STYLE = f"fill:none,stroke:{NEUTRAL},color:{NEUTRAL}"
PALETTE = frozenset({NEUTRAL, "#2f81f7", "#e8710a", "#1a9e5b", "#a371f7", "#d6409f", "#e5534b"})
ALLOWED_TYPES = frozenset(
    {"flowchart", "classDiagram", "sequenceDiagram", "stateDiagram-v2", "erDiagram", "gantt"}
)
SKILL = "the mermaid skill"
# The themeCSS of the standard frontmatter in the skill, one rule per line.
STANDARD_CSS = (
    ".row-rect-odd path, .row-rect-even path, marker circle { fill: none !important; }",
    ".edgePaths path, .flowchart-link, .relation, .transition, .relationshipLine, marker path, "
    "marker circle { stroke: #868e96 !important; }",
    "circle.state-start, circle.state-end "
    "{ fill: #868e96 !important; stroke: #868e96 !important; }",
    "rect.actor, rect.note, rect[class^=activation], polygon.labelBox "
    "{ fill: none !important; stroke: #868e96 !important; }",
    "line[class^=actor-line], line.loopLine, line[class^=messageLine], path[class^=messageLine] "
    "{ stroke: #868e96 !important; }",
    "text.actor, text.actor tspan, .messageText, .noteText, .noteText tspan, .labelText, "
    ".labelText tspan, .loopText, .loopText tspan, text.text { fill: #868e96 !important; }",
)

FENCE_OPEN = re.compile(r"^\s*(`{3,}|~{3,})\s*\{?mermaid\}?\s*$")
HEX = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b")
FILL = re.compile(r"\bfill:\s*([^,;\s\"!]+)")
PROPERTY = re.compile(r"(?<![-\w])(fill|stroke|color):\s*([^,;\s]+)")
NEUTRAL_DEFAULT = re.compile(r"fill:\s*none.*stroke:\s*#868e96", re.IGNORECASE)
SUBGRAPH = re.compile(r"^\s*subgraph\s+([\w-]+)")
CLASS_DECL = re.compile(r"^\s*class\s+([A-Za-z_]\w*)")
CLASS_RELATION = re.compile(
    r"^\s*([A-Za-z_]\w*)\s*(?:\"[^\"]*\"\s*)?"
    r"(?:<\||\*|o|<)?(?:--|\.\.)(?:\|>|\*|o|>)?"
    r"\s*(?:\"[^\"]*\"\s*)?([A-Za-z_]\w*)"
)
STATE_TRANSITION = re.compile(r"^\s*(\[\*\]|[\w]+)\s*-->\s*(\[\*\]|[\w]+)")
STATE_DECL = re.compile(r"^\s*state\s+(?:\"[^\"]*\"\s+as\s+)?([\w]+)(.*)$")
STATE_CLASS = re.compile(r"^\s*class\s+(.+?)\s+([\w]+)\s*$")
Add = Callable[[int, str], None]
KEYWORD_LINES = ("style ", "classDef ", "cssClass ", "class ", "direction ", "note ", "linkStyle ")


@dataclass(frozen=True)
class Finding:
    """One style violation in one diagram."""

    path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.message}"


@dataclass(frozen=True)
class Block:
    """One Mermaid block: its source lines and the file line of its first line."""

    start: int
    lines: list[str]


def mermaid_blocks(text: str) -> list[Block]:
    """Return the Mermaid code blocks of a Markdown text."""
    blocks: list[Block] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        match = FENCE_OPEN.match(lines[index])
        if not match:
            index += 1
            continue
        fence = match.group(1)
        body: list[str] = []
        index += 1
        start = index + 1
        while index < len(lines) and not lines[index].strip().startswith(fence):
            body.append(lines[index])
            index += 1
        blocks.append(Block(start=start, lines=body))
        index += 1
    return blocks


def split_frontmatter(lines: list[str]) -> tuple[list[str], int]:
    """Return the frontmatter lines and the index of the first diagram line."""
    if lines and lines[0].strip() == "---":
        for index in range(1, len(lines)):
            if lines[index].strip() == "---":
                return lines[1:index], index + 1
    return [], 0


def check_block(block: Block, path: str = "<diagram>") -> list[Finding]:
    """Return the style findings for one Mermaid block."""
    findings: list[Finding] = []

    def add(offset: int, message: str) -> None:
        findings.append(Finding(path, block.start + offset, message))

    frontmatter, body_start = split_frontmatter(block.lines)
    body = [
        (body_start + offset, line)
        for offset, line in enumerate(block.lines[body_start:])
        if line.strip() and not line.strip().startswith("%%")
    ]
    for offset, line in enumerate(block.lines):
        if "%%{" in line:
            add(offset, "no %%{init}%% directives; style comes from the mermaid skill")
        for color in HEX.findall(line):
            if color.lower() not in PALETTE:
                add(offset, f"color {color} is not in the palette of {SKILL}")
        for value in FILL.findall(line) if offset >= body_start else []:
            if value.lower() not in {"none", "transparent"}:
                add(offset, f"fill:{value} not allowed; use fill:none (strokes only)")
    if any(re.match(r"\s*theme\s*:", line) for line in frontmatter):
        add(0, "no theme setting; it breaks the light and dark switch")
    if not body:
        add(0, "empty diagram")
        return findings

    first_offset, first_line = body[0]
    kind = first_line.split()[0]
    if kind not in ALLOWED_TYPES:
        hint = {"graph": "use flowchart", "stateDiagram": "use stateDiagram-v2"}.get(
            kind, f"use one of {', '.join(sorted(ALLOWED_TYPES))}"
        )
        add(first_offset, f"diagram type {kind!r} not allowed: {hint}")
        return findings

    statements = body[1:]
    if kind != "gantt" and _theme_css(frontmatter) != " ".join(STANDARD_CSS):
        add(0, f"start with the standard frontmatter from {SKILL}, unchanged")
    _check_text_colors(statements, add)
    if kind == "flowchart":
        _check_flowchart(statements, add)
    elif kind == "classDiagram":
        _check_class_diagram(statements, add)
    elif kind == "sequenceDiagram":
        _check_sequence(statements, add)
    elif kind == "stateDiagram-v2":
        _check_state(statements, first_offset, add)
    elif kind == "erDiagram" and not _has_neutral_default(statements, "classDef default"):
        add(first_offset, f"add `classDef default {NEUTRAL_STYLE}`")
    return findings


def _theme_css(frontmatter: list[str]) -> str:
    """Return the themeCSS value of the frontmatter, whitespace-normalized."""
    for index, line in enumerate(frontmatter):
        key, _, value = line.strip().partition(":")
        if key != "themeCSS":
            continue
        value = value.strip()
        if value in {">-", ">", "|", "|-"}:
            indent = len(line) - len(line.lstrip())
            parts = []
            for following in frontmatter[index + 1 :]:
                if following.strip() and len(following) - len(following.lstrip()) <= indent:
                    break
                parts.append(following.strip())
            value = " ".join(parts)
        return " ".join(value.strip("\"'").split())
    return ""


def _check_text_colors(statements: list[tuple[int, str]], add: Add) -> None:
    """Require text in the stroke color on classDef and style lines, none on linkStyle."""
    for offset, line in statements:
        keyword = line.split(maxsplit=1)[0]
        properties = dict(PROPERTY.findall(line))
        if keyword == "linkStyle" and "color" in properties:
            add(offset, "no color on linkStyle; edge labels keep the theme's box and text")
        elif keyword in {"classDef", "style"} and properties.get("color") != properties.get(
            "stroke"
        ):
            add(offset, "set color equal to stroke, so text stays legible in light and dark mode")


def _has_neutral_default(statements: list[tuple[int, str]], prefix: str) -> bool:
    return any(
        line.strip().startswith(prefix) and NEUTRAL_DEFAULT.search(line) for _, line in statements
    )


def _styled(statements: list[tuple[int, str]]) -> set[str]:
    """Return the names that have a `style <name> fill:none` line."""
    names = set()
    for _, line in statements:
        parts = line.split(maxsplit=2)
        if len(parts) == 3 and parts[0] == "style" and re.search(r"fill:\s*none", parts[2]):
            names.add(parts[1])
    return names


def _check_flowchart(statements: list[tuple[int, str]], add: Add) -> None:
    if not _has_neutral_default(statements, "classDef default"):
        add(0, f"add `classDef default {NEUTRAL_STYLE},stroke-width:1px`")
    styled = _styled(statements)
    for offset, line in statements:
        match = SUBGRAPH.match(line)
        if match and match.group(1) not in styled:
            add(offset, f"subgraph {match.group(1)} needs `style {match.group(1)} fill:none,...`")


def _check_class_diagram(statements: list[tuple[int, str]], add: Add) -> None:
    classes: dict[str, int] = {}
    for offset, line in statements:
        stripped = line.strip()
        if stripped.startswith("note"):
            add(offset, "no notes in class diagrams; they keep a fill. Use prose instead")
            continue
        match = CLASS_DECL.match(line)
        if match:
            classes.setdefault(match.group(1), offset)
            continue
        if stripped.startswith(KEYWORD_LINES) or stripped.startswith(("<<", "+", "-", "#", "~")):
            continue
        relation = CLASS_RELATION.match(line)
        if relation:
            classes.setdefault(relation.group(1), offset)
            classes.setdefault(relation.group(2), offset)
    styled = _styled(statements)
    for name, offset in classes.items():
        if name not in styled:
            add(
                offset,
                f"class {name} needs `style {name} {NEUTRAL_STYLE}`",
            )


def _check_sequence(statements: list[tuple[int, str]], add: Add) -> None:
    for offset, line in statements:
        stripped = line.strip()
        if stripped.startswith("box ") and not stripped.startswith("box transparent"):
            add(offset, "use `box transparent <Label>`")
        if re.match(r"rect\s+rgb", stripped):
            add(offset, "no `rect rgb(...)` highlights")


def _check_state(statements: list[tuple[int, str]], first_offset: int, add: Add) -> None:
    if not _has_neutral_default(statements, "classDef base"):
        add(first_offset, f"add `classDef base {NEUTRAL_STYLE}`")
    states: dict[str, int] = {}
    classed: set[str] = set()
    for offset, line in statements:
        transition = STATE_TRANSITION.match(line)
        if transition:
            for name in transition.groups():
                if name != "[*]":
                    states.setdefault(name, offset)
            continue
        declared = STATE_DECL.match(line)
        if declared and "<<" not in declared.group(2):
            states.setdefault(declared.group(1), offset)
            continue
        assignment = STATE_CLASS.match(line)
        if assignment:
            classed.update(name.strip() for name in assignment.group(1).split(","))
        classed.update(re.findall(r"([\w]+):::\w+", line))
    for name, offset in states.items():
        if name not in classed:
            add(offset, f"state {name} needs a class, for example `class {name} base`")


def check_file(path: Path) -> list[Finding]:
    """Return the findings for all Mermaid blocks in one Markdown file."""
    text = path.read_text(encoding="utf-8")
    return [finding for block in mermaid_blocks(text) for finding in check_block(block, str(path))]


def changed_markdown_files() -> list[Path]:
    """Return modified, staged and untracked Markdown files."""
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
    return [Path(p) for p in sorted(set(tracked + untracked)) if p.endswith(".md")]


def main(argv: list[str]) -> int:
    """Check files and report findings."""
    hook = argv[:1] == ["--hook"]
    try:
        paths = changed_markdown_files() if hook else [Path(arg) for arg in argv]
        findings = [finding for path in paths if path.is_file() for finding in check_file(path)]
    except (OSError, UnicodeDecodeError) as error:
        if not hook:
            raise
        # A hook never blocks the agent on its own failure.
        print(f"check_mermaid skipped: {error}", file=sys.stderr)
        return 0
    if not findings:
        return 0
    print(f"Mermaid style findings (rules: {SKILL}):", file=sys.stderr)
    for finding in findings:
        print(f"  {finding}", file=sys.stderr)
    return 2 if hook else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
