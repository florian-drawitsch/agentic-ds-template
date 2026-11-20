# Factory S4: template integration

Status: Approved for implementation on 2026-11-20.

## Goal and scope

Let generated projects opt into the factory with one Copier answer: render its configuration,
tasks and workflow, enable the plugin and test the rendering. After this stage, ADR-0033's
Verification section is complete except for the live run.

Non-goals: the live run in `energy-forecasting` (a separate repository, manual), accepting
ADR-0033 (a human decision), a hosted GitHub App.

## Prerequisites and decisions

- Depends on [Factory S3: holdout checks, reports and board mirror](2026-11-18-factory-s3-holdout-and-reports.md).
- ADR-0002 and ADR-0022 (file ownership, plugin copies by symlink), ADR-0012 (actions pinned
  by SHA), ADR-0033.
- Approval: the user approved this named plan on 2026-11-20. Delivery may publish a pull
  request. It never merges.

## Implementation sequence

Done early, on the branch `feat/factory-init`, so that projects can set up labels and the
board at copy time: the Copier question (with a validator instead of only `when`), the
rendered `factory.toml`, `taskfiles/factory.yml` with `init`, `status`, `tick` and `sync`, the
script copy, plugin enablement, the guide, the wiring tests and the pairwise matrix. `copier
copy` and `task setup` run `factory.py init --skip-unavailable`. Left for this stage: the
workflow, the `loop` and `report` tasks, and the guide's sections on tokens, secrets and
branch protection.

- [x] **Copier question.** Add `factory` (bool, default false) to the Agents group in
  `copier.yml`, with `when` true only for `ci == 'github'` and both harnesses selected. Help
  text names the cost and the GitHub App or token it needs. Exclude the factory files below
  when it is false. Tests in `tests/template/test_render.py`: question hidden without GitHub CI
  or with one harness; no factory files when false.
- [ ] **Rendered files.**
  - `template/factory.toml`: the defaults from S1 with a comment per key; template-owned.
  - `template/taskfiles/factory.yml`: `factory:status`, `factory:tick`, `factory:labels`,
    `factory:loop` (interval argument), `factory:report`.
  - `template/scripts/agent/factory.py`: relative symlink to the plugin script, like
    `deliver.py`.
  - Enable `factory@agentic-ds-template` in `.claude/settings.json.jinja` and
    `.codex/config.toml.jinja` when `factory` is true.

  Tests: files present when true, symlink resolves and matches the plugin
  (`test_vendored_plugin_files_match_the_plugin`), plugin enabled in both harness configs.
- [ ] **Workflow.** `template/.github/workflows/factory.yml`: triggers `schedule` (every
  30 minutes), `workflow_dispatch` and `issues: [labeled]` filtered to `factory:ready`.
  `concurrency: factory` without cancellation. Job permissions `contents: read` only; the
  controller uses the token from secret `FACTORY_GH_TOKEN` (GitHub App or fine-grained token).
  Model keys come from secrets `ANTHROPIC_API_KEY` and `OPENAI_API_KEY`, passed only to the
  step that runs `task factory:tick`. A daily `report daily` job, a weekly
  `report weekly` job on Mondays and a `report retro` step that runs when the count is reached. Every action pinned by commit SHA. `timeout-minutes` and deliver
  `--max-wait` sized so a run finishes inside one job. Tests: YAML parses, every `uses:` has
  a 40-character SHA, no `pull_request_target`, no issue text interpolated into `run:`,
  and the rendered project's zizmor hook passes on it in the e2e run.
- [x] **Agent wiring tests.** In `tests/template/test_agent_wiring.py`: the factory project
  copy resolves locally, `factory.toml` parses with the controller's `load_config`, the
  workflow's tasks exist in the rendered Taskfile, and `.worktreeinclude` still excludes
  credentials.
- [x] **Pairwise matrix.** Add `factory` to the pairwise parameters in
  `tests/template/test_e2e.py` so at least one rendered project runs `task check` with it on.
- [ ] **Docs.**
  - `template/docs/guides/factory.md` (rendered only when true): setup steps (labels, token,
    secrets, branch protection with required human approval, Codex review), the label states,
    the caps, how to write a factory-ready issue, how to stop the factory.
  - Repository docs: `docs/features.md`, `docs/design/architecture.md` (Copier question table
    and rendered project tree), `README.md` quick start mention, root `AGENTS.md` layout line
    for `plugins/*/`.
- [ ] **Gate.** Run `task check` and `task test-e2e -- -k factory`.

## Acceptance and verification

- `uv run pytest tests/template` passes, including the new cases.
- `task test-e2e -- -k factory` renders and checks a factory project.
- Final gate (run by the delivery controller): `task check`.
- External checks, not part of this run, each needing the user's approval:
  - The live run in `energy-forecasting`: label an issue `factory:ready`, watch it reach
    `factory:verified`, record cost and cycle time in ADR-0033.
  - `task eval PLUGIN=factory` if the skill text changed in this stage.

## Rulings

- The factory question requires both harnesses: the verifier must be the other vendor; if
  wrong, a single-harness project would verify with its own vendor, which ADR-0033 rejects.
- The workflow's own token stays read-only: all writes go through `FACTORY_GH_TOKEN` so PRs
  start CI; if wrong, CI never runs on factory PRs.
