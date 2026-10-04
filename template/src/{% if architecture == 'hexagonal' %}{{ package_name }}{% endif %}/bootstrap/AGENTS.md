# bootstrap

Settings and wiring. The only place that chooses concrete adapters.

- `settings.py`: typed, immutable settings, independent of how they are loaded.
- `container.py`: builds adapters from settings. Add new adapters here, not in use cases.
