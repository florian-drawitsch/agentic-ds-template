import importlib.util
import sys
from pathlib import Path
from types import ModuleType


def load_script(path: Path, name: str) -> ModuleType:
    """Import a standalone script by path so tests can call its functions."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
