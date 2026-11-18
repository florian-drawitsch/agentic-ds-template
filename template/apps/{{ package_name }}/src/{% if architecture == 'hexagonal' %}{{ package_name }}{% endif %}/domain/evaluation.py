"""Evaluation: splits, metrics and the report of a run."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl


@dataclass(frozen=True)
class Report:
    """Evaluation result of one run."""

    model: str
    rows_train: int
    rows_test: int
    mae: float


def split_holdout(table: pl.DataFrame, fraction: float = 0.2) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Split a time-ordered table into training rows and the last `fraction` as hold-out.

    Raises:
        ValueError: If the fraction leaves either part empty.
    """
    test_rows = round(table.height * fraction)
    if not 0 < test_rows < table.height:
        raise ValueError(f"A hold-out fraction of {fraction} leaves no training or test rows")
    return table.head(table.height - test_rows), table.tail(test_rows)


def mean_absolute_error(actual: pl.Series, predicted: pl.Series) -> float:
    """Return the mean absolute error.

    Raises:
        ValueError: If the series differ in length or are empty.
        TypeError: If the values are not numeric.
    """
    if actual.len() != predicted.len() or actual.len() == 0:
        raise ValueError("Actual and predicted values must be non-empty and of equal length")
    value = (actual - predicted).abs().mean()
    if not isinstance(value, int | float):
        raise TypeError("Actual and predicted values must be numeric")
    return float(value)
