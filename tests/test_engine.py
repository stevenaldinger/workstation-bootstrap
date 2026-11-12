"""Engine orchestration against a recording runner, evidence, and Tart unpacking."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from support import (
    FakeRunner,
    make_engine,
    result_of,
    stage_unpacked_tart,
)

import prove_engine.engine as engine_module
from prove_engine import (
    CommandResult,
    Engine,
    HostInfo,
    ProofError,
    pins,
    select_image,
    toolchain,
    write_evidence,
)
from prove_engine.model import Base, RunResult


def test_ensure_tart_rejects_a_tampered_archive_before_unpacking(tmp_path: Path) -> None:
    runner = FakeRunner()
    engine = make_engine(tmp_path, runner)
    engine.release_dir.mkdir(parents=True)
    (engine.release_dir / "tart.tar.gz").write_bytes(b"not the pinned release")
    with pytest.raises(ProofError, match="checksum mismatch"):
        engine.ensure_tart()
    assert not engine.tart.exists()
    assert runner.calls == []


def test_ensure_tart_rejects_a_binary_reporting_another_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = make_engine(tmp_path, FakeRunner([("--version", result_of("2.0.0\n"))]))
    stage_unpacked_tart(engine, monkeypatch)
    with pytest.raises(ProofError, match="expected 2.41.1"):
        engine.ensure_tart()


# --- Engine orchestration against a recording runner ---------------------------------


def test_base_pulls_and_clones_once_then_reuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeRunner(
        [
            ("--version", result_of(pins.TART_VERSION + "\n")),
            ("get ws-base-26", result_of(json.dumps({"Disk": pins.DISK_GB}))),
        ]
    )
    engine = make_engine(tmp_path, runner)
    stage_unpacked_tart(engine, monkeypatch)
    image = select_image(26)
    first = engine.base(26)
    assert first.name == "ws-base-26"
    assert [c for c in runner.tart_calls() if c.startswith(("pull", "clone"))] == [
        f"pull {image.ref}",
        f"clone {image.ref} ws-base-26",
    ]
    runner.calls.clear()
    runner.rules.insert(0, ("list", result_of("ws-base-26\n")))
    engine.base(26)
    assert not [c for c in runner.tart_calls() if c.startswith(("pull", "clone"))]


def test_base_refuses_a_pull_without_room_for_the_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeRunner(
        [("--version", result_of(pins.TART_VERSION + "\n")), ("list", result_of(""))]
    )
    engine = make_engine(tmp_path, runner, free_space=lambda _path: 10 * 1024**3)
    stage_unpacked_tart(engine, monkeypatch)
    with pytest.raises(ProofError, match="free"):
        engine.base(26)
    assert not [c for c in runner.tart_calls() if c.startswith("pull")]


def test_a_run_refuses_to_clone_without_room(tmp_path: Path) -> None:
    runner = FakeRunner()
    engine = make_engine(tmp_path, runner, free_space=lambda _path: 1024**3)
    with (
        pytest.raises(ProofError, match="free"),
        engine.machine("run", Base(select_image(26), "b")),
    ):
        pytest.fail("cloned without room")
    assert runner.tart_calls() == []
    assert not engine.vm_lock.exists()


def test_base_refuses_a_host_that_is_not_apple_silicon(tmp_path: Path) -> None:
    engine = Engine(
        tmp_path,
        host=HostInfo(0, "x86_64", "Linux"),
        env={},
        vm_lock=tmp_path / "vm.lock",
        free_space=lambda _path: 0,
        runner=FakeRunner(),
    )
    with pytest.raises(ProofError, match="Apple Silicon"):
        engine.base()


def test_machine_deletes_its_clone_when_the_run_fails(tmp_path: Path) -> None:
    runner = FakeRunner([("list", result_of(""))])
    engine = make_engine(tmp_path, runner)
    base = Base(select_image(26), "ws-base-26")
    with pytest.raises(RuntimeError, match="boom"), engine.machine("first-run", base) as vm:
        name = vm.name
        raise RuntimeError("boom")
    calls = runner.tart_calls()
    assert calls[0] == f"clone ws-base-26 {name}"
    assert f"delete {name}" in calls


def test_machine_keeps_its_clone_when_asked(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runner = FakeRunner()
    engine = make_engine(tmp_path, runner)
    base = Base(select_image(26), "ws-base-26")
    with engine.machine("debug", base) as vm:
        vm.keep = True
        vm.ip = "192.168.64.7"
    assert not [c for c in runner.tart_calls() if c.startswith("delete")]
    assert "ssh admin@192.168.64.7" in capsys.readouterr().out


def test_reap_deletes_only_run_clones(tmp_path: Path) -> None:
    listing = "ws-base-26\nwsrun-first-run-1\nwsrun-preview-2\nsomething-else\n"
    runner = FakeRunner([("list", result_of(listing))])
    engine = make_engine(tmp_path, runner)
    assert engine.reap() == ["wsrun-first-run-1", "wsrun-preview-2"]
    deleted = [c for c in runner.tart_calls() if c.startswith("delete")]
    assert deleted == ["delete wsrun-first-run-1", "delete wsrun-preview-2"]


def test_a_failed_tart_command_surfaces_its_stderr(tmp_path: Path) -> None:
    runner = FakeRunner([("clone", result_of("", 1, "no such VM"))])
    engine = make_engine(tmp_path, runner)
    with pytest.raises(ProofError, match="no such VM"):
        engine.checked("clone", "a", "b")


# --- Evidence ------------------------------------------------------------------------


def test_write_evidence_records_env_timings_and_output(tmp_path: Path) -> None:
    command = CommandResult(("ssh",), 0, "26.6.2\narm64\n", "", False, 1.0)
    outcome = RunResult(
        "first-run",
        "wsrun-first-run-1",
        command,
        {"guest_macos_major": 26},
        {"elapsed_seconds": 9.5},
    )
    write_evidence(tmp_path / "out", outcome)
    out = tmp_path / "out"
    assert json.loads((out / "env.json").read_text(encoding="utf-8")) == {"guest_macos_major": 26}
    assert json.loads((out / "timings.json").read_text(encoding="utf-8"))["elapsed_seconds"] == 9.5
    assert json.loads((out / "result.json").read_text(encoding="utf-8"))["exit_code"] == 0
    assert (out / "stdout.txt").read_text(encoding="utf-8") == "26.6.2\narm64\n"


# --- Unpacking -----------------------------------------------------------------------


def make_archive(path: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def test_unpack_tart_extracts_the_app_and_the_licence(tmp_path: Path) -> None:
    archive = tmp_path / "tart.tar.gz"
    make_archive(archive, {"tart.app/Contents/MacOS/tart": b"bin", "LICENSE": b"terms"})
    release = tmp_path / "release"
    release.mkdir()
    toolchain.unpack_tart(archive, release)
    assert (release / "tart.app" / "Contents" / "MacOS" / "tart").read_bytes() == b"bin"
    assert (release / "LICENSE").read_bytes() == b"terms"
    assert [p.name for p in release.iterdir() if p.name.startswith("unpack-")] == []


def test_unpack_tart_refuses_an_archive_without_the_app(tmp_path: Path) -> None:
    archive = tmp_path / "tart.tar.gz"
    make_archive(archive, {"other/file": b"x"})
    release = tmp_path / "release"
    release.mkdir()
    with pytest.raises(ProofError, match="no tart.app"):
        toolchain.unpack_tart(archive, release)
    assert list(release.iterdir()) == []


def test_unpack_tart_does_not_write_outside_the_release_folder(tmp_path: Path) -> None:
    archive = tmp_path / "tart.tar.gz"
    make_archive(archive, {"../escaped.txt": b"x", "tart.app/a": b"y"})
    release = tmp_path / "release"
    release.mkdir()
    with pytest.raises(tarfile.FilterError):
        toolchain.unpack_tart(archive, release)
    assert not (tmp_path / "escaped.txt").exists()


def test_ensure_disk_grows_a_base_that_has_the_shipped_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    grown: list[Path] = []

    def move_recovery(image: Path) -> bool:
        grown.append(image)
        return True

    def resize_container(image: Path, *_: object) -> None:
        grown.append(image)

    monkeypatch.setattr(engine_module, "grow_layout", move_recovery)
    monkeypatch.setattr(engine_module, "grow_system_container", resize_container)
    runner = FakeRunner([("get ws-base-26", result_of(json.dumps({"Disk": 50})))])
    engine = make_engine(tmp_path, runner)
    engine.ensure_disk("ws-base-26")
    assert f"set ws-base-26 --disk-size {pins.DISK_GB}" in runner.tart_calls()
    expected = engine.tart_home / "vms" / "ws-base-26" / "disk.img"
    assert grown == [expected, expected]


def test_ensure_disk_leaves_a_grown_base_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine_module, "grow_layout", lambda image: pytest.fail("moved"))
    runner = FakeRunner([("get ws-base-26", result_of(json.dumps({"Disk": pins.DISK_GB})))])
    engine = make_engine(tmp_path, runner)
    engine.ensure_disk("ws-base-26")
    assert not [c for c in runner.tart_calls() if c.startswith("set")]
