"""Workspace boundary checks on rendered apps."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.template.helpers import render


def check(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "scripts/check_workspace.py"],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )


def second_app(root: Path) -> Path:
    app = root / "apps/second_app"
    shutil.copytree(root / "apps/demo_project", app)
    (app / "src/demo_project").rename(app / "src/second_app")
    for path in app.rglob("*"):
        if path.is_file():
            path.write_text(path.read_text().replace("demo_project", "second_app"))
    return app


def test_workspace_accepts_generated_app(tmp_path: Path) -> None:
    root = render(tmp_path / "project")
    assert check(root).returncode == 0


@pytest.mark.parametrize("statement", ["import second_app", "from second_app.model import fit"])
def test_workspace_rejects_cross_app_import(tmp_path: Path, statement: str) -> None:
    root = render(tmp_path / "project")
    second_app(root)
    (root / "apps/demo_project/src/demo_project/bad.py").write_text(statement + "\n")
    result = check(root)
    assert result.returncode == 1
    assert "imports app" in result.stdout


@pytest.mark.parametrize("optional", [False, True])
def test_workspace_rejects_app_dependency(tmp_path: Path, optional: bool) -> None:
    root = render(tmp_path / "project")
    app = second_app(root)
    config = app / "pyproject.toml"
    if optional:
        config.write_text(
            config.read_text()
            + '\n[project.optional-dependencies]\nother = ["demo-project>=0.1"]\n'
        )
    else:
        config.write_text(
            config.read_text().replace("dependencies = [", 'dependencies = ["demo-project>=0.1",')
        )
    result = check(root)
    assert result.returncode == 1
    assert "depends on app" in result.stdout


def test_workspace_requires_registration_or_exclusion(tmp_path: Path) -> None:
    root = render(tmp_path / "project")
    (root / "apps/frontend").mkdir()
    result = check(root)
    assert result.returncode == 1
    assert "apps/frontend" in result.stdout
    config = root / "pyproject.toml"
    config.write_text(
        config.read_text().replace(
            'members = ["apps/*", "packages/*"]',
            'members = ["apps/*", "packages/*"]\nexclude = ["apps/frontend"]',
        )
    )
    assert check(root).returncode == 0
