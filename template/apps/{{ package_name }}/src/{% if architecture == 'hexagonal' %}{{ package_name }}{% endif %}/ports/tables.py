"""Port for reading input tables."""

from __future__ import annotations

from typing import Protocol

import polars as pl


class TableSource(Protocol):
    """Provides the input table."""

    def load(self) -> pl.DataFrame:
        """Return the table."""
        ...
