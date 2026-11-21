# Gemini as a third harness

Status: Approved for implementation on 2026-11-21.

Resolves #19.

## Goal and scope

Gemini CLI becomes a third vendor next to Claude Code and Codex. It can implement and review.
Every pair of two different vendors works:
- in `deliver` (interactive menus and `deliver headless`),
- in `cross-review`,
- in the factory (triage, implementation, verification),
- in the GitHub Actions workflow.

This repository's factory switches its reviewer to Gemini. It then runs in Actions without
OpenAI credit.

Non-goals:
- the completion guard for interactive Gemini sessions (Gemini as implementer runs headless
  only);
- Gemini through Vertex AI;
- a Gemini plugin marketplace or extension;
- changing the default vendor pair (Claude implements, Codex reviews).

## Findings from the probe (2026-11-21)

A probe in GitHub Actions with Gemini CLI 0.70.0 and the repository's `GEMINI_API_KEY`
returned `OK` from `gemini-3.5-flash-lite`.

- **Headless:** `gemini -p <prompt>` runs headless. `--output-format json` returns
  `session_id`, `response` and `stats.models.<model>.tokens` (input, cached, candidates,
  thoughts).
- **Approval modes:** `--approval-mode plan` is read-only. `auto_edit` and `yolo` allow
  edits.
- **Other flags:** `-s/--sandbox`, `-r/--resume` and `--include-directories` exist.
- **Folder trust:** headless runs exit with code 55 in an untrusted folder. They need
  `--skip-trust` or `GEMINI_CLI_TRUST_WORKSPACE=true`.
- **Credentials:** the CLI authenticates from `GEMINI_API_KEY`. A Google AI Pro plan adds
  USD 10 of API credit each month.
- **Models:** `gemini-4-carbon`, `gemini-4-neon` and `gemini-4-hydrogen` answer through the
  CLI with `-m` (checked 2026-11-21). Carbon and Neon have no free tier, so the key draws on
  billing or credits.
- **Stale aliases:** the CLI's own aliases point at older models. `pro` resolves to
  `gemini-3.1-pro-preview-customtools`, `flash` to `gemini-3.8-flash`, and `auto` routes
  between `gemini-3.5-flash-lite` and `gemini-3.8-flash`. Defaults therefore name exact
  model IDs.
- **Google's model guide:** Hydrogen for everyday coding and agents, Neon for multi-file
  refactors and repository-wide debugging, Carbon for deep verification and the strongest
  reasoning. The CLI says it is not optimized for Argon, the flagship.
- **Prices** per million tokens (input / cached input / output):

  | Model | Price | Free tier |
  | --- | --- | --- |
  | `gemini-4-hydrogen` | 1.50 / 0.075 / 7.50 | yes |
  | `gemini-4-neon` | 3.00 / 0.15 / 15.00 | no |
  | `gemini-4-carbon` | 2.00 / 0.10 / 10.00 until 30 November 2026, then 4.00 / 0.20 / 20.00 | no |

- **CLI future:** Google replaced Gemini CLI with Antigravity CLI for unpaid and Google One
  users on 18 June 2026. Use with an API key still works. ADR-0034 records this as a risk to
  recheck.

## Prerequisites and decisions

- **ADR-0017** requires every plugin to work in Claude Code and Codex. A new ADR-0034 records
  Gemini as an optional third harness:
  - Skills and hooks stay required for Claude Code and Codex.
  - Gemini needs only the delivery, review and factory paths.
- **D1:** the vendor rule becomes "implementer and reviewer are different vendors". The
  default reviewer is the first other vendor that is installed, in the order Codex, Claude,
  Gemini.
- **D2:** the factory question in Copier needs GitHub CI and at least two harnesses, instead
  of exactly Claude Code and Codex.
- **D3:** Gemini runs headless only. Interactive `task deliver` offers Gemini as implementer
  for headless runs and as reviewer for any run.
- **D4, model defaults:** each vendor's defaults live in its vendor table, not in scattered
  literals.

  | Role | Gemini default | Why | Menu alternatives |
  | --- | --- | --- | --- |
  | Implementer (`deliver`) | `gemini-4-neon` | Google's pick for multi-file changes, which plans usually are | `gemini-4-hydrogen` (half the price), `gemini-4-carbon` |
  | Reviewer (`cross-review`, factory verification) | `gemini-4-carbon` | strongest reasoning and deep verification, like Opus and GPT-6.1 Sol for the other vendors | `gemini-4-neon` |
  | Factory triage | `gemini-4-hydrogen` | a short read-only classification; cheap and on the free tier | none |

  Never use the CLI aliases. `prices.toml` carries Carbon's price from 1 December 2026, so
  cost caps err on the high side.
- **Approval:** the user approved this named plan with D1 to D4 on 2026-11-21. Delivery may
  publish a pull request. It never merges.

## Implementation sequence

- [x] **Spike, recorded in ADR-0034.** With the installed Gemini CLI, establish:
  1. resuming a session by its `session_id`;
  2. that `plan` mode can still read files;
  3. how the sandbox works: Docker or Podman on Linux, Seatbelt on macOS;
  4. how to deny paths and limit network access through settings or the policy engine;
  5. which environment variables carry credentials.

  Write ADR-0034 with the results and the decisions above. Mark in ADR-0017 that ADR-0034
  amends it.
- [x] **Vendor table, no behavior change.** Replace the two-vendor branches (`other = "claude"
  if harness == "codex" else "codex"` and similar) in `deliver.py`, `cross_review.py` and
  `factory.py` with one table per script. Each entry holds:
  - the command builder;
  - the credential environment variables;
  - the output parser;
  - the pricing source;
  - the default model per role and the models the menus offer (D4). The current literals
    (`sonnet`, `opus`, `gpt-6.1-sol`) move into the table unchanged.

  The scripts stay standalone and stdlib-only, so each keeps its own table. Tests: the
  existing suites pass unchanged, and a parametrized test checks that every vendor has every
  field.
- [x] **Gemini reviewer in `cross-review`.**
  - `--reviewer gemini` runs `gemini -p` with `--approval-mode plan`, `--output-format json`
    and `--skip-trust`, using the same review prompt and verdict contract.
  - The environment keeps only Gemini credentials.
  - Cost comes from the token stats and new entries in `prices.toml` for Hydrogen, Neon and Carbon (D4), with the source
    and the verification date.

  Tests with a fake runner: command shape, parsed verdict, cost, scrubbed environment.
- [x] **Gemini in the factory.**
  - Triage and verification run on `reviewer = "gemini"`: triage with
    `gemini-4-hydrogen`, verification with `gemini-4-carbon` (D4).
  - `_vendors` and the role prompts accept any vendor pair.
  - `required_secrets` asks for `GEMINI_API_KEY` when Gemini is used, and only for the two
    configured vendors.

  Tests: triage command and parsing for Gemini, verification command, the secret
  specifications per vendor pair.
- [x] **Gemini implementer in `deliver headless`.**
  - Each round runs `gemini -p` with `--approval-mode yolo` inside the sandbox from the spike,
    with the worktree as the only writable root, denied paths and no network beyond the
    allowlist.
  - Later rounds resume the session.
  - Usage and cost go to `usage.jsonl`.
  - If the installed CLI cannot deny paths, delivery refuses to start, as it does for old
    Codex versions.

  Tests: command per round, resume, deny refusal, cost parsing.
- [x] **Interactive `task deliver` menus.**
  - Gemini appears as a reviewer for every run, and as an implementer that selects headless
    mode.
  - `--harness` and `--reviewer` accept `gemini` in `start`, `headless` and `resume`.

  Tests: menu choices and the argument parser.
- [x] **Template.**
  - `agent_harnesses` gains `gemini`. It renders `.gemini/settings.json`, which sets
    `AGENTS.md` as the context file, enables folder trust for the project and denies
    credential paths.
  - The factory question follows D2.
  - The factory workflow:
    - installs only the CLIs of the configured vendors, pinned;
    - checks only their secrets;
    - passes `GEMINI_API_KEY` and `GEMINI_CLI_TRUST_WORKSPACE=true`.

  Tests: render with Gemini alone, Gemini with Claude, and all three. Workflow assertions per
  pair. The pairwise matrix gets the new value.
- [x] **This repository.**
  - `factory.toml` sets `reviewer = "gemini"`.
  - The root workflow matches the template.
  - The README, the factory guide, the `deliver` and `cross-review` skills and `docs/features.md`
    name Gemini where they name the vendors.
- [ ] **Final gate** `task check` and the configured review.

## Acceptance and verification

- `uv run pytest tests/test_cross_review.py -q`: a Gemini review passes and fails on fake
  output, and only Gemini credentials reach it.
- `uv run pytest tests/test_deliver.py tests/test_deliver_limits.py -q`: Gemini headless
  rounds, resume, deny refusal and cost.
- `uv run pytest tests/test_factory.py tests/test_factory_stages.py tests/test_factory_secrets.py -q`:
  triage and verification with Gemini, and secrets per vendor pair.
- `uv run pytest tests/template -q`: projects render with each harness combination, and the
  workflow matches the configured vendors.
- Final gate: `task check`.

External checks, which need the repository secrets and spend API credit:
- `task deliver` with Claude implementing and Gemini reviewing completes a small plan.
- `deliver headless` with Gemini implementing and Claude reviewing completes a small plan.
- After merge, an issue labeled `factory:ready` passes triage in Actions with Gemini as the
  reviewer.

## Rulings

- Vendor tables hold the credential variables and the default and menu models per vendor
  (`VENDOR_ENV`, `DEFAULT_MODELS`, `REVIEW_MODELS`, `VENDORS`) instead of full command-builder
  entries: the command builders differ in shape per script, so a field-per-vendor table would
  add indirection without removing branches. A parametrized test checks every vendor has every
  entry. Costs a later refactor if a fourth vendor needs more fields.
- The default reviewer is the first other vendor whose CLI is installed, in the order Codex,
  Claude, Gemini (D1). `deliver` checks `shutil.which`; the factory, which cannot see the
  runner, orders by vendor only. Costs a wrong default when the CLI is missing, which
  preflight reports.
- The template workflow and `factory.toml` render from the first two of Claude, Codex, Gemini
  in `agent_harnesses`, so Gemini is never the implementer there. This repository's workflow is
  the render for Claude and Gemini and a test keeps it equal.
- `gemini-cli` is pinned to 0.70.0, the probe version. Costs a bump when Google ships a
  fixed release.
- Gemini implements in `deliver headless` on macOS only, in single mode, with the cost cap
  checked after each round because the CLI has no per-round budget flag. The round, profile,
  proxy and parsing are built from the spike results and covered by fake-runner tests; no live
  Gemini round ran in the delivery sandbox. External acceptance (both `deliver` runs and the
  Actions triage) is pending, not passed.
- Spike ran outside the delivery sandbox, on macOS with the user's key, because the sandbox has
  neither the CLI nor network access to Google; results and the implementer design are in
  ADR-0034 under "Spike results" and "Gemini as implementer"; if wrong, Gemini rounds fail
  to start or commit, which the implementer tests and the external check catch.
