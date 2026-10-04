# ADR-0006: Offer simple and layered architectures

- **Status:** Superseded by ADR-0023
- **Date:** 2026-10-09

## Context

A ports-and-adapters core paid off in long-lived projects with several data sources and
interfaces. In short analyses it added indirection without benefit.

## Decision

- **`simple`** (default for poc): flat modules for data, features, models, evaluation and CLI.
  One contract: nothing imports entrypoints.
- **`layered`** (default for production): `domain`, `application`, `ports`, `adapters`,
  `entrypoints`, `bootstrap`. Each layer has a short nested AGENTS.md. Config values reach
  application code as immutable objects created in `bootstrap`.
- Moving from simple to layered is a documented manual refactoring, assisted by a kit skill.
  It is not a Copier migration.

## Alternatives considered

- Layered only: overkill for analysis projects.
- Simple only: production projects lose enforceable boundaries.

## Consequences

- Two skeletons to maintain. Both stay small.

## Verification

Render tests run `task check` for both architectures and assert the nested AGENTS.md files
exist only in layered projects.
