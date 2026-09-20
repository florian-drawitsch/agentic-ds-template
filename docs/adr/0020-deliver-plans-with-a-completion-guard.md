# ADR-0020: Deliver plans with a completion guard and cross-vendor review

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

Agents stop early: after part of a plan, after an unverified claim of success, or to check in.
The built-in `/goal` features of both CLIs judge completion from the transcript, which an
agent's own claims can satisfy. Implementer and reviewer loops exist in Superpowers
(`subagent-driven-development`), but nothing enforces read-only reviewers or model tiers. A
reviewer from the same vendor shares the author's blind spots.

## Decision

The `project` plugin ships two skills.

**`deliver`** with a completion guard:

- A Stop hook (`scripts/agent/deliver.py hook`) is installed for both harnesses; both support
  `decision: "block"` on Stop.
- The hook keeps a session working until three conditions hold:
  1. every `- [ ]` item of the plan is ticked,
  2. the check command passes,
  3. if requested, a cross-vendor review of the exact worktree state passed.
- A run binds to one session by a random token that the session must show; other sessions in
  the same folder are never affected.
- Run state lives in the worktree's git directory.
- The guard releases the session after a number of rounds without progress, after a round
  limit, on an explicit `block`, and on any internal error.
- Modes: `subagents` (Superpowers subagent-driven development with `implementer` and `reviewer`
  roles) and `single`.
- Roles are rendered for both harnesses. The implementer runs a mid-tier model. The reviewer
  runs the strongest model and is read-only: tools restricted in Claude Code,
  `sandbox_mode = "read-only"` in Codex.

**Unattended runs:**
- `deliver.py headless` creates a worktree, runs `uv sync` outside any sandbox, starts the
  agent non-interactively and resumes the same session until the guard releases it.
- Claude Code runs with `acceptEdits` and its Bash sandbox in auto-allow mode, with writes
  limited to the worktree, the temp directory and uv's cache, and network limited to package
  hosts. Codex runs with `sandbox_mode = "workspace-write"` and approvals off.

**`cross-review`:**
- Runs the other vendor's CLI headless and read-only, with findings that follow a JSON schema.
- Any blocking or major finding forces `changes_requested`.
- Each review records the worktree signature it reviewed, so the guard accepts only a review
  of the current state.

## Alternatives considered

- **`/goal` alone:** the evaluator reads only the transcript and cannot run the check.
- **Anthropic's `ralph-loop` plugin:** Claude-only, with reported loops leaking into other
  sessions in the same folder and slowdowns on every turn.
- **Claude Code dynamic workflows:** no Codex equivalent.
- **Auto permission mode for headless Claude:** the smoke test showed writes denied, and the
  mode is not available for every model or after a resume.

## Consequences

- Each Codex user reviews and trusts the hook once with `/hooks`. Headless runs do not depend on
  that trust, because the outer loop applies the same completion check.
- A cross-vendor review costs a full session on the other vendor's account.

## Verification

- Unit tests: 36 cases in `tests/` covering run binding, check failure, stall and round limits,
  stale and passing reviews, nested-run isolation, installer idempotency and command shapes.
- Smoke tests on 2026-10-09 in throwaway repositories:
  - a headless Claude Code run (Haiku) delivered a two-item plan, and the hook completed the run
  - an earlier run without the sandbox settings released on stall, as designed
  - a headless Codex run delivered the same plan through the outer loop
  - cross-reviews by both CLIs returned schema-valid results
