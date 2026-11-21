# ADR-0034: Add Gemini as an optional third harness

- **Status:** Accepted
- **Date:** 2026-11-21

## Context

ADR-0017 requires every plugin to work in Claude Code and Codex. The factory and `deliver` need
two different vendors, so a repository without OpenAI credit cannot run them. Gemini CLI
authenticates from `GEMINI_API_KEY`, and a Google AI Pro plan adds API credit.

A probe on 2026-11-21 (Gemini CLI 0.70.0, GitHub Actions) showed:

- `gemini -p <prompt> --output-format json` returns `session_id`, `response` and
  `stats.models.<model>.tokens` (input, cached, candidates, thoughts).
- `--approval-mode plan` is read-only; `auto_edit` and `yolo` allow edits.
- Headless runs exit with code 55 in an untrusted folder; `--skip-trust` or
  `GEMINI_CLI_TRUST_WORKSPACE=true` fixes it.
- The CLI's aliases (`pro`, `flash`, `auto`) point at older models, so defaults name exact IDs.
- Google replaced Gemini CLI with Antigravity CLI for unpaid and Google One users on
  2026-06-18. Use with an API key still works. This is a risk to recheck.

## Decision

Gemini is an optional third harness. This ADR amends ADR-0017.

- Skills and hooks stay required for Claude Code and Codex. Gemini needs only the delivery,
  review and factory paths.
- The rule is "implementer and reviewer are different vendors". The default reviewer is the
  first other vendor whose CLI is installed, in the order Codex, Claude, Gemini.
- The factory question needs GitHub CI and at least two harnesses.
- Gemini runs headless only. It has no completion guard for interactive sessions.
- Model defaults live in each script's vendor table. Never use CLI aliases.

  | Role | Default | Menu alternatives |
  | --- | --- | --- |
  | Implementer (`deliver`) | `gemini-4-neon` | `gemini-4-hydrogen`, `gemini-4-carbon` |
  | Reviewer (`cross-review`, factory verification) | `gemini-4-carbon` | `gemini-4-neon` |
  | Factory triage | `gemini-4-hydrogen` | none |

- `prices.toml` carries Carbon's price from 2026-12-01 (4.00 / 0.20 / 20.00 per million
  tokens), so cost caps err on the high side.

## Status of the spike

Not run: the Gemini CLI was not installed in the delivery sandbox. These stay open, and Gemini
as an implementer in `deliver headless` waits for them:

1. resuming a session by its `session_id`;
2. that `plan` mode can still read files;
3. how the sandbox works (Docker or Podman on Linux, Seatbelt on macOS);
4. how to deny paths and limit network access through settings or the policy engine;
5. which environment variables carry credentials beyond `GEMINI_API_KEY` and `GOOGLE_API_KEY`.

Until item 4 is answered, `deliver` offers Gemini only as a reviewer, and the template's
`.gemini/settings.json` and `.geminiignore` are a best effort that has not been tested against
a live CLI.

## Consequences

- Gemini reviews in `cross-review`, the factory (triage and verification) and `deliver`.
- The factory workflow is rendered per vendor pair and installs only the pinned CLIs it uses.
- Gemini cannot implement yet. The factory orders vendors Claude, Codex, Gemini, so Gemini is
  never the implementer.
