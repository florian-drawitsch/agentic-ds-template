"""Render the template for tests."""

from __future__ import annotations

import itertools
import subprocess
from pathlib import Path
from typing import Any

import copier

ROOT = Path(__file__).resolve().parents[2]
BASE_ANSWERS: dict[str, Any] = {
    "project_name": "demo-project",
    "github_owner": "testowner",
    "description": "Demo project.",
}


def render(destination: Path, *, tasks: bool = False, **answers: Any) -> Path:
    """Render the template's working tree into destination and return it."""
    copier.run_copy(
        str(ROOT),
        destination,
        data=BASE_ANSWERS | answers,
        defaults=True,
        unsafe=True,
        skip_tasks=not tasks,
        quiet=True,
    )
    return destination


def git_init(path: Path) -> None:
    """Initialize a git repository, as the template's post-generation task does."""
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)


def pairwise(parameters: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """Return a small set of combinations that covers every pair of parameter values.

    Greedy: repeatedly pick the full combination that covers the most uncovered pairs. Values
    are tracked by index, so unhashable values such as lists work. The result is deterministic.
    """
    names = list(parameters)
    pairs = list(itertools.combinations(range(len(names)), 2))
    uncovered = {
        (a, i, b, j)
        for a, b in pairs
        for i in range(len(parameters[names[a]]))
        for j in range(len(parameters[names[b]]))
    }
    candidates = list(itertools.product(*(range(len(parameters[n])) for n in names)))
    chosen: list[tuple[int, ...]] = []
    while uncovered:
        best = max(
            candidates,
            key=lambda c: sum((a, c[a], b, c[b]) in uncovered for a, b in pairs),
        )
        chosen.append(best)
        uncovered -= {(a, best[a], b, best[b]) for a, b in pairs}
    return [{n: parameters[n][i] for n, i in zip(names, combo, strict=True)} for combo in chosen]
