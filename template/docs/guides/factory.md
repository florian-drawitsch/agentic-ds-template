# Ticket factory

The ticket factory moves labeled GitHub issues through triage, delivery and independent
verification to a pull request that a human merges. Labels hold the state; a GitHub project
board shows it. The design is ADR-0033 of agentic-ds-template.

The factory is in development. Today it creates the labels and the board, mirrors the issue
states into the board, shows status and spend, and prints the next action. Triage,
implementation and verification by agents follow in the next template releases.

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

   `init` is idempotent. It creates the seven `factory:*` labels and a board named
   `<repository> factory`, links the board to the repository, sets its Status columns and
   writes the board URL to `factory.toml`. Commit that change. `copier copy` and
   `task setup` run the same step and skip it with a note while the repository or the scope
   is missing.

## Use

1. Write an issue with a clear goal and acceptance criteria.
2. A maintainer adds the label `factory:ready`. Only labels set by users with write access
   count; the factory ignores issues released by anyone else.
3. `task factory:status` shows the issues per state, spend against the caps and the next
   action. `task factory:tick` prints that action without changing anything.
4. `task factory:sync` mirrors the labels into the board after manual label changes.

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
waiting, USD 20 per day and USD 100 per sprint by default. Pull requests that touch
`factory.toml`, agent configuration, CI or infrastructure always go to a human.

To stop the factory, remove `factory:ready` from the waiting issues.
