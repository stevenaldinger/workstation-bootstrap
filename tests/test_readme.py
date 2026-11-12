"""Smoke tests: the README states the command shape."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_readme_states_the_single_bootstrap_command_shape() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert (
        "raw.githubusercontent.com/stevenaldinger/workstation-bootstrap/<commit>/start.sh" in readme
    )
    assert "shasum -a 256 -c -" in readme
    assert "/bin/bash /tmp/ws-start.sh" in readme
