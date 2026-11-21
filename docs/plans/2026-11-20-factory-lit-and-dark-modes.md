# Factory lit and dark modes

Status: Approved for implementation on 2026-11-21.

Resolves #17: compare the factory with current practice, let a project choose how much a person
stays in the loop, and challenge the current skills, roles, parallelism and subagent use.

## Findings

### Lit and dark: what changes

Today the factory is dark between the issue and the pull request. A person admits the issue
(`factory:ready`) and merges the pull request. The plan is written inside the delivery run, so
nobody sees it before money is spent on implementation.

Current practice puts human judgment at two points: the design before the build, and the
review gate before the merge (Osmani, "Software factories, light and dark", 2026-07; Böckeler,
2026-02). The factory already has the second gate. It lacks the first one.

A lit factory therefore needs one new gate: **plan approval**. A gate before publication adds
little, because publication is not a merge and the merge is already human.

### Options for the mode switch

| Option | Throughput | Cost | Risk | Complexity |
| --- | --- | --- | --- | --- |
| Mode in `factory.toml` | Lit adds human wait per ticket; dark unchanged | Lit saves the implementation spend of rejected plans | Uniform per repository; risky tickets in a dark repo stay unreviewed | One key |
| Per-ticket label | Only flagged tickets wait | Same as lit for flagged tickets | Depends on the maintainer flagging the right tickets; an admitted user could also unflag | One label, admission rules |
| Gates as board states | Same as the chosen mode | None of its own | Approval is visible on the board and audited on the issue timeline | New states, columns and admission for one more human input |

These are not alternatives: the mode needs a default, tickets need exceptions, and the gate
needs a place on the board.

**Recommendation:**

- `mode = "dark" | "lit"` in `factory.toml`. The default stays `dark`, so current projects
  keep their behavior.
- Escalation only, per ticket. A ticket gets the plan gate in dark mode when triage rates it
  `risk: high` or a maintainer adds `factory:lit`. No label removes the gate in lit mode.
- The gate is a board state. A plan waits in **Plan review**. A maintainer approves it by
  adding `factory:approved` or by moving the card from Plan review to **Building** (D8). The same admission rule as
  for Ready applies: the latest signal from a user with write access counts.
- Approval binds to the plan commit. If the branch changes after approval, the ticket returns
  to Plan review.

Splitting planning from implementation also helps dark mode. The plan becomes a separate,
cheap, committed step, and each stage stays small.

### Challenges to the current design

| Area | Finding | Recommendation | Where |
| --- | --- | --- | --- |
| Parallelism | A tick runs `implement` synchronously and ticks are serialized. A second delivery can never run, so `max_in_progress` above 1 has no effect. An issue still In progress at the start of a tick is treated as interrupted. | Say so in the docs and the config now. Later, start each delivery as its own Actions job (`workflow_dispatch` with a per-issue `concurrency` group), and detect interruption from that job's status. Lit mode makes this more valuable: plans queue while a person reviews. | This plan documents it. Parallel deliveries need a follow-up ADR. |
| Reviews | One ticket gets three Codex reviews of the same diff: the final cross-review inside `deliver headless`, the GitHub Codex review the controller waits for (up to 30 minutes), and the independent verification. The last two are the same vendor and model. | Drop `--cross-review final` from factory runs. The GitHub review feeds `fix-pr` repairs, and the verifier checks the acceptance criteria in a fresh context. | Decision D3; one task in this plan |
| Context in the loop | Headless rounds resume one session, so a long ticket accumulates context. Anthropic's long-running harness starts each session fresh and carries state in files (feature list, progress notes, git log). The plan's checkboxes and the git history already serve as that state. | Add an opt-in `--fresh-rounds` to `deliver headless` and compare pass rate and cost with `task eval` before changing the default. | Follow-up |
| Subagents | `deliver` uses subagents only when asked. Factory runs never use them. Per-task implementer subagents keep the coordinating agent's context small but cost more tokens. | Measure together with fresh rounds. No default change without evidence. | Follow-up |
| Roles | The triage role returns only accept or needs-info. There is no planner role: the worker expands a plan skeleton inside its own run. Model names are pinned in several places (`gpt-6.1-sol` in the Codex reviewer role and the `deliver` defaults). | Add `risk` and its reason to triage. Add a planner role. Read model names from one place in `deliver.toml`. | Triage and planner here; model names as a follow-up |
| Skills | Seven alias skills repeat Superpowers skills, so agents see two triggers for one job. `execute-plan` overlaps `deliver`. `request-review`, `cross-review` and the reviewer role overlap. `wrap-feature` may merge, which a factory run must never do. | Merge or drop the aliases and state what each review path is for. | #10 |
| Terms | The README calls interactive mode the "lit factory". This plan's lit factory is the board with a plan gate. | Decision D2 | Last task |

## Goal and scope

- Add `mode` (dark or lit), the plan stage, the plan gate, approval admission bound to the plan
  commit, risk escalation from triage, and the `factory:lit` label.
- Drop the in-run cross-review from factory deliveries (D3).
- Document the real parallelism limit.

Non-goals:

- parallel deliveries
- fresh-context rounds
- the subagent experiment
- skill cleanup (#10)
- central model configuration
- automatic merge

## Prerequisites and decisions

- ADR-0033 (Proposed) is amended in the first task. It is still open, so no new ADR is needed.
- D1 (decided): the default mode is `dark`. It keeps current behavior, and risk escalation
  covers the dangerous tickets.
- D2 (decided): "lit factory" means the board with the plan gate, and interactive mode stays a
  separate way of working. The README text and the factory figure follow in the last task.
  This matches the issue and Osmani's definition.
- D3 (decided): factory deliveries run without `--cross-review final`. This removes a
  duplicate Codex review per ticket.
- D5 (decided): one model per factory stage, the same in both modes. The strongest model
  goes wherever no person checks the output next; subscriptions and free tiers pay where they
  can.

  | Stage | Model | Paid from |
  | --- | --- | --- |
  | Triage | Gemini `gemini-4-hydrogen` | free tier |
  | Plan | Claude `opus`, high effort | Claude subscription |
  | Implementation | Claude `sonnet`, medium effort | Claude subscription |
  | Verification | Gemini `gemini-4-carbon` | AI Pro credits |

  Opus plans in both modes: in dark mode nobody reads the plan, and in lit mode a better plan
  costs the reviewer less time. Running locally (`task factory:loop`), triage and
  verification may use Codex `gpt-6.1-sol` at high effort on the ChatGPT login instead.
  Depends on [Gemini as a third harness](2026-11-21-gemini-harness.md) for the Gemini stages.
- D6 (decided): design questions reach a person.
  - The planner separates routine rulings, which it decides and records under `## Rulings`,
    from open design questions, which it must not decide. Examples of open questions: a new
    dependency, a change to a public interface, a choice between designs with different costs,
    and a requirement the acceptance criteria do not settle.
  - The planner reports open questions in a `## Open questions` section of the plan, each
    with the options it sees and its recommendation.
  - Any open question sends the ticket to Plan review, in both modes. The controller lists the
    questions in a comment.
  - You answer in a comment, then move the card from Plan review to Building or add `factory:approved`. The
    planner revises the plan with your answers, and the ticket returns to Plan review until no
    question is open.
  - Only answers from users with write access count, under the same latest-signal rule as
    approvals. The answers reach the planner as quoted data, never as instructions.
- D7 (decided): the current plan is part of the ticket.
  - The controller writes the plan into the issue body, in a section between
    `<!-- factory:plan -->` and `<!-- /factory:plan -->` below the reporter's text.
  - The reporter's text stays untouched. Each revision replaces only that section, so the
    ticket always shows the current plan, its open questions and the plan commit it belongs
    to.
  - The plan file on the branch stays the source for implementation, and approval binds to
    its commit. The body section is a copy for reading.
  - Triage reads the issue body without that section, so a plan never feeds back into triage.
- D8 (decided): the board shows one column per owner, not one per state. Labels keep every
  state; the board groups them.

  | Column | Labels behind it | Whose move |
  | --- | --- | --- |
  | Ready | `ready` | you released it; the factory triages next |
  | Planning | `accepted` | factory: triage done, planner working |
  | Plan review | `plan-review` | you: read the plan in the ticket, answer questions, approve |
  | Building | `approved`, `in-progress` | factory: queued or implementing |
  | In review | `in-review` | factory: pull request, CI, repairs, verification |
  | Ready to merge | `verified` | you: merge |
  | Blocked | `needs-info`, `needs-human` | you: the reason is in the last comment |
  | Done | closed | none |

  - "Verified" stays as a state, shown as Ready to merge: it is your merge queue and
    `max_verified` counts it.
  - Needs info and Needs human share Blocked. Their labels stay apart, because reports and
    reruns treat them differently.
  - Board input stays limited to two moves: a card moved into Ready releases a ticket, and a
    card moved from Plan review into Building approves its plan. Every other move goes back
    to the column of the card's label at the next mirror.
- Approval: the user approved this named plan with D1 to D3 on 2026-11-21, D5 on the same day,
  D6 and D7 on the same day, and D8 on 2026-11-22. Delivery may not publish a pull request until the user says
  so.

## Implementation sequence

- [ ] **ADR.** Amend ADR-0033 with:
  - the modes, the plan stage and the Plan review and Approved states;
  - approval admission and its binding to the plan commit;
  - risk escalation and the `factory:lit` label;
  - the review change from D3;
  - the parallelism limit.

  Redraw its state diagram.
- [ ] **Config.** `mode` in `factory.toml`: `"dark"` (default) or `"lit"`, with validation.
  Tests: default, both values, a wrong value rejected.
- [ ] **Stage models (D5).** A `[models]` table in `factory.toml` with `triage`, `plan`,
  `implement` and `verify`, each a model ID with an optional effort, defaulting to D5. The
  controller passes them to triage, the planner session, `deliver headless` and the verifier.
  Tests: defaults, overrides, a model ID passed to each stage's command, an unknown key
  rejected.
- [ ] **States and board (D8).**
  - Add states `PLAN_REVIEW` (`factory:plan-review`) and `APPROVED` (`factory:approved`) to
    the labels and the transitions.
  - `BOARD_COLUMNS` becomes the eight columns of D8, each with the labels it shows. The mirror
    maps a label to its column. Intake reads a move into Ready as a release and a move from
    Plan review into Building as an approval.
  - `init` migrates an existing board: it adds the new columns, moves each card to the column
    of its label and removes the old columns (Needs info, Accepted, Approved, In progress,
    Verified, Needs human) only when they are empty. It reports a column it could not remove.

  Tests: allowed and refused transitions, label creation, every label's column, both input
  moves and a refused move, migration of a fake board with cards in old columns.
- [ ] **Triage risk.**
  - The triage role also returns `risk` (`low` or `high`) and `risk_reason`. High means:
    new dependencies, public interfaces, data handling, security, architecture, or more than
    one module.
  - `parse_triage` treats a missing or unknown `risk` as high (fail closed). The triage marker
    stores it.

  Tests: parse cases. Evals: add a risky issue that must come back `high`, and keep the
  existing three passing.
- [ ] **Planner role and plan stage.**
  - New action `plan` for an accepted issue. It claims the branch and writes the plan
    skeleton.
  - A planner session expands the skeleton with the `write-plan` skill. It uses the new
    `assets/roles/planner.md` and the `deliver headless` worker sandbox, with no GitHub
    credentials.
  - The controller rejects any change outside the plan file and sends such tickets to Needs
    human. Otherwise it commits and pushes the plan, and comments with the link and a marker
    holding the plan commit.
  - Next state: `PLAN_REVIEW` when gated, otherwise `APPROVED`. Gated means lit mode, triage
    risk high, the `factory:lit` label, or an open question (D6).
  - The planner role tells the planner to keep routine rulings and open design questions
    apart, with the examples from D6.

  Tests with a fake runner: each gating reason, an open question gating a dark-mode ticket,
  rejection of an extra file, marker content.
- [ ] **Plan in the ticket (D7).**
  - After each plan or revision, the controller replaces the section between
    `<!-- factory:plan -->` and `<!-- /factory:plan -->` in the issue body, or appends it.
    It holds the plan text, the open questions and the plan commit.
  - The section is size-limited; a longer plan shows its start and a link to the file.
  - `issue_file`, the triage input, strips the section.

  Tests: append, replace, reporter's text unchanged, size limit, triage input without the
  section.
- [ ] **Answers and revision (D6).**
  - When a ticket in Plan review has open questions and receives an approval signal, the
    controller collects the comments after the plan comment from users with write access.
  - The planner gets them as a quoted file and revises the plan. The controller commits it,
    updates the body section, and returns the ticket to Plan review if questions remain,
    otherwise to `APPROVED`.
  - An approval of a plan without open questions moves it to `APPROVED` directly, as before.

  Tests with a fake runner: answers from an outsider ignored, a revision that closes all
  questions, one that leaves a question open, the revised commit bound to the approval.
- [ ] **Approval admission.**
  - Generalize `ready_signals` and `admitted` to approval signals: a `factory:approved` label
    or a move from Plan review to Building on the factory board (D8), by a user with write
    access. The latest
    signal wins.
  - Intake moves an admitted Plan review card to `APPROVED`.
  - `factory:lit` is admitted the same way. Once the gate applies, no later signal removes it.

  Tests: outsider approval ignored, approval by an automated move ignored, a later outsider
  signal revoking an earlier approval, and the label set by hand or by a card move.
- [ ] **Implement from an approved plan.**
  - `implement` starts only from `APPROVED`. It runs `deliver headless` on the committed plan
    without rewriting it, and without `--cross-review final` (D3).
  - When the branch head differs from the approved plan commit, the ticket returns to
    `PLAN_REVIEW` with a comment.
  - Dispatch order: repair, verify, implement, plan, triage.
  - A new `max_plan_review` (default 3) stops planning while that many plans wait for a person.

  Tests: order, the commit-binding refusal, the limit, and the delivery command without the
  cross-review flag.
- [ ] **Status and reports.** `status` and the daily report list plans waiting for approval,
  with their number of open questions, and how long they have waited. The weekly report splits cycle time into time waiting for a
  person and time with agents, so the cost of lit mode is visible. Tests with fixed snapshots.
- [ ] **Parallelism, stated honestly.** The skill, the guide and the `max_in_progress` comment
  in `factory.toml` say that deliveries run one at a time today. Test: the template's
  `factory.toml` renders the comment.
- [ ] **Template and docs.**
  - `mode` with a comment goes in the rendered `factory.toml`.
  - `docs/guides/factory.md` covers the modes, the new labels and columns, how to read the
    plan in the ticket, how to answer open questions, and how to approve or reject a plan. To
    reject, move the card to Needs info with a comment, or back to Ready to plan again.
  - The `factory` skill gets the same changes. The README gets the lit and dark text and the
    figure rows from D2.

  Template tests render both modes.
- [ ] **Final gate** `task check` and the configured review.

## Acceptance and verification

- In dark mode, a low-risk ticket moves from Ready through Accepted and Approved to In
  progress without a person. Check: `uv run pytest tests/test_factory_stages.py -q`.
- In dark mode, a high-risk ticket or one labeled `factory:lit` stops in Plan review. Check:
  same file.
- The board shows the eight columns of D8, and each label lands in its column. Check:
  `uv run pytest tests/test_factory.py -q`.
- In lit mode, every ticket stops in Plan review. An approval by an outsider or a board
  workflow is ignored. An approval by a maintainer leads to implementation of exactly the
  approved plan commit. Check: `uv run pytest tests/test_factory.py -q`.
- Factory deliveries no longer run the in-run cross-review. Check: the delivery command test.
- Each stage runs on its D5 model unless `[models]` overrides it. Check: `uv run pytest tests/test_factory_stages.py -q`.
- A plan with an open question stops in Plan review in dark mode too. After a maintainer's
  answer and approval, the revised plan, not the first one, is implemented. Check: same file.
- The issue body shows the current plan below the reporter's unchanged text, and triage never
  reads it. Check: `uv run pytest tests/test_factory.py -q`.
- The triage evals pass, including the new risky issue. Check: `task eval` (spends tokens; run
  once before merge).
- The final gate is `task check`, run by the delivery controller.

External checks, which need a board on a personal account and `gh` with the `project` scope:

- Run `task factory:init` on this repository's existing board. The old columns are replaced
  by the eight columns of D8, and the cards keep their tickets.
- On a test repository in lit mode, approve a plan by moving its card from Plan review to
  Building. The ticket
  should reach In progress at the next tick.
- Change the plan branch after approval. The ticket should return to Plan review.
- A ticket whose plan has an open question shows the question in its body and waits. After an
  answer and approval, the body shows the revised plan.

## Rulings

None yet.
