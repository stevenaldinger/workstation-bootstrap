"""Layout guard: every tracked Python module stays under the line cap."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAX_LINES = 500


def test_every_python_module_is_within_the_line_cap() -> None:
    listing = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", "*.py"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert listing, "no tracked Python files found"
    oversized = {
        name: count
        for name in listing
        if (count := len((REPO_ROOT / name).read_text(encoding="utf-8").splitlines())) > MAX_LINES
    }
    assert oversized == {}, f"split these modules along responsibility seams: {oversized}"
