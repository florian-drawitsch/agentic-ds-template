# ADR-0012: Harden the supply chain by default

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

Compromised releases of popular packages and GitHub Actions are a recurring attack path, and
coding agents add dependencies quickly.

## Decision

- uv `exclude-newer` with a rolling cooldown of 7 days. Exceptions are per package, with a comment.
- GitHub Actions pinned by commit SHA, workflows linted with zizmor, and minimal `permissions`.
- Dependency update PRs through Renovate with a minimum release age.
- Secrets never in the repository. `.env` is git-ignored, and the guard hook blocks agent reads.

## Alternatives considered

- No cooldown: fastest updates, highest exposure.

## Consequences

- Fresh releases need an explicit per-package exception.

## Verification

prek runs zizmor on workflow changes. A render test asserts `exclude-newer` is set.
