# Apps workspace and fewer shape questions

Status: Implementation verified; final review, outer gate and publication pending.

## Goal and scope

Implement ADR-0030 and ADR-0031 in the template, its tests and this repository's docs.

- Generated projects no longer ask `use_case` or `maturity`. They get no preset dependencies, a
  generic evaluation protocol, no use-case rules in AGENTS.md, a coverage minimum of 80 % and
  `architecture` defaulting to `simple`.
- Generated projects are a virtual uv workspace. The first app lives in `apps/<package_name>/`
  with its own `pyproject.toml`, `src/`, `tests/`, `conf/`, `Taskfile.yml` and optional
  `Dockerfile`. `packages/` holds shared libraries. Root `src/`, `tests/` and `conf/` disappear.
- A second app can be added by a documented procedure, and a slow test proves it.

Non-goals:

- the `new-app` and `kickoff` skills
- a Node or other non-Python app skeleton
- migrating existing generated projects or the showcase repository
- a marketplace release tag or a `marketplace_ref` bump
- re-running `task eval`

## Prerequisites and decisions

- Accepted: ADR-0030 (drop use-case and maturity questions), ADR-0031 (workspace of apps). Both
  are committed on `feat/apps-workspace` together with this plan.
- Still binding: ADR-0005 (import-linter), ADR-0021 (Hydra compose API), ADR-0022 (file
  ownership, as amended), ADR-0023 and ADR-0025 (architectures), ADR-0024 (Azure), ADR-0026
  (Mermaid style).
- Human checkpoints from `AGENTS.md`: no new runtime dependency of this repository is expected;
  stop and ask if one becomes necessary. Third-party bumps in `catalog.toml` are out of scope.
- Approval: the user approved this plan and its scope on 2026-11-18. Delivery may push the
  branch and open a pull request against `main`. It may not merge, tag or request human reviewers.

## Implementation sequence

Write or update the failing test first in every step, then change the template until it passes.
Run focused tests with `uv run pytest tests/template/<file> -q`.

- [x] **Remove `use_case` and `maturity` (ADR-0030).**
  - `copier.yml`: delete both questions. `architecture` defaults to `simple`; its choice labels
    say when to pick each option.
  - `template/pyproject.toml.jinja`: drop the preset map; runtime dependencies come only from
    `dataframes`, `config`, `tracking` and `interfaces` (plus `polars`). `fail_under = 80`.
  - `template/AGENTS.md.jinja`: delete `use_case_titles` and the rules section. The review rule
    that mentioned the use-case rules now names results that contradict
    `docs/method/evaluation-protocol.md`.
  - `template/docs/method/evaluation-protocol.md.jinja`: one generic stub with design, baselines,
    metrics and acceptance sections and TODO markers. Drop the `.jinja` suffix if no Jinja remains.
  - `template/docs/guides/development.md.jinja`: the coverage minimum of 80 % is unconditional.
  - Tests: replace the per-use-case render test with one that asserts the generic protocol and
    no use-case section; assert neither question exists in `copier.yml`; assert `fail_under` is
    80 with default answers; drop `use_case` and `maturity` from `tests/template/test_e2e.py`.
- [x] **Make the root a virtual uv workspace and move the package into `apps/` (ADR-0031).**
  - Move with `git mv`: `template/src/<arch dirs>` to
    `template/apps/{{ package_name }}/src/<arch dirs>`, `template/tests/` to
    `template/apps/{{ package_name }}/tests/`, `template/conf/` to
    `template/apps/{{ package_name }}/conf/`. Keep the existing conditional directory names.
  - New `template/apps/{{ package_name }}/pyproject.toml.jinja`: `[project]` with the runtime
    dependencies, the console script, `[build-system]` and all `[tool.importlinter]` contracts
    from the root file.
  - Root `template/pyproject.toml.jinja`: no `[project]`; `[tool.uv.workspace] members =
    ["apps/*", "packages/*"]`; dependency groups; ruff, ty, pytest and coverage settings.
    pytest uses `testpaths = ["apps/*/tests", "packages/*/tests"]` and
    `--import-mode=importlib`, so two apps may contain test modules with the same name. Coverage
    measures `apps` and `packages` and omits tests.
  - `task setup` installs every workspace member. Use `uv sync --all-packages --locked` if a plain
    `uv sync` does not install them, and do the same in the Dockerfile, CI and Codex environment.
  - `packages/` ships empty or with a README. Check that `uv lock` accepts it; if a file under
    `packages/` breaks the member glob, ship no placeholder and document the directory instead.
  - Hydra keeps `CONFIG_DIR = Path(__file__).resolve().parents[N] / "conf"`, which now resolves
    to `apps/<package>/conf/`. Data paths in `conf/` stay relative to the repository root,
    because tasks run from there. A unit test asserts the resolved config path exists.
  - Conditional files inside `apps/` use conditional file names rather than `_exclude` patterns
    where Copier's `_exclude` cannot see the rendered `package_name`. Verify with render tests.
  - Tests: render tests assert the workspace members, the app files and their absence at the
    root, for all three architectures and both config options.
- [x] **Split tasks, container and contracts by app.**
  - New `template/apps/{{ package_name }}/Taskfile.yml.jinja` with `run`, `sweep`,
    `pipeline-graph`, `api`, `dashboard` and, with a container, `docker:build` and `docker:run`.
    The root includes it under the name `{{ package_name }}` with `dir: .`, so commands run from
    the repository root. Delete `template/taskfiles/docker.yml.jinja` once its tasks moved.
  - The root Taskfile keeps `setup`, `test`, `test-fast`, `lint`, `check`, `mlflow`,
    `update-template`, `clean` and the `docs`, `deliver` and `infra` includes.
  - Dockerfile moves to `template/apps/{{ package_name }}/Dockerfile.jinja`. Build context is the
    repository root. It installs only its app with `uv sync --frozen --no-dev --package
    {{ package_name }}`. Update `.dockerignore`, `taskfiles/infra.yml`, `infra/` and
    `.github/workflows/deploy.yml` to the new path.
  - The import-linter git hook passes every `apps/*/pyproject.toml` and
    `packages/*/pyproject.toml` to `lint-imports` (positional config files). Keep the ADR-0005
    test that a forbidden import fails `task lint`.
- [x] **Add `scripts/check_workspace.py` to the template.**
  - Stdlib only, under 150 lines. Fails with a clear message when an app's source imports another
    app's package, when an app's `pyproject.toml` depends on another app, or when a directory in
    `apps/` has no `pyproject.toml` and is not in the workspace `exclude` list.
  - It runs as a git hook, so `task lint` and `task check` run it.
  - Tests in this repository render a project, then assert the script passes on it, fails on a
    planted cross-app import and fails on an unregistered directory in `apps/`.
- [x] **Update agent wiring, docs and project-owned paths in the template.**
  - `copier.yml` `_exclude`: on update, exclude `apps` and `packages` instead of `src`, `tests`
    and `conf`. Update container and config conditions to the new paths.
  - `_message_after_copy`, `template/README.md.jinja` and `template/AGENTS.md.jinja`: the layout,
    `task {{ package_name }}:run`, and the rules "apps never import each other" and "shared code
    goes to `packages/`". AGENTS.md stays within its line budget.
  - `template/docs/conf.py.jinja` documents every `apps/*/src` and `packages/*/src`;
    `template/taskfiles/docs.yml.jinja` draws diagrams per app.
  - `template/docs/guides/development.md.jinja`: an "Add an app" procedure (copy the app
    directory, rename the package and its references, add one Taskfile include, run
    `uv lock` and `task check`) and a note that non-Python apps need a workspace `exclude` entry.
  - Grep the template for remaining `src/`, `tests/` and `conf/` paths: `.codex/`, `.claude/`,
    `scripts/agent/`, `.worktreeinclude`, `.github/`, `.gitignore`, `.pre-commit-config.yaml`.
  - `plugins/project/skills/template-update/SKILL.md`: list `apps/` and `packages/` as
    project-owned instead of `src/`, `tests/` and `conf/`.
  - Tests: update `tests/template/test_update.py` (project-owned paths survive an update) and
    `tests/template/test_hooks.py` to the new paths.
- [x] **Prove multi-app support end to end.**
  - `tests/template/test_e2e.py`: run `task {{ package_name }}:run` instead of `task run`.
  - New slow test: render the default answers, follow the "Add an app" procedure to create a
    second app from the first, then run `task setup`, `task check` and the second app's `run`.
- [x] **Update this repository's docs.**
  - `docs/design/architecture.md`: question table without `use_case`, `maturity` and `layout`;
    the rendered project tree with `apps/` and `packages/`; the ownership table; the correct
    module list for `simple`; the post-copy message as rendered.
  - `docs/features.md` and `README.md`: options, layout, coverage gate and evaluation protocol.
  - Check `AGENTS.md` of this repository for stale claims.
- [x] **Final gate and review.** `task check` passes; the configured final review passed or its
  findings are fixed and verified.

## Acceptance and verification

| Criterion | Focused check |
| --- | --- |
| No `use_case` or `maturity` question; no use-case section; generic protocol; `fail_under = 80` | `uv run pytest tests/template/test_render.py -q` |
| Rendered project is a workspace with the app in `apps/<package>/` and no root `src/`, `tests/`, `conf/` | `uv run pytest tests/template/test_render.py -q` |
| Cross-app imports and unregistered app directories fail the gate | `uv run pytest tests/template -q -k workspace` |
| Project-owned `apps/` and `packages/` survive `copier update` | `uv run pytest tests/template/test_update.py -q` |
| Each architecture renders, runs and passes its gate | `task test-e2e -- -k "simple or pipeline or hexagonal" --maxfail=1` (needs network for packages) |
| A second app added by the guide passes `task check` | `task test-e2e -- -k second_app` (needs network for packages) |

Final gate, run by the delivery controller: `task check`.

External checks, not required and not authorized by this plan:

- `task eval` spends model tokens; run it only on the user's request.
- Rendering the showcase project against this branch.

## Rulings

- App distribution names match `package_name`: workspace package selection and copied app
  renaming use one name; the root retains no distribution. If wrong, app dependency metadata
  must be renamed before publication.
- Root tool commands use `uv run --all-packages`; app commands use `uv run --package <app>`:
  every test and contract sees all members even after an app-specific sync. If wrong, setup
  installs more workspace packages than necessary.
- `packages/README.md` preserves the shared-library directory: a two-app `uv lock` accepts
  the placeholder. If wrong, locking fails before setup.
- Directory-wide `git mv` was denied by the sandbox, so individual tracked files were moved
  with `git mv`. Empty old directories are untracked and absent from a clean checkout.
- Focused render checks use a temporary source copy omitting the sandbox-denied, unchanged
  `template/.env.example` and empty untracked directories. This validates the implementation
  without reading denied content. Acceptance of that unchanged file remains unverified.
- Import-linter uses one `--config` invocation per member (ADR-0032): released version 2.16
  rejects positional config files. This preserves all contracts without bypassing the
  supply-chain cooldown; the cost is repeated graph construction.

## Verification evidence

All rendering evidence below uses the filtered source described above. Focused logs and the
validation runner are preserved in the delivery run directory under `focused-checks/`.

- 37 render/workspace tests passed after the tooling fixes. The exact default dependency-set
  assertion also passed after tightening it. Earlier, all 66 render/workspace/hook/agent-wiring
  tests passed before the three additional structural cases were added.
- Copier update preservation passed, including app edits/deletions and a shared package.
- The normal slow `second_app` and `forbidden_import` regression tests both passed, 15 other
  slow cases deselected. The two-app test runs setup, the generated `task check`, the second
  app's run command and the multi-app documentation build.
- Independent runtime checks passed for pipeline/Hydra (17 tests, 94.20 % coverage) and
  hexagonal/Python (9 tests, 93.10 % coverage), including each CLI, import contracts, Ruff, ty
  and documentation build. The two-app simple/Hydra project passed 24 tests at 95.45 % coverage.
- The generated two-app `task lint` passed using existing cached hooks. Its actual contract
  hook rejected a planted forbidden import. Fresh hook downloads returned HTTP 403, but the
  existing hook cache allowed the authorized focused acceptance checks to finish.
- Ruff and ty passed for `tests/template/` and `template/scripts/check_workspace.py`.
  `git diff --check` passed. The template path audit found no old root source/config paths
  in agent wiring. This repository's AGENTS.md now describes its existing render tests.
- The repository-wide `task check` remains reserved for the outer controller and was not run.

## Final gates

- The controller's Opus cross-review could not launch: Claude's subprocess hardening requires an
  explicit `allowedTools` declaration that `cross_review.py` does not pass. Publication was off
  for this run (`--no-publish-pr`) because the controller cannot resolve the `github-private` SSH
  alias of `origin`. Both are plugin defects, tracked as a separate task.
- The final review ran outside the controller, with Claude Opus as a read-only reviewer of the
  whole branch. Verdict: changes requested, one blocking and two major findings. All findings are
  fixed:
  - The import-linter hook skips members without `[tool.importlinter]`, so a shared library
    without contracts no longer fails `task lint`.
  - `task docs:diagrams` passes each package directory to pyreverse instead of `apps/<app>/src`.
  - The template-update evals use `apps/` paths.
  - Agent-facing task names carry the app prefix; the guard recommends
    `uv add --package <app> <package>`; the Dockerfile comment says `packages/` must exist.
  - `scripts/check_workspace.py` also scans app tests and reports a package name used by two
    apps.
  - `tests/test_check_mermaid.py` reads the pipeline graph from the app skeleton.
- Verified with `tests/template/test_workspace.py` (8 passed), the slow `second_app` and
  `forbidden_import` tests (2 passed; the second-app test now adds a shared package without
  contracts and asserts diagrams for both apps) and the repository `task check`.
