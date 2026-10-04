"""Pipelines in their default run order."""

from __future__ import annotations

from . import features, inference, training
from .framework import Pipeline

PIPELINES: dict[str, Pipeline] = {
    p.name: p for p in (features.pipeline, training.pipeline, inference.pipeline)
}
