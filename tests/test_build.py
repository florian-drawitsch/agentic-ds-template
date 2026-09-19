import json
import shutil
from pathlib import Path

import pytest

from tests.helpers import load_script

ROOT = Path(__file__).resolve().parents[1]
build = load_script(ROOT / "scripts" / "build.py", "build")


@pytest.fixture
def kit(tmp_path: Path) -> Path:
    shutil.copy(ROOT / "catalog.toml", tmp_path / "catalog.toml")
    (tmp_path / "plugins" / "project").mkdir(parents=True)
    return tmp_path


def test_build_writes_both_marketplaces_and_manifest(kit: Path) -> None:
    assert build.main(["--root", str(kit)]) == 0

    claude = json.loads((kit / ".claude-plugin" / "marketplace.json").read_text())
    codex = json.loads((kit / ".agents" / "plugins" / "marketplace.json").read_text())
    manifest = json.loads((kit / "plugins/project/.claude-plugin/plugin.json").read_text())

    superpowers = next(p for p in claude["plugins"] if p["name"] == "superpowers")
    assert superpowers["source"]["source"] == "github"
    assert len(superpowers["source"]["sha"]) == 40
    codex_superpowers = next(p for p in codex["plugins"] if p["name"] == "superpowers")
    assert codex_superpowers["source"]["source"] == "url"
    assert codex_superpowers["source"]["ref"] == superpowers["source"]["ref"]
    assert manifest["name"] == "project"
    assert manifest["version"] == "0.1.0"


def test_check_detects_stale_output(kit: Path) -> None:
    assert build.main(["--root", str(kit), "--check"]) == 1
    build.main(["--root", str(kit)])
    assert build.main(["--root", str(kit), "--check"]) == 0


def test_single_harness_plugin_is_rejected(kit: Path) -> None:
    catalog = (kit / "catalog.toml").read_text()
    (kit / "catalog.toml").write_text(
        catalog.replace('harnesses = ["claude", "codex"]', 'harnesses = ["claude"]', 1)
    )
    with pytest.raises(ValueError, match="both harnesses"):
        build.main(["--root", str(kit)])


def test_repository_output_is_current() -> None:
    assert build.main(["--check"]) == 0
