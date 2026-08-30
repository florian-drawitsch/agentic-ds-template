# ADR-0004: Require only git, uv and Task on the host

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

OS-specific install scripts and direnv added setup steps that broke on new machines and in
agent sandboxes.

## Decision

- Host prerequisites: git, uv, Task. Copier runs through `uvx`.
- prek, ruff, ty, commitizen, import-linter and pytest are dev dependency groups run with
  `uv run`.
- Task loads `.env` through its `dotenv` setting.
- `task setup` is idempotent and safe to rerun in worktrees and agent environments.

## Alternatives considered

- mise for tool pinning: capable, but one more tool when uv already covers Python tooling.
- direnv: convenient for shells, irrelevant for agents, and an extra trust step.

## Consequences

- Non-Python tools (for example Docker) stay optional and are checked with Task preconditions,
  not installed.

## Verification

CI runs on a clean runner that has only git, uv and Task installed, then runs `task setup` and
`task check`.
