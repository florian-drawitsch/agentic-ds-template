# ADR-0031: Organize generated projects as a uv workspace of apps

- **Status:** Accepted, amended by ADR-0032
- **Date:** 2026-11-18
- **Amends:** ADR-0003, ADR-0005, ADR-0011, ADR-0022

## Context

Generated projects hold one package in `src/<package>/`. Real projects grow several deployable
units: a forecasting job, an agent backend, a frontend. A sibling project moved to `apps/` and
`packages/` in a uv workspace after outgrowing `src/`. Retrofitting that layout touches every
path in the tooling, the Dockerfile, CI and the docs.

ADR-0003 and the design overview list a `layout` question (single or workspace) that was never
implemented.

## Decision

- **Always a workspace.** The root `pyproject.toml` is a virtual uv workspace root with
  `members = ["apps/*", "packages/*"]`, shared tool settings (ruff, ty, pytest, coverage) and the
  development dependency groups. It has no runtime dependencies. There is no `layout` question.
- **One app at generation time.** `package_name` names the first app. It lives in
  `apps/<package_name>/`:
  - `pyproject.toml`: runtime dependencies, console script and import-linter contracts
  - `src/<package_name>/`: the code for the chosen `architecture`, with its nested AGENTS.md files
  - `tests/`, `conf/`, `Taskfile.yml`, and `Dockerfile` when a container is enabled
- **`packages/`** holds shared libraries as workspace members. It starts empty.
- **Apps never import each other.** Shared code moves to `packages/`. A stdlib script,
  `scripts/check_workspace.py`, runs in the merge gate and fails when an app imports or declares
  another app, or when a directory in `apps/` is neither a workspace member nor excluded.
- **Contracts per app.** Each app's import-linter contracts live in its own `pyproject.toml`. The
  git hook passes every `apps/*/pyproject.toml` and `packages/*/pyproject.toml` to `lint-imports`,
  so adding an app never edits a root contract.
- **Tasks route to apps.** The root Taskfile keeps cross-cutting tasks: `setup`, `lint`, `test`,
  `check`, `docs`, `deliver`, `mlflow`, `update-template`, `clean`, `infra`. App tasks (`run`,
  `sweep`, `pipeline-graph`, `api`, `dashboard`, `docker`) live in the app's Taskfile. The root
  includes it under the app's name, for example `task energy_forecasting:run`. The root `test`
  runs every app's tests in one pytest run.
- **Shared at the root:** `data/`, `notebooks/`, `docs/` (one Sphinx tree that documents every
  app and package), `infra/`, CI and agent configuration.
- **Ownership.** `apps/` and `packages/` are project-owned and excluded on update. They replace
  `src/`, `tests/` and `conf/` in the ADR-0022 list.
- **Adding an app** is a documented procedure in `docs/guides/development.md`: copy the app
  skeleton, rename the package, add one Taskfile include. A `new-app` skill may automate it later,
  with evals. Non-Python apps add a workspace `exclude` entry.

## Alternatives considered

- **Keep `src/`:** every multi-app project rebuilds the layout by hand.
- **A `layout` question:** two layouts to render, test and document for one extra directory level.
- **A second Copier template for apps:** a Copier template needs its own `copier.yml` at the root
  of its source, so it would need a second repository and a second update path.
- **A registry file listing every app and allowed path:** the sibling project's registry grew to
  20 KB. Conventions plus a small check do the same job.
- **Separate environments per app:** several lockfiles and duplicated tool configuration.

## Consequences

- Paths get one level deeper: `apps/<package>/src/<package>/`.
- Template improvements to the app skeleton reach only new projects, as with `src/` before.
- A Taskfile include for a second app is a local edit to a template-owned file; the three-way
  merge on update keeps it.
- All apps share one environment and one lockfile, so their dependency versions must agree.
  Container images install only their app with `uv sync --package`.
- The render tests, the end-to-end matrix and the template-update skill and evals use the new
  paths.

## Verification

- Render tests assert the workspace members, the app files, the excluded paths and that no root
  `src/`, `tests/` or `conf/` remains.
- A test proves `scripts/check_workspace.py` rejects a cross-app import.
- A slow test adds a second app by the documented procedure and runs `task check`.
