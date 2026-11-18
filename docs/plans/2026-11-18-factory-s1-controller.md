# Factory S1: deterministic controller core

Status: Proposed; not approved for implementation.

## Goal and scope

Build the deterministic core of the `factory` plugin from ADR-0032: configuration, label
states, admission, a GitHub snapshot, the dispatch decision, the claim, protected paths and the
spend ledger. `tick --dry-run` prints the action the controller would take. Nothing in this
stage calls a model or changes GitHub state except label creation and the claim push.

Non-goals: agent stages (S2), holdout checks, reports and the board mirror (S3), template
integration (S4).

## Prerequisites and decisions

- ADR-0032 (Proposed) defines the design. Its Verification section lists the checks needed
  before acceptance. Accepting it stays a human decision after S4 and the live run.
- ADR-0017 (both harnesses), ADR-0018 (one repository, one tag), ADR-0022 (project copies),
  ADR-0027 and ADR-0028 (the outer controller owns GitHub writes).
- Approval: pending. Once the user approves this named plan, delivery may publish a pull
  request. It never merges.

## Implementation sequence

- [ ] **Plugin skeleton.** Add a `factory` entry to `catalog.toml` (local source
  `./plugins/factory`, harnesses claude and codex, category Developer Tools). Create
  `plugins/factory/skills/factory/SKILL.md` (frontmatter `name: factory`, a description that
  triggers on running or inspecting the ticket factory, and a short body that points to
  `<skill-dir>/scripts/factory.py --help`; mark it as in development until S2 adds evals) and
  `plugins/factory/skills/factory/scripts/factory.py`: stdlib only, inline script metadata like
  `deliver.py`, a `VERSION` constant and argparse subcommands `status`, `tick`, `labels`. Run
  `task build` and commit the generated manifests. Test in `tests/test_factory.py` (load the
  script with `tests/helpers.load_script`): `--help` lists the subcommands, and
  `scripts/build.py --check` passes.
- [ ] **Configuration.** `load_config(root) -> Config` reads `factory.toml` with `tomllib`.
  A frozen dataclass with defaults: `max_in_progress = 1`, `max_verified = 3`,
  `max_repair_cycles = 2`, `review_wait_minutes = 30`, `daily_cap_usd = 20.0`,
  `sprint_cap_usd = 100.0`, `sprint_start` (ISO date, optional), `sprint_days = 14`,
  `protected_paths` (the ADR-0032 defaults), `project` (optional board URL), `holdout`
  (optional, unused until S3), `[deliver]` table with `harness`, `reviewer`, `max_cost`.
  Unknown keys and wrong types raise `SystemExit` with the key name. Tests: defaults without a
  file, overrides, unknown key, wrong type.
- [ ] **Label states.** Constants for the seven labels from ADR-0032 and the transition table
  of its state diagram. `state_of(labels) -> State | None` returns the single factory state and
  raises on two factory labels. `transition(current, target)` rejects transitions that are not
  in the table. `labels` subcommand creates missing labels idempotently with fixed colors and
  descriptions. Tests: every allowed transition, a rejected one, two labels at once, idempotent
  creation against the fake GitHub.
- [ ] **GitHub access.** A `GitHub` class that wraps `gh api` (REST and GraphQL) through one
  `_call(args, input=None)` method, so tests substitute a `FakeGitHub` from
  `tests/factory_fakes.py`. Methods: `issues_with_label`, `issue`, `timeline`,
  `permission(user)`, `pull_for_branch`, `reviews`, `check_runs`, `changed_files(pr)`,
  `add_labels`, `remove_label`, `comment`. Never pass issue text to a shell. Tests use the
  fake only; no test reaches the network.
- [ ] **Admission.** `admitted(issue) -> bool` is true only when the latest `labeled` timeline
  event for `factory:ready` comes from a user whose permission is `admin`, `maintain` or
  `write`. Bots count only when listed in `factory.toml` under `trusted_bots` (default empty).
  Unadmitted issues are skipped and reported by `status`; the controller never comments on
  them. Tests: maintainer admits, read-only user does not, label re-added by an outsider after a
  maintainer removed it does not, bot not listed does not.
- [ ] **Snapshot and dispatch.** `snapshot(github, config) -> Snapshot` collects factory issues
  per state, their linked PRs (branch `feat/<issue>-<slug>`), repair cycle counts (from
  `<!-- factory:repair -->` marker comments) and spend. `next_action(snapshot, config, now)`
  is a pure function returning one `Action(kind, issue, reason)` in the order repair, verify,
  implement, triage, or `Idle(reason)`. Within a state, a `priority:high|medium|low` label
  orders first, then the issue number. Implement is refused when in-progress issues reach
  `max_in_progress` or verified issues reach `max_verified`. Tests: one test per ordering rule,
  per limit and for ties; property-style test that the result never violates a limit.
- [ ] **Claim.** `slug(title)` returns lowercase ASCII words joined by hyphens, at most 40
  characters, never empty (`issue` fallback). `claim(root, issue)` creates
  `feat/<issue>-<slug>` at `origin/<default branch>` and pushes it without force. A rejected
  push returns `False` and changes no labels. Tests run against a local bare repository in
  `tmp_path`: first claim wins, second loses, slug edge cases (unicode, punctuation, long).
- [ ] **Protected paths.** `touches_protected(files, patterns) -> list[str]` matches with
  `fnmatch` on POSIX paths; a pattern ending in `/` covers the directory, and `AGENTS.md`
  matches nested copies. Tests: each default pattern, renames (both old and new path count),
  no false match on `docs/AGENTS.md.bak`.
- [ ] **Spend ledger.** Costs live in GitHub as marker comments
  `<!-- factory:cost {"stage": "...", "usd": 1.23, "at": "ISO8601"} -->` on the ticket.
  `record_cost(github, issue, stage, usd)` writes one; `spend(snapshot, now, config)` returns
  today's and the current sprint's totals in UTC. `next_action` returns `Idle` with the cap
  name once either cap is reached. Tests: day boundary, sprint boundary, malformed marker
  ignored, marker from a non-controller author ignored.
- [ ] **Commands.** `status [--json]` prints counts per state, unadmitted issues, spend and
  caps, and the next action. `tick --dry-run [--json]` prints the next action and changes
  nothing. `tick` without `--dry-run` exits with code 3 and "agent stages arrive in S2" for
  agent actions, and performs only the claim for implement. Exit codes: 0 action done or idle,
  1 error, 3 not implemented. Tests through `main(argv)` with the fake GitHub.
- [ ] **Docs and gate.** Document the plugin in `docs/features.md` (Agentic development,
  marked as in development) and add ADR-0032's controller to the plugin list in
  `docs/design/architecture.md`. Run `task check`.

## Acceptance and verification

- `uv run pytest tests/test_factory.py` passes for each step above.
- `uv run --script plugins/factory/skills/factory/scripts/factory.py tick --dry-run --json`
  runs against the fake in tests and prints one action.
- No test calls the network or a model. `grep -n "shell=True" plugins/factory` finds nothing.
- Final gate (run by the delivery controller): `task check`.
- External check, not part of this run: `factory.py labels` and `status` against a real test
  repository need `gh` authentication and the user's approval of the repository.

## Rulings

- Costs stored as marker comments on issues: GitHub is the only durable store in Actions and
  locally; if wrong, a later stage moves the ledger to the tracking issue.
- Dispatch is a pure function over a snapshot: it is testable without GitHub; if wrong, the
  snapshot grows fields, the rule stays.
- The skill ships in S1 without evals and marked as in development: evals need the triage
  role from S2; if wrong, S1 must not be tagged in a release before S2 lands.
