# ADR-0032: Run workspace contracts with released import-linter

- **Status:** Accepted
- **Date:** 2026-11-18
- **Amends:** ADR-0031

## Context

ADR-0031 specified passing all app and package configuration files as positional arguments.
Generated projects resolve import-linter 2.16 under the seven-day supply-chain cooldown.
That version rejects positional configuration files. Upstream lists that interface under
unreleased changes in its [release notes](https://import-linter.readthedocs.io/en/latest/release_notes/).

## Decision

The hook selects `apps/*/pyproject.toml` and `packages/*/pyproject.toml` and runs
`lint-imports --config <file>` once per file. It stops on the first failure. Unmatched globs
are omitted. Each invocation runs with all workspace members installed.

## Consequences

Every member's contracts are enforced using the released interface. Contract graphs may be
built more than once. No unreleased dependency, version override or cooldown exception is needed.

## Verification

The render test checks the hook's config selection. Focused runtime checks run both apps'
contracts. The slow forbidden-import test verifies that the lint task rejects a violation.
