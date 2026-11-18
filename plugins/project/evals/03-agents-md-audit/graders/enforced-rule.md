---
type: llm
weight: 1
---

PASS if the answer recommends deleting the "Run `ruff format` ..." rule because hooks already enforce formatting.
FAIL if it keeps the rule, only rewords it, or does not mention it.
