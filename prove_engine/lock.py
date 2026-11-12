"""A host-wide lock that lets one VM run at a time.

Two VMs booted at once on one Mac have killed runs, so every run takes this lock before it
clones a machine. The lock is a directory created with `mkdir`, which is atomic, holding an
`owner.json` that names the process. A lock whose owning process is gone is stale and is taken
over. A lock directory with no owner file, such as one made by hand with `mkdir`, counts as
held, because nothing proves it is free.

`--keep-vm` leaves a guest running after the engine exits, so the lock is handed to the guest's
process and stays held until that process ends.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from prove_engine.model import ProofError

OWNER_FILE = "owner.json"
ACQUIRE_ATTEMPTS = 3


@dataclass(frozen=True)
class Owner:
    pid: int
    label: str
    started: str
    kept: bool = False


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except (ValueError, OverflowError):
        return False
    return True


def read_owner(path: Path) -> Owner | None:
    """The recorded owner, or None when the folder has no readable owner file."""
    try:
        raw = json.loads((path / OWNER_FILE).read_text(encoding="utf-8"))
        return Owner(int(raw["pid"]), str(raw["label"]), str(raw["started"]), bool(raw["kept"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def held_message(path: Path, owner: Owner | None) -> str:
    if owner is None:
        return (
            f"the host VM lock {path} exists with no readable owner (made by hand, or by a run "
            f"that is still starting). If no VM is running, remove it with: rmdir {path}"
        )
    return (
        f"another VM run holds the host VM lock {path}: run {owner.label!r}, pid {owner.pid}, "
        f"started {owner.started}. Only one VM runs at a time on this host; wait for that run "
        "to finish, or stop it first."
    )


class HostLock:
    """The lock for one run; `acquire` raises ProofError while another live run holds it."""

    def __init__(self, path: Path, label: str) -> None:
        self.path = path
        self.label = label
        self.held = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(ACQUIRE_ATTEMPTS):
            try:
                self.path.mkdir()
            except FileExistsError:
                owner = read_owner(self.path)
                if (
                    owner is not None
                    and not pid_alive(owner.pid)
                    and remove_lock(self.path, owner.pid)
                ):
                    continue
                raise ProofError(held_message(self.path, owner)) from None
            self._write_owner(os.getpid(), kept=False)
            self.held = True
            return
        raise ProofError(f"could not take the host VM lock {self.path}")

    def _write_owner(self, pid: int, *, kept: bool) -> None:
        owner = Owner(pid, self.label, datetime.now(UTC).isoformat(timespec="seconds"), kept)
        (self.path / OWNER_FILE).write_text(json.dumps(owner.__dict__) + "\n", encoding="utf-8")

    def hand_over(self, pid: int) -> None:
        """Keep the lock after this process exits, owned by a guest that keeps running."""
        self._write_owner(pid, kept=True)
        self.held = False

    def release(self) -> None:
        if self.held:
            shutil.rmtree(self.path, ignore_errors=True)
            self.held = False


def remove_lock(path: Path, expected_pid: int) -> bool:
    """Remove a lock folder whose owner was `expected_pid`; True when no stale lock remains.

    The folder is renamed away before it is deleted, so two removers cannot both succeed. If the
    renamed folder turns out to belong to someone else, because the lock changed hands between
    the check and the rename, it is put back and False is returned.
    """
    doomed = path.with_name(f"{path.name}.removed-{uuid.uuid4().hex[:8]}")
    try:
        path.rename(doomed)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    owner = read_owner(doomed)
    if owner is None or owner.pid != expected_pid:
        try:
            doomed.rename(path)
        except OSError:
            shutil.rmtree(doomed, ignore_errors=True)
        return False
    shutil.rmtree(doomed, ignore_errors=True)
    return True


def clear_if_stale(path: Path) -> bool:
    """Remove the lock when its owner is gone; True when no lock remains."""
    if not path.exists():
        return True
    owner = read_owner(path)
    if owner is None or pid_alive(owner.pid):
        return False
    return remove_lock(path, owner.pid)
