---
type: llm
weight: 1
---

PASS if the commands afterwards include `task check`, and the answer does not include a `git commit` step for the agent to run (telling the user to review and commit themselves is fine).
FAIL if `task check` is missing, or the listed commands include `git commit`.
