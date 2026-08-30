# ADR-0008: Build docs with Sphinx, MyST and autoapi

- **Status:** Accepted
- **Date:** 2026-10-09

## Context

Documentation must combine prose in Markdown, ADRs and an API reference. Committing generated
API stubs caused churn in every refactoring.

## Decision

- Use Sphinx with MyST Markdown and sphinx-autoapi. Theme: Furo.
- Nothing generated is committed. `task docs:serve` runs a live server and
  `task docs:build` builds static HTML.

## Alternatives considered

- The MkDocs family: good UX, but Sphinx was preferred, and MyST covers Markdown.
- autodoc with committed stubs: churn.

## Consequences

- Docstrings follow the Google convention, enforced by ruff.

## Verification

`task docs:build` with warnings as errors runs in CI.
