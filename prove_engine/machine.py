"""One cloned guest: boot, SSH commands, snapshots, and teardown."""

from __future__ import annotations

import contextlib
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path
from typing import IO, TYPE_CHECKING

from prove_engine.logic import parse_vnc_address, ssh_argv, tart_run_argv
from prove_engine.model import (
    Base,
    CommandResult,
    ProcessHandle,
    ProofError,
    Share,
    VncAddress,
)
from prove_engine.pins import (
    BASE_PREFIX,
    GUEST_PASSWORD,
    GUEST_USER,
    IP_WAIT_SECONDS,
    SSH_READY_SECONDS,
    STOP_SECONDS,
    VNC_WAIT_SECONDS,
)
from prove_engine.process import kill_group

if TYPE_CHECKING:
    from prove_engine.engine import Engine


class Machine:
    """One cloned guest: boot, SSH commands, snapshots, teardown."""

    def __init__(self, engine: Engine, name: str, base: Base) -> None:
        self.engine = engine
        self.name = name
        self.base = base
        self.keep = False
        self.shares: tuple[Share, ...] = ()
        self.ip: str | None = None
        # Whether the machine serves its screen over VNC; kept so a snapshot boots the same way.
        self.vnc = False
        self._proc: ProcessHandle | None = None
        self._log: IO[bytes] | None = None

    @property
    def pid(self) -> int | None:
        """The process id of the running guest, or None when it is not running."""
        return self._proc.pid if self._proc is not None and self._proc.poll() is None else None

    @property
    def log_path(self) -> Path:
        return self.engine.logs / f"{self.name}.log"

    def start(self, shares: Sequence[Share] = (), *, vnc: bool = False) -> dict[str, float]:
        """Boot headless (serving the screen over VNC when asked) and wait for an IP address."""
        if self.name.startswith(BASE_PREFIX):
            raise ProofError("the base machine is never booted")
        self.shares = tuple(shares)
        self.vnc = vnc
        for share in self.shares:
            if not share.path.is_dir():
                raise ProofError(f"share {share.name!r} is not a directory: {share.path}")
        self.engine.logs.mkdir(parents=True, exist_ok=True)
        self._log = self.log_path.open("wb")
        argv = tart_run_argv(self.engine.tart, self.name, self.shares, vnc=self.vnc)
        mark = time.monotonic()
        self._proc = self.engine.spawner(argv, env=self.engine.env, log=self._log)
        deadline = mark + IP_WAIT_SECONDS
        while self.ip is None:
            self._raise_if_exited()
            probe = self.engine.tart_cmd("ip", self.name, "--wait", "5", timeout=30)
            if probe.exit_code == 0 and probe.stdout.strip():
                self.ip = probe.stdout.strip()
            elif time.monotonic() > deadline:
                raise ProofError(f"no IP for {self.name} in {IP_WAIT_SECONDS}s\n{self._log_tail()}")
        booted = time.monotonic()
        self.wait_ssh()
        return {
            "boot_seconds": round(booted - mark, 1),
            "ssh_seconds": round(time.monotonic() - booted, 1),
        }

    def vnc_address(self, timeout: float = VNC_WAIT_SECONDS) -> VncAddress:
        """The address of the screen's VNC server, read from the line Tart wrote to the log."""
        deadline = time.monotonic() + timeout
        while True:
            self._raise_if_exited()
            text = (
                self.log_path.read_text(encoding="utf-8", errors="replace")
                if self.log_path.exists()
                else ""
            )
            address = parse_vnc_address(text)
            if address is not None:
                return address
            if time.monotonic() >= deadline:
                raise ProofError(
                    f"no VNC address for {self.name} in {timeout:.0f}s\n{self._log_tail()}"
                )
            time.sleep(0.5)

    def _raise_if_exited(self) -> None:
        if self._proc is not None and self._proc.poll() is not None:
            raise ProofError(f"tart run exited with {self._proc.returncode}\n{self._log_tail()}")

    def _log_tail(self) -> str:
        if self._log is not None:
            self._log.flush()
        text = (
            self.log_path.read_text(encoding="utf-8", errors="replace")
            if self.log_path.exists()
            else ""
        )
        return "\n".join(text.splitlines()[-20:])

    def _ssh_env(self) -> dict[str, str]:
        askpass = self.engine.cache_root / "askpass.sh"
        if not askpass.exists():
            askpass.write_text(f"#!/bin/sh\nprintf '%s\\n' {GUEST_PASSWORD}\n", encoding="utf-8")
            askpass.chmod(0o700)
        return {
            **self.engine.env,
            "SSH_ASKPASS": str(askpass),
            "SSH_ASKPASS_REQUIRE": "force",
            "DISPLAY": ":0",
        }

    def wait_ssh(self, timeout: float = SSH_READY_SECONDS) -> None:
        deadline = time.monotonic() + timeout
        while True:
            self._raise_if_exited()
            if self.run("true", timeout=20).exit_code == 0:
                return
            if time.monotonic() > deadline:
                raise ProofError(f"SSH to {self.name} not ready in {timeout}s\n{self._log_tail()}")
            time.sleep(2)

    def run(self, command: str, *, timeout: float, stdin: str | None = None) -> CommandResult:
        if self.ip is None:
            raise ProofError(f"{self.name} has no IP; call start() first")
        return self.engine.runner(
            ssh_argv(self.ip, command), env=self._ssh_env(), timeout=timeout, stdin=stdin
        )

    def halt(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self.engine.tart_cmd("stop", self.name, "--timeout", str(STOP_SECONDS), timeout=90)
            try:
                self._proc.wait(timeout=STOP_SECONDS)
            except subprocess.TimeoutExpired:
                kill_group(self._proc.pid)
                self._proc.wait()
        if self._log is not None:
            self._log.close()
            self._log = None
        self._proc, self.ip = None, None

    def snapshot(self, tag: str) -> None:
        """Stop, clone the disk to a named copy, and boot again (Tart has no live snapshots)."""
        # Tart stops a guest immediately, so flush the guest's file system first.
        self.run("sync", timeout=60)
        self.halt()
        self.engine.checked("clone", self.name, f"{self.name}-snap-{tag}", "--overwrite")
        self.start(self.shares, vnc=self.vnc)

    def restore(self, tag: str) -> None:
        """Replace this machine with a snapshot and boot it."""
        self.halt()
        self.engine.checked("delete", self.name)
        self.engine.checked("clone", f"{self.name}-snap-{tag}", self.name)
        self.start(self.shares, vnc=self.vnc)

    def destroy(self) -> None:
        if self.keep:
            print(f"kept {self.name}: ssh {GUEST_USER}@{self.ip} (password {GUEST_PASSWORD})")
            return
        self.halt()
        doomed = [self.name]
        with contextlib.suppress(ProofError):
            doomed += [v for v in self.engine.local_vms() if v.startswith(f"{self.name}-snap-")]
        for vm in doomed:
            self.engine.tart_cmd("delete", vm, timeout=120)
