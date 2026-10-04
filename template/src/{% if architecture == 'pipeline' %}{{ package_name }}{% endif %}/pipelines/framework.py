"""A minimal pipeline runner: steps are pure functions with named inputs and outputs."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol


class DataCatalog(Protocol):
    """Where steps read their inputs from and write their outputs to."""

    def load(self, name: str) -> Any:
        """Return a dataset."""
        ...

    def save(self, name: str, value: Any) -> None:
        """Store a dataset."""
        ...


@dataclass(frozen=True)
class Step:
    """One pure function. Inputs are datasets, parameters come from the settings."""

    func: Callable[..., Any]
    inputs: Sequence[str]
    outputs: Sequence[str]
    parameters: Sequence[str] = ()

    @property
    def name(self) -> str:
        """Return the step name."""
        return getattr(self.func, "__name__", repr(self.func))


@dataclass(frozen=True)
class Pipeline:
    """An ordered list of steps."""

    name: str
    steps: Sequence[Step]

    def run(self, catalog: DataCatalog, params: Mapping[str, Any]) -> dict[str, Any]:
        """Run every step in order and return all outputs by name."""
        produced: dict[str, Any] = {}
        for step in self.steps:
            args = [catalog.load(name) for name in step.inputs]
            result = step.func(*args, **{key: params[key] for key in step.parameters})
            values = result if len(step.outputs) > 1 else (result,)
            for name, value in zip(step.outputs, values, strict=True):
                catalog.save(name, value)
                produced[name] = value
        return produced
