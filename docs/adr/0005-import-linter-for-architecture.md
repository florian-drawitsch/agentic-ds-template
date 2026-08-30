# ADR-0005: Enforce architecture with import-linter

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

Layer rules written only in prose erode, especially under agent-generated changes. tach did this
job before, but upstream is no longer maintained and depends on community forks.

## Decision

- Use import-linter. Contracts live in `pyproject.toml` and are rendered per architecture:
  a `layers` contract for the layered core, and `forbidden` contracts for single-importer rules.
- An example single-importer rule: only `adapters.tracking` may import `mlflow`.
- The check runs in prek and in `task check`.

## Alternatives considered

- tach: faster and draws graphs, but unmaintained.
- Architecture tests with AST parsing: flexible, but custom code to maintain.

## Consequences

- Dependency graph rendering is a separate optional task (import-linter does not draw graphs).

## Verification

A render test introduces a forbidden import into a rendered project and asserts that
`task lint` fails.
