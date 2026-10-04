# pipelines

Each module defines one pipeline: pure step functions plus a `pipeline` object that names their
inputs, outputs and parameters.

- Steps take datasets and parameters as arguments and return datasets. No file access, no
  settings, no tracking. The runner and the catalog handle that.
- A dataset is persisted only when `conf/` lists it in the catalog; everything else stays in
  memory for one run.
- Pipelines never import each other; they share data through the catalog. import-linter enforces
  both rules.
- New pipeline: add a module, register it in `__init__.py`, add its datasets to the catalog, and
  unit-test each step.
