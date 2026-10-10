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

A Copier template for agentic development of modern, deployment-ready machine learning and AI applications in Python.

Sets up turn-key Python scaffolding with:

- configurable architecture: hexagonal, pipeline-graph or plain
- best-practice and test-driven agentic setup: Claude Code and Codex integration via local plugins, evaluated skills, hooks, and autonomous execution loops
- modern tooling: uv, task, prek, ruff, ty
- infrastructure as code: Pulumi on Azure, CI/CD via GitHub Actions

## Quickstart

1. **Prerequisites:** Install [git](https://git-scm.com), [uv](https://docs.astral.sh/uv/) and [Task](https://taskfile.dev).
2. **Generation:** Create a project from the template:

   ```bash
   uvx copier copy --trust gh:<owner>/agentic-ds-template my-project
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
3. **Execute:** Run the `deliver` skill in the agent session, or deliver unattended in an isolated worktree (defaults to Claude Code with `sonnet`):

   ```bash
   task agent:deliver PLAN=docs/plans/<plan>.md
   ```

   Customize the model, reasoning effort, or harness with arguments:

   ```bash
   task agent:deliver PLAN=docs/plans/<plan>.md MODEL=claude-5-5-haiku EFFORT=high
   ```

4. **Review:** Run the `cross-review` skill to have the multiple agents review changes read-only.
5. **Finish:** Use the `wrap-feature` skill to perform final validation, clean up branches, or prepare a pull request.
6. **Document:** Record lasting architecture decisions in `docs/adr/`.

### Agent skills

Agents access workflow skills from the `project` plugin and `superpowers`:

| Skill | Source | Purpose |
| --- | --- | --- |
| `brainstorm` | `superpowers` (shimmed) | Explore requirements and design options before planning |
| `write-plan` | `superpowers` (shimmed) | Create structured implementation plans in `docs/plans/` |
| `deliver` | `project` | Run an autonomous execution loop until all tasks and `task check` pass |
| `execute-plan` | `superpowers` (shimmed) | Execute plan tasks in batches with review checkpoints |
| `implement-feature` | `superpowers` (shimmed) | Build features with test-driven development (red-green-refactor) |
| `debug` | `superpowers` (shimmed) | Find root causes through hypothesis-driven reproduction |
| `cross-review` | `project` | Run a headless, read-only review with the alternate agent CLI |
| `request-review` | `superpowers` (shimmed) | Review changed files against the plan in the current session |
| `wrap-feature` | `superpowers` (shimmed) | Verify work, clean up branches, or prepare pull requests |

### Agent tasks

Project taskfiles provide commands to manage agent operations:

- `task agent:status`: Displays repository status and active agent delivery runs.
- `task agent:deliver`: Runs an approved plan unattended in a fresh git worktree (`PLAN=...`, optional `MODEL=...`, `EFFORT=low|medium|high`, `HARNESS=claude|codex`).
- `task agent:check`: Verifies that agent hooks and role configurations are present (alias: `doctor`).

### Roles and safety

- Implementer and reviewer roles split coding and verification.
- Reviewer subagents run with read-only permissions.
- Git hooks reject direct `pip install` commands, manual `uv.lock` edits, and access to secrets.
- Architectural import rules keep frameworks at system boundaries.

## Development

```bash
task setup
task check
```

`task setup` installs the locked development environment. `task check` runs the merge gate. Use
`task test-e2e` for the slower generated-project matrix.

## License

[MIT](LICENSE)
