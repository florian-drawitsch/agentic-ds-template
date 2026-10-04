# entrypoints

Command line, API and dashboard. Thin layers that load settings, call `bootstrap`, run use cases
and present results.

- Framework code (Hydra, FastAPI, Streamlit) stays here. Request and response models live next
  to the route that owns them.
- No domain logic: move it into `domain` or `application` with tests.
