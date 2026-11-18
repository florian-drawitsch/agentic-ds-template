---
name: write-plan
description: Write a detailed, test-first implementation plan in docs/plans/. Alias for superpowers:writing-plans, with the sections a delivery run needs.
---

# Write plan

This skill is an alias for `superpowers:writing-plans`.

Follow the instructions in the `superpowers:writing-plans` skill. Save plans as `docs/plans/YYYY-MM-DD-<topic>.md`.

For a plan that the `deliver` skill will execute, also use the sections of
`<skill-dir>/assets/plan-template.md`: scope and non-goals, prerequisites, a `- [ ]`
implementation sequence, acceptance with focused checks, the final gate, external checks
listed separately with their authorization, and `## Rulings`. Keep established plans as they
are.

Saving a plan does not authorize implementation, commits, a push or a pull request. Ask for
explicit approval of the named plan and its scope. A material change to the scope or to a human
checkpoint needs a new approval.
