"""Smoke tests: the README states the command shape; the check script rejects bad input."""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_readme_states_the_single_bootstrap_command_shape() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert (
        "raw.githubusercontent.com/stevenaldinger/workstation-bootstrap/<commit>/start.sh" in readme
    )
    assert "shasum -a 256 -c -" in readme
    assert "/bin/bash /tmp/ws-start.sh" in readme


def test_check_script_rejects_an_unknown_target() -> None:
    result = subprocess.run(
        [str(REPO_ROOT / "scripts" / "check.sh"), "nonsense"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 2
    assert "usage:" in result.stderr
