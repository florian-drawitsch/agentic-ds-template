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
| `--model` | the reviewer model; default is the reviewer CLI's configured model |

Exit code 0 means the review passed, 2 means changes were requested, and 1 means the review
could not run. Errors such as a missing login or a missing CLI are printed. Report them; do not
retry blindly.

## Handle the findings

- **Blocking and major findings:** fix them, through the `implementer` agent in subagent mode.
  If a finding does not apply, record why in the plan under `## Rulings`. Any blocking or major
  finding turns the verdict into `changes_requested`.
- **Minor findings:** fix them when cheap, otherwise list them in the final report.
- Run the review again after the fixes. The review is bound to the exact state of the worktree
  (commit plus uncommitted changes), so any later edit needs a new review.
- After three review rounds without a pass, stop fixing. Report the remaining findings, and run
  `deliver.py block` if a delivery run is active.

## Cost

Each review is a full agent session of the other vendor, billed to that vendor's account.
Prefer one review of the finished change over reviews of every task; the per-task reviews in a
delivery run use the same-vendor `reviewer` role.
