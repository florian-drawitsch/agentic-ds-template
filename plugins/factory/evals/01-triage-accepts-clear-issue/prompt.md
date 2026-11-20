---
max_turns: 12
timeout_seconds: 300
allowed_tools: [Read, Glob, Grep, Skill]
runs: 3
---

I run the ticket factory in this repository. Do not run any commands. Act as the factory triage role for the issue below and answer with the triage JSON only.

Title: `factory.py status --json` should include the repository name
Body: Running `uv run --script plugins/factory/skills/factory/scripts/factory.py status --json` prints counts, spend and the next action. Add a `repository` key with the `owner/name` string. Expected: the key is present, and the text output is unchanged. A test in `tests/test_factory.py` covers it.
