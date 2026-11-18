# ADR-0028: Repair pull requests in bounded cycles and launch delivery from app sessions

- **Status:** Accepted
- **Date:** 2026-10-31

## Context

ADR-0027 ends a delivery run with a verified pull request. The next step was still manual:
when Codex automatic review left findings on the PR, a human fixed them or started a full new
run. A sibling project with the same delivery controller added three things in real use:

- a bounded repair cycle for review findings on an existing PR;
- launching and resuming runs from an app chat without a terminal;
- fixes for Codex permission overrides with dotted paths and for stale worktree registrations.

It shipped the repair and app stages as separate skills. The template adopts the behavior, not
that skill layout.

## Decision

- **PR repair is a run kind of the `deliver` controller.** `deliver.py fix-pr --pr <n>` (task
  `deliver:fix-pr`) selects unresolved review threads whose first comment is a marked Codex
  finding by the Codex bot. It works on an open, same-repository PR in a clean worktree on the
  PR branch, and refuses dirty or diverged branches. Defaults: 15 rounds, USD 5.
- **Findings are data.** `findings.json` keeps the review text out of the generated plan's
  checklist. The worker records each finding with `finding`: `fixed` runs a focused test bound
  to the committed state, `declined` needs a reason, and `blocked` needs a human.
- **The outer controller owns GitHub writes.** It runs the final gate and the gitleaks scan,
  then pushes without force, replies once per finding, resolves fixed threads and requests one
  Codex re-review. It saves a receipt before the first write. Resuming reconciles saved intents
  with GitHub by marker and author instead of writing twice. Repair never creates a PR, merges
  or requests human reviewers. `pr_findings.py` is a new project copy beside `deliver.py`.
- **App launch.** `status --json` inspects runs without side effects. `resume` selects by token,
  plan, PR or worktree; a unique match needs no terminal and keeps the saved settings.
  `headless --preview` with explicit options changes nothing. `headless` refuses uncommitted
  tracked changes, because the worktree starts from HEAD. An unavailable manual check never
  stops independent work, and it is reported as pending, never as passed.
- **Worktree includes.** Copying `.worktreeinclude` refuses credential patterns, symlinks and
  paths outside the repository, for delivery and repair worktrees alike.
- **Planning text.** `write-plan` gains a plan template with acceptance, external checks and
  rulings. `write-plan` and `brainstorm` state that they authorize neither implementation nor
  publication.

This amends ADR-0027. Branches stay `feat/<topic>`.

## Alternatives considered

- Separate `fix-pr`, `deliver-plan` and `deliver-resume` skills: each new skill needs evals
  (AGENTS.md), and `deliver` already owns the run lifecycle, resume and publication.
- Let the worker post replies itself: it would need GitHub credentials inside the sandbox.
- Repair any PR comment: human comments carry no reliable structure, and the bot marker makes
  selection and reconciliation checkable.

## Consequences

- Repair depends on the Codex review comment marker and bot login. A changed format selects
  nothing; it fails closed.
- Synthetic fixtures cover selection, reconciliation and locking. A live test PR is still
  needed to confirm the current bot identity and marker.
- Launching from a checkout with unrelated uncommitted edits now needs a stash or a commit first.

## Verification

- `tests/test_deliver_repair.py` covers finding selection, dispositions, worktree safety,
  launch, locking, publication staging and reconciliation after lost responses.
- `tests/test_deliver.py` covers the dotted permission keys and stale worktree discovery.
- `tests/template/test_agent_wiring.py` checks that the `pr_findings.py` project copy resolves.
