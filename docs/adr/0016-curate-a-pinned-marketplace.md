# ADR-0016: Curate a pinned marketplace and adopt Superpowers

- **Status:** Accepted, amended by ADR-0018
- **Date:** 2026-10-09
- **Supersedes:** ADR-0014

## Context

ADR-0014 planned own plugins for delivery, data science and each project archetype. Research in
October 2026 showed two things:

- Widely used, maintained plugins already cover planning and execution. Superpowers has about one
  million installs, is MIT-licensed, and is listed in the official directories of both Claude
  Code and Codex.
- Data science and archetype skills mostly restate knowledge a capable model already has. They
  cost context in every session and need maintenance.

## Decision

- `agent-kit` is a curated marketplace for Claude Code and Codex, generated from one catalog.
- It re-exports reviewed third-party plugins at a pinned ref. Version 0.1 contains Superpowers
  v6.4.2.
- It adds one own plugin, `project`, with skills only where nothing suitable exists:
  - `adr`
  - `kickoff`
  - `template-update`
- Superpowers is adapted through project instructions, not wrapper skills. AGENTS.md sends design
  specs and plans to `docs/plans/`, and records decisions settled by an approved design as ADRs.
  Superpowers documents that AGENTS.md instructions and user location preferences override its
  defaults.
- A skill is added only if its evals beat the same task without the skill.

## Alternatives considered

- Own planning and execution skills: duplicate maintained work.
- Compound Engineering: broader (36 skills) and more opinionated, so higher context cost.
- Spec Kit: heavy per feature. OpenSpec: a spec format, not an execution workflow.
- Data science and archetype plugins: domain rules go into the template's AGENTS.md presets and
  tested code instead.

## Consequences

- Third-party updates are deliberate catalog bumps, each with a changelog note.
- Superpowers behavior changes upstream can affect projects. Pinning and reviewing bumps contain
  this.

## Verification

- `task check` validates the generated marketplaces.
- A smoke test installs the pinned Superpowers in both CLIs.
- A behavior eval asserts that a Superpowers plan lands in `docs/plans/` when the project
  AGENTS.md section is present.
