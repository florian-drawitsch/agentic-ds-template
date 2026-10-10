# pipelines

Pipelines are directed acyclic graphs of nodes, in the style of Kedro. A node wraps a pure
function and names its inputs and outputs; edges follow from those names. `graph.py` orders the
nodes with `graphlib` and rejects cycles and datasets produced twice.

- Functions take datasets and parameters as arguments and return datasets. No file access, no
  settings, no tracking: the runner and the catalog handle that.
- Inputs named `params:<key>` come from `params` in `conf/`. Everything else is a dataset.
- A dataset is persisted only when `conf/` lists it in the catalog; the rest stays in memory.
- Pipelines never import each other; they share data through dataset names. import-linter
  enforces both rules.
- New pipeline: add a module with its functions and a `pipeline([...])`, register it in
  `__init__.py`, add persisted datasets to the catalog, and unit-test each function.
- `task pipeline-graph` prints the graph as a Mermaid diagram.
