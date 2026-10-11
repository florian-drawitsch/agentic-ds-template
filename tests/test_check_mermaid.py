"""The Mermaid style checker enforces the house style of the mermaid skill."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).parents[1]
SKILL_DIR = ROOT / "plugins/project/skills/mermaid"
SCRIPT = SKILL_DIR / "scripts/check_mermaid.py"
SKILL = SKILL_DIR / "SKILL.md"

# The standard frontmatter as the skill documents it, so skill and checker cannot drift apart.
FRONTMATTER = re.search(
    r"```text\n(---\n.*?\n---)\n```", SKILL.read_text(encoding="utf-8"), re.DOTALL
)
assert FRONTMATTER is not None
FM = FRONTMATTER.group(1)


@pytest.fixture(scope="module")
def checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_mermaid", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def messages(checker: ModuleType, source: str) -> list[str]:
    text = f"Intro\n\n```mermaid\n{source.strip()}\n```\n"
    return [
        finding.message
        for block in checker.mermaid_blocks(text)
        for finding in checker.check_block(block)
    ]


FLOWCHART = f"""
{FM}
flowchart TB
  classDef default fill:none,stroke:#868e96,stroke-width:1px,color:#868e96
  classDef blue fill:none,stroke:#2f81f7,stroke-width:2px,color:#2f81f7
  linkStyle default stroke:#868e96
  subgraph core[Core]
    A[Reader]:::blue -->|reads| B[(Store)]
  end
  style core fill:none,stroke:#868e96,stroke-dasharray:4 3,color:#868e96
"""

CLASSES = f"""
{FM}
classDiagram
  class Forecaster {{
    <<Protocol>>
    +forecast(frame, spec) QuantileForecast
  }}
  NaiveForecaster ..|> Forecaster
  style Forecaster fill:none,stroke:#a371f7,stroke-width:2px,color:#a371f7
  style NaiveForecaster fill:none,stroke:#868e96,color:#868e96
"""

SEQUENCE = f"""
{FM}
sequenceDiagram
  box transparent Service
    participant A as API
  end
  A->>A: validate
"""

STATES = f"""
{FM}
stateDiagram-v2
  classDef base fill:none,stroke:#868e96,color:#868e96
  [*] --> Uploaded
  Uploaded --> Mapped
  class Uploaded, Mapped base
"""

GANTT = """
gantt
  dateFormat YYYY-MM-DD
  M1 :2026-10-12, 14d
"""


@pytest.mark.parametrize("source", [FLOWCHART, CLASSES, SEQUENCE, STATES, GANTT])
def test_compliant_diagrams_pass(checker: ModuleType, source: str) -> None:
    assert messages(checker, source) == []


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (FLOWCHART.replace(FM, ""), "standard frontmatter"),
        (FLOWCHART.replace("marker circle {", "marker rect {", 1), "standard frontmatter"),
        (FLOWCHART.replace("classDef default", "classDef other"), "classDef default"),
        (FLOWCHART.replace("  style core", "  %% style core"), "subgraph core"),
        (FLOWCHART.replace("#2f81f7", "#ff0000"), "not in the palette"),
        (FLOWCHART.replace("fill:none,stroke:#2f81f7", "fill:#2f81f7"), "fill:#2f81f7"),
        (FLOWCHART.replace(",color:#2f81f7", ""), "color equal to stroke"),
        (FLOWCHART.replace("color:#2f81f7", "color:#868e96"), "color equal to stroke"),
        (FLOWCHART.replace("stroke:#868e96\n", "stroke:#868e96,color:#868e96\n"), "linkStyle"),
        (FLOWCHART.replace("flowchart TB", "graph TB"), "use flowchart"),
        (CLASSES.replace("  style NaiveForecaster", "  %% x"), "class NaiveForecaster"),
        (CLASSES + '  note for Forecaster "x"', "no notes"),
        (SEQUENCE.replace("box transparent", "box Aqua"), "box transparent"),
        (STATES.replace("Uploaded, Mapped", "Uploaded"), "state Mapped"),
        ("%%{init: {'theme': 'dark'}}%%\n" + FLOWCHART, "init"),
    ],
)
def test_violations_are_reported(checker: ModuleType, source: str, expected: str) -> None:
    assert any(expected in message for message in messages(checker, source))


def test_hook_mode_exits_with_two_so_agents_see_findings(
    checker: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = tmp_path / "doc.md"
    doc.write_text("```mermaid\ngraph TB\n  A --> B\n```\n", encoding="utf-8")
    monkeypatch.setattr(checker, "changed_markdown_files", lambda: [doc])

    assert checker.main(["--hook"]) == 2
    assert checker.main([str(doc)]) == 1


def test_hook_never_blocks_the_agent_on_its_own_failure(
    checker: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> list[Path]:
        raise OSError("git is missing")

    monkeypatch.setattr(checker, "changed_markdown_files", broken)
    assert checker.main(["--hook"]) == 0
