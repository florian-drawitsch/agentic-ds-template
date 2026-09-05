# ADR-0013: Carry domain guidance in presets and adopt Superpowers conventions

- **Status:** Accepted
- **Date:** 2026-10-09
- **Amends:** ADR-0003

## Context

ADR-0003 let each archetype preset enable a matching agent-kit plugin. agent-kit dropped its data
science and archetype plugins: they restated knowledge capable models already have. Instead it
curates Superpowers for planning and execution, plus a small `project` plugin (agent-kit ADR-0003).

## Decision

- Archetype presets carry domain guidance in two forms:
  - a short rules section in AGENTS.md, for example "split forecasting data by time only"
  - tested code in the generated package where the steps are deterministic, for example a backtest
    split generator
- All projects enable the same agent-kit plugins: `superpowers` and `project`.
- AGENTS.md has a "Planning and decisions" section:
  - design specs and plans go to `docs/plans/`
  - decisions settled by an approved design become ADRs
  - accepted ADRs outrank code and tests, which outrank plans
- A PR template in the generated project asks for verification evidence.

## Alternatives considered

- Archetype plugins in agent-kit: rejected in agent-kit ADR-0003.

## Consequences

- The `agent_plugins` question is removed; only `agent_kit_ref` remains.

## Verification

Render tests assert the planning section in AGENTS.md, the archetype rules section per preset,
and that both harness configs enable exactly `superpowers` and `project`.
