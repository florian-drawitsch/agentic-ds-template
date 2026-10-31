"""Agent wiring of a rendered project: delivery tasks, Codex settings, permissions, project copies."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest
import yaml

from tests.helpers import load_script
from tests.template.helpers import ROOT, render

SKILLS = ROOT / "plugins/project/skills"
deliver = load_script(SKILLS / "deliver/scripts/deliver.py", "deliver_for_wiring")


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return render(tmp_path_factory.mktemp("wiring") / "project")


def task_names(root: Path) -> set[str]:
    """Return every task name, with included Taskfiles under their namespace."""
    taskfile = yaml.safe_load((root / "Taskfile.yml").read_text())
    names = set(taskfile["tasks"])
    for namespace, include in taskfile.get("includes", {}).items():
        path = include if isinstance(include, str) else include["taskfile"]
        for name in yaml.safe_load((root / path).read_text())["tasks"]:
            names.add(namespace if name == "default" else f"{namespace}:{name}")
    return names


def test_project_copies_match_the_plugin_and_resolve_locally(project: Path) -> None:
    for item in deliver.COMPANIONS:
        copy = project / "scripts/agent" / Path(item).name
        assert copy.read_bytes() == (SKILLS / item).read_bytes(), copy
    local = load_script(project / "scripts/agent/deliver.py", "deliver_project_copy")
    for item in deliver.COMPANIONS:
        assert local.companion(item) == (project / "scripts/agent" / Path(item).name).resolve()


def test_codex_actions_and_setup_use_existing_tasks(project: Path) -> None:
    names = task_names(project)
    assert {"deliver", "deliver:resume", "deliver:status", "deliver:setup", "test-fast"} <= names
    environment = tomllib.loads((project / ".codex/environments/environment.toml").read_text())
    commands = [action["command"] for action in environment["actions"]]
    commands += re.findall(r"^task .+$", environment["setup"]["script"], re.MULTILINE)
    for command in commands:
        assert command.split()[1] in names, command


def test_codex_keeps_harness_files_writable_and_has_a_delivery_profile(project: Path) -> None:
    config = tomllib.loads((project / ".codex/config.toml").read_text())
    roots = {
        (project / ".codex" / path).resolve()
        for path in config["sandbox_workspace_write"]["writable_roots"]
        if not path.startswith("~")
    }
    assert {(project / ".agents").resolve(), (project / ".codex").resolve()} <= roots
    profile = config["permissions"]["deliver"]
    assert profile["filesystem"][":workspace_roots"]["**/*.env"] == "deny"


def test_only_read_only_github_commands_skip_approval(project: Path) -> None:
    allow = json.loads((project / ".claude/settings.json").read_text())["permissions"]["allow"]
    rules = (project / ".codex/rules/default.rules").read_text()
    for mutating in ("gh pr create", "gh pr merge", "gh api"):
        assert not any(rule.startswith(f"Bash({mutating}") for rule in allow)
        assert json.dumps(mutating.split())[:-1] not in rules
    assert "Bash(gh pr view *)" in allow


def test_worktrees_never_receive_credentials(project: Path) -> None:
    assert deliver.worktreeinclude_credentials(project) == []
    sandbox = tomllib.loads((project / "deliver.toml").read_text())["sandbox"]
    assert sandbox == {"extra_hosts": [], "gate": "env-scrub"}


def test_hook_configs_have_no_machine_specific_paths(project: Path) -> None:
    home = re.compile(r"/(?:Users|home)/[^/\s\"]+")
    for path in (".claude/settings.json", ".codex/hooks.json", ".codex/config.toml"):
        assert home.search((project / path).read_text()) is None, path


def test_publication_scans_for_secrets(project: Path) -> None:
    hooks = yaml.safe_load((project / ".pre-commit-config.yaml").read_text())
    ids = {hook["id"] for repo in hooks["repos"] for hook in repo["hooks"]}
    assert "gitleaks" in ids


def test_without_agents_no_delivery_files(tmp_path: Path) -> None:
    root = render(tmp_path / "project", agent_harnesses=[])
    for path in ("deliver.toml", "scripts/agent", ".codex", ".claude"):
        assert not (root / path).exists(), path


def test_project_tools_leave_plugin_copies_alone(project: Path) -> None:
    pyproject = tomllib.loads((project / "pyproject.toml").read_text())
    ruff = set(pyproject["tool"]["ruff"]["extend-exclude"])
    ty = set(pyproject["tool"]["ty"]["src"]["exclude"])
    copies = {f"scripts/agent/{Path(item).name}" for item in deliver.COMPANIONS}
    copies = {copy for copy in copies if copy.endswith(".py")} | {"scripts/agent/deliver.py"}
    assert copies <= ruff
    assert copies <= ty
