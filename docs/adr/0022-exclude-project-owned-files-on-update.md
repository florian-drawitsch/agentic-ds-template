# ADR-0022: Exclude project-owned files on update and vendor plugin files by symlink

- **Status:** Accepted
- **Date:** 2026-10-09
- **Amends:** ADR-0002, ADR-0010

## Context

ADR-0002 planned `_skip_if_exists` for project-owned files. Copier recreates such files on
update when they are missing, so a project that deleted the example model would get it back.

The template also ships files that belong to the `project` plugin:
- the completion guard `deliver.py`
- the implementer and reviewer roles

A second copy in `template/` would drift from the plugin.

## Decision

- **Rendered on copy only.** Project-owned paths are excluded from `copier update` through
  `_exclude` patterns conditioned on `_copier_operation == 'update'`:
  - `README.md`, `src/`, `tests/`, `conf/`, `notebooks/`
  - `docs/context/`, `docs/method/`, `docs/roadmap.md`
  - the first ADR
- **Plugin files are symlinks.** The plugin files in `template/` are symlinks into
  `plugins/project/`. Copier follows them and copies the content verbatim, so the plugin stays
  the single source.
- **Generated project config excludes the vendored copy.** The project's ruff and ty settings
  exclude `scripts/agent/deliver.py`, so project formatting never rewrites it.
- **No project-local skills directory at generation time.** Linking `.claude/skills` to
  `.agents/skills` would need `_preserve_symlinks`, which conflicts with vendoring by symlink.
  Projects add local skills when they need them.

## Alternatives considered

- **`_skip_if_exists`:** recreates deliberately deleted files.
- **Copying plugin files into `template/` in a build step:** another generated artifact to keep
  in sync.

## Consequences

- Template improvements to skeleton code reach only new projects.
- Template-owned files (tooling, hooks, AGENTS.md, docs scaffolding) still merge on update.

## Verification

Render tests assert that the vendored files equal the plugin files and that the hook commands
match the completion guard. The update test lives in the slow suite.
