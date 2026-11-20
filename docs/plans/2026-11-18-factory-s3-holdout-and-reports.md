# Factory S3: holdout checks, reports and board mirror

Status: Approved for implementation on 2026-11-20.

## Goal and scope

Add the sealed holdout checks, the daily and weekly reports, the retrospective PR, the
optional board mirror and board intake from ADR-0033.

Non-goals: template integration (S4). Automatic merge.

## Prerequisites and decisions

- Depends on [Factory S2: agent stages](2026-11-18-factory-s2-agent-stages.md).
- ADR-0033 (Proposed). Holdout data never enters the repository or the worker sandbox.
- Approval: the user approved this named plan on 2026-11-20. Delivery may publish a pull
  request. It never merges.

## Implementation sequence

- [x] **Holdout configuration.** `[holdout]` in `factory.toml`: `source` is a local path or a
  `git` URL with a ref, `command` is the command to run inside the PR worktree with
  `FACTORY_HOLDOUT_DIR` set to the checked-out holdout, `timeout_minutes` defaults to 30.
  Validation rejects a `source` inside the repository. Tests: path, git source, inside-repo
  rejection, missing command.
- [x] **Holdout run.** Before the verifier, the controller fetches the holdout into a
  temporary directory outside every worktree, runs the command with a scrubbed environment
  (reuse the env-scrub approach of `deliver.py`), and keeps only the exit code and a list of
  check names with pass or fail parsed from JSON lines `{"check": str, "passed": bool}` on
  stdout. Other output is discarded. Pass → continue to the verifier. Fail → comment with the
  failed check names only, then `factory:needs-human`. Missing holdout configuration skips the
  step. Tests: pass, fail, timeout, output that tries to leak data is dropped, temporary
  directory removed.
- [x] **Worker denial.** Add the holdout path to the deny list that `deliver.py headless`
  passes to the worker sandbox, through a new `--deny-path` option that `factory.py` sets.
  Tests in `tests/test_deliver.py`: the option appears in the Claude settings and the Codex
  profile of a headless round.
- [x] **Tracking issue.** `ensure_tracking_issue()` finds or creates one issue titled
  "Factory status" with the label `factory:tracking`, pinned when the API allows it. Tests:
  create once, reuse after.
- [x] **Reports.** New subcommand `report daily|weekly`. Daily: issues per state, blocked
  issues with their causes, spend against both caps. Weekly, for the calendar week in UTC:
  throughput (merged PRs), cycle time from `factory:accepted` to merge, age of issues in
  progress, cost per ticket from cost markers, `needs-human` causes grouped. Both post one
  comment on the tracking issue, with a marker so a rerun on the same day or week edits
  instead of adding. Tests with fixed snapshots and `now`.
- [x] **Retrospective.** New key `retro_every` in `factory.toml` (int, default 10). Once that
  many PRs merged since the last retrospective marker, `report retro` collects their
  `needs-human` causes, declined findings and cost outliers (above twice the median) into a
  file, runs the `agents-md` skill through `deliver.py headless` with a generated plan that
  asks for proposed AGENTS.md or skill changes only, publishes the PR and writes a
  `<!-- factory:retro -->` marker on the tracking issue. The PR touches protected paths, so it
  is labeled `factory:needs-human` and never verified automatically. Tests with a fake deliver
  runner: below the count nothing runs, at the count one run, the marker resets the count.
- [x] **Board mirror.** Done early: `factory.py init` creates and links the board and sets
  its Status columns, and `factory.py sync` mirrors every open issue. Left for this stage:
  after every label change, the controller mirrors that one issue with the same code. Errors
  are logged and never fail the tick. Tests: mirror called once per label change, `gh` error
  tolerated.
- [x] **Board intake.** New `GitHub` method `status_changes(issue)` reads the issue's
  `ProjectV2ItemStatusChangedEvent` timeline items through GraphQL: actor, project, status,
  `wasAutomated`, time. Extend admission from S1: the latest Ready signal, either a
  `factory:ready` label event or a move into Ready on the factory board, must come from an
  admitted user. Moves on other boards and automated moves are ignored; the controller's own
  identity counts as admitted. `intake(github, board, config)` runs before the snapshot in
  `tick`: for each card in Ready whose issue is open with label state none or Needs info and
  an admitted move, it sets `factory:ready` (removing `factory:needs-info`). After intake, a
  tick with `--scheduled` runs the full `sync`, which moves hand-placed cards in other columns
  back. Intake errors are logged and never fail the tick. Tests with the fake GitHub: a
  maintainer's move sets the label, a read-only user's move does not, an automated move does
  not, a move on another board does not, Needs info moved to Ready becomes Ready, an issue
  already Accepted is left alone, a later outsider label after an admitted move is rejected,
  the controller's own label passes admission, a card dragged to Verified returns to its
  label's column.
- [x] **Docs and gate.** Document holdouts (with the data science example from ADR-0033),
  reports, the board mirror and board intake in the skill and `docs/features.md`. Run
  `task check`.

## Acceptance and verification

- `uv run pytest tests/test_factory.py tests/test_deliver.py` passes.
- A holdout test proves that only check names and pass or fail reach the comment.
- Final gate (run by the delivery controller): `task check`.
- External checks, not part of this run: posting reports and updating a real board need `gh`
  with project scope and the user's approval of the repository. The same applies to the live
  check that a card moved on a board owned by a personal account becomes `factory:ready`.

## Rulings

- Holdout results reduced to check names and pass or fail: details would let the worker fit
  the holdout through repeated runs; if wrong, a human reads the full output locally.
- Report comments are edited in place by marker: one comment per day keeps the tracking issue
  readable; if wrong, switch to new comments.
- Tests for S3 live in `tests/test_factory_s3.py`, with the fake GitHub extended in
  `tests/factory_fakes.py`: `tests/test_factory.py` is already 840 lines; if wrong, merge the
  files. The deny-path tests are in `tests/test_deliver.py` as planned.
- Git holdout source written as `<url>#<ref>` in one `source` string: keeps the `[holdout]`
  table to the three planned keys; if wrong, add a `ref` key.
- Check names are reduced to `[\w .:/-]` and 80 characters, at most 100 checks: the name is the
  only text that reaches the comment, so it cannot carry markup; if wrong, loosen the filter.
- Declined findings for the retrospective come through an injectable `Env.declined` source that
  returns nothing by default: the controller has no stable API to the delivery run's finding
  records; if wrong, wire it to the run directory's `findings.json`.
- The `task check` step of the docs item runs in the controller's final gate, not here: the
  run instructions give the controller the broad gate. Focused checks run here: ruff, ty,
  `tests/test_factory*.py`, `tests/test_deliver.py` and the build check.
- Board lookups in a tick are one extra `gh` call set per tick; errors only log. If the board
  cost matters, cache the board id in `.factory/`.
- External acceptance pending, not passed: posting reports and updating a real board with
  `gh` project scope, and the live check that a card moved on a personal-account board becomes
  `factory:ready`.
