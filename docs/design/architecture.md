# Architecture

## Repositories

```text
agentic-ds-template (this repository, ADR-0018)
├── copier.yml, template/        Copier template; only template/ is rendered
├── catalog.toml                 marketplace source: superpowers (pinned), project
├── .claude-plugin/, .agents/plugins/, plugins/project/
│        │ copier copy / update            ▲ marketplace at a pinned tag
│        ▼                                  │
│   generated project ──────────────────────┘
│   ├── .claude/settings.json            registers the marketplace, enables plugins, hooks
│   ├── .agents/plugins/marketplace.json same for Codex
│   └── .codex/config.toml, hooks.json   enables plugins, hooks
└── energy-forecasting (separate repository: a generated showcase project)
```

- The template never copies skills. Generated projects reference this repository's marketplace
  at a pinned tag. Updating skills means tagging a release and bumping the ref: one answer
  change (`copier update --data marketplace_ref=v1.3.0`) or a manual edit.
- AGENTS.md carries a "Planning and decisions" section that adapts Superpowers: specs and plans
  in `docs/plans/`, durable decisions as ADRs (ADR-0013). It also carries the writing style
  section (ADR-0019).
- Project-local skills are added when a project needs one, in `.agents/skills/<name>/` (Codex),
  linked from `.claude/skills/<name>` (Claude Code). `task deliver:doctor` reports missing links.
  The template ships none (ADR-0022, ADR-0027).

## Marketplace

The marketplace is curated (ADR-0016) and vendor-independent (ADR-0017):

- **`superpowers`:** re-exported at a pinned tag and commit.
- **`project`:** this repository's own plugin.
  - `deliver` executes a plan until it is verifiably done. A completion guard sends the session
    back to work until every plan item is ticked, the check passes and an optional cross-vendor
    review passed (ADR-0020).
    Runs end as a GitHub PR for the verified branch, within cost and turn budgets (ADR-0027).
    `fix-pr` repairs Codex review findings on an existing PR in one bounded cycle; the outer
    controller owns every GitHub write (ADR-0028).
  - `cross-review` runs the other vendor's agent as a read-only reviewer.
  - `github-create-pr` validates the PR description against the PR template and creates or
    updates the PR idempotently; `deliver` uses it for publication.
  - `mermaid` defines the diagram house style; its checker runs after every edit and as a git
    hook in generated projects (ADR-0026).
  - `template-update` merges a newer template version by file ownership; `agents-md` keeps
    AGENTS.md accurate (ADR-0029).
  - Planned: `adr`, `kickoff`.
  - Skill evals live in `plugins/project/evals/`; `task eval` scores them against a no-plugin
    baseline (ADR-0029).

## Copier question model

Questions appear in four groups. `when:` hides questions that do not apply.

### 1. Identity

| Key | Type | Default |
| --- | --- | --- |
| `project_name` | str | |
| `package_name` | str | derived, with validator |
| `description` | str | |
| `author_name`, `author_email` | str | personal identity |
| `github_owner` | str | personal account |
| `python_version` | choice | 3.14 (3.13 available) |

### 2. Shape

| Key | Type | Choices (default first) | Effect |
| --- | --- | --- | --- |
| `architecture` | choice | simple, pipeline, hexagonal | package layout and import contracts (ADR-0023) |
| `interfaces` | multiselect | none selected; api, dashboard | FastAPI app, Streamlit app, their tasks and tests |
| `config` | choice | hydra, python | App-local Hydra `conf/` or a typed settings module |
| `dataframes` | choice | polars, polars_pandas | dependency set, conversion helpers at boundaries |
| `tracking` | choice | mlflow, none | tracking adapter, tasks, data folder |
| `notebooks` | bool | yes | `notebooks/`, kernel task, nbstripout |
| `infrastructure` | choice | none, azure | Pulumi project in `infra/`, deploy workflow, deployment guide (ADR-0024) |
| `azure_components` | multiselect | job, storage, key_vault, plus api when chosen; mlflow optional | which Azure resources the stack creates |
| `container` | bool | no (forced yes with Azure) | Dockerfile and tasks |
| `ci` | choice | github, none | workflow running `task check` |

### 3. Agents

| Key | Type | Default |
| --- | --- | --- |
| `agent_harnesses` | multiselect | claude, codex |
| `marketplace_ref` | str | latest tag |

Every project enables `superpowers` and `project` (ADR-0013).

### 4. Optional context

Free text and multi-line. Every question can be skipped. An empty answer renders a TODO stub.

| Key | Prompt |
| --- | --- |
| `context_problem` | What problem does this project solve, and for whom? |
| `context_decisions` | Which decision or process will use the results? |
| `context_data` | Which data sources are expected? |
| `context_success` | How is success measured? |
| `context_constraints` | Constraints: compliance, personal data, compute, deadlines? |

Answers seed `docs/context/project.md` and the project section of AGENTS.md. After generation,
`_message_after_copy` prints the setup, check and app run commands, then asks the user to
trust the project and fill `docs/context/` before the first plan.

## File ownership

| Owner | Files | Copier mechanism |
| --- | --- | --- |
| Template | tool configs, Taskfile, CI, hooks, permissions, AGENTS.md rules, docs scaffolding | re-rendered on update, three-way merged with local edits |
| Project | `README.md`, `apps/`, `packages/`, `notebooks/`, `docs/context/`, `docs/method/`, `docs/roadmap.md`, `docs/adr/0001` | rendered on copy, excluded on update (ADR-0022) |
| Plugin | `scripts/agent/{deliver,cross_review,create_pr,pr_findings,check_mermaid}.py`, `scripts/agent/prices.toml`, `.claude/agents/*`, `.codex/agents/*` | symlinks into `plugins/project/`, copied verbatim (ADR-0022, ADR-0027, ADR-0028) |

Nested app AGENTS.md files belong to the project with the rest of `apps/`.

The project section of AGENTS.md is rendered once from the context answers. Its template text
never changes, so later updates cannot conflict with edits there.

## Rendered project (hexagonal, all options on)

```
<project>/
├── AGENTS.md                    canonical instructions, under ~100 lines
├── CLAUDE.md                    @AGENTS.md plus Claude-only notes
├── .claude/
│   ├── settings.json            permissions, hooks, marketplace, enabled plugins, attribution off
│   └── agents/                  implementer.md, reviewer.md (read-only)
├── .codex/
│   ├── config.toml              sandbox, approval policy, `deliver` profile, marketplace, plugins
│   ├── hooks.json               same session, guard, format and completion hooks as Claude
│   ├── agents/                  implementer.toml, reviewer.toml (read-only sandbox)
│   ├── environments/            worktree setup and app actions: check, test, deliver, resume
│   └── rules/default.rules      read-only gh commands allowed, rule-breaking commands forbidden
├── .worktreeinclude             git-ignored files for new worktrees, never credentials
├── deliver.toml                 extra network hosts and gate isolation for headless delivery
├── .github/workflows/ci.yml     task check, actions pinned by SHA
├── .pre-commit-config.yaml      prek
├── .vscode/extensions.json      Mermaid preview
├── Taskfile.yml                 root tasks; includes taskfiles/*.yml
├── pyproject.toml               virtual workspace, shared tooling and coverage
├── data/01_raw … 09_tracking/   numbered data layers, git-ignored except the folders
├── docs/
│   ├── conf.py, index.md        Sphinx, MyST, autoapi, Mermaid in the house style
│   ├── context/                 project.md, data.md, glossary.md (project-owned)
│   ├── method/                  generic evaluation-protocol.md with TODOs
│   ├── adr/                     index.md, 0000-template.md, 0001-record-decisions.md
│   ├── plans/
│   ├── roadmap.md
│   └── guides/development.md
├── notebooks/
├── scripts/agent/               status.py (session start), guard.py (tool guard),
│                                format_changed.py (format on edit),
│                                deliver.py (completion guard, copied from the plugin),
│                                check_mermaid.py (diagram style, copied from the plugin)
├── apps/<package>/
│   ├── pyproject.toml           runtime dependencies, script and import contracts
│   ├── Taskfile.yml             app tasks, included with dir: .
│   ├── Dockerfile               optional, repository-root build context
│   ├── conf/                    Hydra or TOML configuration
│   ├── src/<package>/
│   │   ├── domain/              pure types and logic
│   │   ├── application/         use cases
│   │   ├── ports/               outbound interfaces
│   │   ├── adapters/            I/O and tracking
│   │   ├── bootstrap/           settings and wiring
│   │   └── entrypoints/         CLI, API, dashboard
│   └── tests/{unit,integration}/
└── packages/                    shared workspace libraries
```

With `architecture=pipeline`, `apps/<package>/src/<package>/` holds `catalog.py`, `settings.py`, `cli.py` and
`pipelines/` (`graph`, `features`, `training`, `inference`): graphs of nodes in the style of
Kedro (ADR-0025). With `architecture=simple`, it holds flat modules (`config.py`, `data.py`, `model.py`, `evaluate.py`,
`pipeline.py`, `tracking.py`, `cli.py`). Nothing imports entry points; frameworks and MLflow
stay at the edges. Apps never import each other. Shared code goes to `packages/`.

## Testing the template

1. **Fast suite (every push):** render the default answers and both config options for each architecture.
   Assert no unrendered Jinja, valid TOML, YAML and JSON, AGENTS.md within its line budget, and
   import contracts that match the chosen layout.
2. **Matrix suite (CI):** pairwise combinations of the shape options (about 15 projects instead
   of several hundred). Each runs `uv sync --all-packages` and `task check`.
3. **Update suite:** render version N-1, edit project-owned files, update to HEAD, assert no
   conflicts in project-owned files.
4. **Agent wiring:** validate the rendered Claude settings and Codex config with each CLI's
   validator where one exists.

The root has no `[project]`. It owns development groups and shared tool settings. App
configuration resolves beside its source; configured data paths resolve from the repository
root. `scripts/check_workspace.py` checks app boundaries and registration in the merge gate.
Adding a second app follows the generated development guide and has a slow end-to-end test.

For `project_name=demo-project` with default options, the post-copy message is:

```text
Created demo-project. Next steps:

  cd <destination>
  task setup          # environment, hooks, notebook kernel, agent plugins
  task check          # lint, architecture contracts and tests

Put the input data into data/01_raw/input.csv, then run the pipeline with `task demo_project:run`.

Then open the project in Claude Code or Codex and trust it, so the hooks and the
<github_owner>/agentic-ds-template marketplace load. Fill docs/context/ before the first plan.
```
