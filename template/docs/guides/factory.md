# Ticket factory

The ticket factory moves labeled GitHub issues through triage, delivery and independent
verification to a pull request that a human merges. Labels hold the state; a GitHub project
board shows it. The design is ADR-0033 of agentic-ds-template.

The workflow `.github/workflows/factory.yml` runs the factory in GitHub Actions. It costs
model tokens and Actions minutes: set the spend caps in `factory.toml` before the first run.

## Set up

1. Push the project to GitHub.
2. Give `gh` access to projects, once per machine:

   ```bash
   gh auth refresh -s project
   ```

3. Create the labels and the board:

   ```bash
   task factory:init
   ```

   `init` is idempotent. It creates the `factory:*` and `priority:*` labels and a Kanban board
   named `<repository> factory`, links the board to the repository, sets its Status columns
   and adds its views. The board is found by that name under the repository's owner, so a
   fork or transferred repository gets its own board. `copier copy` and `task setup` run the
   same step and skip it with a note while the repository or the scope is missing.

4. Create the token. The workflow's own token is read-only, so all writes use the secret
   `FACTORY_GH_TOKEN`: a GitHub App installation token or a fine-grained token with
   read and write access to contents, issues, pull requests and projects. Pull requests it
   opens then start CI, which the default token never does.
5. Add the secrets under Settings, Secrets and variables, Actions: `FACTORY_GH_TOKEN`,
   `ANTHROPIC_API_KEY` and `OPENAI_API_KEY`. The model keys reach only the steps that run
   the factory tasks.
6. Protect `main` under Settings, Branches: require a pull request with one human approval,
   require the CI check, and do not let the token's account bypass these rules. The factory
   never merges; a human does.
7. Enable Codex code review for the repository. The factory waits for its review before it
   marks a pull request verified.
8. Private repositories on the Free plan have limited Actions minutes. Lengthen the cron
   interval in the workflow, or run `task factory:loop` on your machine instead.

## Use

1. Write an issue with a clear goal and acceptance criteria.
2. Start the ticket: a maintainer adds the label `factory:ready`, or moves its card to
   Ready on the board and waits up to 10 minutes for the next scheduled run. Only users with
   write access count; the factory ignores issues released by anyone else.
3. `task factory:status` shows the issues per state, spend against the caps and the next
   action. `task factory:tick -- --dry-run` prints that action without changing anything.
4. `task factory:sync` mirrors the labels into the board after manual label changes.
5. `task factory:loop 300` runs the tick on your machine every 300 seconds (default 600).
6. `task factory:report -- daily` or `weekly` posts the report on the "Factory status" issue.
   The workflow posts the daily report at 06:00 UTC and the weekly one on Mondays at 07:00
   UTC. After `retro_every` merged pull requests, the daily run also starts a retrospective.

## Write a factory-ready issue

- State the goal in one sentence and the observable result that means it is done.
- List acceptance criteria as checkable statements. The verifier judges the change against
  them.
- Name constraints: files or modules to leave alone, data that must not be touched.
- Keep the scope to one pull request. Split larger work into several issues.
- Set `priority:high`, `priority:medium` or `priority:low` to order the queue.

## Board

The board is a Kanban board: work flows continuously, one ticket at a time, with no sprints
or estimates. `init` adds three views:

| View | Shows |
| --- | --- |
| Board | One column per factory state, from Ready to Done |
| Backlog | Tickets not started yet: Ready, Needs info, Accepted |
| Needs attention | Tickets waiting for a person: Needs info, Needs human, Verified |

Order the queue with the labels `priority:high`, `priority:medium` and `priority:low`; the
factory picks higher priority first, then the lower issue number. To show the factory's
limits on the board, set a column limit of 1 on In progress and 3 on Verified in the board
settings. Labels drive the factory: moving a card does not change its label, so change the
label instead.

| Label | Board column | Meaning |
| --- | --- | --- |
| `factory:ready` | Ready | Released by a maintainer |
| `factory:needs-info` | Needs info | Triage needs more information |
| `factory:accepted` | Accepted | Triaged and queued |
| `factory:in-progress` | In progress | Claimed, implementation running |
| `factory:in-review` | In review | Pull request open and under review |
| `factory:verified` | Verified | All checks passed, waiting for a human merge |
| `factory:needs-human` | Needs human | Stopped, a human must decide |

Closed issues move to Done through the board's built-in workflow.

## Limits

`factory.toml` sets the limits: one issue in progress, at most three verified pull requests
waiting, USD 20 per day and USD 100 per calendar week by default. Pull requests that touch
`factory.toml`, agent configuration, CI or infrastructure always go to a human.

## Stop the factory

- Remove `factory:ready` from the waiting issues.
- To pause everything, disable the workflow under Actions, or delete `FACTORY_GH_TOKEN`.
  A running job finishes its tick; the next one does nothing.
