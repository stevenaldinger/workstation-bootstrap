"""Process execution and host facts."""

from __future__ import annotations

import contextlib
import os
import platform
import secrets
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from prove_engine.model import CommandResult, HostInfo, ProcessHandle, ProofError
from prove_engine.pins import EXIT_TIMED_OUT, HEARTBEAT_SECONDS


def kill_group(pid: int) -> None:
    """Kill a process group; one that already ended is not an error."""
    # macOS answers PermissionError for a group whose leader is a zombie.
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal.SIGKILL)


def run_command(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    timeout: float | None = None,
    stdin: str | None = None,
    progress: str | None = None,
    heartbeat: float = HEARTBEAT_SECONDS,
) -> CommandResult:
    """Run argv in its own process group; on timeout kill the whole group.

    With `progress`, a still-running line naming it goes to stderr every `heartbeat` seconds.
    """
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
    deadline = None if timeout is None else started + timeout
    pending_input = stdin
    timed_out = False
    while True:
        wait = heartbeat if progress else None
        if deadline is not None:
            remaining = max(deadline - time.monotonic(), 0.0)
            wait = remaining if wait is None else min(wait, remaining)
        try:
            out, err = proc.communicate(pending_input, timeout=wait)
            break
        except subprocess.TimeoutExpired:
            # The input is already with the process; a second call must not send it again.
            pending_input = None
            if deadline is not None and time.monotonic() >= deadline:
                kill_group(proc.pid)
                out, err = proc.communicate()
                timed_out = True
                break
            print(
                f"still running: {progress} ({time.monotonic() - started:.0f}s)",
                file=sys.stderr,
                flush=True,
            )
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


def unique_suffix() -> str:
    """Six random hex digits, so two runs of one scenario in one second get different names."""
    return secrets.token_hex(3)


def host_info() -> HostInfo:
    version = platform.mac_ver()[0] or "0"
    return HostInfo(int(version.split(".")[0]), platform.machine(), platform.system())
