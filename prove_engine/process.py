"""Process execution and host facts."""

from __future__ import annotations

import os
import platform
import shutil
import signal
import subprocess
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from prove_engine.model import CommandResult, HostInfo, ProcessHandle, ProofError
from prove_engine.pins import EXIT_TIMED_OUT


def run_command(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    stdin: str | None = None,
) -> CommandResult:
    """Run argv in its own process group; on timeout kill the whole group."""
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            list(argv),
            env=None if env is None else dict(env),
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        raise ProofError(f"cannot start {argv[0]}: {exc}") from exc
    try:
        out, err = proc.communicate(stdin, timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        out, err = proc.communicate()
        timed_out = True
    code = EXIT_TIMED_OUT if timed_out else proc.returncode
    return CommandResult(
        tuple(argv), code, out or "", err or "", timed_out, time.monotonic() - started
    )


def spawn_process(argv: Sequence[str], *, env: Mapping[str, str], log: IO[bytes]) -> ProcessHandle:
    """Start argv detached in its own session, writing its output to log."""
    try:
        return subprocess.Popen(
            list(argv),
            env=dict(env),
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        raise ProofError(f"cannot start {argv[0]}: {exc}") from exc


def host_free_bytes(path: Path) -> int:
    """Free bytes on the volume that holds path."""
    return shutil.disk_usage(path).free


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d%H%M%S")


def host_info() -> HostInfo:
    version = platform.mac_ver()[0] or "0"
    return HostInfo(int(version.split(".")[0]), platform.machine(), platform.system())
