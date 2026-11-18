# Factory S3: holdout checks, reports and board mirror

Status: Proposed; not approved for implementation.

## Goal and scope

Add the sealed holdout checks, the budget and sprint reports, the retrospective PR and the
optional board mirror from ADR-0033.

Non-goals: template integration (S4). Automatic merge.

## Prerequisites and decisions

- Depends on [Factory S2: agent stages](2026-11-18-factory-s2-agent-stages.md).
- ADR-0033 (Proposed). Holdout data never enters the repository or the worker sandbox.
- Approval: pending. Once the user approves this named plan, delivery may publish a pull
  request. It never merges.

## Implementation sequence

- [ ] **Holdout configuration.** `[holdout]` in `factory.toml`: `source` is a local path or a
  `git` URL with a ref, `command` is the command to run inside the PR worktree with
  `FACTORY_HOLDOUT_DIR` set to the checked-out holdout, `timeout_minutes` defaults to 30.
  Validation rejects a `source` inside the repository. Tests: path, git source, inside-repo
  rejection, missing command.
- [ ] **Holdout run.** Before the verifier, the controller fetches the holdout into a
  temporary directory outside every worktree, runs the command with a scrubbed environment
  (reuse the env-scrub approach of `deliver.py`), and keeps only the exit code and a list of
  check names with pass or fail parsed from JSON lines `{"check": str, "passed": bool}` on
  stdout. Other output is discarded. Pass → continue to the verifier. Fail → comment with the
  failed check names only, then `factory:needs-human`. Missing holdout configuration skips the
  step. Tests: pass, fail, timeout, output that tries to leak data is dropped, temporary
  directory removed.
- [ ] **Worker denial.** Add the holdout path to the deny list that `deliver.py headless`
  passes to the worker sandbox, through a new `--deny-path` option that `factory.py` sets.
  Tests in `tests/test_deliver.py`: the option appears in the Claude settings and the Codex
  profile of a headless round.
- [ ] **Tracking issue.** `ensure_tracking_issue()` finds or creates one issue titled
  "Factory status" with the label `factory:tracking`, pinned when the API allows it. Tests:
  create once, reuse after.
- [ ] **Reports.** New subcommand `report daily|sprint`. Daily: issues per state, blocked
  issues with their causes, spend against both caps. Sprint: merged PRs with cycle time from
  `factory:accepted` to merge, cost per ticket from cost markers, `needs-human` causes grouped.
  Both post one comment on the tracking issue, with a marker so a rerun on the same day or
  sprint edits instead of adding. Tests with fixed snapshots and `now`.
- [ ] **Retrospective.** `report retro` collects the sprint's `needs-human` causes, declined
  findings and cost outliers (above twice the median) into a file, runs the `agents-md` skill
  through `deliver.py headless` with a generated plan that asks for proposed AGENTS.md or skill
  changes only, and publishes the PR. The PR touches protected paths, so it is labeled
  `factory:needs-human` and never verified automatically. Tests with a fake deliver runner.
- [ ] **Board mirror.** With `project` set, after every label change the controller updates
  the item's single-select field `Status` to the state name through `gh api graphql`, adding
  the issue to the project when missing. Errors are logged and never fail the tick. Tests: field
  update payload, missing item added, GraphQL error tolerated.
- [ ] **Docs and gate.** Document holdouts (with the data science example from ADR-0033),
  reports and the board mirror in the skill and `docs/features.md`. Run `task check`.

## Acceptance and verification

- `uv run pytest tests/test_factory.py tests/test_deliver.py` passes.
- A holdout test proves that only check names and pass or fail reach the comment.
- Final gate (run by the delivery controller): `task check`.
- External checks, not part of this run: posting reports and updating a real board need `gh`
  with project scope and the user's approval of the repository.

## Rulings

- Holdout results reduced to check names and pass or fail: details would let the worker fit
  the holdout through repeated runs; if wrong, a human reads the full output locally.
- Report comments are edited in place by marker: one comment per day keeps the tracking issue
  readable; if wrong, switch to new comments.
