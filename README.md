# agentic-ds-template

**Data science projects, scaffolded for coding agents.**

Configurable project scaffolding is combined with a shared operating model for Claude Code, Codex
and human developers. A use case and a preferred stack are chosen once. The generated project
stays maintainable and evolves with the template, while project-owned code and context are never
overwritten.

![License: MIT](https://img.shields.io/badge/license-MIT-blue)
![Python 3.13 | 3.14](https://img.shields.io/badge/python-3.13%20%7C%203.14-blue)
![Copier](https://img.shields.io/badge/copier-%E2%89%A59.5-blue)
![Agents: Claude Code | Codex](https://img.shields.io/badge/agents-Claude%20Code%20%7C%20Codex-blue)

Patterns proven in dozens of data science projects with leading companies in various industries,
including pharma, automotive, banking and retail.

## Why agentic-ds-template

- **One operating model for agents and people.** Claude Code and Codex read the same `AGENTS.md`,
  hooks and permission rules. The conventions a developer follows are followed by both agents.
- **Presets for seven use cases.** Forecasting, anomaly detection, optimization, computer vision,
  GenAI, tabular prediction and analysis each come with dependencies, domain rules for agents and
  an evaluation protocol.
- **Plans are delivered to the end.** A completion guard keeps the agent working until every plan
  item is ticked and `task check` passes. The work is split between implementer and read-only
  reviewer roles.
- **A second vendor reviews the result.** A finished change can be reviewed read-only by the other
  vendor's agent. Findings follow a fixed schema and are bound to the exact state that was
  reviewed.
- **Guardrails as code.** Hooks block pip installs, hand edits of `uv.lock`, `.env` access and
  force pushes. Import contracts keep frameworks such as Hydra, MLflow and FastAPI at the edges of
  the architecture.
- **Template updates without conflicts in project code.** Improvements to tooling, hooks and
  agent rules arrive with `task update-template`. Source code, tests and context documents are
  rendered once and left untouched.
- **A small, current toolchain.** uv, Task, prek, ruff, ty, import-linter, Sphinx and MLflow are
  preconfigured. Only git, uv and Task are required on the host, and new package releases are
  held back for seven days.
- **Tested across combinations.** Every pair of options is rendered and run through the generated
  project's full gate in a pairwise end-to-end suite.

## Quickstart

1. **Prerequisites:** [git](https://git-scm.com), [uv](https://docs.astral.sh/uv/) and
   [Task](https://taskfile.dev) are required.
2. **Generation:** a project is created from the template:

   ```bash
   uvx copier copy --trust gh:<owner>/agentic-ds-template my-project
   ```

   `--trust` is needed for the two post-generation steps, `git init` and `uv lock`.
3. **First run:** the environment is installed, example data is written, and the gate is run:

   ```bash
   cd my-project && task setup && task example-data && task check
   ```

4. **Agents:** once the folder is trusted in Claude Code or Codex, hooks, roles and plugins are
   loaded. A plan is then delivered with the `deliver` skill.

Later template versions are pulled in with `task update-template`.

## Options

| Question | Choices | Default |
| --- | --- | --- |
| Use case | forecasting, anomaly detection, optimization, computer vision, GenAI, tabular, analysis | forecasting |
| Maturity | proof of concept, production (coverage gate, layered default) | proof of concept |
| Architecture | simple flat modules, layered (domain to entry points) | by maturity |
| Interfaces | command line, plus REST API (FastAPI) and dashboard (Streamlit) | command line |
| Configuration | Hydra, native Python (TOML and dataclasses) | Hydra |
| Dataframes | Polars, Polars and pandas | Polars |
| Tracking | MLflow with a local SQLite store, none | MLflow |
| Extras | notebooks, Dockerfile, GitHub Actions with Renovate | notebooks, CI |
| Agents | Claude Code, Codex | both |

Optional free-text answers about the problem, decisions, data, success criteria and constraints
are written into the project's context documents and `AGENTS.md`.

## Plugin marketplace

The repository also serves as a plugin marketplace for Claude Code and Codex. Generated projects
register it at a pinned tag, and it can be added to any other repository.

| Plugin | Source | Purpose |
| --- | --- | --- |
| `superpowers` | [obra/superpowers](https://github.com/obra/superpowers), pinned | Brainstorming, planning, test-driven execution, review and branch finishing |
| `project` | `plugins/project` | `deliver` executes a plan until it is verifiably done, also unattended in a worktree. `cross-review` has the other vendor's agent review a change read-only |

## Documentation

- Design overview: [docs/design/architecture.md](docs/design/architecture.md)
- Decision records: [docs/adr](docs/adr/index.md)
- Plugin setup: [plugins/project/README.md](plugins/project/README.md)

## License

[MIT](LICENSE)
