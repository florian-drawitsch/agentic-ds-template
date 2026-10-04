"""Data catalog: maps dataset names to files.

Datasets listed in the catalog are read from and written to their files. Every other output stays
in memory for the duration of one run, so intermediate results need no file.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import polars as pl


def read(path: Path) -> Any:
    """Read a table (CSV, Parquet) or a JSON object.

    Raises:
        ValueError: If the file format is not supported.
    """
    if path.suffix == ".csv":
        return pl.read_csv(path)
    if path.suffix == ".parquet":
        return pl.read_parquet(path)
    if path.suffix == ".json":
        return json.loads(path.read_text(encoding="utf-8"))
    raise ValueError(f"Unsupported dataset format: {path.suffix}")


def write(path: Path, value: Any) -> None:
    """Write a table (CSV, Parquet) or a JSON-serializable object.

    Raises:
        ValueError: If the file format is not supported.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".csv":
        value.write_csv(path)
    elif path.suffix == ".parquet":
        value.write_parquet(path)
    elif path.suffix == ".json":
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    else:
        raise ValueError(f"Unsupported dataset format: {path.suffix}")


class Catalog:
    """Load and save named datasets."""

    def __init__(self, paths: Mapping[str, Path]) -> None:
        """Remember where each persisted dataset lives."""
        self.paths = dict(paths)
        self._memory: dict[str, Any] = {}

    def load(self, name: str) -> Any:
        """Return a dataset produced earlier in this run, or read it from its file.

        Raises:
            KeyError: If the dataset is neither in memory nor in the catalog.
        """
        if name in self._memory:
            return self._memory[name]
        if name not in self.paths:
            raise KeyError(
                f"Dataset {name!r} is not in the catalog and was not produced in this run"
            )
        return read(self.paths[name])

    def save(self, name: str, value: Any) -> None:
        """Keep a dataset for this run, and write it if the catalog lists it."""
        self._memory[name] = value
        if name in self.paths:
            write(self.paths[name], value)
