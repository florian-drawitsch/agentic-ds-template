# ADR-0021: Run Hydra through its compose API

- **Status:** Accepted
- **Date:** 2026-10-09
- **Amends:** ADR-0007

## Context

The first rendered project failed at `task run`. Python 3.14's argparse validates help strings,
and hydra-core 1.3.7, the latest stable release on 2026-10-09, passes a lazy help object that
fails that check. `@hydra.main` therefore crashes before any project code runs. Only 1.4
development builds exist beyond 1.3.7.

## Decision

- Generated entry points compose the configuration with `initialize_config_dir` and `compose`,
  behind a small argparse front end.
- Positional arguments are Hydra overrides (`task run -- data.target=y`).
- `--multirun` expands comma-separated values into one run per combination, using Hydra's own
  override parser (`task sweep -- data.target=a,b`).
- Hydra stays the default configuration system and is still imported only in entry points.

## Alternatives considered

- **Default to Python 3.13 when Hydra is chosen:** ties the Python version to a library bug.
- **Patch Hydra's help object at import time:** fragile across Hydra releases.

## Consequences

- No Hydra output directories, working-directory changes or launcher plugins. Runs are tracked
  in MLflow instead.
- The command line is testable end to end without subprocesses.
- Revisit `@hydra.main` when a stable Hydra release supports Python 3.14.

## Verification

Generated tests run the CLI with overrides and check sweep expansion. The end-to-end template
tests run `task run` in every rendered combination that uses Hydra.
