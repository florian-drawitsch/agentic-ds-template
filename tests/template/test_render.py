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

deliver = load_script(ROOT / "plugins/project/skills/deliver/scripts/deliver.py", "deliver_for_render")

JINJA_LEFTOVER = re.compile(r"\{\{ |\{%|\{#")
USE_CASES = [
    "forecasting",
    "anomaly_detection",
    "optimization",
    "computer_vision",
    "genai",
    "tabular",
    "analysis",
]


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
        maturity="production",
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
    for name in ("Taskfile.yml", ".pre-commit-config.yaml", ".copier-answers.yml", ".github/workflows/ci.yml"):
        yaml.safe_load((root / name).read_text())
    for name in (".claude/settings.json", ".codex/hooks.json"):
        json.loads((root / name).read_text())


def test_agents_md_stays_within_budget(default: Path) -> None:
    text = (default / "AGENTS.md").read_text()
    assert len(text.splitlines()) <= 100
    for section in ("## Project", "## Forecasting rules", "## Planning and decisions", "## Writing style"):
        assert section in text
    assert (default / "CLAUDE.md").read_text().startswith("@AGENTS.md")


@pytest.mark.parametrize("use_case", USE_CASES)
def test_every_use_case_renders_its_rules_and_protocol(tmp_path: Path, use_case: str) -> None:
    root = render(tmp_path / "project", use_case=use_case)
    agents = (root / "AGENTS.md").read_text()
    protocol = (root / "docs/method/evaluation-protocol.md").read_text()
    assert re.search(r"^## .+ rules$", agents, re.MULTILINE)
    assert len(agents.splitlines()) <= 100
    assert "## " in protocol.split("\n", 2)[2]


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
    assert (default / "scripts/agent/deliver.py").read_bytes() == (plugin / "scripts/deliver.py").read_bytes()
    reviewer = (default / ".codex/agents/reviewer.toml").read_text()
    assert reviewer == (plugin / "assets/roles/codex/reviewer.toml").read_text()


@pytest.mark.parametrize(
    ("harnesses", "present", "absent"),
    [
        (["codex"], [".codex/hooks.json", "scripts/agent/guard.py"], [".claude", "CLAUDE.md"]),
        (["claude"], [".claude/settings.json", "CLAUDE.md"], [".codex"]),
        ([], ["AGENTS.md"], [".claude", ".codex", "scripts/agent"]),
    ],
)
def test_harness_selection(tmp_path: Path, harnesses: list[str], present: list[str], absent: list[str]) -> None:
    root = render(tmp_path / "project", agent_harnesses=harnesses)
    assert all((root / path).exists() for path in present)
    assert not any((root / path).exists() for path in absent)


def test_hexagonal_architecture(full: Path) -> None:
    package = full / "src/demo_project"
    for layer in ("domain", "application", "ports", "adapters", "bootstrap", "entrypoints"):
        assert (package / layer / "AGENTS.md").is_file()
    contracts = tomllib.loads((full / "pyproject.toml").read_text())["tool"]["importlinter"]["contracts"]
    assert contracts[0]["type"] == "layers"
    assert (package / "entrypoints/api.py").is_file()
    assert (full / "tests/integration/test_api.py").is_file()


def test_options_select_files_and_dependencies(full: Path, default: Path) -> None:
    full_deps = tomllib.loads((full / "pyproject.toml").read_text())["project"]["dependencies"]
    default_deps = tomllib.loads((default / "pyproject.toml").read_text())["project"]["dependencies"]
    assert {"pandas", "fastapi", "streamlit", "mlflow"} <= set(full_deps)
    assert "hydra-core" not in full_deps
    assert {"hydra-core", "mlflow", "polars"} <= set(default_deps)
    assert (full / "conf/config.toml").is_file()
    assert not (full / "conf/config.yaml").exists()
    assert (default / "conf/config.yaml").is_file()
    assert (full / "Dockerfile").is_file()
    assert not (default / "Dockerfile").exists()
    coverage = tomllib.loads((full / "pyproject.toml").read_text())["tool"]["coverage"]["report"]
    assert coverage["fail_under"] == 80


def test_optional_parts_can_be_switched_off(tmp_path: Path) -> None:
    root = render(tmp_path / "project", tracking="none", notebooks=False, ci="none")
    pyproject = (root / "pyproject.toml").read_text()
    assert "mlflow" not in pyproject
    assert not (root / "notebooks").exists()
    assert not (root / ".github").exists()
    assert not (root / "tests/integration/test_tracking.py").exists()


def test_context_answers_seed_project_owned_docs(tmp_path: Path) -> None:
    root = render(tmp_path / "project", context_problem="Retailers lose sales to stock-outs.")
    assert "Retailers lose sales to stock-outs." in (root / "AGENTS.md").read_text()
    assert "Retailers lose sales to stock-outs." in (root / "docs/context/project.md").read_text()
    assert "TODO" in (root / "docs/context/data.md").read_text()


def test_no_caches_or_vcs_files_are_rendered(default: Path) -> None:
    unwanted = [
        p for p in default.rglob("*") if p.name in {"__pycache__", ".DS_Store"} or p.suffix == ".pyc"
    ]
    assert unwanted == []


def test_pipeline_architecture(tmp_path: Path) -> None:
    root = render(tmp_path / "project", maturity="production", config="python")
    package = root / "src/demo_project"
    for module in ("catalog.py", "settings.py", "cli.py", "pipelines/graph.py"):
        assert (package / module).is_file()
    for pipeline in ("features", "training", "inference"):
        assert (package / "pipelines" / f"{pipeline}.py").is_file()
    contracts = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["importlinter"]["contracts"]
    assert {"independence", "forbidden"} == {c["type"] for c in contracts}
    assert "[catalog]" in (root / "conf/config.toml").read_text()


def test_azure_infrastructure(tmp_path: Path) -> None:
    root = render(
        tmp_path / "project",
        interfaces=["api"],
        infrastructure="azure",
        azure_components=["job", "api", "storage"],
    )
    for path in ("infra/stack.py", "infra/__main__.py", "infra/Pulumi.yaml", "infra/tests/test_stack.py"):
        assert (root / path).is_file()
    assert (root / "Dockerfile").is_file()
    assert (root / ".github/workflows/deploy.yml").is_file()
    assert (root / "docs/guides/deployment.md").is_file()
    stack = yaml.safe_load((root / "infra/Pulumi.dev.yaml").read_text())["config"]
    assert stack["demo-project-infra:job"] is True
    assert stack["demo-project-infra:mlflow"] is False
    assert "infra:test" in (root / "Taskfile.yml").read_text()
    assert "api: bool = True" in (root / "infra/stack.py").read_text()


def test_no_infrastructure_by_default(default: Path) -> None:
    for path in ("infra", "taskfiles/infra.yml", ".github/workflows/deploy.yml", "docs/guides/deployment.md"):
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
