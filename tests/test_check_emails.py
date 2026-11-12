"""Tracked files may name only addresses at domains reserved for examples."""

from __future__ import annotations

import subprocess
from pathlib import Path

import check_emails
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
# Built from parts so this file does not hold an address the check would refuse.
REAL = "someone" + "@" + "gmail" + ".com"


def track(root: Path, text: str) -> Path:
    git = ["git", "-C", str(root)]
    subprocess.run([*git, "init", "-q"], check=True)
    (root / "notes.md").write_text(text, encoding="utf-8")
    subprocess.run([*git, "add", "notes.md"], check=True)
    return root


def test_the_repository_itself_is_clean() -> None:
    assert check_emails.check(REPO_ROOT) == []


def test_a_real_looking_address_fails_by_file_and_line_without_being_printed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = track(tmp_path, f"first line\ncontact {REAL} now\n")
    assert check_emails.main(["--root", str(root)]) == 1
    error = capsys.readouterr().err
    assert "notes.md:2:" in error
    assert REAL not in error
    assert "gmail" not in error


@pytest.mark.parametrize(
    "text",
    [
        "owner@work.example",
        "git@example.test",
        "a@example.com b@mail.example.org c@example.net",
        "me@host.localhost me@x.invalid",
        "git clone git@github.com:owner/repo.git",
        "uses: actions/checkout@3d3c42e5  # v7.0.1",
    ],
)
def test_reserved_domains_remotes_and_version_pins_pass(tmp_path: Path, text: str) -> None:
    assert check_emails.check(track(tmp_path, text + "\n")) == []
