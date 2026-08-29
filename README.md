# ds-project-template

A [Copier](https://copier.readthedocs.io) template for data science projects that are built
together with coding agents (Claude Code and Codex).

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
  - guardrail hooks shared by both harnesses
  - one permission list rendered for each harness
  - skills from [agent-kit](../agent-kit), pinned to a version
- **Context and decision docs** that stay project-owned across template updates.

## Status

Design phase. Decisions are recorded in [docs/adr](docs/adr/index.md), and the overall design is
in [docs/design/architecture.md](docs/design/architecture.md).

## License

[MIT](LICENSE)
