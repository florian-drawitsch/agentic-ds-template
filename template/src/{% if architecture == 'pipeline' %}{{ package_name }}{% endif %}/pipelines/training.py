"""Training pipeline: fit the baseline and evaluate it on a time-ordered hold-out."""

from __future__ import annotations

from typing import Any

import polars as pl

from .framework import Pipeline, Step


def split_holdout(
    features: pl.DataFrame, fraction: float = 0.2
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Split a time-ordered table into training rows and the last `fraction` as hold-out.

    Raises:
        ValueError: If the fraction leaves either part empty.
    """
    test_rows = round(features.height * fraction)
    if not 0 < test_rows < features.height:
        raise ValueError(f"A hold-out fraction of {fraction} leaves no training or test rows")
    return features.head(features.height - test_rows), features.tail(test_rows)


def fit_baseline(train: pl.DataFrame, target: str) -> dict[str, float]:
    """Fit the mean baseline every later model must beat.

    Raises:
        TypeError: If the target has no numeric values.
    """
    value = train[target].mean()
    if not isinstance(value, int | float):
        raise TypeError("The target needs numeric values")
    return {"mean": float(value)}


def evaluate(test: pl.DataFrame, model: dict[str, float], target: str) -> dict[str, Any]:
    """Return the evaluation report of the model on the hold-out.

    Raises:
        TypeError: If the target has no numeric values.
    """
    error = (test[target] - model["mean"]).abs().mean()
    if not isinstance(error, int | float):
        raise TypeError("The target needs numeric values")
    return {"model": "mean_baseline", "rows_test": test.height, "mae": float(error)}


pipeline = Pipeline(
    "training",
    [
        Step(split_holdout, inputs=["features"], outputs=["train", "test"]),
        Step(fit_baseline, inputs=["train"], outputs=["model"], parameters=["target"]),
        Step(evaluate, inputs=["test", "model"], outputs=["report"], parameters=["target"]),
    ],
)
