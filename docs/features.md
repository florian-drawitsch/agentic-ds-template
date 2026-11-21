# Feature index

Every feature of the template and its plugin marketplace, grouped by area. Each entry names the
files or Copier answers that turn it on. Decisions behind the features are in
[`docs/adr/`](adr/index.md); the design overview is [`docs/design/architecture.md`](design/architecture.md).

- [Tooling](#tooling)
- [Scaffolding and architecture](#scaffolding-and-architecture)
- [Tasks](#tasks)
- [Documentation](#documentation)
- [Agentic development](#agentic-development)
- [Template repository](#template-repository)

## Tooling

### Host prerequisites

Only git, [uv](https://docs.astral.sh/uv/) and [Task](https://taskfile.dev) are needed on the
host. uv installs Python and every other tool (ADR-0004).

### Python environment

| Feature | Details |
| --- | --- |
| Python | 3.14 (default) or 3.13, set by `python_version` |
| Package manager | uv with a committed `uv.lock`; `task setup` runs `uv sync --all-packages --locked` |
| Build backend | `uv_build` |
| Dependency groups | `dev` (ipykernel, plotly), `test` (pytest, pytest-cov, httpx), `lint`, `docs`; all installed by default |
| Supply-chain cooldown | `exclude-newer = "7 days"`: only releases older than a week resolve (ADR-0012) |
| Local settings | `.env` (git-ignored), loaded into every task; `.env.example` is committed |
| Line endings | `.gitattributes` forces LF and marks `uv.lock` as generated |

### Code quality

| Tool | Role | Configuration |
| --- | --- | --- |
| [Ruff](https://docs.astral.sh/ruff/) | Format and lint | Line length 100; rule sets E, W, F, I, UP, B, SIM, RUF, D, N, PTH, RET, PT, C4, PIE, DTZ, TRY, PERF, NPY; Google docstrings |
| [ty](https://github.com/astral-sh/ty) | Type checking | `[tool.ty]` in `pyproject.toml` |
| [import-linter](https://import-linter.readthedocs.io) | Architecture contracts | `[tool.importlinter]` per app, generated per architecture (ADR-0005) |
| [pytest](https://docs.pytest.org) and pytest-cov | Tests and branch coverage | Strict markers, strict xfail; coverage minimum 80 % for every project |
| [commitizen](https://commitizen-tools.github.io/commitizen/) | Conventional Commit messages | Checked in the `commit-msg` hook |
| [nbstripout](https://github.com/kynan/nbstripout) | Strip notebook outputs | With `notebooks=true` |

### Git hooks

[prek](https://github.com/j178/prek) runs the hooks in `.pre-commit-config.yaml` on commit.
`task lint` runs all of them on tracked and untracked files without staging.

| Hook | Checks |
| --- | --- |
| gitleaks | Secrets, redacted in output |
| File hygiene | Trailing whitespace, end of file, line endings, YAML, TOML, JSON, merge markers, large files, private keys |
| ruff format, ruff check | Formatting and lint, with safe autofixes |
| ty | Types |
| import contracts | `lint-imports --config <member>/pyproject.toml` for every member (ADR-0032) |
| workspace | Reject cross-app imports, dependencies and unregistered app directories |
| uv-lock | `uv.lock` matches `pyproject.toml` |
| nbstripout | Notebook outputs (with notebooks) |
| mermaid-style | Mermaid house style in Markdown (with agents) |
| zizmor | GitHub workflow security (with GitHub CI) |
| commitizen | Commit message format |

### Supply chain and CI

| Feature | Details |
| --- | --- |
| GitHub Actions CI | `.github/workflows/ci.yml` runs `task check` and `task docs:build` on pushes to `main` and on PRs |
| Pinned actions | Every action pinned by commit SHA; minimal `permissions`; no persisted credentials |
| Renovate | `renovate.json`: digest pinning, 7-day minimum release age, weekly lockfile maintenance |
| PR template | `.github/pull_request_template.md`: summary, changes, decisions, verification, risks |
| Container | `apps/<app>/Dockerfile` on `python:<version>-slim` with uv, installs only its app, pipeline as entry point (`container=true`, forced with Azure) |

## Scaffolding and architecture

### Copier questions

Projects are generated with [Copier](https://copier.readthedocs.io). Questions come in four
groups; questions that do not apply are hidden.

| Group | Answer | Choices (default first) | Effect |
| --- | --- | --- | --- |
| Identity | `project_name`, `package_name`, `description`, `author_*`, `github_owner`, `python_version` | | Names, metadata, marketplace and CI links |
| Shape | `architecture` | simple, pipeline, hexagonal | Package layout and import contracts |
| | `interfaces` | none; api, dashboard | FastAPI app, Streamlit app, their tasks and tests |
| | `config` | hydra, python | App-local `conf/config.yaml` or `conf/config.toml` with dataclasses |
| | `dataframes` | polars, polars_pandas | Adds pandas and pyarrow |
| | `tracking` | mlflow, none | MLflow tracking module or a null tracker |
| | `notebooks` | yes, no | `notebooks/`, Jupyter kernel, nbstripout |
| | `infrastructure` | none, azure | Pulumi project in `infra/`, deploy workflow, deployment guide |
| | `azure_components` | job, storage, key_vault (+ api); mlflow optional | Azure resources the stack creates |
| | `container` | no (yes with Azure) | Dockerfile and app `docker:` tasks |
| | `ci` | github, none | CI workflow, Renovate, PR template |
| Agents | `agent_harnesses` | claude, codex | Agent config per harness; none removes agent config and delivery files |
| | `marketplace_repo`, `marketplace_ref` | this repository, `v0.1.0` | Pinned plugin marketplace |
| | `factory` | no, yes; needs `ci=github` and both harnesses | Ticket factory: config, tasks, guide, labels and board |
| Context | `context_problem`, `context_decisions`, `context_data`, `context_success`, `context_constraints` | free text, optional | Seeds `docs/context/` and the project section of AGENTS.md; empty answers leave TODO stubs |

### Architectures

All three ship a working baseline pipeline, tests and import contracts (ADR-0023).

| Architecture | Layout | Import contracts |
| --- | --- | --- |
| `simple` | Flat modules: `config`, `data`, `model` (mean baseline), `evaluate`, `pipeline`, `tracking`, `cli` | Nothing imports entry points; frameworks and MLflow stay at the edges |
| `pipeline` | `pipelines/` with `graph`, `features`, `training`, `inference`; `catalog.py` maps dataset names to files; `settings.py`; `cli.py` | Nothing imports entry points; steps stay pure (no catalog, settings or tracking); pipelines are independent; frameworks and MLflow at the edges |
| `hexagonal` | `domain`, `application`, `ports`, `adapters`, `bootstrap`, `entrypoints`, each with its own AGENTS.md | Layers point inward; frameworks only in entry points; MLflow only in the tracking adapter |

Pipeline graphs (ADR-0025):

- A node wraps a pure function with named inputs and outputs. Edges follow from the names.
- `graphlib` derives the order and rejects cycles; `params:<key>` inputs come from the config.
- Datasets listed in the catalog are read and written as CSV, Parquet or JSON; others stay in
  memory.
- `task <app>:pipeline-graph` prints any pipeline as a Mermaid diagram.

### Workspace and dependencies

Every project is a virtual uv workspace with `apps/*` and `packages/*` members (ADR-0031).
The first app has its own source, tests, configuration, contracts, tasks and optional container.
Apps never import each other. Shared libraries go to `packages/`.

Runtime dependencies start with Polars, plus pandas, Hydra, MLflow, FastAPI and Streamlit when
chosen. No use-case preset is installed (ADR-0030). Every project gets a generic evaluation
protocol with TODOs for design, baselines, metrics and acceptance.

### Data science defaults

| Feature | Details |
| --- | --- |
| Data layers | `data/01_raw` to `data/09_tracking`, numbered in the Kedro style; contents git-ignored |
| Configuration | Hydra through its compose API with command-line overrides and multirun sweeps (ADR-0021), or a TOML file read into dataclasses |
| Experiment tracking | MLflow with a local SQLite store and artifacts in `data/09_tracking/` |
| REST API | FastAPI app with `/health` and `/report`, served by uvicorn |
| Dashboard | Streamlit app (excluded from coverage) |
| Notebooks | `notebooks/` for exploration, a named Jupyter kernel, outputs stripped on commit |
| Tests | `apps/<app>/tests/unit/` for logic, `apps/<app>/tests/integration/` for boundaries, per option |

### Azure infrastructure

`infrastructure=azure` adds a Pulumi project in Python under `infra/` (ADR-0024).

- Always created: resource group, container registry without admin user, managed identity with
  pull rights, Log Analytics workspace, Container Apps environment.
- Optional per stack: scheduled container job (daily at 03:00 UTC by default), API container
  app, Azure Files share mounted as `/app/data`, Key Vault, Azure Machine Learning workspace as
  the MLflow tracking server.
- `infra/tests/` checks the stack with Pulumi mocks, without an Azure account.
- State lives in Azure Blob Storage; secrets use a passphrase provider.
- `.github/workflows/deploy.yml` deploys on demand with OpenID Connect, so no Azure secret is
  stored in GitHub.

### File ownership and template updates

Copier tracks the answers in `.copier-answers.yml`, so projects can pull later template
versions (ADR-0002, ADR-0022).

| Owner | Files | On update |
| --- | --- | --- |
| Template | Tool configs, Taskfile, CI, hooks, permissions, AGENTS.md rules, docs scaffolding | Three-way merged with local edits |
| Project | `README.md`, `apps/`, `packages/`, `notebooks/`, `docs/context/`, `docs/method/`, `docs/roadmap.md`, first ADR | Never touched |
| Plugin | `scripts/agent/` copies of plugin scripts, agent role files | Take the template side |

## Tasks

`task --list` shows every task in a project. Tasks marked with a condition exist only when that
answer is chosen.

### Core

| Task | Does | Condition |
| --- | --- | --- |
| `task setup` | Sync the locked environment, install git hooks, register the notebook kernel | |
| `task check` | Merge gate: every git hook, the tests, the infrastructure tests | |
| `task lint` | Run every git hook on all files without staging | |
| `task test` | pytest with coverage | |
| `task test-fast` | pytest without coverage, for focused checks | |
| `task <app>:run` | Run the pipeline; Hydra overrides after `--` | |
| `task <app>:pipeline-graph` | Print a pipeline as a Mermaid diagram | pipeline |
| `task <app>:sweep` | Run every combination of comma-separated overrides | hydra |
| `task mlflow` | MLflow UI on port 5000 (`PORT=` to change) | mlflow |
| `task <app>:api` | Serve the REST API with auto-reload | api |
| `task <app>:dashboard` | Run the Streamlit dashboard | dashboard |
| `task update-template` | Pull the latest template version with inline conflict markers; refuses a dirty tree | |
| `task clean` | Preview, confirm, then delete untracked files, keeping `data/` and `.env` | |

### Documentation

| Task | Does |
| --- | --- |
| `task docs:serve` | Live-reloading docs at `http://127.0.0.1:8000` |
| `task docs:build` | Build HTML, treating warnings as errors |
| `task docs:diagrams` | Generate package and class diagrams as Mermaid with pyreverse |

### Delivery

Available with at least one agent harness; `agent:` is an alias for `deliver:`.

| Task | Does |
| --- | --- |
| `task deliver` | Run a committed plan unattended in a new worktree and open a GitHub PR |
| `task deliver -- --preview` | Show the menus and resolved settings without starting an agent |
| `task deliver:resume` | Pick a saved run from any worktree and continue it with its session and budgets |
| `task deliver:fix-pr -- <number>` | Fix unresolved Codex review findings on an open PR in one bounded cycle |
| `task deliver:status` | Repository state, runs, token usage and estimated cost (`-- --all-worktrees`) |
| `task deliver:setup` | Prepare dependencies and hooks in a delivery worktree |
| `task deliver:doctor` | Check hooks, roles, plugin copies and project skill links |

### Container and infrastructure

| Task | Does | Condition |
| --- | --- | --- |
| `task <app>:docker:build` | Build the image | container |
| `task <app>:docker:run` | Run the pipeline in the container with `data/` mounted | container |
| `task infra:test` | Test the stack with Pulumi mocks | azure |
| `task infra:bootstrap STATE_ACCOUNT=<name>` | Create the Pulumi state storage, once per subscription | azure |
| `task infra:init` | Create a stack (`STACK=prod`; default `dev`) | azure |
| `task infra:preview` | Show planned changes | azure |
| `task infra:up` | Create or update the resources | azure |
| `task infra:release` | Build the image in the registry and roll it out | azure |
| `task infra:destroy` | Delete every resource of a stack, after a prompt | azure |

## Documentation

### Site

[Sphinx](https://www.sphinx-doc.org) with [MyST](https://myst-parser.readthedocs.io) Markdown,
the Furo theme, sphinx-autoapi for the API reference and sphinxcontrib-mermaid (ADR-0008). CI
builds the site with warnings as errors.

### Structure

| Path | Content | Owner |
| --- | --- | --- |
| `docs/context/project.md` | Problem, decisions supported, success, constraints; seeded from the context answers | Project |
| `docs/context/data.md` | Sources, data contract, time and availability, personal data | Project |
| `docs/context/glossary.md` | Domain terms | Project |
| `docs/method/evaluation-protocol.md` | Design, baselines, metrics and acceptance, with project-owned TODOs | Project |
| `docs/adr/` | Decision records with an index, a template and a first record | Template, first ADR project |
| `docs/plans/` | Design specs and implementation plans, `YYYY-MM-DD-<topic>.md` | Project |
| `docs/roadmap.md` | Roadmap | Project |
| `docs/guides/development.md` | Setup, workflow, agent setup, quality gate, updates | Template |
| `docs/guides/deployment.md` | Azure setup and GitHub deployment | Template (with Azure) |
| `docs/architecture/generated/` | Diagrams from `task docs:diagrams` | Generated |

### Diagrams

- One Mermaid house style for GitHub, VS Code and Sphinx, in light and dark mode (ADR-0026).
- The `mermaid` skill defines when to draw, which type and the stroke-only palette.
- `scripts/agent/check_mermaid.py` enforces it after every agent edit and as a git hook.
- `.vscode/extensions.json` recommends the Mermaid preview extension.

### Decision records

- Accepted ADRs outrank code and tests, which outrank plans.
- An approved design that settles a durable decision gets an ADR before implementation.

## Agentic development

Claude Code and Codex are configured side by side and share one operating model. Every plugin
works in both (ADR-0017). Gemini is an optional third harness for review, triage and the
factory; it implements headless on macOS only (ADR-0034).

### Instructions

| File | Role |
| --- | --- |
| `AGENTS.md` | Canonical instructions, under about 100 lines: project, commands, layout, rules, planning, diagrams, PR review rules, definition of done, writing style (ADR-0009, ADR-0019) |
| `CLAUDE.md` | Imports `AGENTS.md` and adds Claude-only notes |
| Nested `AGENTS.md` | One per hexagonal layer and in `pipelines/`, read before adding code there |

The `## Project` section of AGENTS.md is project-owned; template updates never change it.

### Hooks

The same hooks run in both harnesses (ADR-0010). Hook code never blocks a session because of
its own errors.

| Event | Script | Does |
| --- | --- | --- |
| Session start | `status.py` | Prints branch, uncommitted changes, recent commits, latest plan and its open items, active delivery runs |
| Before a tool call | `guard.py` | Blocks pip installs, `.env` reads and writes, hand edits of `uv.lock`, edits of `.git/`, force pushes and `--no-verify` commits, with the reason and the alternative |
| After an edit | `format_changed.py` | Formats changed Python files with Ruff |
| After an edit | `check_mermaid.py --hook` | Checks the Mermaid house style |
| Stop | `deliver.py hook` | Completion guard for delivery runs |

### Permissions and sandboxing

- One permission list (`includes/permissions.jinja`) renders into Claude Code settings and
  Codex rules.
- Allowed without a prompt: tasks, uv, pytest, ruff, ty, read-only git, read-only `gh`.
- Denied: force pushes, `--no-verify`, pip installs, reading `.env`.
- Codex: `workspace-write` sandbox, approvals on request, medium effort (high when planning), a
  `deliver` permission profile that denies `.env` files.
- Commit and PR attribution lines are off.

### Plugin marketplace

- Projects reference this repository's marketplace at a pinned tag; skills are never copied
  into projects (ADR-0016, ADR-0018).
- Two plugins are enabled: `superpowers` (pinned upstream tag and commit) and `project`.
- Updating skills means bumping `marketplace_ref`.
- Project-specific skills go into `.agents/skills/<name>/`, linked from `.claude/skills/<name>`.

### Skills

| Skill | Source | Purpose |
| --- | --- | --- |
| `brainstorm` | superpowers alias | Explore requirements and design options before planning |
| `write-plan` | superpowers alias, with delivery sections | Test-first plan in `docs/plans/` |
| `deliver` | project | Execute a plan under the completion guard, review, publish a PR; resume runs; repair PR findings |
| `cross-review` | project | Headless, read-only review by the other vendor's CLI with structured findings |
| `github-create-pr` | project | Validate the description against the PR template; create or update the PR idempotently |
| `mermaid` | project | Diagram house style with a checker |
| `template-update` | project | Pull a newer template version, merge conflicts by file ownership, regenerate `uv.lock`, run `task check`, no commit |
| `agents-md` | project | Audit or update AGENTS.md against tasks, code, hooks and accepted ADRs |
| `execute-plan` | superpowers alias | Execute a plan in batches with checkpoints |
| `implement-feature` | superpowers alias | Test-driven development |
| `debug` | superpowers alias | Hypothesis-driven debugging |
| `request-review` | superpowers alias | In-session review against the plan |
| `wrap-feature` | superpowers alias | Verify, clean up, merge or open a PR |

### Agent roles

- `implementer` and `reviewer` in `.claude/agents/` and `.codex/agents/`.
- The reviewer is read-only and runs a frontier model at high effort.

### Plan delivery

`task deliver` turns a committed plan into a verified pull request (ADR-0020, ADR-0027).

| Feature | Details |
| --- | --- |
| Completion guard | A Stop hook sends the agent back to work until every `- [ ]` item is ticked, the final check passed, the review passed and the PR exists |
| Isolation | New worktree in `.worktrees/<topic>` on `feat/<topic>`; files in `.worktreeinclude` are copied, credentials refused |
| Menus | Plan, harness, model, effort, reviewer and review budget (0 to 3 passes); explicit flags for scripts |
| Cross-vendor review | One focused review by the other vendor; required findings fixed with a regression test and verified |
| Rulings | Routine decisions recorded in the plan under `## Rulings`; durable ones as ADRs |
| Budgets | USD 2, 150 turns and 90 minutes per round; USD 10 per run; USD 2 per review |
| Usage limits | Waits for the announced reset or backs off, up to 48 hours, then resumes the same session |
| Cost ledger | Tokens, estimated USD, wall time and waits per run in `usage.jsonl` |
| Headless sandbox | Provider tokens scrubbed; network limited to package registries plus `deliver.toml` hosts; git hook and config hashes checked every round |
| Publication | The outer controller runs the final gate, scans commits with gitleaks, pushes without force, creates or updates the PR; never merges, never requests human reviewers |
| Run states | active, reviewing, verifying, publishing, completed, blocked, stalled, exhausted, stopped; state lives in the git directory, not in history |
| Resume | `task deliver:resume` from any worktree; switching vendors keeps the run and budgets |
| PR repair | `task deliver:fix-pr` fixes unresolved Codex review threads, replies per finding, resolves fixed threads, requests one re-review; USD 5 and 15 rounds by default (ADR-0028) |
| Doctor | `task deliver:doctor` checks hooks, roles, plugin copies, skill links and `.worktreeinclude` |

### Ticket factory

The `factory` plugin runs an opt-in ticket factory from GitHub issue labels (ADR-0033, Proposed).
The deterministic controller, `plugins/factory/skills/factory/scripts/factory.py`, ships first.
The controller calls no model; the agent stages (triage, implement, repair, verify) run through the
harness CLIs.

| Feature | Details |
| --- | --- |
| Configuration | `factory.toml`: limits, repair cycles, daily and weekly caps in USD, protected paths, trusted bots; unknown keys and wrong types are errors |
| Label states | Seven `factory:*` labels and the transition table of ADR-0033; `factory.py labels` creates them idempotently |
| Board | `factory.py init` creates and links a Kanban board with one Status column per state and the views Board, Backlog and Needs attention, found by name under the repository's owner so forks get their own; `sync` mirrors labels into it |
| Priority | `priority:high`, `priority:medium`, `priority:low` labels, created by `init`, order the queue within a state |
| Generated projects | Copier question `factory` (needs GitHub CI and both harnesses); renders `factory.toml`, `taskfiles/factory.yml` (`init`, `status`, `tick`, `labels`, `loop`, `report`, `sync`), the script copy, `.github/workflows/factory.yml` (30-minute tick, `factory:ready` label trigger, daily and weekly reports, retro; SHA-pinned actions, read-only workflow token, writes through `FACTORY_GH_TOKEN`) and `docs/guides/factory.md`; copy and `task setup` run `init` and skip it with a note while GitHub is not ready |
| This repository | `task factory:init`, `factory:tick`, `factory:loop`, `factory:report`, `factory:sync`, `factory:secrets` and `.github/workflows/factory.yml`, the same workflow as generated projects |
| Credentials | Local runs use the existing `gh`, `claude` and `codex` logins. GitHub Actions uses `FACTORY_GH_TOKEN`, `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY`, and `OPENAI_API_KEY`; `task factory:secrets` sets them interactively. Each agent CLI receives only its own vendor login, never a GitHub token |
| Admission | `factory:ready` counts only when a user with write access added it last; unadmitted issues are skipped and listed by `status` |
| Agent stages | `tick` runs triage (read-only reviewer harness, JSON verdict and acceptance criteria), implement (`deliver headless --publish-pr`), repair (`deliver fix-pr`, bounded by `max_repair_cycles`) and verify (`cross_review.py --acceptance` by the other vendor); issue text reaches agents only as a fenced data file and never as checkbox syntax |
| Review gates | Protected paths, failing checks, a missing Codex review and a rejected verification set `factory:needs-human` |
| Evals | `task eval PLUGIN=factory` scores triage and the negative case against a no-plugin baseline |
| Dispatch | `tick --dry-run` prints one action in the order repair, verify, implement, triage; `priority:` labels, then issue number; limits on work in progress and waiting verified PRs |
| Claim | The first non-forced push of `feat/<issue>-<slug>` wins |
| Protected paths | `touches_protected` checks a diff, renames included |
| Spend ledger | Cost markers in ticket comments; the daily or weekly cap turns the tick into idle |
| Holdout checks | `[holdout]` in `factory.toml` (local path or `git` URL with a ref, command, timeout): run before the verifier with a scrubbed environment; only check names and pass or fail are kept; a local path is denied to the worker with `deliver.py headless --deny-path`; a failure sets `factory:needs-human` |
| Reports | `report daily` and `report weekly` edit one marked comment per day or week on the "Factory status" issue (`factory:tracking`): states, blocked causes, spend, throughput, cycle time, cost per ticket |
| Retrospective | `report retro` after `retro_every` merged PRs: evidence file, `agents-md` run through `deliver.py headless`, PR labeled `factory:needs-human`, marker on the tracking issue |
| Board mirror and intake | Each label change mirrors that issue into the board; an admitted move into Ready sets `factory:ready` (other boards, automated and outsider moves are ignored); `tick --scheduled` runs the full sync |

### Codex app

`.codex/environments/environment.toml` sets up new worktrees with `task deliver:setup` and adds
one-click actions: Check, Lint, Test, Serve docs, Deliver, Resume delivery, Delivery status.

## Template repository

Features for maintaining this repository rather than generated projects.

| Task | Does |
| --- | --- |
| `task setup` | Install the locked development environment |
| `task build` | Generate both marketplace files and plugin manifests from `catalog.toml` |
| `task check` | Build check, Ruff, ty, pytest and manifest validation |
| `task test-e2e` | Render a pairwise set of projects and run each one's `task check` (slow, ADR-0011) |
| `task validate` | Validate manifests with `claude plugin validate` |
| `task eval` | Score `project` skills against a no-plugin baseline with `claude plugin eval` (spends tokens) |
| `task dev` | Register this working copy as a local marketplace in both CLIs |
| `task format` | Format and autofix |

Tests cover rendering (no Jinja leftovers, valid configs, AGENTS.md budget, every use case and
option), agent wiring, hook behavior, template updates and every plugin script.
