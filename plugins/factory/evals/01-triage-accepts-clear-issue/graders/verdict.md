---
type: llm
weight: 1
---

PASS if the answer is JSON with verdict "accept" and at least one testable acceptance criterion about the `repository` key in the JSON output.
FAIL if the verdict is "needs-info", the criteria list is empty, or the answer is not the JSON object.
