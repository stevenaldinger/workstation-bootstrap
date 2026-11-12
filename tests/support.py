"""Shared test doubles and fixtures."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

import prove_engine.engine as engine_module
from prove_engine import (
    CommandResult,
    Engine,
    HostInfo,
)

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


def result_of(stdout: str, code: int = 0, stderr: str = "") -> CommandResult:
    return CommandResult(("x",), code, stdout, stderr)


def make_engine(tmp_path: Path, runner: FakeRunner) -> Engine:
    return Engine(tmp_path / "cache", host=APPLE_SILICON, env={"PATH": "/usr/bin"}, runner=runner)


def stage_unpacked_tart(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave a stand-in archive that matches the (patched) pin and an already-unpacked binary."""
    engine.release_dir.mkdir(parents=True)
    (engine.release_dir / "tart.tar.gz").write_bytes(b"stand-in")
    engine.tart.parent.mkdir(parents=True)
    engine.tart.write_text("", encoding="utf-8")
    monkeypatch.setattr(engine_module, "TART_SHA256", hashlib.sha256(b"stand-in").hexdigest())
