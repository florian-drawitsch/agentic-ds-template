# project plugin

Workflows for projects built with Claude Code and Codex.

| Skill | Purpose |
| --- | --- |
| `deliver` | Execute an approved plan end to end. A completion guard (Stop hook) keeps the session working until every plan item is ticked, the check passes, an optional cross-vendor review passed and, with `--publish-pr`, a PR exists for the verified branch. Unattended runs work in a worktree within cost, turn and time budgets, and can be resumed |
| `cross-review` | Have the other vendor's CLI review the change, headless and read-only, with structured findings. Required findings are fixed and verified without another review |
| `github-create-pr` | Validate a PR description against `.github/pull_request_template.md`, then push and create or update the PR idempotently. Never requests human reviewers |
| `mermaid` | House style for Mermaid diagrams: when to draw, which type, stroke-only palette, required style lines. `scripts/check_mermaid.py` checks Markdown files against it |
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

- `scripts/agent/deliver.py`: the project copy the hooks call, with `cross_review.py`,
  `create_pr.py` and `prices.toml` beside it, so the project runs without the plugin
- a Stop hook in `.claude/settings.json` and `.codex/hooks.json`
- `implementer` and `reviewer` roles in `.claude/agents/` and `.codex/agents/`. The reviewer is
  read-only and runs at high effort. Models default to `sonnet` and `opus` for Claude. The Codex
  reviewer defaults to `gpt-6.1-sol`; the Codex implementer inherits the session model unless
  `--codex-implementer-model` is given.
- `.worktrees/` in `.gitignore`

Optional: a `deliver.toml` at the repository root adds network hosts for headless rounds
(`[sandbox].extra_hosts`).

Projects generated from this repository's template get these files at generation time.
