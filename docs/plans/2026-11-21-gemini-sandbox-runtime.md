# Gemini implementer in sandbox-runtime

Status: Approved for implementation on 2026-11-21.

## Goal and scope

Gemini can implement in `deliver headless` on Linux as well as on macOS. The factory can then
run Gemini as the implementer on GitHub's `ubuntu-latest` runners.

Both systems use one sandbox: Anthropic's open-source `sandbox-runtime` (`srt`). It uses
Seatbelt on macOS and bubblewrap with a network proxy on Linux. It replaces the custom Seatbelt
profile and the stdlib allowlist proxy that `deliver.py` writes for Gemini today. It reads the
same rules `deliver` already builds for Claude's sandbox.

Non-goals:
- network access from the Gemini agent's own shell commands (see the findings);
- masking the Gemini key with TLS termination;
- `srt` for Claude or Codex, which keep their own sandboxes;
- Windows.

## Findings from the probe (2026-11-21)

Eight probe runs on `ubuntu-latest`, with `sandbox-runtime` 0.0.83, bubblewrap 0.11.1 and
Gemini CLI 0.70.0 using `gemini-4-hydrogen`:

| Check, from the agent's shell | Result |
| --- | --- |
| Gemini key | absent; Gemini's shell tool drops it, even without the redaction setting |
| Other secret variables (`srt` `credentials.envVars`, mode `deny`) | withheld |
| `~/.ssh` (`denyRead`) | blocked |
| Writes outside the project | blocked |
| Writes inside the project | allowed |
| Commit from a worktree, with the shared `.git` in `allowWrite` | works |
| `uv` on the runner | available |
| Gemini's own API calls | work through `srt`'s proxy and allowlist |
| `example.com` | blocked |
| `pypi.org` | blocked: Gemini's shell tool drops the proxy variables, so agent commands have no network at all |

Runner preparation that made it work:

1. `apt-get install bubblewrap socat ripgrep`.
2. `sysctl -w kernel.apparmor_restrict_unprivileged_userns=0`. Ubuntu 24.04 strips
   capabilities from unprivileged user namespaces.
3. `apparmor_parser -R /etc/apparmor.d/bwrap-userns-restrict`. Without this, `srt`'s seccomp
   helper fails with `write /proc/self/setgroups ... Permission denied`.
4. Create `~/.gemini`, `~/.cache`, `~/.npm` and `/tmp/claude` first. `srt` sets `TMPDIR` to
   `/tmp/claude`, and it cannot bind a writable path that does not exist.

Steps 2 and 3 weaken the host's hardening. That is acceptable on a throwaway hosted runner, not
on a developer machine. On macOS none of the four steps is needed.

## Prerequisites and decisions

- Depends on ADR-0034 and the merged Gemini harness work (#19, PR #41).
- **D1:** `srt` is the Gemini implementer's sandbox on macOS and Linux. The custom Seatbelt
  profile, `GEMINI_SANDBOX_PROXY_COMMAND` and the stdlib proxy are removed.
- **D2:** the `srt` settings come from the same builder as Claude's sandbox settings: writable
  roots, protected git paths as `denyWrite`, denied paths, allowed hosts, and credential files
  and variables as `deny`. Two additions:
  - the Gemini API hosts are added to `allowedDomains`;
  - `GEMINI_API_KEY` is not withheld from the `gemini` process, which needs it. Gemini's
    redaction setting stays as a second layer for the agent's shell.
- **D3:** agent shell commands have no network. Dependencies come from the setup command
  (`uv sync`), which runs before the agent starts. A plan that adds a dependency blocks the run
  with a clear reason, instead of failing on a network error.
- **D4:** `srt` is pinned in `deliver.toml` and in the factory workflow, like the agent CLIs.
  `deliver doctor` reports it missing when Gemini is configured as the implementer.
- **D5:** the factory workflow prepares the runner (findings steps 1 to 4) only when Gemini is
  the implementer.
- **Approval:** the user approved this named plan with D1 to D5 on 2026-11-21. Delivery may
  publish a pull request. It never merges.

## Implementation sequence

- [x] **Shared sandbox rules.** Split `claude_sandbox_settings` into a builder that returns
  the rule set (filesystem, network, credentials) and a Claude wrapper that adds Claude's keys
  (`enabled`, `autoAllowBashIfSandboxed`, `allowUnsandboxedCommands`, `failIfUnavailable`).
  Claude's settings stay byte-identical. Tests: the existing Claude settings tests pass
  unchanged, and the shared builder is tested on its own.
- [x] **`srt` settings for Gemini.**
  - From the shared rules, with the Gemini hosts added and `GEMINI_API_KEY` left out of the
    credential variables.
  - Written to the run directory, outside the worktree.

  Tests: hosts, denied paths, protected git paths, the shared `.git` writable, and the key not
  withheld.
- [x] **Gemini rounds run under `srt`.**
  - Each round runs `srt --settings <run-dir>/srt.json -- gemini ...` without Gemini's own
    `-s` sandbox.
  - `require_gemini_implementer` checks for `srt`, plus bubblewrap, socat and ripgrep on
    Linux, and names what is missing.
  - The round creates `~/.gemini`, `~/.cache` and the `srt` temp directory before starting.

  Tests with a fake runner: the command per round, resume, refusal when `srt` is missing,
  directories created.
- [x] **Remove the Seatbelt path.** Delete `gemini_bundle_profile`, `seatbelt_quote`,
  `gemini_denials`, the profile copy and its `.git/info/exclude` entry, the proxy script and
  the proxy variables. Tests that covered them are replaced by the tests above.
- [x] **No network for agent commands (D3).** The headless prompt for a Gemini round says
  that shell commands have no network and that a needed new dependency is a reason to block.
  Test: the prompt text for Gemini rounds.
- [x] **Pin and doctor (D4).** `srt` version in `deliver.toml` and the template's copy, and in
  `deliver doctor`. Tests: doctor output with and without `srt`.
- [x] **Factory workflow (D5).** When the implementer is Gemini, the template workflow:
  - installs the pinned `srt` with the CLIs;
  - adds a "Prepare the sandbox" step with findings steps 1 to 4.

  The root workflow follows its render. Tests: render with Gemini implementing (step present,
  pinned `srt`) and with Claude implementing (step absent). zizmor stays clean.
- [x] **Docs.**
  - ADR-0034 records the probe results, D1 to D5, and that Gemini implements on Linux too.
  - `deliver`'s skill text, the factory guide and the feature index describe the
    requirements.
- [x] **Final gate** `task check` and the configured review.

## Acceptance and verification

- Claude's sandbox settings are unchanged. Check:
  `uv run pytest tests/test_deliver.py -q -k sandbox`.
- A Gemini round's command runs under `srt` with the generated settings, on macOS and Linux
  alike. Check: `uv run pytest tests/test_deliver.py -q -k gemini`.
- Delivery refuses Gemini as implementer when `srt` or a Linux dependency is missing, and names
  it. Check: same file.
- The factory workflow prepares the runner only for a Gemini implementer. Check:
  `uv run pytest tests/template/test_agent_wiring.py -q`.
- Final gate: `task check`, run by the delivery controller.

External checks, which need the repository's `GEMINI_API_KEY` secret and spend free-tier or
credit usage:

- `deliver headless` with Gemini implementing and Claude reviewing completes a small plan on
  `ubuntu-latest` and on macOS.
- A factory ticket with `harness = "gemini"` and `reviewer = "claude"` reaches In review in
  Actions.

## Rulings

- Where the `srt` version is pinned: `srt_version` under `[sandbox]` in the template's
  `deliver.toml`, plus the workflow's npm install line. Costs a double edit on a bump.
- "Implementer is Gemini" cannot be known at render time, because the template orders vendors
  Claude, Codex, Gemini. The workflow installs `srt` and has a "Prepare the sandbox" step whenever
  Gemini is one of the two vendors; the step exits early unless `factory.toml` has
  `harness = "gemini"`. If wrong, a runner pays one skipped step.
- `deliver doctor` reads the implementer from `factory.toml`; other projects get no check
  beyond preflight, which refuses at launch.
- The `srt` settings also make `~/.gemini` writable, which Gemini needs for sessions.
- Final gate: this run verified focused checks only (ruff, ty, and the deliver, template, factory
  and cross-review tests). The delivery controller runs `task check` and the review outside the
  sandbox. The two live checks under "External checks" are pending, not passed: they need the
  `GEMINI_API_KEY` secret and spend usage.
