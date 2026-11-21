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

4. Choose where the factory runs. Both modes use the same labels, board and limits.

   | Mode | Runs on | Credentials | Cost |
   | --- | --- | --- | --- |
   | Local | your machine, while `task factory:loop` runs | your `gh`, `claude` and `codex` logins (browser, app or CLI) | your subscriptions; no Actions minutes |
   | GitHub Actions | GitHub's runners, every hour, with your machine off | repository secrets | Actions minutes (free for public repositories) and model usage |

   **Local:** sign in once, then start the loop. It needs no secrets.

   ```bash
   gh auth login
   ```

   ```bash
   claude
   ```

   ```bash
   codex login
   ```

   ```bash
   task factory:loop
   ```

   **GitHub Actions:** set the three secrets step by step. The command shows what is missing,
   opens each page in the browser, and stores each value with `gh secret set` without
   printing it:

   ```bash
   task factory:secrets
   ```

   - `FACTORY_GH_TOKEN`: a classic token with the `repo`, `project` and `read:org` scopes.
     `gh` needs `read:org` to tell whether the board belongs to a user or an organization. The
     command opens the token page with these scopes selected. Fine-grained tokens cannot reach a board
     owned by a personal account. You can instead reuse the token of your `gh` login, which
     carries every scope `gh` was granted. The workflow's own token is read-only, and pull
     requests it opened would not start CI.
   - Claude: `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`, which uses your Claude
     subscription through a browser login, or an `ANTHROPIC_API_KEY` billed per token.
   - Codex: `OPENAI_API_KEY`, billed per token.
   - Gemini: `GEMINI_API_KEY`, billed per token; a Google AI Pro plan adds API credit.

   Only the two vendors in `factory.toml` need secrets.

   Gemini as the implementer (`harness = "gemini"`) runs under `srt`. The workflow installs the
   pinned `srt` and prepares the runner: bubblewrap, socat, ripgrep and relaxed Ubuntu
   user-namespace rules, which is acceptable on a throwaway hosted runner only. The agent's
   shell commands have no network, so a plan that needs a new dependency blocks the run.

   Until the secrets exist, each workflow run skips its steps with a notice instead of
   failing. `task factory:secrets -- --check` lists what is missing.

5. Protect `main` under Settings, Branches: require a pull request and the CI check, and do
   not allow force pushes. The factory never merges; a human does. GitHub does not let you
   approve your own pull requests, so if `FACTORY_GH_TOKEN` belongs to your account, either
   require no approvals or create a separate machine account for the token and require one
   approval.
6. Enable Codex code review for the repository. The factory waits for its review before it
   marks a pull request verified.
7. Private repositories on the Free plan have limited Actions minutes: about 750 of the 2,000
   a month are spent on idle polling every hour. Lengthen the cron interval in the workflow,
   or use the local mode.

## Use

1. Write an issue with a clear goal and acceptance criteria.
2. Start the ticket: a maintainer adds the label `factory:ready`, or moves its card to
   Ready on the board and waits up to an hour for the next scheduled run. Only users with
   write access count; the factory ignores issues released by anyone else.
3. Triage accepts or asks for information, then the planner writes the plan and commits it.
   In **dark** mode (default) a low-risk plan with no open question goes straight to
   implementation. A ticket stops in **Plan review** when `factory.toml` sets `mode = "lit"`,
   triage rated it `risk: high`, a maintainer added `factory:lit`, or the plan has an open
   question. Only escalation exists per ticket: no label removes the gate in lit mode.
4. In Plan review, read the plan in the ticket body (between the `factory:plan` markers, below
   your own text). Open questions are listed in a comment. Answer in a comment, then approve:
   add `factory:approved`, or move the card from Plan review to Building. Only users with
   write access count, and the latest signal wins. After answers the planner revises the plan
   and the ticket returns to Plan review until no question is open. If you change the branch
   after approval, the ticket returns to Plan review.
5. To reject a plan, move the card to Needs info (Blocked) with a comment, or back to Ready to
   plan again.
6. `task factory:status` shows the issues per state, spend against the caps and the next
   action. `task factory:tick -- --dry-run` prints that action without changing anything.
7. `task factory:sync` mirrors the labels into the board after manual label changes.
8. `task factory:loop 300` runs the tick on your machine every 300 seconds (default 1800).
9. `task factory:report -- daily` or `weekly` posts the report on the "Factory status" issue.
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
| Board | One column per owner, from Ready to Done |
| Backlog | Tickets not started yet: Ready, Planning, Plan review |
| Needs attention | Tickets waiting for a person: Plan review, Blocked, Ready to merge |

Order the queue with the labels `priority:high`, `priority:medium` and `priority:low`; the
factory picks higher priority first, then the lower issue number. Labels keep every state and
the board groups them. Two card moves are input: a card moved into Ready releases a ticket,
and a card moved from Plan review into Building approves its plan. Any other move goes back to
the column of the card's label at the next mirror.

| Column | Labels | Whose move |
| --- | --- | --- |
| Ready | `factory:ready` | you released it; the factory triages next |
| Planning | `factory:accepted` | factory: triage done, planner working |
| Plan review | `factory:plan-review` | you: read the plan, answer questions, approve |
| Building | `factory:approved`, `factory:in-progress` | factory: queued or implementing |
| In review | `factory:in-review` | factory: pull request, CI, repairs, verification |
| Ready to merge | `factory:verified` | you: merge |
| Blocked | `factory:needs-info`, `factory:needs-human` | you: the reason is in the last comment |
| Done | closed | none |

`factory:lit` marks a ticket whose plan needs approval. `task factory:init` migrates an older
board: it moves each card to its new column and removes old columns that are empty.

Closed issues move to Done through the board's built-in workflow.

## Limits

`factory.toml` sets the limits: one issue in progress (deliveries run one at a time today, so a higher value has no effect), at most three verified pull requests
waiting, USD 20 per day and USD 100 per calendar week by default. Pull requests that touch
`factory.toml`, agent configuration, CI or infrastructure always go to a human.

## Stop the factory

- Remove `factory:ready` from the waiting issues.
- To pause everything, disable the workflow under Actions, or delete `FACTORY_GH_TOKEN`.
  A running job finishes its tick; the next one does nothing.
