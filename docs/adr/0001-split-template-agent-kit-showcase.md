# ADR-0001: Split into template, agent kit and showcase repositories

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

Generic agent skills copied into every generated project drift apart and cannot be improved in
one place. A template alone does not prove it produces working projects, and it hides the data
science work that employers want to see.

## Decision

Maintain three repositories:

- `ds-project-template`: Copier template.
- `agent-kit`: plugin marketplace for Claude Code and Codex.
- `energy-forecasting`: a project generated from the template and built with the kit.

The template references the kit by pinned ref. It never vendors the kit's skills.

## Alternatives considered

- Kit inside the template repository: fewer repositories, but skills could only update through
  `copier update`, and the kit would not be usable outside generated projects.
- No showcase: less work, but no evidence that the whole chain works.

## Consequences

- The kit needs its own versioning and release tags.
- Generated projects need access to the private kit repository (SSH or `gh` credentials) until
  it becomes public.

## Verification

Template render tests assert that the rendered marketplace references point to the pinned kit
ref and that no skill files are copied into the project.
