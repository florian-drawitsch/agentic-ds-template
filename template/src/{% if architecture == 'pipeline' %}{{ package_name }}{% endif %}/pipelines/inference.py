"""Inference pipeline: score features with the trained model."""

from __future__ import annotations

import polars as pl

from .framework import Pipeline, Step


def predict(features: pl.DataFrame, model: dict[str, float]) -> pl.DataFrame:
    """Predict the learned mean for each row."""
    return pl.DataFrame({"prediction": [model["mean"]] * features.height})


pipeline = Pipeline(
    "inference", [Step(predict, inputs=["features", "model"], outputs=["predictions"])]
)
