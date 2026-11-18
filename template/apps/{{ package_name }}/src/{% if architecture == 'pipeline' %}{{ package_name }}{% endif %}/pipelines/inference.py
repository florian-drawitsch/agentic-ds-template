"""Inference pipeline: score features with the trained model."""

from __future__ import annotations

import polars as pl

from .graph import Pipeline, node, pipeline


def predict(features: pl.DataFrame, model: dict[str, float]) -> pl.DataFrame:
    """Predict the learned mean for each row."""
    return pl.DataFrame({"prediction": [model["mean"]] * features.height})


def create_pipeline() -> Pipeline:
    """Return the inference pipeline."""
    return pipeline(
        [node(predict, inputs=["features", "model"], outputs="predictions", tags=["inference"])]
    )
