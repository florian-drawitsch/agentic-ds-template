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
  adding `factory:approved` or by moving the card to **Approved**. The same admission rule as
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
- Approval: the user approved this named plan with D1 to D3 on 2026-11-21. Delivery may not
  publish a pull request until the user says so.

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
- [ ] **States and board.**
  - Add states `PLAN_REVIEW` (`factory:plan-review`, column "Plan review") and `APPROVED`
    (`factory:approved`, column "Approved") to the labels, the transitions and
    `BOARD_COLUMNS`.
  - `init` adds missing columns to an existing board without reordering or renaming the
    others.

  Tests: allowed and refused transitions, label creation, column migration on a fake board.
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
    risk high, or the `factory:lit` label.

  Tests with a fake runner: each gating reason, rejection of an extra file, marker content.
- [ ] **Approval admission.**
  - Generalize `ready_signals` and `admitted` to approval signals: a `factory:approved` label
    or a move to Approved on the factory board, by a user with write access. The latest
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
- [ ] **Status and reports.** `status` and the daily report list plans waiting for approval
  and how long they have waited. The weekly report splits cycle time into time waiting for a
  person and time with agents, so the cost of lit mode is visible. Tests with fixed snapshots.
- [ ] **Parallelism, stated honestly.** The skill, the guide and the `max_in_progress` comment
  in `factory.toml` say that deliveries run one at a time today. Test: the template's
  `factory.toml` renders the comment.
- [ ] **Template and docs.**
  - `mode` with a comment goes in the rendered `factory.toml`.
  - `docs/guides/factory.md` covers the modes, the new labels and columns, and how to approve
    or reject a plan. To reject, move the card to Needs info with a comment, or back to Ready
    to plan again.
  - The `factory` skill gets the same changes. The README gets the lit and dark text and the
    figure rows from D2.

  Template tests render both modes.
- [ ] **Final gate** `task check` and the configured review.

## Acceptance and verification

- In dark mode, a low-risk ticket moves from Ready through Accepted and Approved to In
  progress without a person. Check: `uv run pytest tests/test_factory_stages.py -q`.
- In dark mode, a high-risk ticket or one labeled `factory:lit` stops in Plan review. Check:
  same file.
- In lit mode, every ticket stops in Plan review. An approval by an outsider or a board
  workflow is ignored. An approval by a maintainer leads to implementation of exactly the
  approved plan commit. Check: `uv run pytest tests/test_factory.py -q`.
- Factory deliveries no longer run the in-run cross-review. Check: the delivery command test.
- The triage evals pass, including the new risky issue. Check: `task eval` (spends tokens; run
  once before merge).
- The final gate is `task check`, run by the delivery controller.

External checks, which need a board on a personal account and `gh` with the `project` scope:

- On a test repository in lit mode, approve a plan by moving its card to Approved. The ticket
  should reach In progress at the next tick.
- Change the plan branch after approval. The ticket should return to Plan review.

## Rulings

None yet.
