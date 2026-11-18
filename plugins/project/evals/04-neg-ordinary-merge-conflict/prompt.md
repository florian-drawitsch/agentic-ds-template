---
max_turns: 8
timeout_seconds: 180
allowed_tools: [Read, Glob, Grep, Skill]
runs: 3
---

I merged my colleague's branch and got a conflict in `apps/demo_project/src/demo_project/features.py`. Do not run
anything; just show me the resolved code.

```python
def lag_features(frame, lags):
<<<<<<< HEAD
    return frame.with_columns([pl.col("y").shift(lag).alias(f"lag_{lag}") for lag in lags])
=======
    lags = sorted(set(lags))
    return frame.with_columns([pl.col("y").shift(lag).alias(f"y_lag_{lag}") for lag in lags])
>>>>>>> feature/lag-names
```

We agreed to keep my column names and the colleague's deduplication.
