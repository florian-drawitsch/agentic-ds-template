import subprocess
import sys
from pathlib import Path

import pytest

# Tests import scripts from template/; never leave bytecode there for Copier to pick up.
sys.dont_write_bytecode = True


@pytest.fixture
def fake_ssh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Put an `ssh` on PATH whose config maps the alias `github-private` to github.com."""
    bin_dir = tmp_path / "fake-ssh-bin"
    bin_dir.mkdir()
    script = bin_dir / "ssh"
    script.write_text(
        '#!/bin/sh\n[ "$1" = "-G" ] || exit 2\n'
        'case "$2" in github-private) echo "hostname github.com" ;; *) echo "hostname $2" ;; esac\n'
    )
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{Path('/usr/bin')}:{Path('/bin')}")


@pytest.fixture
def clone(tmp_path):
    def git(cwd, *args):
        subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)

    probe = subprocess.run(
        ["git", "init", "-q", str(tmp_path / "probe")], capture_output=True, check=False
    )
    if probe.returncode != 0:
        pytest.skip("git cannot write repositories under this temporary directory")
    bare = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--template=", "--bare", "-b", "main", str(bare)],
        check=True,
        capture_output=True,
    )
    seed = tmp_path / "seed"
    subprocess.run(
        ["git", "clone", "--template=", str(bare), str(seed)], check=True, capture_output=True
    )
    git(seed, "config", "user.email", "a@b.c")
    git(seed, "config", "user.name", "a")
    (seed / "f").write_text("x")
    git(seed, "add", "f")
    git(seed, "commit", "-m", "init")
    git(seed, "push", "origin", "HEAD:main")
    clones = []
    for name in ("one", "two"):
        path = tmp_path / name
        subprocess.run(
            ["git", "clone", "--template=", str(bare), str(path)], check=True, capture_output=True
        )
        clones.append(path)
    return clones
