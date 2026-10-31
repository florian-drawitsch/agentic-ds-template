# ADR-0027: Deliver plans as verified pull requests with bounded cost

- **Status:** Accepted
- **Date:** 2026-10-31

## Context

ADR-0020 ends a delivery run in a worktree with ticked items and a passed check. A human still
pushes, writes the PR description and opens the PR. Real use of the skill showed more gaps:

- Per-task reviews and repeated cross-reviews cost more than they found. Reviews also drifted
  into cosmetic requests.
- Unattended runs had no cost cap and no record of spent tokens.
- The agent sandbox exposed provider tokens and credential files to headless rounds.
- `.worktreeinclude` copied `.env` into every worktree.
- A cut-off run could not be resumed after the terminal closed.
- The project copy of `deliver.py` could not find the cross-review and PR scripts outside the
  plugin.

## Decision

The `deliver` skill adopts a hardened controller:

- **Defaults:** `single` mode, one focused final review by the other vendor at high effort, a
  review budget of 0 to 3 passes. Required findings are fixed autonomously and recorded with
  `resolve-review`, bound to the code state. No review is repeated to obtain a pass.
- **Publication:** `--publish-pr` pushes without force and creates or updates the PR through
  the new `github-create-pr` skill. The outer controller runs the final gate, a gitleaks scan
  of the published commits and the push, outside the agent sandbox. The run completes only with
  a PR receipt for the verified commit. Human reviewers are never requested automatically.
- **Budgets:** per-round cost, turn and time caps, a run cost cap and a review cost cap. Usage
  is logged per round in `usage.jsonl`. Codex costs are estimated from `prices.toml`.
- **Sandbox:** headless rounds scrub provider tokens, deny credential paths and limit network
  access to package registries plus hosts in the project's `deliver.toml`. Codex uses the
  `deliver` permission profile from `.codex/config.toml`. Git hooks and config are hashed
  around every round.
- **Operation:** `task deliver` offers terminal menus for plan, models, effort and review budget.
  `task deliver:resume` continues a saved run from any worktree. Branches are `feat/<topic>`.
- **Project copies:** `install` copies `deliver.py`, `cross_review.py`, `create_pr.py` and
  `prices.toml` flat into `scripts/agent/`. `deliver.py` looks for each beside itself first,
  then in the plugin layout. The template vendors the same files by symlink (ADR-0022).
- **Project skills:** live in `.agents/skills/<name>/` (read by Codex), with a relative symlink
  in `.claude/skills/<name>` (read by Claude Code). `deliver.py doctor` reports missing links.
- **Permissions:** only read-only `gh` commands skip approval. `gh pr create` asks, except
  inside `task deliver`, whose controller publishes.

This amends ADR-0020. Its completion guard, run binding, run state location and stall release
remain.

## Alternatives considered

- Keep publication manual: every run ends with the same copy-and-paste steps.
- Let the agent push from inside its sandbox: needs credentials in the sandbox.
- Keep `scripts/agent/deliver.py` as a symlink into a skill vendored in the project:
  generated projects reference the plugin through the marketplace and have no skill directory.

## Consequences

- PR delivery needs `gh` authenticated for `github.com` and gitleaks, which `task setup`
  installs as a git hook.
- Codex review needs CLI 0.162.0 or later. The `deliver` profile needs 0.163.0; older CLIs run
  with network access disabled after setup.
- `prices.toml` needs updates when model prices change. Estimates may differ from
  subscription charges.
- Model names in menus and role files follow the vendors' current frontier and small models.

## Verification

- `tests/test_deliver.py`, `tests/test_deliver_limits.py`, `tests/test_cross_review.py` and
  `tests/test_create_pr.py` cover the controller, budgets, sandbox settings, publication and the
  PR helper.
- `tests/template/test_agent_wiring.py` checks a rendered project: project copies resolve
  locally, Codex actions name existing tasks, only read-only `gh` commands are allowed, and no
  credentials reach worktrees.
