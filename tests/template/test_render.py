"""Fast structural tests: render answer combinations without running post-generation tasks."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import pytest
import yaml

from tests.helpers import load_script
from tests.template.helpers import ROOT, git_init, render

deliver = load_script(
    ROOT / "plugins/project/skills/deliver/scripts/deliver.py", "deliver_for_render"
)

JINJA_LEFTOVER = re.compile(r"\{\{ |\{%|\{#")


def files(root: Path) -> list[Path]:
    return [p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts]


@pytest.fixture(scope="module")
def default(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return render(tmp_path_factory.mktemp("default") / "project")


@pytest.fixture(scope="module")
def full(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return render(
        tmp_path_factory.mktemp("full") / "project",
        architecture="hexagonal",
        interfaces=["api", "dashboard"],
        config="python",
        dataframes="polars_pandas",
        container=True,
    )


@pytest.mark.parametrize("fixture", ["default", "full"])
def test_no_jinja_leftovers(fixture: str, request: pytest.FixtureRequest) -> None:
    root = request.getfixturevalue(fixture)
    leftovers = [
        str(path.relative_to(root))
        for path in files(root)
        if JINJA_LEFTOVER.search(path.read_text(encoding="utf-8", errors="ignore"))
    ]
    assert leftovers == []


@pytest.mark.parametrize("fixture", ["default", "full"])
def test_config_files_parse(fixture: str, request: pytest.FixtureRequest) -> None:
    root = request.getfixturevalue(fixture)
    for name in ("pyproject.toml", ".codex/config.toml"):
        tomllib.loads((root / name).read_text())
    for name in (
        "Taskfile.yml",
        ".pre-commit-config.yaml",
        ".copier-answers.yml",
        ".github/workflows/ci.yml",
    ):
        yaml.safe_load((root / name).read_text())
    for name in (".claude/settings.json", ".codex/hooks.json"):
        json.loads((root / name).read_text())


def test_agents_md_stays_within_budget(default: Path) -> None:
    text = (default / "AGENTS.md").read_text()
    assert len(text.splitlines()) <= 100
    for section in ("## Project", "## Planning and decisions", "## Writing style"):
        assert section in text
    assert (default / "CLAUDE.md").read_text().startswith("@AGENTS.md")


def test_generic_protocol_and_fixed_coverage(default: Path) -> None:
    questions = yaml.safe_load((ROOT / "copier.yml").read_text())
    assert "use_case" not in questions
    assert "maturity" not in questions
    assert questions["architecture"]["default"] == "simple"
    agents = (default / "AGENTS.md").read_text()
    assert not re.search(r"^## .+ rules$", agents, re.MULTILINE)
    protocol = (default / "docs/method/evaluation-protocol.md").read_text()
    for heading in ("Design", "Baselines", "Metrics", "Acceptance"):
        assert f"## {heading}" in protocol
    assert "TODO" in protocol
    config = tomllib.loads((default / "pyproject.toml").read_text())
    assert config["tool"]["coverage"]["report"]["fail_under"] == 80


@pytest.mark.parametrize("architecture", ["simple", "pipeline", "hexagonal"])
@pytest.mark.parametrize("config", ["hydra", "python"])
def test_workspace_layout(tmp_path: Path, architecture: str, config: str) -> None:
    root = render(tmp_path / "project", architecture=architecture, config=config)
    settings = tomllib.loads((root / "pyproject.toml").read_text())
    assert "project" not in settings
    assert settings["tool"]["uv"]["workspace"]["members"] == ["apps/*", "packages/*"]
    assert "--import-mode=importlib" in settings["tool"]["pytest"]["ini_options"]["addopts"]
    app = root / "apps/demo_project"
    for path in (
        "src/demo_project",
        "tests",
        "conf",
        "pyproject.toml",
        "Taskfile.yml",
    ):
        assert (app / path).exists()
    for path in ("src", "tests", "conf"):
        assert not (root / path).exists()
    suffix = "yaml" if config == "hydra" else "toml"
    assert sorted(p.name for p in (app / "conf").iterdir()) == [f"config.{suffix}"]
    tasks = yaml.safe_load((root / "Taskfile.yml").read_text())
    assert tasks["includes"]["demo_project"]["dir"] == "."
    assert "run" not in tasks["tasks"]
    assert "--all-packages" in str(tasks["tasks"]["setup"])


def test_hooks_match_the_completion_guard(default: Path) -> None:
    settings = json.loads((default / ".claude/settings.json").read_text())
    codex = json.loads((default / ".codex/hooks.json").read_text())
    assert settings["hooks"]["Stop"][0]["hooks"][0]["command"] == deliver.CLAUDE_HOOK_COMMAND
    assert codex["hooks"]["Stop"][0]["hooks"][0]["command"] == deliver.CODEX_HOOK_COMMAND
    git_init(default)
    problems = [p for p in deliver.doctor(default) if "uv is not on PATH" not in p]
    assert problems == []


def test_marketplace_is_referenced_at_the_pinned_tag(default: Path) -> None:
    settings = json.loads((default / ".claude/settings.json").read_text())
    codex = tomllib.loads((default / ".codex/config.toml").read_text())
    source = settings["extraKnownMarketplaces"]["agentic-ds-template"]["source"]
    assert source == {"source": "github", "repo": "testowner/agentic-ds-template", "ref": "v0.1.0"}
    assert codex["marketplaces"]["agentic-ds-template"]["ref"] == "v0.1.0"
    assert set(settings["enabledPlugins"]) == {
        "superpowers@agentic-ds-template",
        "project@agentic-ds-template",
    }
    assert settings["attribution"] == {"commit": "", "pr": ""}
    for marketplace_file in ("catalog.toml", ".claude-plugin", "plugins", "copier.yml"):
        assert not (default / marketplace_file).exists()


def test_vendored_plugin_files_match_the_plugin(default: Path) -> None:
    plugin = ROOT / "plugins/project/skills/deliver"
    assert (default / "scripts/agent/deliver.py").read_bytes() == (
        plugin / "scripts/deliver.py"
    ).read_bytes()
    mermaid = ROOT / "plugins/project/skills/mermaid/scripts/check_mermaid.py"
    assert (default / "scripts/agent/check_mermaid.py").read_bytes() == mermaid.read_bytes()
    reviewer = (default / ".codex/agents/reviewer.toml").read_text()
    assert reviewer == (plugin / "assets/roles/codex/reviewer.toml").read_text()


def test_sphinx_applies_the_mermaid_house_style(default: Path) -> None:
    checker = load_script(
        ROOT / "plugins/project/skills/mermaid/scripts/check_mermaid.py", "check_mermaid"
    )
    conf: dict[str, object] = {"__file__": str(default / "docs/conf.py")}
    exec((default / "docs/conf.py").read_text(), conf)
    assert conf["mermaid_init_config"] == {
        "startOnLoad": False,
        "themeCSS": " ".join(checker.STANDARD_CSS),
    }
    assert conf["myst_fence_as_directive"] == ["mermaid"]


@pytest.mark.parametrize(
    ("harnesses", "present", "absent"),
    [
        (["codex"], [".codex/hooks.json", "scripts/agent/guard.py"], [".claude", "CLAUDE.md"]),
        (["claude"], [".claude/settings.json", "CLAUDE.md"], [".codex"]),
        ([], ["AGENTS.md"], [".claude", ".codex", "scripts/agent"]),
    ],
)
def test_harness_selection(
    tmp_path: Path, harnesses: list[str], present: list[str], absent: list[str]
) -> None:
    root = render(tmp_path / "project", agent_harnesses=harnesses)
    assert all((root / path).exists() for path in present)
    assert not any((root / path).exists() for path in absent)
    hooks = (root / ".pre-commit-config.yaml").read_text()
    assert ("scripts/agent/check_mermaid.py" in hooks) == bool(harnesses)


def test_hexagonal_architecture(full: Path) -> None:
    package = full / "apps/demo_project/src/demo_project"
    for layer in ("domain", "application", "ports", "adapters", "bootstrap", "entrypoints"):
        assert (package / layer / "AGENTS.md").is_file()
    contracts = tomllib.loads((full / "apps/demo_project/pyproject.toml").read_text())["tool"][
        "importlinter"
    ]["contracts"]
    assert contracts[0]["type"] == "layers"
    assert (package / "entrypoints/api.py").is_file()
    assert (full / "apps/demo_project/tests/integration/test_api.py").is_file()


def test_options_select_files_and_dependencies(full: Path, default: Path) -> None:
    full_deps = tomllib.loads((full / "apps/demo_project/pyproject.toml").read_text())["project"][
        "dependencies"
    ]
    default_deps = tomllib.loads((default / "apps/demo_project/pyproject.toml").read_text())[
        "project"
    ]["dependencies"]
    assert {"pandas", "fastapi", "streamlit", "mlflow"} <= set(full_deps)
    assert "hydra-core" not in full_deps
    assert {"hydra-core", "mlflow", "polars"} <= set(default_deps)
    assert (full / "apps/demo_project/conf/config.toml").is_file()
    assert not (full / "apps/demo_project/conf/config.yaml").exists()
    assert (default / "apps/demo_project/conf/config.yaml").is_file()
    assert (full / "apps/demo_project/Dockerfile").is_file()
    assert not (default / "apps/demo_project/Dockerfile").exists()
    coverage = tomllib.loads((full / "pyproject.toml").read_text())["tool"]["coverage"]["report"]
    assert coverage["fail_under"] == 80


def test_optional_parts_can_be_switched_off(tmp_path: Path) -> None:
    root = render(tmp_path / "project", tracking="none", notebooks=False, ci="none")
    pyproject = (root / "pyproject.toml").read_text()
    assert "mlflow" not in pyproject
    assert not (root / "notebooks").exists()
    assert not (root / ".github").exists()
    assert not (root / "apps/demo_project/tests/integration/test_tracking.py").exists()


def test_context_answers_seed_project_owned_docs(tmp_path: Path) -> None:
    root = render(tmp_path / "project", context_problem="Retailers lose sales to stock-outs.")
    assert "Retailers lose sales to stock-outs." in (root / "AGENTS.md").read_text()
    assert "Retailers lose sales to stock-outs." in (root / "docs/context/project.md").read_text()
    assert "TODO" in (root / "docs/context/data.md").read_text()


def test_no_caches_or_vcs_files_are_rendered(default: Path) -> None:
    unwanted = [
        p
        for p in default.rglob("*")
        if p.name in {"__pycache__", ".DS_Store"} or p.suffix == ".pyc"
    ]
    assert unwanted == []


def test_pipeline_architecture(tmp_path: Path) -> None:
    root = render(tmp_path / "project", architecture="pipeline", config="python")
    package = root / "apps/demo_project/src/demo_project"
    for module in ("catalog.py", "settings.py", "cli.py", "pipelines/graph.py"):
        assert (package / module).is_file()
    for pipeline in ("features", "training", "inference"):
        assert (package / "pipelines" / f"{pipeline}.py").is_file()
    contracts = tomllib.loads((root / "apps/demo_project/pyproject.toml").read_text())["tool"][
        "importlinter"
    ]["contracts"]
    assert {"independence", "forbidden"} == {c["type"] for c in contracts}
    assert "[catalog]" in (root / "apps/demo_project/conf/config.toml").read_text()


def test_azure_infrastructure(tmp_path: Path) -> None:
    root = render(
        tmp_path / "project",
        interfaces=["api"],
        infrastructure="azure",
        azure_components=["job", "api", "storage"],
    )
    for path in (
        "infra/stack.py",
        "infra/__main__.py",
        "infra/Pulumi.yaml",
        "infra/tests/test_stack.py",
    ):
        assert (root / path).is_file()
    assert (root / "apps/demo_project/Dockerfile").is_file()
    assert (root / ".github/workflows/deploy.yml").is_file()
    assert (root / "docs/guides/deployment.md").is_file()
    stack = yaml.safe_load((root / "infra/Pulumi.dev.yaml").read_text())["config"]
    assert stack["demo-project-infra:job"] is True
    assert stack["demo-project-infra:mlflow"] is False
    assert "infra:test" in (root / "Taskfile.yml").read_text()
    assert "api: bool = True" in (root / "infra/stack.py").read_text()


def test_no_infrastructure_by_default(default: Path) -> None:
    for path in (
        "infra",
        "taskfiles/infra.yml",
        ".github/workflows/deploy.yml",
        "docs/guides/deployment.md",
    ):
        assert not (default / path).exists()


@pytest.mark.parametrize(
    ("answers", "message"),
    [
        ({"azure_components": ["api"]}, "REST API interface"),
        ({"azure_components": ["mlflow"], "tracking": "none"}, "MLflow tracking"),
    ],
)
def test_azure_component_validation(tmp_path: Path, answers: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        render(tmp_path / "project", infrastructure="azure", **answers)


def test_custom_app_paths_and_optional_files(tmp_path: Path) -> None:
    root = render(tmp_path / "project", package_name="custom_app", container=True, config="python")
    app = root / "apps/custom_app"
    assert (app / "Dockerfile").is_file()
    assert (app / "conf/config.toml").is_file()
    assert not (app / "conf/config.yaml").exists()
    dockerfile = (app / "Dockerfile").read_text()
    assert "--package custom_app" in dockerfile
    assert "COPY apps ./apps" in dockerfile
    assert "apps/custom_app/Dockerfile" in (app / "Taskfile.yml").read_text()
