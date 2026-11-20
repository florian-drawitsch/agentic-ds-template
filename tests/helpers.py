import atexit
import importlib.util
import shutil
import subprocess
import sys
import tempfile
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


_seeds: dict[str, Path] = {}


def checkout(destination: Path, message: str = "initial") -> Path:
    """Copy a repository with one empty commit on `main` and a GitHub `origin` into destination.

    The repository is built once per process and message; copying it is much cheaper than
    running `git init` and `git commit` for every test.
    """
    if message not in _seeds:
        seed = Path(tempfile.mkdtemp(prefix="tests-checkout-")) / "checkout"
        atexit.register(shutil.rmtree, seed.parent, ignore_errors=True)
        seed.mkdir()
        for args in (
            ["init", "-q", "-b", "main"],
            ["commit", "--allow-empty", "-qm", message],
            ["remote", "add", "origin", "git@github.com:example/project.git"],
        ):
            git = ["git", "-c", "user.name=test", "-c", "user.email=test@example.com"]
            subprocess.run([*git, *args], cwd=seed, check=True, capture_output=True)
        _seeds[message] = seed
    shutil.copytree(_seeds[message], destination, symlinks=True)
    return destination
