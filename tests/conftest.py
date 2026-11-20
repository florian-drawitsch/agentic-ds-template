import atexit
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Tests import scripts from template/; never leave bytecode there for Copier to pick up.
sys.dont_write_bytecode = True

# Every fast test must finish within this many seconds (setup and call, each). Mark longer tests
# `slow`. Raise the limit for one run with TEST_BUDGET_SECONDS on a heavily loaded machine.
BUDGET_SECONDS = float(os.environ.get("TEST_BUDGET_SECONDS", "10"))


def _isolate_git() -> None:
    """Run git without the developer's global and system config.

    Personal settings such as LFS filters, hooks or commit signing slow tests down and can
    change their results. Tests get a fixed identity and default branch instead.
    """
    home = Path(tempfile.mkdtemp(prefix="tests-git-"))
    atexit.register(shutil.rmtree, home, ignore_errors=True)
    config = home / "gitconfig"
    config.write_text(
        "[user]\n\tname = test\n\temail = test@example.com\n"
        "[init]\n\tdefaultBranch = main\n"
        "[commit]\n\tgpgsign = false\n"
        "[tag]\n\tgpgsign = false\n"
    )
    os.environ["GIT_CONFIG_GLOBAL"] = str(config)
    os.environ["GIT_CONFIG_NOSYSTEM"] = "1"


_isolate_git()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]):
    """Fail fast tests that exceed the time budget, so slow tests cannot pile up unnoticed."""
    outcome = yield
    report = outcome.get_result()
    if (
        call.when in {"setup", "call"}
        and report.passed
        and report.duration > BUDGET_SECONDS
        and item.get_closest_marker("slow") is None
    ):
        report.outcome = "failed"
        report.longrepr = (
            f"{call.when} took {report.duration:.1f} s, over the {BUDGET_SECONDS:g} s budget for "
            "fast tests. Make it faster (share renders and fixtures, fake subprocesses) or mark "
            "it `slow`."
        )


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
