"""Baseline model every later model must beat."""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl


@dataclass
class MeanBaseline:
    """Predict the training mean of the target."""

    mean: float | None = None

    def fit(self, target: pl.Series) -> MeanBaseline:
        """Learn the mean of the target.

        Raises:
            TypeError: If the target has no numeric values.
        """
        value = target.mean()
        if not isinstance(value, int | float):
            raise TypeError("The target needs numeric values")
        self.mean = float(value)
        return self

    def predict(self, rows: int) -> pl.Series:
        """Predict the learned mean for each row.

        Raises:
            RuntimeError: If the model has not been fitted.
        """
        if self.mean is None:
            raise RuntimeError("Fit the model before predicting")
        return pl.Series("prediction", [self.mean] * rows)
