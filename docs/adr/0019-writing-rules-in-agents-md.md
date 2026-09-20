# ADR-0019: Keep writing rules in the project's AGENTS.md

- **Status:** Accepted
- **Date:** 2026-10-09
- **Supersedes:** ADR-0015

## Context

ADR-0015 kept personal writing rules in a user-level profile that an install task linked into
each harness's user configuration. That adds an install step and a file outside the repository
for a few lines of text.

## Decision

- Writing rules live in a short "Writing style" section of AGENTS.md: this repository's own file,
  and the AGENTS.md the template renders.
- They apply to every text an agent produces in the project: replies, docs, docstrings, comments,
  commit messages and reports.

## Alternatives considered

- **A user-level profile:** an extra install step, and the rules are invisible to collaborators.
- **A dotfiles repository:** the same drawbacks, outside the project.

## Consequences

- Collaborators see and share the rules.
- The rules cost a few lines of the AGENTS.md budget (ADR-0009).

## Verification

A template render test asserts that the rendered AGENTS.md contains the writing style section
within its line budget.
