---
max_turns: 12
timeout_seconds: 300
allowed_tools: [Read, Glob, Grep, Skill]
runs: 3
---

Our data science project was generated from the agentic-ds-template Copier template. I just ran
`task update-template -- --defaults` and Copier left conflicts in three files. Do not run any
commands. Tell me how you resolve each file and list the exact commands I should run afterwards.

`pyproject.toml`:

```toml
dependencies = [
<<<<<<< before updating
    "polars>=1.30",
    "lightgbm>=4.6",
=======
    "polars>=1.34",
>>>>>>> after updating
]
```

`uv.lock` has conflict markers in about 40 places.

`scripts/agent/deliver.py`:

```python
<<<<<<< before updating
DEFAULT_MAX_COST = 25.0  # raised locally for long runs
=======
DEFAULT_MAX_COST = 10.0
>>>>>>> after updating
```
