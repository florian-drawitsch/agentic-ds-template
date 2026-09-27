"""Slow test: `copier update` keeps project-owned files and brings template-owned changes.

Clones the committed template, so commit template changes before running `task test-e2e`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import copier
import pytest

from tests.template.helpers import BASE_ANSWERS, ROOT

GIT_USER = ["-c", "user.name=test", "-c", "user.email=test@example.com"]


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *GIT_USER, *args], cwd=cwd, check=True, capture_output=True)


@pytest.mark.slow
def test_update_keeps_project_owned_files(tmp_path: Path) -> None:
    template = tmp_path / "template"
    subprocess.run(["git", "clone", "-q", str(ROOT), str(template)], check=True)
    git(template, "tag", "v1")
    guide = template / "template/docs/guides/development.md.jinja"
    guide.write_text(guide.read_text() + "\nTemplate change for the update test.\n")
    git(template, "commit", "-qam", "test: change a template-owned file")
    git(template, "tag", "v2")

    project = tmp_path / "project"
    copier.run_copy(
        str(template), project, data=BASE_ANSWERS, defaults=True, vcs_ref="v1",
        unsafe=True, skip_tasks=True, quiet=True,
    )
    git(project, "init", "-q", "-b", "main")
    model = project / "src/demo_project/model.py"
    model.write_text('"""Project-specific model."""\n')
    (project / "src/demo_project/evaluate.py").unlink()
    (project / "docs/context/project.md").write_text("# Project context\n\nOur own text.\n")
    git(project, "add", "-A")
    git(project, "commit", "-qm", "project changes")

    copier.run_update(
        project, defaults=True, vcs_ref="v2", unsafe=True, skip_tasks=True, overwrite=True,
        quiet=True,
    )

    assert model.read_text() == '"""Project-specific model."""\n'
    assert not (project / "src/demo_project/evaluate.py").exists()
    assert (project / "docs/context/project.md").read_text().endswith("Our own text.\n")
    assert "Template change for the update test." in (project / "docs/guides/development.md").read_text()
