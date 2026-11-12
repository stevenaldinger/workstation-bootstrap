"""Typed records and the error type shared across the engine."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any, Protocol


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
class VncAddress:
    """Where a guest's screen is served: the address and one-time password Tart prints."""

    host: str
    port: int
    password: str


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


class Runner(Protocol):
    """Runs a command to completion and returns what it printed."""

    def __call__(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
        stdin: str | None = None,
    ) -> CommandResult: ...


# Free bytes on the volume holding a path.
FreeSpace = Callable[[Path], int]


class ProcessHandle(Protocol):
    """What the engine needs from a started process."""

    @property
    def pid(self) -> int: ...

    @property
    def returncode(self) -> int | None: ...

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...


class Spawner(Protocol):
    """Starts a long-running process that outlives the call (the guest's `tart run`)."""

    def __call__(
        self, argv: Sequence[str], *, env: Mapping[str, str], log: IO[bytes]
    ) -> ProcessHandle: ...
