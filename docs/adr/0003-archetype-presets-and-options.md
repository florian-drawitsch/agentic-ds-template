# ADR-0003: Model questions as archetype presets plus capability options

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

Past projects fall into six archetypes: forecasting, anomaly detection, optimization, computer vision, GenAI and tabular prediction. They share almost all tooling
and differ in dependencies, evaluation risks and delivery interfaces.

## Decision

- `use_case` selects an archetype preset. It sets the dependency group, the domain rules section
  in AGENTS.md, `docs/method/evaluation-protocol.md` and the default agent plugin.
- Independent options cover shape: `maturity`, `architecture`, `layout`, `interfaces`, `config`,
  `dataframes`, `tracking`, `notebooks`, `container`, `ci`.
- Presets only set defaults; every option can still be changed.
- Optional free-text context questions seed the context documents.

## Alternatives considered

- One template per archetype: duplicated tooling, divergent updates.
- No archetypes: generic AGENTS.md without the domain rules that prevent typical modeling errors.

## Consequences

- Domain rules per archetype must stay short and checkable, for example "split forecasting data
  by time, never randomly".
- The answer space is large, so testing uses a pairwise matrix (ADR-0011).

## Verification

Render tests assert one rendered project per archetype passes `task check`, and that each
archetype's AGENTS.md section and method stub exist.
