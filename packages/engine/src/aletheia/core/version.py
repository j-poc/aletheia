"""Code identity for reproducibility stamps.

Every ingest run and every evidence card records the exact commit that produced
it. A result whose code version is unknown cannot be reproduced, and a number
that cannot be reproduced is not evidence.

A dirty working tree is reported as such (``<sha>-dirty``) rather than quietly
passing off uncommitted changes as the committed ones.
"""

from __future__ import annotations

import shutil
import subprocess
from functools import cache
from pathlib import Path

UNKNOWN = "unknown"


@cache
def code_version(repo_root: Path | None = None) -> str:
    """Git description of the working tree, e.g. ``a1b2c3d`` or ``a1b2c3d-dirty``.

    Returns ``"unknown"`` outside a repository. Cached: this is called on every
    row write, and shelling out per row would dominate ingest time.
    """
    root = repo_root or Path(__file__).resolve().parents[4]
    sha = _git(root, "rev-parse", "--short=12", "HEAD")
    if sha is None:
        return UNKNOWN
    dirty = _git(root, "status", "--porcelain")
    return f"{sha}-dirty" if dirty else sha


def _git(root: Path, *args: str) -> str | None:
    executable = shutil.which("git")
    if executable is None:
        return None
    try:
        completed = subprocess.run(  # noqa: S603 - resolved absolute path, fixed argv, no shell
            [executable, "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()
