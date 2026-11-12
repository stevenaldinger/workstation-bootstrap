"""The engine: Tart acquisition, the base image, per-run clones, and evidence."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from prove_engine.cache import check_purgeable, claim_root, purge_root
from prove_engine.disk import disk_is_grown, grow_layout, grow_system_container
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
    FreeSpace,
    HostInfo,
    ProofError,
    Runner,
    RunResult,
    Share,
    Spawner,
)
from prove_engine.pins import (
    BASE_PREFIX,
    CLONE_TIMEOUT_SECONDS,
    DEFAULT_COMMAND,
    DISK_GB,
    FREE_GB_FIRST_PULL,
    FREE_GB_PER_RUN,
    PULL_TIMEOUT_SECONDS,
    RUN_PREFIX,
    TART_SHA256,
    TART_TIMEOUT_SECONDS,
    TART_URL,
    TART_VERSION,
)
from prove_engine.process import run_command, spawn_process, unique_suffix, utc_stamp
from prove_engine.toolchain import download, unpack_tart


class Engine:
    def __init__(
        self,
        cache_root: Path,
        *,
        host: HostInfo,
        env: Mapping[str, str],
        vm_lock: Path,
        free_space: FreeSpace,
        runner: Runner = run_command,
        spawner: Spawner = spawn_process,
    ) -> None:
        self.cache_root = cache_root
        self.vm_lock = vm_lock
        self.free_space = free_space
        self.spawner = spawner
        self.host = host
        self.runner = runner
        self.tart_home = cache_root / "tart-home"
        self.release_dir = cache_root / "tart" / TART_VERSION
        self.tart = self.release_dir / "tart.app" / "Contents" / "MacOS" / "tart"
        self.logs = cache_root / "logs"
        self.env = {**env, "TART_HOME": str(self.tart_home)}

    def tart_cmd(
        self, *args: str, timeout: float = TART_TIMEOUT_SECONDS, progress: str | None = None
    ) -> CommandResult:
        return self.runner(
            [str(self.tart), *args], env=self.env, timeout=timeout, progress=progress
        )

    def checked(
        self, *args: str, timeout: float = TART_TIMEOUT_SECONDS, progress: str | None = None
    ) -> CommandResult:
        result = self.tart_cmd(*args, timeout=timeout, progress=progress)
        if result.timed_out:
            raise ProofError(f"tart {' '.join(args)} timed out after {timeout:.0f}s")
        if result.exit_code != 0:
            detail = (result.stderr or result.stdout).strip()
            raise ProofError(f"tart {' '.join(args)} failed ({result.exit_code}): {detail}")
        return result

    def ensure_tart(self) -> Path:
        claim_root(self.cache_root)
        archive = self.release_dir / "tart.tar.gz"
        self.release_dir.mkdir(parents=True, exist_ok=True)
        if archive.exists() and not archive_matches(archive):
            # A cached archive that fails its checksum is a partial or damaged download.
            archive.unlink()
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
            check_free_space(self.free_space(self.tart_home), FREE_GB_FIRST_PULL)
            self.checked(
                "pull",
                image.ref,
                timeout=PULL_TIMEOUT_SECONDS,
                progress=f"tart pull of macOS {image.major} ({image.tag})",
            )
            self.checked("clone", image.ref, name, timeout=CLONE_TIMEOUT_SECONDS)
        self.ensure_disk(name)
        return Base(image, name)

    def ensure_disk(self, name: str) -> None:
        """Give the never-booted base a DISK_GB disk whose system container uses all of it.

        The state is read from the disk's partition table, not from the size Tart recorded, so a
        grow that stopped half way (the size set, the container not yet resized) is finished by
        the next call.
        """
        image = self.tart_home / "vms" / name / "disk.img"
        info = json.loads(self.checked("get", name, "--format", "json").stdout)
        if int(info["Disk"]) < DISK_GB:
            self.checked("set", name, "--disk-size", str(DISK_GB))
        if disk_is_grown(image):
            return
        grow_layout(image)
        grow_system_container(image, self.runner, self.env)
        if not disk_is_grown(image):
            raise ProofError(f"the disk of {name} is still not grown after resizing its container")

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
            claim_root(self.cache_root)
            self.tart_home.mkdir(parents=True, exist_ok=True)
            check_free_space(self.free_space(self.tart_home), FREE_GB_PER_RUN)
            vm = Machine(self, machine_name(scenario, utc_stamp(), unique_suffix()), base)
            self.checked("clone", base.name, vm.name, timeout=CLONE_TIMEOUT_SECONDS)
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
            env["gatekeeper"] = enable_gatekeeper(vm)
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

    def _reap(self) -> list[str]:
        """Delete every run clone, a live run's included; reach it only through `cleanup`."""
        stale = [name for name in self.local_vms() if name.startswith(RUN_PREFIX)]
        for name in stale:
            self.tart_cmd("stop", name, "--timeout", "5", timeout=60)
            self.tart_cmd("delete", name, timeout=120)
        return stale

    def cleanup(self, *, purge: bool = False) -> list[str]:
        """Delete leftover run clones; with `purge`, also the engine's own cache folders.

        A purge that would be refused is refused before anything is deleted.
        """
        home = Path(self.env.get("HOME") or Path.home())
        if purge:
            check_purgeable(self.cache_root, home)
        owner = read_owner(self.vm_lock)
        if self.vm_lock.exists() and (owner is None or (pid_alive(owner.pid) and not owner.kept)):
            raise ProofError(held_message(self.vm_lock, owner))
        reaped = self._reap() if self.tart.exists() else []
        clear_if_stale(self.vm_lock)
        if purge:
            purge_root(self.cache_root, home)
        return reaped


def archive_matches(archive: Path) -> bool:
    try:
        verify_sha256(archive, TART_SHA256)
    except ProofError:
        return False
    return True


def enable_gatekeeper(vm: Machine) -> str:
    """Turn Gatekeeper on and confirm it; the vanilla image ships with it off.

    `spctl --status` prints "assessments enabled" or "assessments disabled" (see spctl(8)).
    """
    enabled = vm.run("sudo spctl --global-enable", timeout=60)
    if enabled.exit_code != 0:
        detail = (enabled.stderr or enabled.stdout).strip()
        raise ProofError(f"enabling Gatekeeper failed ({enabled.exit_code}): {detail}")
    status = vm.run("spctl --status", timeout=60)
    if "assessments enabled" not in status.stdout:
        raise ProofError(
            f"Gatekeeper is not enabled after spctl --global-enable: {status.stdout.strip()!r}"
        )
    return "enabled"


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
