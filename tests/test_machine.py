"""Process handling and the Machine: timeouts and SSH commands."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from support import (
    FakeRunner,
    make_engine,
    result_of,
)

from prove_engine import (
    Machine,
    ProofError,
    Share,
    VncAddress,
    pins,
    run_command,
    select_image,
)
from prove_engine.model import Base

# --- Timeouts and process handling ---------------------------------------------------


def test_run_command_returns_output_and_exit_code() -> None:
    result = run_command(["/bin/sh", "-c", "echo out; echo err >&2; exit 3"], timeout=10)
    assert (result.exit_code, result.stdout, result.stderr) == (3, "out\n", "err\n")
    assert not result.timed_out


def test_run_command_feeds_stdin() -> None:
    result = run_command(["/bin/cat"], timeout=10, stdin="from stdin")
    assert result.stdout == "from stdin"


def test_run_command_kills_the_whole_process_group_on_timeout() -> None:
    started = time.monotonic()
    result = run_command(["/bin/sh", "-c", "sleep 30 & sleep 30"], timeout=0.5)
    assert result.timed_out
    assert result.exit_code == pins.EXIT_TIMED_OUT
    assert time.monotonic() - started < 10


def test_run_command_reports_a_missing_executable() -> None:
    with pytest.raises(ProofError, match="cannot start"):
        run_command(["/nonexistent/binary"])


def test_machine_run_passes_the_timeout_and_ssh_command_to_the_runner(tmp_path: Path) -> None:
    runner = FakeRunner()
    engine = make_engine(tmp_path, runner)
    engine.cache_root.mkdir(parents=True)
    vm = Machine(engine, "wsrun-x-1", Base(select_image(26), "ws-base-26"))
    vm.ip = "192.168.64.5"
    vm.run("uname -m", timeout=42.0, stdin="data")
    assert runner.timeouts == [42.0]
    assert runner.calls[0][-1] == "uname -m"
    askpass = engine.cache_root / "askpass.sh"
    assert askpass.stat().st_mode & 0o777 == 0o700


def test_machine_run_requires_a_started_machine(tmp_path: Path) -> None:
    vm = Machine(make_engine(tmp_path, FakeRunner()), "wsrun-x-1", Base(select_image(26), "b"))
    with pytest.raises(ProofError, match="call start"):
        vm.run("true", timeout=1)


def test_wait_ssh_gives_up_after_its_deadline(tmp_path: Path) -> None:
    runner = FakeRunner([("ssh", result_of("", 255, "refused"))])
    engine = make_engine(tmp_path, runner)
    engine.cache_root.mkdir(parents=True)
    vm = Machine(engine, "wsrun-x-1", Base(select_image(26), "b"))
    vm.ip = "10.0.0.9"
    with pytest.raises(ProofError, match="not ready"):
        vm.wait_ssh(timeout=0)


def test_the_base_machine_is_never_booted(tmp_path: Path) -> None:
    engine = make_engine(tmp_path, FakeRunner())
    vm = Machine(engine, "ws-base-26", Base(select_image(26), "ws-base-26"))
    with pytest.raises(ProofError, match="never booted"):
        vm.start()


def test_start_refuses_a_share_that_is_not_a_directory(tmp_path: Path) -> None:
    engine = make_engine(tmp_path, FakeRunner())
    vm = Machine(engine, "wsrun-x-1", Base(select_image(26), "b"))
    with pytest.raises(ProofError, match="not a directory"):
        vm.start([Share("repo", tmp_path / "missing", True)])


# --- The screen over VNC -----------------------------------------------------------


def test_vnc_address_comes_from_the_line_tart_wrote_to_the_machines_log(tmp_path: Path) -> None:
    engine = make_engine(tmp_path, FakeRunner())
    vm = Machine(engine, "wsrun-x-1", Base(select_image(26), "ws-base-26"))
    engine.logs.mkdir(parents=True)
    vm.log_path.write_text(
        "starting\nVNC server is running at vnc://:pw-one-two@127.0.0.1:5901\n", encoding="utf-8"
    )
    assert vm.vnc_address(timeout=1) == VncAddress("127.0.0.1", 5901, "pw-one-two")


def test_vnc_address_gives_up_with_the_log_tail_when_tart_printed_none(tmp_path: Path) -> None:
    engine = make_engine(tmp_path, FakeRunner())
    vm = Machine(engine, "wsrun-x-1", Base(select_image(26), "ws-base-26"))
    engine.logs.mkdir(parents=True)
    vm.log_path.write_text("no address here\n", encoding="utf-8")
    with pytest.raises(ProofError, match="no VNC address"):
        vm.vnc_address(timeout=0)


def test_snapshot_boots_again_with_the_screen_served_when_it_was_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = make_engine(tmp_path, FakeRunner())
    engine.cache_root.mkdir(parents=True)
    vm = Machine(engine, "wsrun-x-1", Base(select_image(26), "ws-base-26"))
    vm.ip = "10.0.0.3"
    vm.vnc = True
    booted: list[bool] = []

    def record_start(self: Machine, shares: object = (), *, vnc: bool = False) -> dict[str, float]:
        booted.append(vnc)
        return {}

    monkeypatch.setattr(Machine, "start", record_start)
    vm.snapshot("clean")
    assert booted == [True]


# --- Snapshots -----------------------------------------------------------------------


def test_snapshot_flushes_the_guest_before_copying_its_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeRunner()
    engine = make_engine(tmp_path, runner)
    engine.cache_root.mkdir(parents=True)
    vm = Machine(engine, "wsrun-x-1", Base(select_image(26), "ws-base-26"))
    vm.ip = "10.0.0.3"
    monkeypatch.setattr(Machine, "start", lambda self, shares=(), *, vnc=False: {})
    vm.snapshot("clean")
    sequence = [call[-1] if call[0] == "ssh" else " ".join(call[1:]) for call in runner.calls]
    assert sequence == ["sync", "clone wsrun-x-1 wsrun-x-1-snap-clean --overwrite"]


def test_restore_replaces_the_machine_with_its_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeRunner()
    engine = make_engine(tmp_path, runner)
    vm = Machine(engine, "wsrun-x-1", Base(select_image(26), "ws-base-26"))
    monkeypatch.setattr(Machine, "start", lambda self, shares=(), *, vnc=False: {})
    vm.restore("clean")
    assert runner.tart_calls() == ["delete wsrun-x-1", "clone wsrun-x-1-snap-clean wsrun-x-1"]
