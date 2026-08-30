# ADR-0007: Default to Hydra, MLflow and Polars, each behind a boundary

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

Hydra, MLflow and Polars are the most common choices in recent projects. Each one tends to leak
into domain code when nothing stops it.

## Decision

- **Config:** `hydra` (default) composes configs in `conf/` at the entrypoint and converts them
  to typed, immutable objects. `python` uses a typed settings module instead. Application code
  sees the same typed objects either way.
- **Tracking:** `mlflow` (default) uses a local SQLite store in `data/tracking`, behind a
  tracking port. Tasks start the UI and archive the store. `none` renders a no-op adapter.
- **Dataframes:** `polars` (default). `polars_pandas` adds pandas and converts only at adapter
  boundaries.

## Alternatives considered

- Hydra in every function: convenient, but couples tests to the Hydra context.
- A remote MLflow server by default: too heavy for a proof of concept. A URI override covers it.

## Consequences

- Swapping tracking or config later touches adapters and bootstrap only.

## Verification

import-linter forbids `hydra` outside entrypoints and bootstrap, and `mlflow` outside the
tracking adapter. Render tests cover each option.
