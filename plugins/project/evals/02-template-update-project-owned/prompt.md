---
max_turns: 10
timeout_seconds: 240
allowed_tools: [Read, Glob, Grep, Skill]
runs: 3
---

We updated our project from the agentic-ds-template Copier template with
`task update-template -- --defaults`. The new template version changed its `README.md`, its
`tests/conftest.py` and the starter text in the `## Project` section of `AGENTS.md`, but none of
those changes arrived in our repository. Is the update broken? Should I copy the new template
versions of those files over ours? Do not run anything.
