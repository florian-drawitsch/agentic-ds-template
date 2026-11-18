# adapters

Implementations of the ports: files, databases, APIs, tracking, model stores.

- Each adapter implements one port and maps external formats to domain types.
- Heavy libraries stay in their adapter; import-linter enforces it (for example MLflow only in
  `adapters/tracking.py`).
- Test adapters against temporary files or local services, not mocks of the library.
