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

## Spike results

Run on 2026-11-21 on macOS with Gemini CLI 0.70.0, `gemini-4-hydrogen`, and the key in
`~/.gemini/.env`. Each check ran a script in a throwaway repository and its worktree.

1. **Resume:** `gemini -r <session_id> -p <prompt>` continues the session and keeps its ID.
2. **Plan mode:** `--approval-mode plan` reads files and refuses shell commands. A reviewer
   therefore gets the diff in its prompt and cannot run tests.
3. **Sandbox on macOS:** `-s` with `SEATBELT_PROFILE=<name>`.

   | Setup | Key in shell | `~/.ssh`, `~/.gemini/.env` | Writes outside project | Network | Commit in a worktree |
   | --- | --- | --- | --- | --- | --- |
   | No sandbox | present | readable | allowed | open | works |
   | `strict-open` | present | blocked | blocked | open | not tested |
   | `strict-proxied` plus redaction plus allowlist proxy | absent | blocked | blocked | allowlist only | fails: `.git/worktrees/<name>/index.lock` is outside the project |
   | Custom profile: `strict-proxied` plus the shared git directory, with redaction and proxy | absent | blocked | blocked | allowlist only | works |

   - `--include-directories` combined with `-s` fails in 0.70.0: the relaunched process rejects
     the argument. Do not use it for the git directory.
   - On Linux the sandbox runs in Docker or Podman. This was not tested.
4. **Path denial and network:**
   - The policy engine has no path field. Its argument patterns do not stop shell commands, so
     paths are denied by the Seatbelt profile, not by policy rules.
   - `strict-proxied` allows outbound traffic only to `localhost:8877`. Gemini CLI starts the
     command in `GEMINI_SANDBOX_PROXY_COMMAND` outside the sandbox and sets `HTTPS_PROXY`
     inside it.
   - A stdlib CONNECT proxy that allowed `generativelanguage.googleapis.com`,
     `play.googleapis.com` and the `deliver.toml` hosts passed `pypi.org` and refused
     `example.com`.
5. **Credentials:**
   - Gemini CLI reads `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`
     and `GOOGLE_CLOUD_PROJECT`, and it loads `~/.gemini/.env`.
   - Shell commands inherit the key unless `security.environmentVariableRedaction` is
     enabled with the key names in `blocked`.
   - OAuth logins live under `~/.gemini/`. The strict profiles deny writes there and do not
     list it as readable.

## Gemini as implementer

The spike above used Gemini's own Seatbelt sandbox, which exists only on macOS. A second probe on
2026-11-21 replaced it with Anthropic's `sandbox-runtime` (`srt` 0.0.83, bubblewrap 0.11.1,
Gemini CLI 0.70.0, `gemini-4-hydrogen`, eight runs on `ubuntu-latest`). `srt` uses Seatbelt on
macOS and bubblewrap with a network proxy on Linux, and reads the rules `deliver` already
builds for Claude.

| Check, from the agent's shell | Result |
| --- | --- |
| Gemini key | absent: Gemini's shell tool drops it |
| Other secret variables (`credentials.envVars`, mode `deny`) | withheld |
| `~/.ssh` (`denyRead`) | blocked |
| Writes outside the project | blocked |
| Writes inside the project | allowed |
| Commit from a worktree, shared `.git` in `allowWrite` | works |
| Gemini's own API calls | work through `srt`'s proxy and allowlist |
| `example.com`, `pypi.org` | blocked: the shell tool drops the proxy variables, so agent commands have no network |

Runner preparation on Ubuntu 24.04:

1. `apt-get install bubblewrap socat ripgrep`.
2. `sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`.
3. `apparmor_parser -R /etc/apparmor.d/bwrap-userns-restrict`, or `srt`'s seccomp helper fails.
4. Create `~/.gemini`, `~/.cache`, `~/.npm` and `/tmp/claude` first.

Steps 2 and 3 weaken the host's hardening. That is acceptable on a throwaway hosted runner, not
on a developer machine. macOS needs none of them.

Decisions:

- **D1:** `srt` is the Gemini implementer's sandbox on macOS and Linux. The custom Seatbelt
  profile, `GEMINI_SANDBOX_PROXY_COMMAND` and the stdlib proxy are gone.
- **D2:** the `srt` settings come from the builder that also feeds Claude's sandbox settings:
  writable roots, protected git paths, denied paths, allowed hosts, credential files and
  variables. The Gemini API hosts are added. `GEMINI_API_KEY` is not withheld from the `gemini`
  process; Gemini's redaction setting keeps it out of the agent's shell.
- **D3:** agent shell commands have no network. `uv sync` in the setup command installs
  dependencies before the agent starts. A plan that needs a new dependency blocks the run.
- **D4:** `srt` is pinned in `deliver.toml` (`srt_version`) and in the factory workflow.
  `deliver doctor` reports a missing `srt`, `bwrap`, `socat` or `rg` when `factory.toml` selects
  Gemini as the implementer.
- **D5:** the factory workflow prepares the runner (steps 1 to 4) only when `factory.toml`
  selects Gemini as the implementer.

Each round writes `srt.json` and Gemini's settings to the run directory, outside the worktree,
points `GEMINI_CLI_SYSTEM_SETTINGS_PATH` at the settings, and runs
`srt --settings <run-dir>/srt.json -- gemini -m <model> --approval-mode yolo --output-format
json --skip-trust -p`, or with `-r <session_id>` for later rounds. Delivery refuses Gemini as
implementer when `srt` is missing, or on Linux when `bwrap`, `socat` or `rg` is.

Reviewing works everywhere. Plan mode alone is not read-only, because Gemini CLI 0.70.0 approves
`exit_plan_mode` in headless runs and switches to YOLO. Reviews and triage therefore also load an
admin policy that denies mode changes, writes, shell commands and network tools. The review
inputs go on standard input, since the file tools cannot read the Git directory.

## Consequences

- Gemini reviews in `cross-review`, the factory (triage and verification) and `deliver`.
- The factory workflow is rendered per vendor pair and installs only the pinned CLIs it uses.
- Gemini implements in `deliver headless` on macOS and Linux, inside `srt`. The factory orders
  vendors Claude, Codex, Gemini, so Gemini implements there only when `factory.toml` says so.
  The workflow then needs a prepared runner (D5).
- Agent shell commands have no network, so a plan that adds a dependency blocks the run.
- Gemini CLI updates can change the sandbox behaviour. Rerun these checks when bumping the
  pinned version.
