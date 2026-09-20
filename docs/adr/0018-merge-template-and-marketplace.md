# ADR-0018: Merge the template and the marketplace into one repository

- **Status:** Accepted
- **Date:** 2026-10-09
- **Supersedes:** ADR-0001
- **Amends:** ADR-0013, ADR-0016

## Context

ADR-0001 split the work into three repositories: a template, an agent kit and a showcase. After
ADR-0016 the agent kit holds little content:
- one pinned third-party plugin entry
- the `project` plugin with two skills
- a build script

That content is tightly coupled to the template. The `deliver` installer targets the template's
conventions (`scripts/agent/`, `task check`, `docs/plans/`), and the template renders the same
hooks and roles.

A spike on 2026-10-09 checked the marketplace mechanics with Claude Code 2.1.220 and Codex CLI
0.153.0, each in an isolated configuration directory:

- **One manifest:** Codex accepts the Claude-format plugin manifest (`.claude-plugin/plugin.json`),
  so a local plugin needs only one.
- **Pinning:** Claude Code pins third-party plugins by GitHub `repo`, `ref` and `sha`. Codex pins
  them by `url` and `ref`.
- **Local plugins:** both CLIs install local plugins from relative paths in the marketplace.

## Decision

- The repository `agentic-ds-template` is both the Copier template and the plugin marketplace.
  - Copier renders only `template/` (`_subdirectory`), so the marketplace files at the root
    (`catalog.toml`, `.claude-plugin/`, `.agents/plugins/`, `plugins/`) never reach generated
    projects.
- Generated projects register this repository as a marketplace at a pinned release tag. The
  Copier question `agent_kit_ref` becomes `marketplace_ref`.
- One version tag covers the template and the marketplace.
- The showcase project stays a separate repository, because it is a generated project.

## Alternatives considered

- **Keep two repositories:** separate CI, releases and READMEs for little content, and a version
  matrix between installer and template.
- **Copy the skills into projects through Copier:** skill fixes would need `copier update`,
  every project would carry copies, and third-party plugins could not be pinned.

## Consequences

- A change to a hook and to the template that renders it is one commit and one tag.
- Template tests can install the plugin from the same checkout and test the whole path.
- Other repositories can still add the marketplace without using the template.

## Verification

- `task check` validates the generated marketplaces and the plugin manifest.
- Template render tests will assert that rendered projects reference this repository at the
  pinned tag and contain no marketplace files.
