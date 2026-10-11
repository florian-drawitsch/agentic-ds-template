"""Pipelines as directed acyclic graphs of nodes, in the style of Kedro.

A node wraps a pure function and names its inputs and outputs. Edges follow from the names: a
node that consumes `features` depends on the node that produces it. The standard library's
`graphlib` derives the execution order and rejects cycles, so the order in which nodes are listed
does not matter. Inputs named `params:<key>` are read from the run parameters instead of the data
catalog.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from graphlib import CycleError, TopologicalSorter
from typing import Any, Protocol

PARAMS = "params:"
# The standard frontmatter and style lines of the mermaid skill's house style.
MERMAID_STYLE = (
    "---",
    "config:",
    "  themeCSS: >-",
    "    .row-rect-odd path, .row-rect-even path, marker circle { fill: none !important; }",
    "    .edgePaths path, .flowchart-link, .relation, .transition, .relationshipLine, marker path,"
    " marker circle { stroke: #868e96 !important; }",
    "    circle.state-start, circle.state-end { fill: #868e96 !important; stroke: #868e96"
    " !important; }",
    "    rect.actor, rect.note, rect[class^=activation], polygon.labelBox { fill: none !important;"
    " stroke: #868e96 !important; }",
    "    line[class^=actor-line], line.loopLine, line[class^=messageLine],"
    " path[class^=messageLine] { stroke: #868e96 !important; }",
    "    text.actor, text.actor tspan, .messageText, .noteText, .noteText tspan, .labelText,"
    " .labelText tspan, .loopText, .loopText tspan, text.text { fill: #868e96 !important; }",
    "---",
)


class DataCatalog(Protocol):
    """Where nodes read their inputs from and write their outputs to."""

    def load(self, name: str) -> Any:
        """Return a dataset."""
        ...

    def save(self, name: str, value: Any) -> None:
        """Store a dataset."""
        ...


@dataclass(frozen=True)
class Node:
    """A pure function with named inputs and outputs."""

    func: Callable[..., Any]
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    name: str
    tags: frozenset[str] = field(default_factory=frozenset)

    def __call__(self, *args: Any) -> dict[str, Any]:
        """Run the function and map its result to the output names.

        Raises:
            ValueError: If the function returns a different number of values than declared.
        """
        result = self.func(*args)
        if len(self.outputs) == 1:
            return {self.outputs[0]: result}
        if not isinstance(result, tuple | list) or len(result) != len(self.outputs):
            raise ValueError(f"Node {self.name!r} must return {len(self.outputs)} values")
        return dict(zip(self.outputs, result, strict=True))


def _names(value: str | Sequence[str]) -> tuple[str, ...]:
    return (value,) if isinstance(value, str) else tuple(value)


def node(
    func: Callable[..., Any],
    inputs: str | Sequence[str] = (),
    outputs: str | Sequence[str] = (),
    *,
    name: str | None = None,
    tags: Sequence[str] = (),
) -> Node:
    """Create a node. Inputs are passed to `func` positionally, in the given order."""
    return Node(
        func=func,
        inputs=_names(inputs),
        outputs=_names(outputs),
        name=name or getattr(func, "__name__", repr(func)),
        tags=frozenset(tags),
    )


@dataclass(frozen=True)
class Pipeline:
    """A validated graph of nodes. Combine pipelines with `+`."""

    nodes: tuple[Node, ...]

    def __post_init__(self) -> None:
        """Reject duplicate node names, datasets produced twice and cycles."""
        names = [n.name for n in self.nodes]
        if duplicates := sorted({n for n in names if names.count(n) > 1}):
            raise ValueError(f"Duplicate node names: {duplicates}")
        producers: dict[str, str] = {}
        for n in self.nodes:
            for output in n.outputs:
                if output in producers:
                    raise ValueError(
                        f"Dataset {output!r} is produced by {producers[output]!r} and {n.name!r}"
                    )
                producers[output] = n.name
        self.order()

    def __add__(self, other: Pipeline) -> Pipeline:
        """Combine two pipelines; nodes they share by name appear once."""
        known = {n.name for n in self.nodes}
        return Pipeline(self.nodes + tuple(n for n in other.nodes if n.name not in known))

    @property
    def inputs(self) -> set[str]:
        """Return the datasets the pipeline needs from the catalog."""
        consumed = {i for n in self.nodes for i in n.inputs if not i.startswith(PARAMS)}
        return consumed - self.outputs

    @property
    def outputs(self) -> set[str]:
        """Return the datasets the pipeline produces."""
        return {o for n in self.nodes for o in n.outputs}

    def order(self) -> list[Node]:
        """Return the nodes in an order that respects every edge.

        Raises:
            ValueError: If the graph contains a cycle.
        """
        producer = {o: n.name for n in self.nodes for o in n.outputs}
        graph = {n.name: {producer[i] for i in n.inputs if i in producer} for n in self.nodes}
        by_name = {n.name: n for n in self.nodes}
        try:
            return [by_name[name] for name in TopologicalSorter(graph).static_order()]
        except CycleError as error:
            raise ValueError(f"Pipeline contains a cycle: {error.args[1]}") from error

    def only_tagged(self, *tags: str) -> Pipeline:
        """Return the sub-pipeline of nodes that carry any of the tags."""
        return Pipeline(tuple(n for n in self.nodes if n.tags & set(tags)))

    def to_mermaid(self) -> str:
        """Render the graph as Mermaid in the house style: datasets rounded, parameters framed."""
        lines = [
            *MERMAID_STYLE,
            "flowchart LR",
            "    classDef default fill:none,stroke:#868e96,stroke-width:1px,color:#868e96",
            "    linkStyle default stroke:#868e96",
        ]
        datasets = sorted({d for n in self.nodes for d in (*n.inputs, *n.outputs)})
        ids = {d: f"d{i}" for i, d in enumerate(datasets)}
        lines += [
            f'    {ids[d]}[["{d}"]]' if d.startswith(PARAMS) else f'    {ids[d]}(["{d}"])'
            for d in datasets
        ]
        for i, n in enumerate(self.order()):
            lines.append(f'    n{i}["{n.name}"]')
            lines += [f"    {ids[d]} --> n{i}" for d in n.inputs]
            lines += [f"    n{i} --> {ids[d]}" for d in n.outputs]
        return "\n".join(lines)


def pipeline(nodes: Sequence[Node]) -> Pipeline:
    """Create a pipeline from nodes listed in any order."""
    return Pipeline(tuple(nodes))


def run(graph: Pipeline, catalog: DataCatalog, params: Mapping[str, Any]) -> dict[str, Any]:
    """Run every node in dependency order and return all outputs by name.

    Raises:
        KeyError: If a `params:` input is missing from the parameters.
    """
    produced: dict[str, Any] = {}
    for current in graph.order():
        args = [
            params[name.removeprefix(PARAMS)] if name.startswith(PARAMS) else catalog.load(name)
            for name in current.inputs
        ]
        for name, value in current(*args).items():
            catalog.save(name, value)
            produced[name] = value
    return produced
