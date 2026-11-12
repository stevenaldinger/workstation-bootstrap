"""The engine: Tart acquisition, the base image, per-run clones, and evidence."""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prove_engine.disk import grow_layout, grow_system_container
from prove_engine.lock import HostLock, clear_if_stale, held_message, pid_alive, read_owner
from prove_engine.logic import (
    check_free_space,
    check_host,
    default_target_major,
    env_facts,
    machine_name,
    parse_guest_facts,
    select_image,
    validate_scenario,
    verify_sha256,
)
from prove_engine.machine import Machine
from prove_engine.model import (
    Base,
    CommandResult,
    HostInfo,
    ProofError,
    Runner,
    RunResult,
    Share,
    Spawner,
)
from prove_engine.pins import (
    BASE_PREFIX,
    DEFAULT_COMMAND,
    DISK_GB,
    FREE_GB_FIRST_PULL,
    FREE_GB_PER_RUN,
    RUN_PREFIX,
    TART_SHA256,
    TART_URL,
    TART_VERSION,
)
from prove_engine.process import run_command, spawn_process, utc_stamp
from prove_engine.toolchain import download, unpack_tart


class Engine:
    def __init__(
        self,
        cache_root: Path,
        *,
        host: HostInfo,
        env: Mapping[str, str],
        vm_lock: Path,
        runner: Runner = run_command,
        spawner: Spawner = spawn_process,
    ) -> None:
        self.cache_root = cache_root
        self.vm_lock = vm_lock
        self.spawner = spawner
        self.host = host
        self.runner = runner
        self.tart_home = cache_root / "tart-home"
        self.release_dir = cache_root / "tart" / TART_VERSION
        self.tart = self.release_dir / "tart.app" / "Contents" / "MacOS" / "tart"
        self.logs = cache_root / "logs"
        self.env = {**env, "TART_HOME": str(self.tart_home)}

    def tart_cmd(self, *args: str, timeout: float | None = None) -> CommandResult:
        return self.runner([str(self.tart), *args], env=self.env, timeout=timeout)

    def checked(self, *args: str, timeout: float | None = None) -> CommandResult:
        result = self.tart_cmd(*args, timeout=timeout)
        if result.exit_code != 0:
            detail = (result.stderr or result.stdout).strip()
            raise ProofError(f"tart {' '.join(args)} failed ({result.exit_code}): {detail}")
        return result

    def ensure_tart(self) -> Path:
        archive = self.release_dir / "tart.tar.gz"
        self.release_dir.mkdir(parents=True, exist_ok=True)
        if not archive.exists():
            download(TART_URL, archive)
        verify_sha256(archive, TART_SHA256)
        if not self.tart.exists():
            unpack_tart(archive, self.release_dir)
        reported = self.checked("--version", timeout=30).stdout.strip()
        if reported != TART_VERSION:
            raise ProofError(f"tart reports {reported}, expected {TART_VERSION}")
        return self.tart

    def local_vms(self) -> list[str]:
        out = self.checked("list", "--source", "local", "--quiet", timeout=60).stdout
        return [line.strip() for line in out.splitlines() if line.strip()]

    def base(self, target_major: int | None = None) -> Base:
        """Pull the pinned image and clone it once to a base that is never booted."""
        check_host(self.host)
        image = select_image(target_major or default_target_major(self.host.major))
        self.ensure_tart()
        name = f"{BASE_PREFIX}{image.major}"
        if name not in self.local_vms():
            self.tart_home.mkdir(parents=True, exist_ok=True)
            check_free_space(shutil.disk_usage(self.tart_home).free, FREE_GB_FIRST_PULL)
            self.checked("pull", image.ref)
            self.checked("clone", image.ref, name)
        self.ensure_disk(name)
        return Base(image, name)

    def ensure_disk(self, name: str) -> None:
        """Give the never-booted base a DISK_GB disk whose system container uses all of it."""
        info = json.loads(self.checked("get", name, "--format", "json").stdout)
        if int(info["Disk"]) >= DISK_GB:
            return
        self.checked("set", name, "--disk-size", str(DISK_GB))
        image = self.tart_home / "vms" / name / "disk.img"
        grow_layout(image)
        grow_system_container(image, self.runner, self.env)

    @contextmanager
    def machine(self, scenario: str, base: Base) -> Iterator[Machine]:
        """Clone the base for one run and always delete the clone afterwards.

        The host VM lock is held from before the clone until the clone is deleted, so a second
        run on this host fails fast instead of booting a second VM. A kept guest hands the lock
        to its own process.
        """
        lock = HostLock(self.vm_lock, scenario)
        lock.acquire()
        vm: Machine | None = None
        try:
            self.tart_home.mkdir(parents=True, exist_ok=True)
            check_free_space(shutil.disk_usage(self.tart_home).free, FREE_GB_PER_RUN)
            vm = Machine(self, machine_name(scenario, utc_stamp()), base)
            self.checked("clone", base.name, vm.name)
            try:
                yield vm
            finally:
                vm.destroy()
        finally:
            if vm is not None and vm.keep and vm.pid is not None:
                lock.hand_over(vm.pid)
            else:
                lock.release()

    def run(
        self,
        scenario: str,
        *,
        script: str,
        timeout: float,
        shares: Sequence[Share] = (),
        target_major: int | None = None,
        out_dir: Path | None = None,
        keep_vm: bool = False,
    ) -> RunResult:
        """Boot a fresh clone, enable Gatekeeper, run the script, record evidence."""
        validate_scenario(scenario)
        base = self.base(target_major)
        started = datetime.now(UTC)
        timings: dict[str, Any] = {"scenario": scenario, "started": started.isoformat()}
        clock = time.monotonic()
        with self.machine(scenario, base) as vm:
            timings["clone_seconds"] = round(time.monotonic() - clock, 1)
            timings.update(vm.start(shares))
            facts = vm.run(DEFAULT_COMMAND, timeout=60)
            env = env_facts(parse_guest_facts(facts.stdout), base.image, self.host)
            vm.run("sudo spctl --global-enable", timeout=60)
            mark = time.monotonic()
            result = vm.run("/bin/bash -s", timeout=timeout, stdin=script)
            timings["script_seconds"] = round(time.monotonic() - mark, 1)
            if keep_vm:
                vm.keep = True
        timings["ended"] = datetime.now(UTC).isoformat()
        timings["elapsed_seconds"] = round(time.monotonic() - clock, 1)
        outcome = RunResult(scenario, vm.name, result, env, timings, keep_vm)
        if out_dir is not None:
            write_evidence(out_dir, outcome)
        return outcome

    def reap(self) -> list[str]:
        """Delete run clones left behind by a crashed controller."""
        stale = [name for name in self.local_vms() if name.startswith(RUN_PREFIX)]
        for name in stale:
            self.tart_cmd("stop", name, "--timeout", "5", timeout=60)
            self.tart_cmd("delete", name, timeout=120)
        return stale

    def cleanup(self, *, purge: bool = False) -> list[str]:
        owner = read_owner(self.vm_lock)
        if self.vm_lock.exists() and (owner is None or (pid_alive(owner.pid) and not owner.kept)):
            raise ProofError(held_message(self.vm_lock, owner))
        reaped = self.reap() if self.tart.exists() else []
        clear_if_stale(self.vm_lock)
        if purge:
            shutil.rmtree(self.cache_root, ignore_errors=True)
        return reaped


def write_evidence(out_dir: Path, outcome: RunResult) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {
        "env.json": outcome.env,
        "timings.json": outcome.timings,
        "result.json": {
            "scenario": outcome.scenario,
            "exit_code": outcome.command.exit_code,
            "timed_out": outcome.command.timed_out,
        },
    }
    for name, data in files.items():
        (out_dir / name).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    (out_dir / "stdout.txt").write_text(outcome.command.stdout, encoding="utf-8")
    (out_dir / "stderr.txt").write_text(outcome.command.stderr, encoding="utf-8")
