---
name: github-create-pr
description: Prepare and create a GitHub pull request from the current branch with Conventional Commit title, template-conformant description, validation evidence, and duplicate checks. Use when the user asks to draft, open, or create a PR, and for the publication step of a delivery run.
---

# Create a GitHub pull request

Create a reviewable PR from committed work. Drafting a description is read-only. Pushing a branch
and creating the PR are external mutations and require the user's request, immediate approval, or
a `task deliver` run, which authorizes both after verification.

## Reviewer requests

Never automatically request human reviewers or teams, including on scheduled runs,
PR creation, updates, or retries. Do not pass `--reviewer`, `--add-reviewer`, or call
the GitHub review-request API for humans. Codex automatic PR review is separate
and remains enabled; delivery cross-review selects an agent, not a GitHub reviewer.

## Prepare

1. Read `AGENTS.md`, including its code review rules, for the changed areas.
2. Confirm the source is a feature branch, not `main`, and has commits to review.
3. Inspect `git status`, the diff against `main`, and the commit list. Stop if work is
   uncommitted, unrelated changes would enter the PR, or the commits do not form meaningful
   Conventional Commit chunks.
4. Reject commit messages or trailers with coding-agent attribution, such as `Co-Authored-By`,
   `Generated with`, or equivalent wording.
5. Derive a Conventional Commit title, `type(scope): summary`, without agent attribution.
6. Fill every level-two section of `.github/pull_request_template.md`, in order, from the actual
   diff, decisions, and verification output. Drop the template's HTML comments and guidance text.
   Write `Not run: <reason>` for a required check that did not run; never invent results. Tick
   only checklist items that hold. Name affected ADRs, or state that no
   recorded decision changed.
7. Run `task check` unless the user requested description-only work.

## Publish safely

1. Confirm `gh auth status --hostname github.com` succeeds with an account that can open PRs on
   the `origin` repository. Check access without side effects:

   ```bash
   uv run --no-project python <skill-dir>/scripts/create_pr.py --preflight
   ```

2. Save the description outside the tracked tree and preview the request:

   ```bash
   uv run --no-project python <skill-dir>/scripts/create_pr.py \
     --title "type(scope): summary" --description-file /tmp/pr-body.md
   ```

3. Review the resolved repository, source, destination, title, and sections. The helper rejects
   missing, reordered or empty sections, unfinished placeholders, and agent attribution.
4. Once authorized, repeat the command with `--execute --push`. It pushes the checked-out branch
   without force, then creates the PR, or updates the title and description of the matching open
   PR on a retry.

In a delivery run, do not call the helper directly; `deliver.py publish` validates the run and
invokes it. Report the PR URL, source and destination branches, title, and checks run. Do not
merge, approve, add labels, or change repository settings.
