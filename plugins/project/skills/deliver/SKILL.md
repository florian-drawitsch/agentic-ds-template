---
name: deliver
description: Use when an approved implementation plan should be executed end to end without stopping, by one agent or by implementer and reviewer subagents, optionally finished with a cross-vendor review, or run unattended in a separate worktree. A completion guard keeps the session working until every plan item is ticked and the project check passes. Not for writing the plan itself.
---

# Deliver a plan end to end

A delivery run executes an approved plan until it is verifiably done. A Stop hook (the
completion guard) sends the session back to work whenever it tries to stop early. The run is
done only when:

1. every `- [ ]` item in the plan is ticked,
2. the check command passes (default `task check`), and
3. if requested, a cross-vendor review of the exact current state passed.

`<skill-dir>` below is the directory that contains this file. After installation, the project
copy `scripts/agent/deliver.py` offers the same commands.

## 1. Preconditions

- A plan file with `- [ ]` items exists, usually `docs/plans/YYYY-MM-DD-<topic>.md`. It should
  come from a planning workflow (for example Superpowers `writing-plans`) on the strongest
  available model. If there is no plan, plan first and get it approved. Do not start a run.
- Work on a feature branch or worktree, never on the default branch.
- The guard and roles are installed. Check with
  `uv run --script <skill-dir>/scripts/deliver.py doctor`. If anything is missing, ask the
  user once, then run `uv run --script <skill-dir>/scripts/deliver.py install`. Codex users
  must review and trust the new hook with `/hooks`.

## 2. Choose the options

Use the user's stated options. Otherwise use these defaults and state them in one line:

| Option | Default | Alternative |
| --- | --- | --- |
| `--mode` | `subagents`: a fresh implementer per task, a reviewer after each | `single`: one agent executes inline, for small or tightly coupled plans |
| `--cross-review` | `final` when the other vendor's CLI (`claude` or `codex`) is installed, else `off` | `off` |
| `--reviewer` | the other vendor: `codex` when you are Claude, `claude` when you are Codex | |
| `--check` | `task check` | the project's merge gate |

## 3. Start the run

```bash
uv run --script scripts/agent/deliver.py start --plan <plan> --mode subagents \
  --cross-review final --reviewer codex
```

The output ends with `deliver-run: <token>`. From now on, end every turn with that exact line.
The guard binds the run to this session when it sees the token.

## 4. Execute

Follow the plan task by task without pausing for check-ins.

- **subagents mode:** follow Superpowers `subagent-driven-development`.
  - Dispatch each task to the `implementer` agent and each review to the `reviewer` agent.
  - The roles set the model tiers: the implementer runs a mid-tier model, the reviewer the
    strongest. Use a cheaper model only for fully specified single-file steps.
  - When the reviewer requests changes, send the findings back to the implementer and review
    again. From the fourth fix round, start a fresh implementer on a stronger model. After the
    fifth, decide and record a ruling.
- **single mode:** follow Superpowers `executing-plans`, then dispatch one `reviewer` for the
  whole change.
- Tick a checkbox only after its step is verified: tests ran and passed, or the review passed.
- Settle ambiguities yourself and record each ruling in the plan under `## Rulings`:
  `<what you decided>: <why>; <what it costs if wrong>`. When a ruling changes a durable
  decision, record an ADR.

## 5. Stop only for these reasons

- an irreversible or destructive operation,
- a security-sensitive action,
- an external side effect such as a push, merge or publish,
- a plan so broken that every way forward is a guess.

In those cases run
`uv run --script scripts/agent/deliver.py block --token <token> --reason "<why>"`,
explain the decision needed, and stop. To abandon a run, use `stop` instead of `block`.

## 6. Finish

When every item is ticked, the guard runs the check command. If the check fails, it sends you
back with the failing output. With `--cross-review final`, run the `cross-review` skill with
`--token <token>` once the check passes.
- Fix blocking and major findings, or record in the plan why a finding does not apply.
- Then run the review again. The guard accepts only a review of the current state.

Final report:
- the run status (`deliver.py status`)
- the plan items completed
- the check result
- the review verdicts
- the rulings made
- what remains for the human, such as pushing or opening a PR

## Unattended runs

To run a plan without a human present, start it from a terminal or from Task:

```bash
uv run --script scripts/agent/deliver.py headless --plan <plan> --harness codex \
  --cross-review final --reviewer claude
```

- It creates a worktree on a `deliver/<plan>` branch next to the repository. Files listed in
  `.worktreeinclude` are copied into it. The plan must be committed first.
- It starts the run, launches the agent non-interactively, and resumes the same session with
  the guard's message until the run is released.
- Claude Code runs in `auto` permission mode; Codex runs `workspace-write` with approvals off.
  Both are confined to the new worktree.
- It survives usage and rate limits. A round cut off by a limit is not counted. The run waits
  until the announced reset, or backs off exponentially when no reset time is known, then
  resumes the same session. `--max-wait` (hours, default 48) caps the total wait.
- The JSON event log is kept in the run directory.

## Run states

| Status | Meaning |
| --- | --- |
| `active` | the guard keeps the bound session working |
| `completed` | all three completion conditions held |
| `blocked` | a human decision is needed; the reason is in the run note |
| `stalled` | several rounds ended without any change to the code, the plan or the reviews |
| `exhausted` | the round limit (`--max-rounds`, default 40) was reached, or a usage limit needs a longer wait than `--max-wait` allows |
| `stopped` | cancelled, or the plan file disappeared |

Run state lives in the git directory of the worktree (`agent-runs/`), never in the commit
history.
