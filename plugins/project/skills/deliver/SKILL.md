---
name: deliver
description: Use when an approved implementation plan should be executed end to end without stopping, by one agent or by implementer and reviewer subagents, optionally finished with a cross-vendor review, or run unattended in a separate worktree. A completion guard keeps the session working until every plan item is ticked and the project check passes. Not for writing the plan itself.
---

# Deliver a plan end to end

A delivery run executes an approved plan until it is verifiably done. A Stop hook (the
completion guard) sends the session back to work whenever it tries to stop early. The run is
done only when:

1. every `- [ ]` item in the plan is ticked,
2. the selected final check passes once on the final code,
3. if requested, the final review passed or its required findings were fixed and verified, and
4. with `--publish-pr`, a real GitHub PR exists for the verified, committed branch.

`task deliver` defaults to one implementation agent, one focused final review, `task check`, and
PR publication. Invoking it authorizes a normal branch push and PR creation after verification.
It does not authorize merging, force-pushing, labels, or tags.

`<skill-dir>` below is the directory that contains this file. After installation, the project
copy `scripts/agent/deliver.py` offers the same commands. `install` also copies the files the
controller needs at run time next to it: `cross_review.py`, `create_pr.py` and `prices.toml`.
The project then runs without the plugin.

## Reviewer requests

Never automatically request human reviewers or teams, including on scheduled runs,
PR creation, updates, or retries. Do not pass `--reviewer`, `--add-reviewer`, or call
the GitHub review-request API for humans. Codex automatic PR review is separate
and remains enabled; delivery cross-review selects an agent, not a GitHub reviewer.

## Terminal dialogs

Run `task deliver` to choose a plan, implementation harness, reviewer, and review budget.
Defaults are Codex/Sol, Claude/Opus, and one review. Implementation menus include GPT-6.1 Sol
and GPT-6 Luna for Codex, and Sonnet, Opus and Haiku for Claude. They show default effort
and offer an effort selector: high for Luna/Haiku, medium for frontier implementers.
Reviewers always use a frontier model (Sol or Opus in the menus) at high effort;
Luna/Haiku reviewer overrides are rejected. `--effort` overrides implementation effort.
Menus use the session styling and respect `NO_COLOR`; explicit model flags and saved
models bypass model selection. Run `task deliver:resume` from any project
worktree to choose a saved run. Resume keeps its token, session, files, logs, and consumed
budgets. It uses the current controller and asks before integrating a completed prerequisite
branch. A vendor change starts a fresh vendor session while retaining the delivery run.

Use `task deliver -- --preview` or `task deliver:resume -- --preview` to try the menus without
launching an agent, reading local credentials, modifying run state, or publishing. Up/down arrows move the selection; Enter selects the highlighted default; `q` cancels.
Numbers followed by Enter also work. Terminals without cursor support use numbered prompts. Fully specified CLI flags remain available without a
terminal. Resume can also select directly with `--worktree PATH --token TOKEN`; use
`--fresh-session` only when the previous vendor session cannot be reused. Increase exhausted
budgets explicitly with `--max-rounds`, `--max-wait`, or (for a cost-exhausted run) a higher `--max-cost`.

The review budget is 0, 1, 2, or 3 completed passes. Zero disables review; one is the default.
At the budget limit, verify the fixes and continue to the final gate and PR without another
verdict. Changing reviewers does not reset the count.

## 1. Preconditions

- A plan file with `- [ ]` items exists, usually `docs/plans/YYYY-MM-DD-<topic>.md`. Reuse
  accepted ADRs and existing authorization. If the plan is missing, plan first
  and resolve material questions before starting a run.
- Work on a feature branch or worktree, never on the default branch.
- For PR delivery, `gh auth status --hostname github.com` succeeds with an account that can open
  PRs on `origin`. SSH remains the Git push credential. Preflight checks repository and PR access
  before implementation; a missing write permission can still block publication. Report that
  error without claiming success.
- Codex review requires CLI >=0.162.0. `--codex-bin` overrides `CODEX_BIN`, then PATH. The
  selected path is persisted with the run. Upgrade an incompatible CLI instead of guessing model
  names.
- The guard and roles are installed. Check with
  `uv run --script <skill-dir>/scripts/deliver.py doctor`. If anything is missing, ask the
  user once, then run `uv run --script <skill-dir>/scripts/deliver.py install`. Codex users must review and trust
  the new hook with `/hooks`.

## 2. Choose the options

Use the user's stated options. Otherwise use these defaults and state them in one line:

| Option | Default | Alternative |
| --- | --- | --- |
| `--mode` | `single`: one implementation agent | `subagents` for explicitly requested independent work |
| `--cross-review` | `final` when the other vendor's CLI (`claude` or `codex`) is installed, else `off` | `off` |
| `--reviewer` | the other vendor: `codex` when you are Claude, `claude` when you are Codex | |
| `--check` | `task check` | a narrower gate only when the user asks for one |
| `--reviewer-model` | `gpt-6.1-sol` for Codex; `opus` for Claude, always at high effort | exact supported model ID |
| `--codex-bin` | `CODEX_BIN`, then PATH | explicit executable path |
| `--publish-pr` | enabled by `task deliver` | `--no-publish-pr` for an explicit local-only run |
| `--pr-remote`, `--pr-destination` | `origin`, `main` | explicit GitHub target |

## 3. Start the run

```bash
uv run --script scripts/agent/deliver.py start --plan <plan> --mode single --focused-review \
  --check "task check" --cross-review final --reviewer codex
```

The output ends with `deliver-run: <token>`. From now on, end every turn with that exact line.
The guard binds the run to this session when it sees the token.

## 4. Execute

Follow the plan task by task without pausing for check-ins.

- **single mode:** execute the plan inline. Run focused tests during implementation, for
  example `task test-fast -- tests/<area>`. Load the `mermaid` skill before editing a diagram.
- **subagents mode:** dispatch only when explicitly requested, under the repository workflow in
  `AGENTS.md`. Do not add per-task reviews; keep one final review of the complete change.
- Tick a checkbox only after its step is verified: tests ran and passed, or the review passed.
- Resolve routine choices yourself. Ask before any human checkpoint in `AGENTS.md`, such as an
  incompatible public interface change, a new runtime dependency or a release tag. Record each
  routine ruling under `## Rulings`:
  `<what you decided>: <why>; <what it costs if wrong>`. When a ruling changes a durable
  decision, record an ADR.

## 5. Stop only for these reasons

- an irreversible or destructive operation,
- a security-sensitive action,
- a push, merge, tag or publication that is not already authorized by the user or this run,
- a plan so broken that every way forward is a guess.

In those cases run
`uv run --script scripts/agent/deliver.py block --token <token> --reason "<why>"`,
explain the decision needed, and stop. To abandon a run, use `stop` instead of `block`.

## 6. Review, verify, publish

When every item is ticked, the guard requests the final review. Commit the implementation and
the plan first, and run the guard-provided review command so the selected executable and model
are preserved.

- The review covers critical design and correctness issues and code that is materially hard to
  maintain, including the blocking review rules in `AGENTS.md`.
- Implement every blocking or major finding autonomously with the smallest clear fix and a
  focused regression test. Ask only when a fix needs a material design or scope decision.
- Commit the fixes and record them with `resolve-review`, which runs the focused check you name
  and binds the resolution to the exact code state. A further model review runs only within an
  explicitly selected budget above one; never review until a pass is obtained.

The guard then runs the final gate once. A failure returns its output for autonomous repair; a
pass is cached and reused while the code is unchanged, including for publication.

For PR delivery, fill every section of `.github/pull_request_template.md` from the actual diff and
verification output, as the `github-create-pr` skill describes. Keep the description outside the
tracked tree, such as the run directory or `/tmp`. Then prepare publication:

```bash
uv run --no-project python scripts/agent/deliver.py publish --token <token> \
  --title "feat(scope): describe the change" --description-file <description-file>
```

Interactive runs publish directly. In headless mode the command validates plan and review
readiness, stages the title and description, and asks the agent to end its turn; the outer task
runs the final gate, refuses uncommitted work, pushes without force, and creates or updates the
matching open PR outside the agent sandbox. A manual retry outside the sandbox can use
`publish --execute`. The PR receipt must match the current commit, branch and destination. A
failed publication leaves local work intact; a copy-ready description does not complete it.
Report the error and block the run if credentials or permissions need user action.

Final report:

- the run status (`deliver.py status`)
- the plan items completed
- the check result
- the review verdicts and verified fixes
- the rulings made
- the actual PR URL, or an explicit publication blocker
- what remains for the human, such as reviewing and merging the PR

## Unattended runs

To run a plan without a human present, start it from a terminal or from Task:

```bash
uv run --script scripts/agent/deliver.py headless --plan <plan> --harness codex \
  --cross-review final --reviewer claude
```

- It creates a worktree in `.worktrees/<plan>` of the main repository on a `feat/<topic>`
  branch, stripping a leading `YYYY-MM-DD-` from the plan filename. For example,
  `2026-10-20-harness-s1-cost-caps.md` becomes `feat/harness-s1-cost-caps`.
  `--branch` overrides the name; existing saved runs keep their branches. `.worktrees/` must be git-ignored; `install` adds it to `.gitignore`. Files listed in
  `.worktreeinclude` are copied into it. Never include `.env*` files or credential paths; `doctor` warns about these patterns. The plan must be committed first.

- It starts the run, launches the agent non-interactively, and resumes the same session with
  the guard's message until the run is released.
- Claude Code runs in `acceptEdits` permission mode; Codex runs `workspace-write` with approvals
  off. Both are confined to the new worktree. The agent runs focused checks only; the outer
  controller owns the final gate, including autofix hooks that may write protected instruction
  files, and the push and PR.
- Headless rounds scrub common provider-token environment variables. Claude fails closed when its
  sandbox is unavailable and denies listed credential paths. Newer Codex CLIs use the `deliver`
  permission profile; older versions run with network access disabled after setup. Network access
  defaults to Python and npm registries; add project hosts under `[sandbox].extra_hosts` in the
  project's `deliver.toml`, with a comment explaining why. Git hook and config hashes are checked around every round.
- The outer gate uses a reduced environment. `[sandbox].gate = "srt"` opts into the optional `srt`
  wrapper; the default is `"env-scrub"`. Publication scans commits since the PR base with gitleaks.
  Record confirmed false positives by fingerprint in `.gitleaksignore` and add a short comment.
- It survives usage and rate limits. A round cut off by a limit is not counted. The run waits
  until the announced reset, or backs off exponentially when no reset time is known, then
  resumes the same session. `--max-wait` (hours, default 48) caps the total wait.
- The JSON event log is kept in the run directory.

## Run states

| Status | Meaning |
| --- | --- |
| `active` | the guard keeps the bound session working |
| `reviewing` | the controller runs the configured final review outside the implementation sandbox |
| `completed` | all configured completion conditions held |
| `verifying` | the agent yielded the final gate to the outer task |
| `publishing` | the outer task runs final verification, then the push and PR |
| `blocked` | a human decision is needed; the reason is in the run note |
| `stalled` | several rounds ended without any change to the code, the plan or the reviews |
| `exhausted` | the round or wait limit was reached, or less than USD 0.25 remains in the run cost budget |
| `stopped` | cancelled, or the plan file disappeared |

Run state lives in the git directory of the worktree (`agent-runs/`), never in the commit
history.

`task deliver:status` reports the current worktree. Add `-- --all-worktrees` to
include completed and unfinished runs across registered worktrees with their paths.

Each implementation round and review appends parsed usage to `usage.jsonl`. Claude's resumed
session cost is stored as a per-round delta. Codex estimates use Standard model prices in
`<skill-dir>/scripts/prices.toml` (`scripts/agent/prices.toml` in the project copy), charging cached input (a subset of Codex input tokens) only at the cached
rate; subscription charges may differ from these estimates. The status command and
run exit summary report token totals, estimated USD, wall time and limit waits. Defaults are
USD 2 per round, 150 turns, 90 minutes per round, USD 10 per run, and USD 2 per review. Reviews
share the run budget, and the run stops as `exhausted` once the ledger reaches the cap after
any round, including a review started inside it; `--max-cost 0` disables the run cost cap.

## Project integration

Follow `AGENTS.md`, including its human checkpoints and code review rules. Run the narrower checks
it names when the plan touches their area. Preserve unrelated work. Do not start delivery merely
because the skill was installed. Use `task deliver -- --help` for unattended options and
`task deliver:status` for run state.
