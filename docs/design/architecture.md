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
- Project-local skills (truly project-specific) live in `.agents/skills/`, which Codex reads
  natively. `.claude/skills` is a symlink to it, kept by Copier's `_preserve_symlinks`.

## Marketplace

The marketplace is curated (ADR-0016) and vendor-independent (ADR-0017):

- **`superpowers`:** re-exported at a pinned tag and commit.
- **`project`:** this repository's own plugin.
  - `deliver` executes a plan until it is verifiably done. A completion guard sends the session
    back to work until every plan item is ticked, the check passes and an optional cross-vendor
    review passed (ADR-0020).
  - `cross-review` runs the other vendor's agent as a read-only reviewer.
  - Planned: `adr`, `kickoff`, `template-update`.

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
| `use_case` | choice | forecasting, anomaly_detection, optimization, computer_vision, genai, tabular, analysis | dependency preset, AGENTS.md domain rules, method stub, tested domain helpers |
| `maturity` | choice | poc, production | coverage gate, type strictness, CI depth, default architecture |
| `architecture` | choice | simple (poc default), layered (production default) | package layout and import contracts |
| `layout` | choice | single, workspace | single package or uv workspace with `apps/` and `packages/` |
| `interfaces` | multiselect | none selected; api, dashboard | FastAPI app, Streamlit app, their tasks and tests |
| `config` | choice | hydra, python | Hydra `conf/` or a typed settings module |
| `dataframes` | choice | polars, polars_pandas | dependency set, conversion helpers at boundaries |
| `tracking` | choice | mlflow, none | tracking adapter, tasks, data folder |
| `notebooks` | bool | yes | `notebooks/`, kernel task, nbstripout |
| `container` | bool | no | Dockerfile, compose file, tasks |
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
`_message_after_copy` points to the kickoff skill, which interviews the user and fills the rest.

## File ownership

| Owner | Files | Copier mechanism |
| --- | --- | --- |
| Template | tool configs, Taskfile, CI, hooks, permissions, AGENTS.md rules, nested AGENTS.md, docs scaffolding | re-rendered on update, three-way merged with local edits |
| Project | `docs/context/*`, `docs/adr/0001+`, roadmap, README body, source code beyond the skeleton | `_skip_if_exists` |

The project section of AGENTS.md is rendered once from the context answers. Its template text
never changes, so later updates cannot conflict with edits there.

## Rendered project (layered, single package, all options on)

```
<project>/
├── AGENTS.md                    canonical instructions, under ~100 lines
├── CLAUDE.md                    @AGENTS.md plus Claude-only notes
├── .agents/
│   ├── skills/                  project-local skills (often empty at start)
│   └── plugins/marketplace.json references this repository at the pinned tag (Codex)
├── .claude/
│   ├── settings.json            permissions, hooks, marketplace, enabled plugins
│   ├── agents/                  implementer.md, reviewer.md (read-only)
│   └── skills -> ../.agents/skills
├── .codex/
│   ├── config.toml              sandbox, approval policy, enabled plugins
│   ├── hooks.json               same guard and completion hooks as Claude
│   ├── agents/                  implementer.toml, reviewer.toml (read-only sandbox)
│   └── rules/default.rules      keychain-dependent commands
├── .worktreeinclude
├── .github/workflows/ci.yml     task check, actions pinned by SHA
├── .pre-commit-config.yaml      prek
├── Taskfile.yml                 root tasks; includes taskfiles/*.yml
├── pyproject.toml               deps, ruff, ty, pytest, coverage, import-linter
├── conf/                        Hydra configs (config=hydra)
├── data/{raw,interim,processed,models,reports,tracking}/
├── docs/
│   ├── conf.py, index.md        Sphinx, MyST, autoapi
│   ├── context/                 project.md, data.md, glossary.md (project-owned)
│   ├── method/                  evaluation-protocol.md for the archetype
│   ├── adr/                     index.md, 0000-template.md, 0001-record-decisions.md
│   ├── plans/
│   ├── roadmap.md
│   └── guides/development.md
├── notebooks/
├── scripts/agent/               status.py (session hydration), guard.py (tool guard),
│                                deliver.py (completion guard, installed from the marketplace)
├── src/<package>/
│   ├── domain/        AGENTS.md  pure types and logic, no I/O
│   ├── application/   AGENTS.md  use cases, pipeline steps
│   ├── ports/         AGENTS.md  outbound interfaces
│   ├── adapters/      AGENTS.md  data sources, tracking, models, reporting
│   ├── entrypoints/   AGENTS.md  cli, api, dashboard
│   └── bootstrap/     AGENTS.md  composition root and config loading
└── tests/{unit,integration}/
```

With `architecture=simple`, `src/<package>/` holds flat modules (`data.py`, `features.py`,
`models.py`, `evaluate.py`, `cli.py`) and a single import contract: entrypoints import the rest,
nothing imports entrypoints.

## Testing the template

1. **Fast suite (every push):** render the default answers and one combination per archetype.
   Assert no unrendered Jinja, valid TOML, YAML and JSON, AGENTS.md within its line budget, and
   import contracts that match the chosen layout.
2. **Matrix suite (CI):** pairwise combinations of the shape options (about 15 projects instead
   of several hundred). Each runs `uv sync` and `task check`.
3. **Update suite:** render version N-1, edit project-owned files, update to HEAD, assert no
   conflicts in project-owned files.
4. **Agent wiring:** validate the rendered Claude settings and Codex config with each CLI's
   validator where one exists.
