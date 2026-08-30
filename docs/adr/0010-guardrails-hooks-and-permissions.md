# ADR-0010: Enforce agent guardrails with hooks and one permission list

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

Rules such as "never use pip", "never edit uv.lock", "do not read .env" and "start by checking
repository state" were written as prose and depended on the agent remembering them. Permission
allowlists were maintained separately per harness.

## Decision

- `scripts/agent/guard.py` decides allow or deny for shell commands and file edits. It is wired
  as a PreToolUse hook in `.claude/settings.json` and `.codex/hooks.json`.
- A PostToolUse hook formats edited Python files.
- A SessionStart hook runs `scripts/agent/status.py`, which prints branch, working-tree state,
  open plan and context entry points.
- Allow and deny rules are defined once in template data and rendered into Claude permissions
  and Codex rules and sandbox settings.

## Alternatives considered

- Prose only: unreliable.
- A separate hook script per harness: duplicated logic.

## Consequences

- Hook payload formats differ per harness. The guard script normalizes both inputs.

## Verification

Unit tests feed recorded hook payloads from both harnesses into `guard.py`. Render tests assert
both harness configs reference the same script.
