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
    "architecture": ["simple", "layered"],
    "config": ["hydra", "python"],
    "tracking": ["mlflow", "none"],
    "dataframes": ["polars", "polars_pandas"],
    "interfaces": [[], ["api"], ["dashboard"], ["api", "dashboard"]],
    "maturity": ["poc", "production"],
    "agent_harnesses": [["claude", "codex"], ["codex"], []],
    "ci": ["github", "none"],
}
# Computer vision is covered structurally; its PyTorch stack is too heavy for every run.
LIGHT_USE_CASES = ["forecasting", "anomaly_detection", "optimization", "genai", "tabular", "analysis"]
COMBINATIONS = [
    combination | {"use_case": LIGHT_USE_CASES[index % len(LIGHT_USE_CASES)]}
    for index, combination in enumerate(pairwise(PARAMETERS))
]


def run(command: list[str], cwd: Path) -> None:
    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    result = subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 0, f"{' '.join(command)} failed:\n{result.stdout[-4000:]}\n{result.stderr[-4000:]}"


@pytest.mark.slow
@pytest.mark.parametrize("answers", COMBINATIONS, ids=lambda a: "-".join(str(v) for v in a.values()))
def test_rendered_project_passes_its_gate(tmp_path: Path, answers: dict[str, Any]) -> None:
    root = render(tmp_path / "project", tasks=True, **answers)
    run(["uv", "sync", "--locked"], root)
    run(["task", "example-data"], root)
    run(["task", "run"], root)
    run(["task", "check"], root)
    run(["task", "docs:build"], root)
