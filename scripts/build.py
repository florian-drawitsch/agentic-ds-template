"""Generate the marketplace files and local plugin manifests from catalog.toml.

Usage:
    python scripts/build.py           write the generated files
    python scripts/build.py --check   fail if any generated file is stale
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HARNESSES = {"claude", "codex"}
CODEX_POLICY = {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}


def load_catalog(root: Path) -> dict[str, Any]:
    """Read and validate the catalog."""
    catalog = tomllib.loads((root / "catalog.toml").read_text(encoding="utf-8"))
    names = [plugin["name"] for plugin in catalog["plugins"]]
    if len(names) != len(set(names)):
        raise ValueError("Plugin names in catalog.toml must be unique")
    for plugin in catalog["plugins"]:
        harnesses = set(plugin["harnesses"])
        if harnesses != HARNESSES:
            raise ValueError(f"Plugin {plugin['name']} must support both harnesses (ADR-0017)")
        source = plugin["source"]
        if source["type"] == "local" and not (root / source["path"]).is_dir():
            raise ValueError(f"Local plugin path does not exist: {source['path']}")
        if source["type"] == "git" and not {"repo", "ref", "sha"} <= source.keys():
            raise ValueError(f"Git plugin {plugin['name']} needs repo, ref and sha")
    return catalog


def claude_marketplace(catalog: dict[str, Any]) -> dict[str, Any]:
    """Build the Claude Code marketplace file."""
    meta = catalog["marketplace"]
    entries = []
    for plugin in catalog["plugins"]:
        source = plugin["source"]
        if source["type"] == "local":
            claude_source: Any = source["path"]
        else:
            claude_source = {
                "source": "github",
                "repo": source["repo"],
                "ref": source["ref"],
                "sha": source["sha"],
            }
        entries.append(
            {"name": plugin["name"], "source": claude_source, "description": plugin["description"]}
        )
    return {
        "name": meta["name"],
        "description": meta["description"],
        "owner": {"name": meta["owner"]},
        "plugins": entries,
    }


def codex_marketplace(catalog: dict[str, Any]) -> dict[str, Any]:
    """Build the Codex marketplace file."""
    meta = catalog["marketplace"]
    entries = []
    for plugin in catalog["plugins"]:
        source = plugin["source"]
        if source["type"] == "local":
            codex_source = {"source": "local", "path": source["path"]}
        else:
            codex_source = {
                "source": "url",
                "url": f"https://github.com/{source['repo']}.git",
                "ref": source["ref"],
            }
        entries.append(
            {
                "name": plugin["name"],
                "source": codex_source,
                "policy": CODEX_POLICY,
                "category": plugin["category"],
            }
        )
    return {
        "name": meta["name"],
        "interface": {"displayName": meta["display_name"]},
        "plugins": entries,
    }


def plugin_manifest(catalog: dict[str, Any], plugin: dict[str, Any]) -> dict[str, Any]:
    """Build the manifest of a local plugin. Codex reads the same Claude-format file."""
    meta = catalog["marketplace"]
    return {
        "name": plugin["name"],
        "version": meta["version"],
        "description": plugin["description"],
        "author": {"name": meta["owner"]},
        "license": meta["license"],
    }


def outputs(root: Path) -> dict[Path, str]:
    """Return every generated file with its expected content."""
    catalog = load_catalog(root)
    files = {
        root / ".claude-plugin" / "marketplace.json": claude_marketplace(catalog),
        root / ".agents" / "plugins" / "marketplace.json": codex_marketplace(catalog),
    }
    for plugin in catalog["plugins"]:
        if plugin["source"]["type"] == "local":
            manifest = root / plugin["source"]["path"] / ".claude-plugin" / "plugin.json"
            files[manifest] = plugin_manifest(catalog, plugin)
    return {path: json.dumps(data, indent=2) + "\n" for path, data in files.items()}


def main(argv: list[str] | None = None) -> int:
    """Write or check the generated files."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if generated files are stale")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    stale = []
    for path, content in outputs(args.root).items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == content:
            continue
        if args.check:
            stale.append(path.relative_to(args.root))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            print(f"wrote {path.relative_to(args.root)}")
    if stale:
        print("Stale generated files, run `task build`:", *stale, sep="\n  ", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
