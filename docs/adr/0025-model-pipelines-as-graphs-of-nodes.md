# ADR-0025: Model pipelines as graphs of nodes

- **Status:** Accepted
- **Date:** 2026-10-09
- **Amends:** ADR-0023

## Context

ADR-0023 defined pipelines as ordered lists of steps. The order was implicit in the list, and
nothing checked that a step's inputs existed or that two steps did not write the same dataset.
Graph-based pipelines are the established model in data science (Kedro nodes and pipelines,
Hamilton, Dagster assets): dependencies are explicit, and the graph can be validated, filtered
and drawn.

## Decision

- Pipelines are directed acyclic graphs of nodes, using Kedro's vocabulary:
  - `node(func, inputs, outputs, tags)` and `pipeline([...])`
  - `params:<key>` inputs for parameters
  - one `create_pipeline()` per module
  - a `__default__` pipeline that combines all of them
- Edges follow from dataset names. The standard library's `graphlib.TopologicalSorter` derives
  the execution order. Listing order does not matter.
- Pipelines are validated when built: duplicate node names, datasets with two producers and
  cycles are rejected.
- Pipelines combine with `+`, filter by tag, and render as Mermaid
  (`task pipeline-graph`, `--graph`).
- The implementation is about 150 lines of standard library code, with no new dependency.

## Alternatives considered

- **Kedro itself:** 17 dependencies, its own CLI, config and project conventions, which overlap
  with Hydra, Task and the template layout.
- **Apache Hamilton:** builds the graph from function parameter names, which is elegant. But the
  last release was in April 2026 and Python 3.14 support is unconfirmed.
- **Ordered step lists (ADR-0023):** no validation, and the order is easy to break.

## Consequences

- Selected pipelines run as one graph (`--pipeline inference --pipeline features` runs features
  first).
- Projects can move to Kedro later with few changes, because the vocabulary matches.

## Verification

- Generated unit tests cover:
  - ordering by edges
  - multiple outputs
  - cycle and duplicate-producer rejection
  - composition and tag filtering
  - Mermaid output
- Integration tests run full, partial and graph-only CLI invocations.
