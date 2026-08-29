# ADR-0002: Use Copier with template-owned and project-owned files

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

Projects must keep receiving template improvements. Older generations of the source template
showed that projects stop updating once updates conflict with local edits.

## Decision

- Use Copier. Template-owned files are re-rendered and three-way merged on `copier update`.
- Project-owned files are listed in `_skip_if_exists`: context documents, ADRs from 0001,
  roadmap, README body.
- Template text inside AGENTS.md's project section never changes after release, so local edits
  there never conflict.
- Breaking renames ship with `_migrations` entries.
- `_tasks` stay minimal: `git init`, `uv lock`. Full setup is opt-in.

## Alternatives considered

- Cookiecutter: no update path.
- Cruft on top of Cookiecutter: weaker update and migration support than Copier.

## Consequences

- Each template release needs a changelog entry that states migrations.
- Every new file must be classified as template-owned or project-owned.

## Verification

The update suite renders the previous release, edits project-owned files, updates to HEAD and
asserts no conflict markers in project-owned files.
