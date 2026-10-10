# project plugin

Workflows for projects built with Claude Code and Codex.

| Skill | Purpose |
| --- | --- |
| `deliver` | Execute an approved plan end to end. A completion guard (Stop hook) keeps the session working until every plan item is ticked, the check passes and, optionally, a cross-vendor review passed. Supports single-agent and implementer and reviewer subagent modes, and unattended runs in a worktree |
| `cross-review` | Have the other vendor's CLI review the change, headless and read-only, with structured findings |
| `brainstorm` | Alias for `superpowers:brainstorming`: explore requirements and design tradeoffs |
| `write-plan` | Alias for `superpowers:writing-plans`: write an implementation plan in `docs/plans/` |
| `execute-plan` | Alias for `superpowers:executing-plans`: execute a plan step by step with review checkpoints |
| `implement-feature` | Alias for `superpowers:test-driven-development`: build features test-first (red-green-refactor) |
| `debug` | Alias for `superpowers:systematic-debugging`: isolate and fix issues with hypothesis tests |
| `request-review` | Alias for `superpowers:requesting-code-review`: review changed files against the plan |
| `wrap-feature` | Alias for `superpowers:finishing-a-branch`: verify work, merge branches, or open pull requests |

## Setup in a repository

```bash
uv run --script <plugin>/skills/deliver/scripts/deliver.py install
```

This adds, without touching other settings:

- `scripts/agent/deliver.py`: the project copy the hooks call
- a Stop hook in `.claude/settings.json` and `.codex/hooks.json`
- `implementer` and `reviewer` roles in `.claude/agents/` and `.codex/agents/`. The reviewer is
  read-only. Models default to `sonnet` and `opus` for Claude; Codex roles inherit the session
  model unless `--codex-implementer-model` or `--codex-reviewer-model` is given.

Projects generated from this repository's template get these files at generation time.
