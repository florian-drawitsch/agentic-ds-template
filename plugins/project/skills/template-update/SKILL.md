---
name: template-update
description: Use for conflicts that `copier update` or `task update-template` left in a project generated from the agentic-ds-template Copier template (markers labelled `before updating` and `after updating`, `.rej` files, a conflicted `uv.lock`), to pull a newer template version into such a project, and to explain why some template changes did not arrive, even when the user does not say "template". Decides each conflict by file ownership, regenerates the lockfile, runs `task check` and reports without committing. Not for ordinary git merge or rebase conflicts, plain dependency upgrades, or editing one answer in `.copier-answers.yml`.
---

# Update a project from its template

The project was generated with Copier. `task update-template` runs
`uvx copier update --trust --conflict inline` and refuses to start on a dirty tree. Where a
template change collides with a local edit, Copier writes conflict markers into the file.

Run the update, merge every conflict so local customizations survive and template improvements
arrive, refresh dependencies, verify, and report. Do not commit: the user reviews the diff.

## 1. Before the update

1. `git status --porcelain` must print nothing. Otherwise stop and ask the user to commit or
   stash. On a dirty tree, Copier's markers mix with the user's own unfinished edits.
2. Check that `.copier-answers.yml` exists. Without it, the project was not generated from the
   template: say so and stop.
3. Note `_commit` from `.copier-answers.yml` to report the version change at the end.
4. Suggest a dedicated branch, for example `git switch -c chore/template-update`.

## 2. Run it

- With a terminal: `task update-template`. Copier asks every template question again, with the
  recorded answers as defaults. Keep them unless the user wants a change.
- Without a terminal (the usual case for an agent): `task update-template -- --defaults`.
  `--defaults` reuses the recorded answers unchanged. Without it, the prompts fail and Copier
  aborts without touching files.
- Pass `-- --vcs-ref <tag>` only when the user names a version.

If Copier reports that the project is up to date, stop and say so.

## 3. Find what needs attention

Run both scans; an update can add new, untracked files.

```bash
git grep -nE '^(<{7}|={7}|>{7})' || true
rg -n '^(<{7}|={7}|>{7})' --glob '!.git' --glob '!.venv' || true
find . \( -name '*.rej' -o -name '*.orig' \) -not -path './.git/*' -not -path './.venv/*'
git status --porcelain
```

Also look over the files that changed without conflicts.

## 4. Merge each conflict

Copier marks the local content `before updating` and the template content `after updating`.
Merge deliberately; never pick a side blindly.

- **Keep** local customizations: names, added dependencies, custom tasks, project settings.
- **Adopt** template improvements: new tasks, tool and version bumps, new guardrails and hooks.
- Remove all three marker lines.

File ownership decides some conflicts for you:

- **Project-owned files** are excluded from updates: `README.md`, `apps/`, `packages/`,
  `notebooks/`, `docs/context/`, `docs/method/`, `docs/roadmap.md` and the first ADR. Never copy
  template versions of them in by hand. The `## Project` section of `AGENTS.md` is project-owned
  too.
- **Plugin copies** in `scripts/agent/` (`deliver.py`, `cross_review.py`, `create_pr.py`,
  `pr_findings.py`, `prices.toml`, `check_mermaid.py`) and the role files in `.claude/agents/`
  and `.codex/agents/` are verbatim copies of the plugin. Take the template side, even when a
  comment says the local edit was deliberate. A local edit there is drift: tell the user, and
  suggest a command-line option or a change in the plugin instead.
- **`uv.lock`:** never merge markers by hand. Take either side, then run `uv lock`.
- **High-churn files** need real merges: `pyproject.toml`, `Taskfile.yml` and `taskfiles/`,
  `AGENTS.md` outside the project section, `.pre-commit-config.yaml`, `.github/workflows/`,
  `.claude/settings.json`, `.codex/config.toml`.
- Copier's marketplace pin (`marketplace_ref`) moves with the update. Keep the new pin unless
  the user asked to stay on a version.

Ask the user when you cannot tell whether a local change was intentional. Afterwards, the scans
in step 3 must find no markers and no `.rej` files.

## 5. Refresh and verify

```bash
uv lock
task setup
task check
```

`task setup` installs the locked environment and the git hooks. When `task check` fails, read
the output: usually a conflict was merged wrongly or a new template rule needs a project change.
Fix it, or report it with the output. Never weaken a check to make it pass.

## 6. Report, without committing

- template version: `_commit` before and after
- each file that had markers, with one line on how you merged it
- drift you found in plugin copies
- whether `uv.lock` changed
- the `task check` result
- next step for the user: review `git diff` and commit. Never put `git commit` into the
  commands you run or recommend.
