---
name: agents-md
description: Audit or update a project's AGENTS.md agent instructions when commands, paths, architecture boundaries, human checkpoints, review rules or the definition of done change, or when the user asks whether AGENTS.md is still accurate. Use only for AGENTS.md and nested AGENTS.md files, not for other documentation.
---

# Maintain AGENTS.md

AGENTS.md is the single instruction file for Claude Code and Codex; `CLAUDE.md` imports it with
`@AGENTS.md` and adds Claude-only notes. Keep it short, current and executable.

## Inspect

1. List the instruction files: `rg --files -g 'AGENTS.md' -g 'CLAUDE.md'`.
2. Read the root file and every nested file for the area in question.
3. Check each claim against the repository, not against memory:
   - commands against `task --list` and the Taskfiles;
   - tools, dependency groups and import contracts against `pyproject.toml`;
   - paths and layers against the source tree;
   - rules against accepted ADRs in `docs/adr/`;
   - hooks and permissions against `.claude/settings.json`, `.codex/` and `scripts/agent/`.
4. Collect the problems:
   - stale commands, paths or tool names;
   - rules that contradict an accepted ADR;
   - rules that a linter, hook or test already enforces (remove them, do not restate them);
   - implementation detail that belongs in code, a README, an ADR or the roadmap;
   - nested files that repeat their parent.

Treat the repository as the authority. Do not assume a harness, a CLI or a file exists until you
have seen it.

## Report or update

- **Audit or review request:** report each finding with the file, the line and the evidence
  that contradicts it. Do not edit.
- **Update request:** make the smallest change that restores accurate guidance.
- The root file holds repository-wide content: project, commands, layout, rules, planning,
  pull-request review rules, definition of done and writing style. Keep it under about 100
  lines; make room by cutting before you add.
- In a project generated from agentic-ds-template, only the `## Project` section is
  project-owned. The other sections come from the template and are merged on every
  `task update-template`. Put project-specific rules in `## Project` or in a nested AGENTS.md;
  change a template section only to fix it, and mention that the fix belongs upstream too.
- Add a nested AGENTS.md only when a directory has durable rules that differ from its parent.
  It refines the root; it never repeats it.
- Follow the writing style section of the file you edit.

## Validate

Run `task lint` after a Markdown-only change and `task check` when behavior changed too. Report
the files you inspected, the changes you made and the commands you ran.
