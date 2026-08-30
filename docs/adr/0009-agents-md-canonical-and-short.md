# ADR-0009: Keep AGENTS.md canonical and short

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

Instruction files grew past 200 lines and mixed project facts, generic advice, linter rules and
personal reply preferences. Long files dilute the rules that matter.

## Decision

- AGENTS.md is the single source for both harnesses.
- CLAUDE.md contains `@AGENTS.md` plus Claude-only notes. Claude Code can now read AGENTS.md
  directly when no CLAUDE.md exists, but that path is recent and gated, so the explicit import
  stays.
- The root file stays under about 100 lines, with these sections: project, commands, layout and
  rules, context routing, definition of done, archetype rules.
- Anything a linter, hook or test enforces is not repeated in prose.
- Nested AGENTS.md files refine the root for one layer. They never repeat it.

## Alternatives considered

- A symlink from CLAUDE.md to AGENTS.md: breaks on Windows and needed a setup script.

## Consequences

- The `agents-md-audit` kit skill keeps files within budget.

## Verification

A render test asserts the line budget and that CLAUDE.md imports AGENTS.md.
