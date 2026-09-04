# ADR-0011: Test the template by rendering a pairwise matrix

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

With about 11 shape options, the full answer space has several hundred combinations. Defects
usually come from interactions between two options.

## Decision

- Generate a pairwise covering set (about 15 combinations) plus one default render per archetype.
- CI renders each combination and runs `uv sync` and `task check` inside it.
- A fast structural suite runs on every push. The update suite (ADR-0002) runs on release
  branches.

## Alternatives considered

- Defaults only: misses interactions.
- The full product: too slow.

## Consequences

- The matrix is regenerated when options change. The generator is seeded for stable output.

## Verification

This ADR describes the verification for the others. CI status is the check.
