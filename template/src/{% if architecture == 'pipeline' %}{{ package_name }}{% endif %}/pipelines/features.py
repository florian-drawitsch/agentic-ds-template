"""Feature pipeline: turn the raw table into model-ready features."""

from __future__ import annotations

import polars as pl

from .framework import Pipeline, Step


def build_features(raw: pl.DataFrame, target: str) -> pl.DataFrame:
    """Keep numeric columns and the rows that have a target, in their original order."""
    numeric = [name for name, dtype in raw.schema.items() if dtype.is_numeric()]
    return raw.select(numeric).drop_nulls(target)


pipeline = Pipeline(
    "features", [Step(build_features, inputs=["raw"], outputs=["features"], parameters=["target"])]
)
