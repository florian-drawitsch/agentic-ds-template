# ADR-0017: Include only vendor-independent plugins

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

Several popular plugins work in one harness only:
- code-simplifier, claude-md-management, commit-commands and feature-dev run only in Claude Code.
- Plugin hooks run only in the Codex desktop app.

Projects that use both harnesses would behave differently depending on the tool.

## Decision

The catalog includes a plugin only if it works in both Claude Code and Codex. The one exception:
each harness has a full equivalent of its own, and the catalog includes both.

## Alternatives considered

- Include Claude-only extras for convenience: inconsistent behavior between harnesses.

## Consequences

- Built-in commands of each harness, such as review and simplify, cover the excluded plugins.
- `agents-md-audit` is deferred instead of adopting claude-md-management.

## Verification

The catalog records the supported harnesses per plugin. The build fails for a plugin that lacks
a manifest for either harness, unless the catalog names its equivalent.
