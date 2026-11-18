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
