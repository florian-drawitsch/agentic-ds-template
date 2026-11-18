---
type: llm
weight: 1
---

PASS if the answer says not to merge the `uv.lock` conflict hunks by hand and regenerates the lockfile with `uv lock`.
FAIL if it tells the user to edit the lockfile hunks manually or never runs `uv lock`.
