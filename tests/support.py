"""Shared test doubles and fixtures."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import IO

import pytest

import prove_engine.engine as engine_module
from prove_engine import (
    CommandResult,
    Engine,
    HostInfo,
)
from prove_engine.model import FreeSpace

REPO_ROOT = Path(__file__).resolve().parents[1]
APPLE_SILICON = HostInfo(15, "arm64", "Darwin")


class FakeRunner:
    """Records every command and answers from a script of (prefix, result) rules."""

    def __init__(self, rules: Sequence[tuple[str, CommandResult]] = ()) -> None:
        self.rules = list(rules)
        self.calls: list[tuple[str, ...]] = []
        self.timeouts: list[float | None] = []

    def __call__(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
        stdin: str | None = None,
    ) -> CommandResult:
        self.calls.append(tuple(argv))
        self.timeouts.append(timeout)
        line = " ".join(str(part) for part in argv)
        for fragment, result in self.rules:
            if fragment in line:
                return result
        return result_of("")

    def tart_calls(self) -> list[str]:
        return [" ".join(call[1:]) for call in self.calls if call[0].endswith("/tart")]


class FakeProcess:
    """A started process that stays alive until `finish` is called."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode if self.returncode is not None else 0

    def finish(self, code: int = 0) -> None:
        self.returncode = code


class FakeSpawner:
    """Records started processes instead of running them."""

    def __init__(self) -> None:
        self.started: list[tuple[str, ...]] = []
        self.processes: list[FakeProcess] = []

    def __call__(
        self, argv: Sequence[str], *, env: Mapping[str, str], log: IO[bytes]
    ) -> FakeProcess:
        self.started.append(tuple(argv))
        process = FakeProcess(900000 + len(self.processes))
        self.processes.append(process)
        return process


def result_of(stdout: str, code: int = 0, stderr: str = "") -> CommandResult:
    return CommandResult(("x",), code, stdout, stderr)


def make_engine(
    tmp_path: Path,
    runner: FakeRunner,
    spawner: FakeSpawner | None = None,
    free_space: FreeSpace | None = None,
) -> Engine:
    return Engine(
        tmp_path / "cache",
        host=APPLE_SILICON,
        env={"PATH": "/usr/bin"},
        vm_lock=tmp_path / "vm.lock",
        free_space=free_space or (lambda _path: 500 * 1024**3),
        runner=runner,
        spawner=spawner or FakeSpawner(),
    )


def stage_unpacked_tart(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave a stand-in archive that matches the (patched) pin and an already-unpacked binary."""
    engine.release_dir.mkdir(parents=True)
    (engine.release_dir / "tart.tar.gz").write_bytes(b"stand-in")
    engine.tart.parent.mkdir(parents=True)
    engine.tart.write_text("", encoding="utf-8")
    monkeypatch.setattr(engine_module, "TART_SHA256", hashlib.sha256(b"stand-in").hexdigest())
