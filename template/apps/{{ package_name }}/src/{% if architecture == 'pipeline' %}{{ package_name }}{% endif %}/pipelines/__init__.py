"""Pipeline registry. `__default__` combines every pipeline into one graph."""

from __future__ import annotations

from . import features, inference, training
from .graph import Pipeline

PIPELINES: dict[str, Pipeline] = {
    "features": features.create_pipeline(),
    "training": training.create_pipeline(),
    "inference": inference.create_pipeline(),
}
PIPELINES["__default__"] = PIPELINES["features"] + PIPELINES["training"] + PIPELINES["inference"]
