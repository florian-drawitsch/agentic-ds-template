"""Synthetic example data, so the pipeline runs before real data arrives."""

from __future__ import annotations

import random
import sys
from pathlib import Path

import polars as pl


def example_table(rows: int = 200, seed: int = 0) -> pl.DataFrame:
    """Return a synthetic table with one feature and a noisy linear target, in time order."""
    rng = random.Random(seed)
    x = [rng.uniform(0, 10) for _ in range(rows)]
    return pl.DataFrame(
        {"step": list(range(rows)), "x": x, "target": [2.0 * v + 1.0 + rng.gauss(0, 1) for v in x]}
    )


def main(argv: list[str] | None = None) -> int:
    """Write the example table to the path given as the only argument."""
    args = sys.argv[1:] if argv is None else argv
    path = Path(args[0] if args else "data/raw/example.csv")
    path.parent.mkdir(parents=True, exist_ok=True)
    example_table().write_csv(path)
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
