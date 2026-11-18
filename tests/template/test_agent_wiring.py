"""Agent wiring of a rendered project: tasks, settings, permissions and copies."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

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


# --- ticket factory ---

FACTORY = ROOT / "plugins/factory/skills/factory/scripts/factory.py"
FACTORY_FILES = (
    "factory.toml",
    "taskfiles/factory.yml",
    "scripts/agent/factory.py",
    "docs/guides/factory.md",
)
FACTORY_INIT = "uv run --script scripts/agent/factory.py init --skip-unavailable"


@pytest.fixture(scope="module")
def factory_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return render(tmp_path_factory.mktemp("factory") / "project", factory=True)


def test_factory_files_follow_the_answer(project: Path, factory_project: Path) -> None:
    for path in FACTORY_FILES:
        assert not (project / path).exists(), path
        assert (factory_project / path).is_file(), path


def test_factory_question_needs_github_ci_and_both_harnesses(tmp_path: Path) -> None:
    cases: dict[str, dict[str, Any]] = {
        "no-ci": {"ci": "none"},
        "one": {"agent_harnesses": ["claude"]},
    }
    for name, answers in cases.items():
        assert not (render(tmp_path / name, **answers) / "factory.toml").exists(), name
        with pytest.raises(ValueError, match="other vendor"):
            render(tmp_path / f"{name}-forced", factory=True, **answers)


def test_factory_copy_matches_the_plugin_and_reads_its_config(factory_project: Path) -> None:
    copy = factory_project / "scripts/agent/factory.py"
    assert copy.read_bytes() == FACTORY.read_bytes()
    module = load_script(copy, "factory_project_copy")
    config = module.load_config(factory_project)
    ci = yaml.safe_load((factory_project / ".github/workflows/ci.yml").read_text())
    assert set(config.required_checks) <= {job["name"] for job in ci["jobs"].values()}
    assert {config.deliver.harness, config.deliver.reviewer} == {"claude", "codex"}
    assert config.project is None


def test_factory_tasks_plugins_and_lint_excludes_are_wired(
    project: Path, factory_project: Path
) -> None:
    assert {"factory:init", "factory:status", "factory:tick", "factory:sync"} <= task_names(
        factory_project
    )
    setup = yaml.safe_load((factory_project / "Taskfile.yml").read_text())["tasks"]["setup"]
    assert FACTORY_INIT in setup["cmds"]
    copier = yaml.safe_load((ROOT / "copier.yml").read_text())
    assert FACTORY_INIT in [task["command"] for task in copier["_tasks"]]
    for root, enabled in ((factory_project, True), (project, False)):
        claude = json.loads((root / ".claude/settings.json").read_text())["enabledPlugins"]
        codex = tomllib.loads((root / ".codex/config.toml").read_text())["plugins"]
        assert ("factory@agentic-ds-template" in claude) is enabled
        assert ("factory@agentic-ds-template" in codex) is enabled
    pyproject = tomllib.loads((factory_project / "pyproject.toml").read_text())
    assert "scripts/agent/factory.py" in pyproject["tool"]["ruff"]["extend-exclude"]
    assert "scripts/agent/factory.py" in pyproject["tool"]["ty"]["src"]["exclude"]
    assert "guides/factory" in (factory_project / "docs/index.md").read_text()


def test_factory_init_skips_before_the_repository_exists(factory_project: Path) -> None:
    result = subprocess.run(
        [sys.executable, "scripts/agent/factory.py", "init", "--skip-unavailable"],
        cwd=factory_project,
        env={"PATH": "/usr/bin:/bin", "HOME": str(factory_project), "GH_CONFIG_DIR": "/nonexistent"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "skipped" in result.stderr
    assert "project =" not in (factory_project / "factory.toml").read_text().replace("# project", "")
