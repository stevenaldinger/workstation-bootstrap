"""Typed records and the error type shared across the engine."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class ProofError(Exception):
    """A hard failure: the environment or the engine, not the command under test."""


@dataclass(frozen=True)
class Image:
    """A pinned vanilla image for one macOS major version."""

    major: int
    repository: str
    tag: str
    digest: str

    @property
    def ref(self) -> str:
        return f"{self.repository}@{self.digest}"


@dataclass(frozen=True)
class HostInfo:
    major: int
    machine: str
    system: str = "Darwin"


@dataclass(frozen=True)
class Share:
    """A host directory shared into the guest at /Volumes/My Shared Files/<name>."""

    name: str
    path: Path
    read_only: bool = False

    def to_arg(self) -> str:
        return f"{self.name}:{self.path}" + (":ro" if self.read_only else "")


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    elapsed: float = 0.0


@dataclass(frozen=True)
class Base:
    image: Image
    name: str


@dataclass
class RunResult:
    scenario: str
    vm_name: str
    command: CommandResult
    env: dict[str, Any]
    timings: dict[str, Any]
    kept: bool = False


Runner = Callable[..., CommandResult]
