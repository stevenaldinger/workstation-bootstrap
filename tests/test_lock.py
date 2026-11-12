"""The host VM lock: one VM run at a time, atomic, with stale-owner takeover."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from support import FakeRunner, FakeSpawner, make_engine, result_of

from prove_engine import ProofError, lock, select_image
from prove_engine.model import Base

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE = Base(select_image(26), "ws-base-26")


def dead_pid() -> int:
    """A process id that no longer exists: a child that has already exited and been reaped."""
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    return child.pid


def write_owner(path: Path, pid: int, *, kept: bool = False) -> None:
    path.mkdir(parents=True)
    owner = {"pid": pid, "label": "earlier-run", "started": "2026-01-01T00:00:00+00:00"}
    (path / lock.OWNER_FILE).write_text(json.dumps({**owner, "kept": kept}), encoding="utf-8")


def test_acquire_creates_the_lock_with_its_owner_and_release_removes_it(tmp_path: Path) -> None:
    held = lock.HostLock(tmp_path / "vm.lock", "first-run")
    held.acquire()
    owner = lock.read_owner(tmp_path / "vm.lock")
    assert owner is not None
    assert (owner.label, owner.kept) == ("first-run", False)
    held.release()
    assert not (tmp_path / "vm.lock").exists()


def test_a_second_run_is_refused_with_the_holder_named(tmp_path: Path) -> None:
    first = lock.HostLock(tmp_path / "vm.lock", "first-run")
    first.acquire()
    with pytest.raises(ProofError, match=r"another VM run holds.*'first-run'.*Only one VM runs"):
        lock.HostLock(tmp_path / "vm.lock", "second-run").acquire()
    assert lock.read_owner(tmp_path / "vm.lock") is not None
    first.release()


def test_a_lock_made_by_hand_counts_as_held(tmp_path: Path) -> None:
    path = tmp_path / "vm.lock"
    path.mkdir()
    with pytest.raises(ProofError, match="no readable owner.*rmdir"):
        lock.HostLock(path, "run").acquire()
    assert path.is_dir()


def test_a_lock_with_a_dead_owner_is_taken_over(tmp_path: Path) -> None:
    path = tmp_path / "vm.lock"
    write_owner(path, dead_pid())
    held = lock.HostLock(path, "new-run")
    held.acquire()
    owner = lock.read_owner(path)
    assert owner is not None
    assert owner.label == "new-run"
    held.release()


def test_removing_a_lock_that_changed_hands_puts_it_back(tmp_path: Path) -> None:
    path = tmp_path / "vm.lock"
    held = lock.HostLock(path, "live-run")
    held.acquire()
    assert lock.remove_lock(path, expected_pid=dead_pid()) is False
    owner = lock.read_owner(path)
    assert owner is not None
    assert owner.label == "live-run"
    assert [p.name for p in tmp_path.iterdir()] == ["vm.lock"]


def test_removing_a_lock_that_is_already_gone_succeeds(tmp_path: Path) -> None:
    assert lock.remove_lock(tmp_path / "vm.lock", expected_pid=1) is True


def test_exactly_one_of_many_racing_processes_wins(tmp_path: Path) -> None:
    path = tmp_path / "vm.lock"
    script = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "from prove_engine import ProofError\n"
        "from prove_engine.lock import HostLock\n"
        "held = HostLock(Path(sys.argv[1]), 'racer')\n"
        "try:\n"
        "    held.acquire()\n"
        "except ProofError:\n"
        "    print('lost')\n"
        "else:\n"
        "    print('won')\n"
        "    time.sleep(1.5)\n"
    )
    racers = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(path)],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            text=True,
        )
        for _ in range(8)
    ]
    results = [racer.communicate()[0].strip() for racer in racers]
    assert sorted(results) == ["lost"] * 7 + ["won"]


def test_hand_over_keeps_the_lock_for_a_surviving_guest(tmp_path: Path) -> None:
    held = lock.HostLock(tmp_path / "vm.lock", "debug")
    held.acquire()
    held.hand_over(4242)
    held.release()
    owner = lock.read_owner(tmp_path / "vm.lock")
    assert owner is not None
    assert (owner.pid, owner.kept) == (4242, True)


def test_engine_machine_refuses_a_second_vm_before_cloning(tmp_path: Path) -> None:
    runner = FakeRunner()
    engine = make_engine(tmp_path, runner)
    write_owner(engine.vm_lock, os.getpid())
    with pytest.raises(ProofError, match="another VM run holds"), engine.machine("run", BASE):
        pytest.fail("the second VM started")
    assert runner.tart_calls() == []


def test_engine_machine_releases_the_lock_when_the_run_fails(tmp_path: Path) -> None:
    engine = make_engine(tmp_path, FakeRunner([("list", result_of(""))]))
    with pytest.raises(RuntimeError, match="boom"), engine.machine("run", BASE):
        assert engine.vm_lock.is_dir()
        raise RuntimeError("boom")
    assert not engine.vm_lock.exists()


def test_engine_machine_releases_the_lock_when_the_clone_fails(tmp_path: Path) -> None:
    runner = FakeRunner([("clone", result_of("", 1, "no space"))])
    engine = make_engine(tmp_path, runner)
    with pytest.raises(ProofError, match="no space"), engine.machine("run", BASE):
        pytest.fail("the clone failed")
    assert not engine.vm_lock.exists()


def test_a_kept_guest_holds_the_lock_until_its_process_ends(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    spawner = FakeSpawner()
    runner = FakeRunner([("/tart ip ", result_of("192.168.64.7\n")), ("ssh", result_of(""))])
    engine = make_engine(tmp_path, runner, spawner)
    with engine.machine("debug", BASE) as vm:
        vm.start()
        vm.keep = True
    owner = lock.read_owner(engine.vm_lock)
    assert owner is not None
    assert (owner.pid, owner.kept) == (spawner.processes[0].pid, True)
    assert "kept wsrun-debug-" in capsys.readouterr().out
    spawner.processes[0].finish()
    with engine.machine("next", BASE):
        pass
    assert not engine.vm_lock.exists()


def test_cleanup_refuses_while_a_run_holds_the_lock(tmp_path: Path) -> None:
    runner = FakeRunner()
    engine = make_engine(tmp_path, runner)
    write_owner(engine.vm_lock, os.getpid())
    with pytest.raises(ProofError, match="another VM run holds"):
        engine.cleanup()
    assert runner.calls == []


def test_cleanup_clears_a_stale_lock(tmp_path: Path) -> None:
    engine = make_engine(tmp_path, FakeRunner())
    write_owner(engine.vm_lock, dead_pid())
    engine.cleanup()
    assert not engine.vm_lock.exists()


def test_cleanup_reaps_a_kept_guest_whose_process_is_gone(tmp_path: Path) -> None:
    runner = FakeRunner([("list", result_of("wsrun-debug-1\n"))])
    engine = make_engine(tmp_path, runner)
    engine.tart.parent.mkdir(parents=True)
    engine.tart.write_text("", encoding="utf-8")
    write_owner(engine.vm_lock, dead_pid(), kept=True)
    assert engine.cleanup() == ["wsrun-debug-1"]
    assert not engine.vm_lock.exists()
