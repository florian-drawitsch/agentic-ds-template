---
max_turns: 10
timeout_seconds: 240
allowed_tools: [Read, Glob, Grep, Skill]
runs: 3
---

Is our AGENTS.md still accurate? The project was generated from the agentic-ds-template Copier
template. Do not edit anything; just tell me what is wrong. Also: where should I add our new rule
"All SQL lives in `sql/`"?

Facts you can rely on:

- `task --list` prints: `check`, `docs:serve`, `lint`, `run`, `setup`, `test`, `test-fast`.
- `docs/adr/0004-use-polars.md` (status Accepted) says: "Use polars for all data frames."
- `.pre-commit-config.yaml` runs `ruff-format` on every commit, and a PostToolUse hook formats
  every Python file an agent edits.

Current AGENTS.md (excerpt):

```markdown
## Project

<!-- Project-owned: refine freely. Template updates never change this section. -->
Demand forecasts for 400 stores.

## Commands

- `task check`: lint, types, import contracts and tests. This is the merge gate.
- `task mlflow`: MLflow UI for runs tracked in `data/09_tracking/`.

## Rules

- Use pandas for all data frames.
- Run `ruff format` on every file before you commit.
- Notebooks are for exploration. Move reusable logic into the package, with tests.
```
