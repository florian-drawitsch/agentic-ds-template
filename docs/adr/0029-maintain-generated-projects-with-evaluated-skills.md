# ADR-0029: Maintain generated projects with evaluated skills

- **Status:** Accepted, amended by ADR-0030
- **Date:** 2026-10-31

## Context

Generated projects update with `task update-template`, which runs `copier update` with inline
conflict markers. Merging those markers well depends on knowing which files the project owns
(ADR-0002, ADR-0022), that `uv.lock` is regenerated rather than merged, and that plugin copies
in `scripts/agent/` follow the plugin. ADR-0016 planned a `template-update` skill for this.

AGENTS.md drifts as tasks, paths and decisions change. A sibling project with the same agent
setup keeps it accurate with a maintenance skill, and its review rules require each finding to
name a location, the broken rule and whether it blocks.

This repository's AGENTS.md requires evals that beat the no-skill baseline for every new skill,
but no skill had evals and the repository had no way to run them.

## Decision

- **`template-update` skill** in the `project` plugin. It requires a clean tree, runs
  `task update-template` (`-- --defaults` without a terminal), and merges conflicts by file
  ownership:
  - project-owned paths are never refilled from the template;
  - plugin copies and role files take the template side, and local edits are reported as drift;
  - `uv.lock` is regenerated with `uv lock`.

  It then runs `task setup` and `task check` and reports without committing.
- **`agents-md` skill.** It checks AGENTS.md claims against `task --list`, `pyproject.toml`,
  the source tree, accepted ADRs and the hook configuration. An audit only reports. An update
  makes the smallest fix, keeps the root under about 100 lines, and puts project rules in the
  project-owned `## Project` section or a nested AGENTS.md.
- **Review rules.** The template's AGENTS.md asks for file and line, the broken rule or record,
  and whether a finding blocks. Breaking the use-case rules now blocks a merge. The file stays
  within its line budget.
- **Skill evals.** Eval cases live in `plugins/project/evals/`, one directory per case, in the
  `claude plugin eval` format. `task eval` scores them with and without the plugin. Results are
  git-ignored. A skill ships when its cases score higher with the plugin than without it.

## Alternatives considered

- A wrapper script around `copier update`: conflict resolution needs judgment, not a fixed rule
  per file.
- A custom eval runner: `claude plugin eval` already runs a no-plugin baseline, LLM and regex
  graders, cost caps and reports.
- Running evals in `task check`: they spend model tokens and vary between runs.

## Consequences

- Evals run on demand and cost a few US dollars per full run. Judging uses an LLM, so a score
  differs between runs; three runs per case and arm keep the delta readable.
- `claude plugin eval` measures Claude Code only. The skill text stays harness-neutral, but
  Codex behavior is not scored.
- Existing `project` skills still have no evals. They get cases when they next change.

## Verification

- `task eval` on 2026-10-31 (Sonnet as agent and judge, three runs per case and arm):

  | Case | With plugin | Without | Skill fired |
  | --- | --- | --- | --- |
  | `01-template-update-conflicts` | 1.00 | 0.50 | 3 of 3 |
  | `02-template-update-project-owned` | 1.00 | 0.33 | 3 of 3 |
  | `03-agents-md-audit` | 1.00 | 0.87 | 3 of 3 |
  | `04-neg-ordinary-merge-conflict` (must not fire) | 1.00 | 1.00 | never suggested an update |

- `task check` covers the manifests and the AGENTS.md line budget for every use case.
