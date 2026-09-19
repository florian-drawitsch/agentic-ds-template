# project plugin

Workflows for projects built with Claude Code and Codex.

| Skill | Purpose |
| --- | --- |
| `deliver` | Execute an approved plan end to end. A completion guard (Stop hook) keeps the session working until every plan item is ticked, the check passes and, optionally, a cross-vendor review passed. Supports single-agent and implementer and reviewer subagent modes, and unattended runs in a worktree |
| `cross-review` | Have the other vendor's CLI review the change, headless and read-only, with structured findings |

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
