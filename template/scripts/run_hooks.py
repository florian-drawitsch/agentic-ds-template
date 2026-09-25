"""Run every git hook on tracked and untracked files without staging anything."""

from __future__ import annotations

import subprocess
import sys


def main() -> int:
    """Collect the repository's files and pass them to prek."""
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        capture_output=True,
        check=True,
    ).stdout
    files = [name.decode() for name in listed.split(b"\0") if name]
    if not files:
        return 0
    return subprocess.run(["uv", "run", "prek", "run", "--files", *files], check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
