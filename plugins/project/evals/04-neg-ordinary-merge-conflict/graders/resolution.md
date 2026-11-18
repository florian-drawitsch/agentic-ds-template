---
type: llm
weight: 1
---

PASS if the resolved function deduplicates and sorts `lags` (`sorted(set(lags))`) and names the columns `f"lag_{lag}"`, with no conflict markers.
FAIL if the columns are named `y_lag_{lag}`, the deduplication is missing, or markers remain.
