# ADR-0026: Draw diagrams in one Mermaid house style

- **Status:** Accepted
- **Date:** 2026-10-11

## Context

Agents draw many diagrams: in plans, design specs, ADRs, docs and chat. Left to defaults, each
diagram picks its own type, shapes and colors. Theme defaults also break across renderers: the
VS Code preview drew dark text on a dark editor, and filled shapes clash in dark mode. The first
generated project (a forecasting service) introduced a house style and a checker; this decision
moves both into the plugin and the template.

## Decision

- Diagrams are Mermaid only. The `mermaid` skill of the `project` plugin defines the house
  style: when to draw, which established diagram type fits, shape meanings, a stroke-only
  palette with at least 3:1 contrast on light and dark backgrounds, and the style lines each
  type needs.
- `<skill-dir>/scripts/check_mermaid.py` enforces the mechanical rules. Generated projects carry
  a copy in `scripts/agent/check_mermaid.py` (ADR-0022) that runs after every Claude Code and
  Codex edit and as a git hook.
- The implementer and reviewer roles load the skill for diagrams. The template's AGENTS.md
  states the rule.
- Sphinx renders diagrams with `sphinxcontrib-mermaid` and applies the style's CSS globally,
  because MyST drops the per-diagram frontmatter.

## Alternatives considered

- Style rules in AGENTS.md only: too long for an always-loaded file, and nothing enforces
  them.
- A skill in each generated project (`.claude/skills/`, `.agents/skills/`): two copies per
  project, and projects installed without the template would not get it.
- Rendered images (SVG or PNG) in the repository: not diffable, and they drift from the code.

## Consequences

- Diagrams look the same in GitHub, the VS Code preview and Sphinx, in light and dark themes.
- Every diagram carries a fixed frontmatter block. Generated diagrams, such as
  `task pipeline-graph`, must emit it too.
- Changing the palette or the frontmatter means changing the skill, the checker, the Sphinx
  configuration and the pipeline graph together. Tests compare them.

## Verification

- `tests/test_check_mermaid.py`: checker rules, skill and checker frontmatter agree, the hook
  never blocks on its own failure, template pipeline graphs pass the checker.
- `tests/template/test_render.py`: the vendored checker matches the plugin, the git hook exists
  only with agent harnesses, and Sphinx applies the checker's CSS.
