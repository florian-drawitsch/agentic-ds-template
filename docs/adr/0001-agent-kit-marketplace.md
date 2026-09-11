# ADR-0001: Distribute generic skills through the agent-kit marketplace

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

Claude Code and Codex both support plugin marketplaces that bundle skills. The marketplace and
manifest formats overlap, and Codex reads Claude-style marketplace files as a compatible source.

## Decision

- `agent-kit` is one marketplace with plugins `core`, `ds` and one per archetype.
- It ships manifests for both harnesses, generated from one source.
- Generated projects register the marketplace at a pinned ref:
  - Claude Code: `extraKnownMarketplaces` and `enabledPlugins` in `.claude/settings.json`.
  - Codex: `.agents/plugins/marketplace.json` and plugin entries in `.codex/config.toml`.
- Project-local skills live in `.agents/skills`, and `.claude/skills` links to it.
- Skills adapted from upstream keep their license and a provenance note.

## Alternatives considered

- Vendoring skills into each project through Copier: drift, and updates require a template
  release.
- A third-party cross-harness skill installer: one more dependency for a problem both harnesses
  now solve natively.

## Consequences

- Skills appear under a plugin prefix (for example `core:implement-feature`).
- Plugin-bundled hooks are not used, because Codex runs them only for manually installed desktop
  plugins.

## Verification

`claude plugin validate` and the Codex equivalent run in kit CI. A smoke test installs the
marketplace into a rendered project for each harness and lists the enabled skills.
