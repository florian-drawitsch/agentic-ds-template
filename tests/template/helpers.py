"""Render the template for tests.

Rendering is the most expensive step in the fast suite, so `render` keeps it cheap:

- It renders from a plain snapshot of the working tree, not from this git repository. From a
  repository, Copier clones it on every call.
- It turns off Copier's per-file git index sync, which starts one `git` process per rendered
  file. That sync only matters for `copier update` in an existing repository, never for a fresh
  render.
- It renders each distinct set of answers once per test process and hands every caller its own
  copy, so tests may change the files they get.
"""

from __future__ import annotations

import atexit
import functools
import itertools
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from unittest import mock

import copier
import copier._main

ROOT = Path(__file__).resolve().parents[2]
BASE_ANSWERS: dict[str, Any] = {
    "project_name": "demo-project",
    "github_owner": "testowner",
    "description": "Demo project.",
}

# Private Copier API. If an upgrade renames it, fail here instead of silently slowing down.
assert hasattr(copier._main, "is_git_available"), "copier._main.is_git_available is gone"


@functools.cache
def _scratch() -> Path:
    path = Path(tempfile.mkdtemp(prefix="template-renders-"))
    atexit.register(shutil.rmtree, path, ignore_errors=True)
    return path


@functools.cache
def template_snapshot() -> Path:
    """Copy the files Copier would see in this working tree: tracked and untracked, not ignored.

    Symlinks stay symlinks, so links from `template/` into `plugins/` still resolve.
    """
    listing = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode()
    snapshot = _scratch() / "template"
    for name in sorted(set(filter(None, listing.split("\0")))):
        source, target = ROOT / name, snapshot / name
        if not os.path.lexists(source):
            continue  # tracked but deleted in the working tree
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_symlink():
            target.symlink_to(os.readlink(source))
        else:
            shutil.copy2(source, target)
    return snapshot


def _copy(destination: Path, *, tasks: bool, answers: dict[str, Any]) -> None:
    with mock.patch.object(copier._main, "is_git_available", lambda: False):
        copier.run_copy(
            str(template_snapshot()),
            destination,
            data=BASE_ANSWERS | answers,
            defaults=True,
            unsafe=True,
            skip_tasks=not tasks,
            quiet=True,
        )


_renders: dict[str, Path] = {}


def render(destination: Path, *, tasks: bool = False, **answers: Any) -> Path:
    """Render the template's working tree into destination and return it.

    Without post-generation tasks, identical answers render once per process and later calls
    copy that result. Prefer answers an existing test already uses over a new combination.
    """
    if tasks:
        _copy(destination, tasks=True, answers=answers)
        return destination
    key = json.dumps(answers, sort_keys=True)
    if key not in _renders:
        cached = Path(tempfile.mkdtemp(dir=_scratch())) / "project"
        _copy(cached, tasks=False, answers=answers)
        _renders[key] = cached
    shutil.copytree(_renders[key], destination, symlinks=True)
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
