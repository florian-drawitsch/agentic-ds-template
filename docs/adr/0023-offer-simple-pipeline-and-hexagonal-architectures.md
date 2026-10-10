# ADR-0023: Offer simple, pipeline and hexagonal architectures

- **Status:** Accepted, amended by ADR-0025
- **Date:** 2026-10-09
- **Supersedes:** ADR-0006

## Context

ADR-0006 offered a flat layout and a layered (hexagonal) one. Most production ML work in past
projects was batch work: features, training and scoring. Hexagonal layers suit services with
several integrations, but give batch ML little structure where it matters: steps, datasets and
their order.

## Decision

The `architecture` question offers three options:

- **`simple`** (default for proofs of concept): flat modules.
- **`pipeline`** (default for production): `features`, `training` and `inference` pipelines.
  - Each pipeline is a list of pure steps with named inputs, outputs and parameters, run by a
    small runner.
  - A data catalog maps dataset names to files configured in `conf/`. Unlisted datasets stay in
    memory for one run.
  - The command line runs all pipelines or a selection (`--pipeline training`).
- **`hexagonal`** (formerly `layered`): domain, ports, adapters, application, bootstrap and
  entry points, for services with several integrations.

## Alternatives considered

- **Kedro, ZenML or Dagster:** a framework dependency and its conventions in every project. The
  runner here is about fifty lines and can be replaced by an orchestrator later.
- **A separate option for feature, training and inference pipelines:** these became the default
  content of the pipeline architecture instead.
- **Clean or onion architecture:** duplicates hexagonal.
- **Vertical slices:** rare in ML work.

## Consequences

- The pipeline layout adds three import contracts:
  - steps never import the catalog, settings or tracking
  - pipelines are independent of each other
  - nothing imports the entry points
- Answer files that used `layered` must be changed to `hexagonal` before an update. No such
  projects exist yet.

## Verification

- Render tests check each architecture's files and contracts.
- Pairwise end-to-end tests run `task run`, `task check` and `task docs:build` for all three
  architectures.
