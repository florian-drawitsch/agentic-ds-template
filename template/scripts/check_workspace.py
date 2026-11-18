"""Reject dependencies between apps and unregistered app directories."""

from __future__ import annotations

import ast
import fnmatch
import re
import sys
import tomllib
from pathlib import Path


def normalized(name: str) -> str:
    """Normalize a Python distribution name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def check(root: Path) -> list[str]:
    """Return violations of the workspace's app boundaries."""
    workspace = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["uv"]["workspace"]
    errors: list[str] = []
    apps: dict[Path, dict] = {}
    packages: dict[str, Path] = {}
    names: dict[str, Path] = {}
    for app in sorted((root / "apps").iterdir()):
        if not app.is_dir():
            continue
        relative = app.relative_to(root).as_posix()
        if any(fnmatch.fnmatchcase(relative, pattern) for pattern in workspace.get("exclude", [])):
            continue
        config = app / "pyproject.toml"
        if not config.is_file() or not any(
            fnmatch.fnmatchcase(relative, pattern) for pattern in workspace.get("members", [])
        ):
            errors.append(
                f"{relative}: needs pyproject.toml and workspace membership, or an exclude entry"
            )
            continue
        data = tomllib.loads(config.read_text())
        apps[app] = data
        names[normalized(data["project"]["name"])] = app
        source = app / "src"
        if source.is_dir():
            for module in source.iterdir():
                if module.is_dir() or module.suffix == ".py":
                    packages[module.stem if module.is_file() else module.name] = app
    for app, data in apps.items():
        project = data["project"]
        dependencies = list(project.get("dependencies", []))
        for group in project.get("optional-dependencies", {}).values():
            dependencies.extend(group)
        for dependency in dependencies:
            match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", dependency)
            owner = names.get(normalized(match[0])) if match else None
            if owner is not None and owner != app:
                errors.append(f"{app.relative_to(root)} depends on app {owner.name}: {dependency}")
        for path in sorted((app / "src").rglob("*.py")):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                imports: list[str] = []
                if isinstance(node, ast.Import):
                    imports = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imports = [node.module]
                else:
                    continue
                for module in imports:
                    owner = packages.get(module.split(".")[0])
                    if owner is not None and owner != app:
                        errors.append(
                            f"{path.relative_to(root)}:{node.lineno} imports app {owner.name}"
                        )
    return errors


def main() -> int:
    """Check the workspace from the repository root."""
    try:
        errors = check(Path.cwd())
    except (KeyError, OSError, SyntaxError, ValueError) as error:
        print(f"Workspace check failed: {error}")
        return 1
    for error in errors:
        print(error)
    return int(bool(errors))


if __name__ == "__main__":
    sys.exit(main())
