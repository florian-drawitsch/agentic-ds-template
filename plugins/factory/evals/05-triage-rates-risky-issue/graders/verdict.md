---
type: llm
weight: 1
---

PASS if the answer is JSON with verdict "accept", at least one testable acceptance criterion, and `"risk": "high"` with a one-sentence `risk_reason`.
FAIL if `risk` is missing or "low", or the answer is not the JSON object.
