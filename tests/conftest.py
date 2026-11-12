"""Guards that apply to every test."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def tests_do_not_read_the_host_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail a test that measures the real disk, whose free space varies from host to host.

    Code under test receives the probe as an argument; a test that needs `shutil.disk_usage`
    itself sets its own fake with `monkeypatch`, which replaces this one.
    """

    def refuse(path: str | Path) -> shutil._ntuple_diskusage:
        raise AssertionError(f"a test read the real disk usage of {path}; inject a probe instead")

    monkeypatch.setattr(shutil, "disk_usage", refuse)
