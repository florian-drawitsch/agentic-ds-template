"""Port for experiment tracking."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol


class Tracker(Protocol):
    """Records the parameters, metrics and artifacts of one run."""

    def record(
        self, params: Mapping[str, object], metrics: Mapping[str, float], artifacts: Sequence[Path]
    ) -> None:
        """Record one run."""
        ...
