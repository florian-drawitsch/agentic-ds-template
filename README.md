# agentic-ds-template

![Claude Code](https://img.shields.io/badge/Claude_Code-000000?style=flat-square&logo=anthropic&logoColor=white)
![Codex](https://img.shields.io/badge/Codex-412991?style=flat-square&logo=openai&logoColor=white)
![uv](https://img.shields.io/badge/uv-262626?style=flat-square&logo=python&logoColor=white)
![Task](https://img.shields.io/badge/Task-29BEB0?style=flat-square)
![ty](https://img.shields.io/badge/ty-333333?style=flat-square)
![prek](https://img.shields.io/badge/prek-333333?style=flat-square)
![Pulumi](https://img.shields.io/badge/Pulumi-8A3391?style=flat-square&logo=pulumi&logoColor=white)
![Ruff](https://img.shields.io/badge/Ruff-E5A00D?style=flat-square)
![MLflow](https://img.shields.io/badge/MLflow-0194E2?style=flat-square&logo=mlflow&logoColor=white)
![Polars](https://img.shields.io/badge/Polars-0033A0?style=flat-square)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white)
![Streamlit](https://img.shields.io/badge/Streamlit-FF4B4B?style=flat-square&logo=streamlit&logoColor=white)

A Copier template for agentic development of modern, production-ready machine learning and AI applications in Python.

Sets up turn-key Python scaffolding with:

- configurable architecture: hexagonal, pipeline-graph or plain
- best-practice and test-driven agentic setup: Claude Code and Codex integration via local plugins, evaluated skills, hooks, and autonomous execution loops
- modern tooling: uv, task, prek, ruff, ty
- infrastructure as code: Pulumi on Azure, CI/CD via GitHub Actions

## Quickstart

1. **Prerequisites:** Install [git](https://git-scm.com), [uv](https://docs.astral.sh/uv/) and [Task](https://taskfile.dev).
2. **Generation:** Create a project from the template:

   ```bash
   uvx copier copy --trust gh:florian-drawitsch/agentic-ds-template <my-project>
   ```

   `--trust` allows the post-generation tasks (`git init` and `uv lock`) to run.
3. **Setup and verification:** Install dependencies and run the quality gate:

   ```bash
   cd my-project && task setup && task check
   ```

4. **Agents:** Open the project in Claude Code or Codex and trust the workspace. Hooks, roles and plugins load automatically. Deliver plans with the `deliver` skill.

Pull later template versions with `task update-template`.

## Agentic development

Claude Code and Codex share an operating model defined in `AGENTS.md`.

### Workflow

1. **Brainstorm:** Use the `brainstorm` skill to examine requirements and evaluate design tradeoffs.
2. **Plan:** Use the `write-plan` skill to generate a test-first implementation plan in `docs/plans/<plan>.md`.
3. **Execute:** Run the `deliver` skill in the agent session, or deliver unattended in an isolated worktree:

   ```bash
   task deliver
   ```

   Terminal menus select the plan, the implementation harness, model and effort, the reviewer and the review budget. `task deliver -- --preview` shows the menus without starting an agent. Explicit flags work too, for example `task deliver -- --plan docs/plans/<plan>.md --harness claude --model sonnet`.

4. **Review:** One focused review by the other vendor's agent, read-only. The implementing agent fixes required findings and verifies them without another review.
5. **Publish:** After `task check` passes, the run pushes a `feat/<topic>` branch and opens or updates a GitHub pull request. It never merges and never requests human reviewers.
6. **Document:** Record lasting architecture decisions in `docs/adr/`.

### Agent skills

Agents access workflow skills from the `project` plugin and `superpowers`:

| Skill | Source | Purpose |
| --- | --- | --- |
| `brainstorm` | `superpowers` (shimmed) | Explore requirements and design options before planning |
| `write-plan` | `superpowers` (shimmed) | Create structured implementation plans in `docs/plans/` |
| `deliver` | `project` | Run an autonomous execution loop until all tasks and `task check` pass, then open a PR |
| `execute-plan` | `superpowers` (shimmed) | Execute plan tasks in batches with review checkpoints |
| `implement-feature` | `superpowers` (shimmed) | Build features with test-driven development (red-green-refactor) |
| `debug` | `superpowers` (shimmed) | Find root causes through hypothesis-driven reproduction |
| `cross-review` | `project` | Run a headless, read-only review with the alternate agent CLI |
| `github-create-pr` | `project` | Validate a PR description against the PR template and create or update the PR |
| `mermaid` | `project` | Draw diagrams in one Mermaid house style, checked after every edit |
| `request-review` | `superpowers` (shimmed) | Review changed files against the plan in the current session |
| `wrap-feature` | `superpowers` (shimmed) | Verify work, clean up branches, or prepare pull requests |

### Agent tasks

Project taskfiles provide commands to manage agent operations:

- `task deliver`: Runs a committed plan unattended in a fresh git worktree and opens a pull request. Caps cost per round (USD 2) and per run (USD 10); waits out usage limits for up to 48 hours.
- `task deliver:resume`: Continues a saved run from any worktree, keeping its session and budgets.
- `task deliver:status`: Displays repository status, delivery runs, token usage and estimated cost (`-- --all-worktrees` for every worktree).
- `task deliver:doctor`: Verifies hooks, roles, project script copies and project skill links.

The Codex app gets the same tasks as actions in `.codex/environments/`. Project-specific skills live in `.agents/skills/<name>/`, with a symlink in `.claude/skills/<name>` for Claude Code.

## License

[MIT](LICENSE)
