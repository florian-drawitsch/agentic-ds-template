# ADR-0002: Keep personal preferences in a user-level profile

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

Reply shape and writing style rules are personal. Copied into project instructions, they would
apply to every collaborator and take up root AGENTS.md budget.

## Decision

- `agent-kit/profile/AGENTS.md` holds the personal reply and style rules.
- `task install-profile` links it into the user-level instruction file of each harness.

## Alternatives considered

- Including the rules in the template: wrong scope.

## Consequences

- Rules that every project needs (for example "no agent attribution in commits") stay in the
  template.

## Verification

Review only.
