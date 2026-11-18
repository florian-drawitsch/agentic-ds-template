"""Slow end-to-end tests: render a pairwise set of answers and run each project's merge gate.

Run with `task test-e2e`. Needs network access for dependency resolution on the first run.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.template.helpers import pairwise, render

PARAMETERS: dict[str, list[Any]] = {
    "architecture": ["simple", "pipeline", "hexagonal"],
    "config": ["hydra", "python"],
    "tracking": ["mlflow", "none"],
    "dataframes": ["polars", "polars_pandas"],
    "interfaces": [[], ["api"], ["dashboard"], ["api", "dashboard"]],
    "agent_harnesses": [["claude", "codex"], ["codex"], []],
    "ci": ["github", "none"],
    "infrastructure": ["none", "azure"],
    "factory": [False, True],
}
# The factory needs GitHub CI and both harnesses; elsewhere Copier rejects it.
COMBINATIONS = [
    answers
    | {
        "factory": answers["factory"]
        and answers["ci"] == "github"
        and answers["agent_harnesses"] == ["claude", "codex"]
    }
    for answers in pairwise(PARAMETERS)
]


def run(command: list[str], cwd: Path) -> None:
    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, (
        f"{' '.join(command)} failed:\n{result.stdout[-4000:]}\n{result.stderr[-4000:]}"
    )


@pytest.mark.slow
@pytest.mark.parametrize(
    "answers", COMBINATIONS, ids=lambda a: "-".join(str(v) for v in a.values())
)
def test_rendered_project_passes_its_gate(tmp_path: Path, answers: dict[str, Any]) -> None:
    root = render(tmp_path / "project", tasks=True, **answers)
    run(["task", "setup"], root)
    rows = "".join(f"{i},{i % 10},{2 * (i % 10) + 1}\n" for i in range(50))
    (root / "data" / "01_raw" / "input.csv").write_text("step,x,target\n" + rows)
    run(["task", "demo_project:run"], root)
    run(["task", "check"], root)
    run(["task", "docs:build"], root)


@pytest.mark.slow
def test_second_app_passes_workspace_gate(tmp_path: Path) -> None:
    from tests.template.test_workspace import second_app

    root = render(tmp_path / "project", tasks=True)
    second_app(root)
    taskfile = root / "Taskfile.yml"
    taskfile.write_text(
        taskfile.read_text().replace(
            "includes:\n",
            "includes:\n  second_app:\n    taskfile: ./apps/second_app/Taskfile.yml\n    dir: .\n",
        )
    )
    # A shared library without import contracts must not break the contract hook.
    shared = root / "packages/shared"
    (shared / "src/shared").mkdir(parents=True)
    (shared / "src/shared/__init__.py").write_text('"""Shared helpers."""\n')
    (shared / "pyproject.toml").write_text(
        '[project]\nname = "shared"\nversion = "0.1.0"\nrequires-python = ">=3.13"\n\n'
        '[build-system]\nrequires = ["uv_build>=0.12.9,<0.13"]\nbuild-backend = "uv_build"\n'
    )
    run(["uv", "lock"], root)
    run(["task", "setup"], root)
    rows = "".join(f"{i},{i % 10},{2 * (i % 10) + 1}\n" for i in range(50))
    (root / "data/01_raw/input.csv").write_text("step,x,target\n" + rows)
    run(["task", "check"], root)
    run(["task", "second_app:run"], root)
    run(["task", "docs:build"], root)
    run(["task", "docs:diagrams"], root)
    diagrams = {path.name for path in (root / "docs/architecture/generated").iterdir()}
    assert {"packages_demo_project.mmd", "packages_second_app.mmd"} <= diagrams


@pytest.mark.slow
def test_forbidden_import_fails_lint(tmp_path: Path) -> None:
    root = render(
        tmp_path / "project", tasks=True, tracking="none", notebooks=False, config="python"
    )
    run(["task", "setup"], root)
    model = root / "apps/demo_project/src/demo_project/model.py"
    model.write_text(model.read_text() + "\nfrom demo_project import cli  # noqa: F401\n")
    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    result = subprocess.run(
        ["task", "lint"], cwd=root, env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode != 0
    assert "Nothing imports entry points" in result.stdout + result.stderr
    assert "BROKEN" in result.stdout + result.stderr
