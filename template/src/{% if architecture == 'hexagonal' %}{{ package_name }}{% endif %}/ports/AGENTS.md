# ports

Protocols for outbound dependencies: data sources, report stores, trackers, model stores.

- Define the narrowest interface the application needs, in domain vocabulary.
- Import only the domain. No implementations here; they live in `adapters/`.
