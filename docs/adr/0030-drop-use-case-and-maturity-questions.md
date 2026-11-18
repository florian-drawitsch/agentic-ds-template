# ADR-0030: Drop the use-case and maturity questions

- **Status:** Accepted
- **Date:** 2026-11-18
- **Amends:** ADR-0003, ADR-0013, ADR-0023, ADR-0029

## Context

ADR-0003 introduced `use_case` and `maturity`. In rendered projects they deliver little:

- **`use_case`** adds a dependency preset, four AGENTS.md rules and an evaluation protocol stub.
  - No generated code imports the preset dependencies. `torch`, `lightning` or `cvxpy` only slow
    `uv sync` and CI.
  - Every use case gets the same CSV, mean-baseline and holdout skeleton. For computer vision,
    GenAI or optimization the skeleton does not fit the problem.
  - The rules are textbook advice capable models already know. ADR-0013 dropped archetype
    plugins for the same reason.
  - ADR-0013 promised tested domain code per archetype, such as a backtest split generator.
    None exists.
  - One answer cannot describe a repository with several apps of different kinds (ADR-0031).
  - Seven values multiply the render and end-to-end matrix.
- **`maturity`** sets the coverage minimum (80 or 0) and the default `architecture`.
  - The default is a second way to answer `architecture`.
  - A proof of concept gets no coverage gate at all, although agent-written code needs one most.
  - Maturity changes over a project's life; a Copier answer stays fixed.

The evaluation protocol stub is the useful part: it makes a project decide its baselines, metrics
and acceptance criteria before modeling.

## Decision

- Remove the `use_case` and `maturity` questions.
- Render no preset dependencies. Agents add libraries with `uv add` when the work needs them.
- Render one generic `docs/method/evaluation-protocol.md` with design, baselines, metrics and
  acceptance sections. It stays project-owned.
- Remove the use-case rules section from AGENTS.md. The review rule that blocked a merge for
  breaking those rules now blocks a merge for results that contradict the evaluation protocol.
- Every project gets a branch coverage minimum of 80 %. A project lowers it by editing its
  configuration; `copier update` keeps that edit.
- `architecture` defaults to `simple`. Each choice's help text says when to pick it.
- Per-domain guidance moves to the planned `kickoff` skill, which writes project-specific rules
  into project-owned documents. It ships only with evals that beat the no-skill baseline.

## Alternatives considered

- **Keep `use_case` but generate real domain code per archetype:** seven skeletons to maintain,
  still one answer per repository.
- **Make `use_case` a multiselect:** keeps the unused dependencies and generic rules.
- **Keep `maturity` for the coverage gate only:** a single number in `pyproject.toml` is easier
  to change than a Copier answer.

## Consequences

- Fewer questions and a smaller test matrix.
- Copier no longer asks the questions; leftover keys in existing answer files have no effect. An
  update removes the use-case rules from AGENTS.md and sets the coverage minimum to 80 %.
- The per-archetype protocol text is no longer rendered. It survives in git history as input for
  the `kickoff` skill.

## Verification

- Render tests assert that the questions are gone, that AGENTS.md has no use-case section, that
  the evaluation protocol is generic and that `fail_under` is 80.
- The end-to-end matrix no longer varies `use_case` or `maturity`.
