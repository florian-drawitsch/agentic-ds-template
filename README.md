# agentic-ds-template

A [Copier](https://copier.readthedocs.io) template for data science projects built together with
coding agents, and the plugin marketplace those projects use. Claude Code and Codex are both
supported.

Patterns proven in dozens of data science projects with leading companies in various industries,
including pharma, automotive, banking and retail.

## What it generates

- **A project tailored to the use case.** Pick forecasting, anomaly detection, optimization,
  computer vision, GenAI, tabular prediction or analysis. Each preset sets dependencies, domain
  rules for agents and an evaluation-protocol stub.
- **Options for the shape of the project:**
  - architecture: simple or layered
  - layout: single package or uv workspace
  - interfaces: CLI always; API and dashboard optional
  - config: Hydra or native Python
  - dataframes: Polars or Polars plus pandas
  - tracking: MLflow or none
  - container and CI
- **Tooling** with uv, Task, prek, ruff, ty, import-linter and Sphinx. The host needs only git,
  uv and Task.
- **Agent setup:**
  - a canonical `AGENTS.md`
  - guardrail and completion hooks for both harnesses
  - read-only reviewer and implementer roles
  - this repository's marketplace, pinned to a release tag
- **Context and decision docs** that stay project-owned across template updates.

## Plugin marketplace

This repository is also a plugin marketplace for Claude Code and Codex. Generated projects
register it at a pinned tag, and any other repository can add it as well.

| Plugin | Source | Purpose |
| --- | --- | --- |
| `superpowers` | [obra/superpowers](https://github.com/obra/superpowers), pinned | Brainstorming, planning, test-driven execution, review, finishing branches |
| `project` | `plugins/project` | `deliver`: execute a plan until it is verifiably done, with a completion guard, implementer and reviewer roles and unattended runs. `cross-review`: read-only review by the other vendor's agent |

## Status

Design and marketplace in place, template rendering in progress. Decisions are recorded in
[docs/adr](docs/adr/index.md), and the overall design is in
[docs/design/architecture.md](docs/design/architecture.md).

## License

[MIT](LICENSE)
