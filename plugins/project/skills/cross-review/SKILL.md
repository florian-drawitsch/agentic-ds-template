---
name: cross-review
description: Use when a change should be reviewed by the other vendor's coding agent (Codex reviews Claude's work, Claude reviews Codex's work) before it is considered done, including the final review of a delivery run. The reviewer runs headless and read-only and returns structured findings. Not for reviewing your own change with your own model, and not for PR comments.
---

# Cross-vendor review

A reviewer from another vendor is less likely to share the author's blind spots. This skill runs
the other CLI headless and read-only:

- **Claude Code:** only read and git-read tools are allowed, and edits are denied.
- **Codex:** runs in the read-only sandbox.

The reviewer returns a verdict and findings that follow a JSON schema.

`<skill-dir>` below is the directory that contains this file.

## Run it

```bash
uv run --script <skill-dir>/scripts/cross_review.py --reviewer codex \
  --token <delivery-run-token> --plan <plan>
```

| Option | Meaning |
| --- | --- |
| `--reviewer` | `codex` when you are Claude, `claude` when you are Codex |
| `--token` | the delivery run the review belongs to; the completion guard reads it from there |
| `--plan` | the plan the change implements, for the spec-compliance check |
| `--base` | the commit to compare against; default is the merge base with `main` |
| `--model` | exact reviewer model; Codex defaults to `gpt-6.1-sol`, Claude to `opus`; both run at high effort |
| `--codex-bin` | Codex executable; overrides `CODEX_BIN`, then PATH |
| `--preflight` | check executable availability and supported Codex version without a model request |

Codex >=0.162.0 is required. An older CLI can reject `gpt-6.1-sol` even when the account supports
it. Upgrade the terminal CLI or select a current executable with `--codex-bin`; keep the requested
model. Delivery preflight checks this before implementation, and records the executable path and
model for the final review command.

Exit code 0 means the review passed, 2 means changes were requested, and 1 means the review
could not run. Errors such as a missing login or a missing CLI are printed. Report them; do not
retry blindly.

## Handle the findings

Perform one final focused review by default; additional passes require an explicit delivery
review budget. The reviewer reports only critical design and correctness issues and code that
materially harms maintenance, not cosmetic edits, speculative abstractions, extra test layers,
process artifacts, or unrelated cleanup.

Implement every blocking or major finding autonomously and run focused regression tests. Routine
fixes are already authorized; do not stop at the review result or ask the user to implement
them. A material scope or design decision, or a human checkpoint in `AGENTS.md`, is the
exception: resolve it before proceeding.

For an active delivery run, commit the fixes and write a small JSON report outside the tracked
tree. `summary` describes the changes; `fixes` lists each finding by its zero-based index and the
concrete `change`. Then record verification with:

```bash
uv run --no-project python scripts/agent/deliver.py resolve-review --token <token> \
  --fixes-file <fixes.json> --check "task test-fast -- tests/<affected>"
```

The command requires every blocking or major finding, runs the focused check, and records the
resolved code state separately from the original review. Continue to the final gate and PR. The
review command counts all recorded passes against the saved budget, regardless of vendor; failed
CLI launches do not count. Never review until a pass is obtained.

## Cost

Each review is a full agent session of the other vendor, billed to that vendor's account.
Prefer one review of the finished change over reviews of every task. Run the broad final gate
once after fixes; repeat checks only to resolve a failure or cover newly changed behavior, never
to obtain another review verdict.
