"""Engine orchestration against a recording runner, evidence, and Tart unpacking."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest
from disk_support import make_disk, resize_container, stage_grown_base
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
from prove_engine.disk import disk_is_grown, grow_layout
from prove_engine.model import Base, RunResult
from prove_engine.toolchain import download


def test_ensure_tart_rejects_a_tampered_archive_before_unpacking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeRunner()
    engine = make_engine(tmp_path, runner)
    engine.release_dir.mkdir(parents=True)
    (engine.release_dir / "tart.tar.gz").write_bytes(b"not the pinned release")

    def fetch_the_same_wrong_bytes(url: str, destination: Path) -> None:
        destination.write_bytes(b"not the pinned release")

    monkeypatch.setattr(engine_module, "download", fetch_the_same_wrong_bytes)
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
    stage_grown_base(engine.tart_home / "vms" / "ws-base-26" / "disk.img")
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


def test_cleanup_deletes_only_run_clones(tmp_path: Path) -> None:
    listing = "ws-base-26\nwsrun-first-run-1\nwsrun-preview-2\nsomething-else\n"
    runner = FakeRunner([("list", result_of(listing))])
    engine = make_engine(tmp_path, runner)
    engine.tart.parent.mkdir(parents=True)
    engine.tart.write_text("", encoding="utf-8")
    assert engine.cleanup() == ["wsrun-first-run-1", "wsrun-preview-2"]
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


# --- Growing the base disk ----------------------------------------------------------


class DiskHarness:
    """A synthetic base disk, with the host-only container resize replaced by its table effect."""

    def __init__(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recorded_gb: int, *, stage: str
    ) -> None:
        self.runner = FakeRunner([("get ws-base-26", result_of(json.dumps({"Disk": recorded_gb})))])
        self.engine = make_engine(tmp_path, self.runner)
        self.image = self.engine.tart_home / "vms" / "ws-base-26" / "disk.img"
        self.image.parent.mkdir(parents=True)
        make_disk(self.image)
        if stage in {"moved", "grown"}:
            grow_layout(self.image)
        if stage == "grown":
            resize_container(self.image)
        self.resizes = 0
        monkeypatch.setattr(engine_module, "grow_system_container", self.resize)

    def resize(self, image: Path, *_: object) -> None:
        self.resizes += 1
        resize_container(image)

    def sets(self) -> list[str]:
        return [c for c in self.runner.tart_calls() if c.startswith("set")]


def test_ensure_disk_grows_a_base_that_has_the_shipped_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = DiskHarness(tmp_path, monkeypatch, 50, stage="shipped")
    harness.engine.ensure_disk("ws-base-26")
    assert harness.sets() == [f"set ws-base-26 --disk-size {pins.DISK_GB}"]
    assert harness.resizes == 1
    assert disk_is_grown(harness.image)


def test_ensure_disk_finishes_a_grow_that_stopped_before_the_layout_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = DiskHarness(tmp_path, monkeypatch, pins.DISK_GB, stage="shipped")
    harness.engine.ensure_disk("ws-base-26")
    assert harness.sets() == []
    assert harness.resizes == 1
    assert disk_is_grown(harness.image)


def test_ensure_disk_finishes_a_grow_that_stopped_before_the_container_resize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = DiskHarness(tmp_path, monkeypatch, pins.DISK_GB, stage="moved")
    harness.engine.ensure_disk("ws-base-26")
    assert harness.sets() == []
    assert harness.resizes == 1
    assert disk_is_grown(harness.image)


def test_ensure_disk_leaves_a_grown_base_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = DiskHarness(tmp_path, monkeypatch, pins.DISK_GB, stage="grown")
    before = harness.image.read_bytes()
    harness.engine.ensure_disk("ws-base-26")
    assert harness.sets() == []
    assert harness.resizes == 0
    assert harness.image.read_bytes() == before


def test_ensure_disk_fails_when_the_resize_leaves_the_container_small(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness = DiskHarness(tmp_path, monkeypatch, 50, stage="shipped")
    monkeypatch.setattr(engine_module, "grow_system_container", lambda *_: None)
    with pytest.raises(ProofError, match="still not grown"):
        harness.engine.ensure_disk("ws-base-26")


# --- The run path --------------------------------------------------------------------

# What the guest prints: `sw_vers -productVersion` and `uname -m` (arm64), `spctl --status`
# (see spctl(8)), and `tart ip` (one address and a newline).
GUEST_FACTS = "26.6.2\narm64\n"
GATEKEEPER_ON = "assessments enabled\n"
GATEKEEPER_OFF = "assessments disabled\n"


def run_engine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *rules: tuple[str, CommandResult],
) -> tuple[Engine, FakeRunner]:
    runner = FakeRunner(
        [
            *rules,
            ("/tart ip ", result_of("192.168.64.7\n")),
            ("sw_vers", result_of(GUEST_FACTS)),
            ("spctl --global-enable", result_of("")),
            ("spctl --status", result_of(GATEKEEPER_ON)),
            ("/bin/bash -s", result_of("script output\n")),
        ]
    )
    engine = make_engine(tmp_path, runner)
    monkeypatch.setattr(
        Engine, "base", lambda self, major=None: Base(select_image(26), "ws-base-26")
    )
    return engine, runner


def guest_commands(runner: FakeRunner) -> list[str]:
    return [call[-1] for call in runner.calls if call[0] == "ssh"]


def test_run_enables_gatekeeper_before_the_script_and_records_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, runner = run_engine(tmp_path, monkeypatch)
    outcome = engine.run("first-run", script="echo hi\n", timeout=30, out_dir=tmp_path / "out")
    assert guest_commands(runner)[-4:] == [
        pins.DEFAULT_COMMAND,
        "sudo spctl --global-enable",
        "spctl --status",
        "/bin/bash -s",
    ]
    script_call = next(i for i, call in enumerate(runner.calls) if call[-1] == "/bin/bash -s")
    assert runner.stdins[script_call] == "echo hi\n"
    assert outcome.env["gatekeeper"] == "enabled"
    env = json.loads((tmp_path / "out" / "env.json").read_text(encoding="utf-8"))
    assert env["gatekeeper"] == "enabled"
    assert outcome.command.stdout == "script output\n"


def test_run_stops_before_the_script_when_gatekeeper_cannot_be_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, runner = run_engine(
        tmp_path,
        monkeypatch,
        ("spctl --global-enable", result_of("", 1, "operation not permitted")),
    )
    with pytest.raises(
        ProofError, match=r"enabling Gatekeeper failed \(1\): operation not permitted"
    ):
        engine.run("first-run", script="echo hi\n", timeout=30)
    assert "/bin/bash -s" not in guest_commands(runner)
    assert any(c.startswith("delete wsrun-first-run-") for c in runner.tart_calls())
    assert not engine.vm_lock.exists()


def test_run_stops_before_the_script_when_gatekeeper_stays_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, runner = run_engine(
        tmp_path, monkeypatch, ("spctl --status", result_of(GATEKEEPER_OFF))
    )
    with pytest.raises(ProofError, match="Gatekeeper is not enabled"):
        engine.run("first-run", script="echo hi\n", timeout=30)
    assert "/bin/bash -s" not in guest_commands(runner)


def test_run_reports_a_failing_script_without_raising(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine, runner = run_engine(tmp_path, monkeypatch, ("/bin/bash -s", result_of("", 7, "boom")))
    outcome = engine.run("first-run", script="exit 7\n", timeout=30)
    assert outcome.command.exit_code == 7
    assert any(c.startswith("delete wsrun-first-run-") for c in runner.tart_calls())


# --- Limits on Tart commands ---------------------------------------------------------


def test_pull_and_clone_get_long_limits_and_pull_reports_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeRunner(
        [
            ("--version", result_of(pins.TART_VERSION + "\n")),
            ("get ws-base-26", result_of('{"Disk": 100}')),
        ]
    )
    engine = make_engine(tmp_path, runner)
    stage_unpacked_tart(engine, monkeypatch)
    stage_grown_base(engine.tart_home / "vms" / "ws-base-26" / "disk.img")
    engine.base(26)
    calls = zip(runner.calls, runner.timeouts, runner.progress, strict=True)
    limits = {call[1]: (timeout, label) for call, timeout, label in calls if "/tart" in call[0]}
    assert limits["pull"][0] == pins.PULL_TIMEOUT_SECONDS
    assert limits["pull"][1] is not None
    assert limits["clone"][0] == pins.CLONE_TIMEOUT_SECONDS
    assert limits["get"][0] == pins.TART_TIMEOUT_SECONDS


def test_a_tart_command_that_times_out_is_reported_as_such(tmp_path: Path) -> None:
    timed_out = CommandResult(("tart",), pins.EXIT_TIMED_OUT, "", "", True)
    engine = make_engine(tmp_path, FakeRunner([("pull", timed_out)]))
    with pytest.raises(ProofError, match=r"tart pull x timed out after 5s"):
        engine.checked("pull", "x", timeout=5)


def test_two_runs_of_one_scenario_in_one_second_get_different_clone_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(engine_module, "utc_stamp", lambda: "20260101000000")
    runner = FakeRunner([("list", result_of(""))])
    engine = make_engine(tmp_path, runner)
    base = Base(select_image(26), "ws-base-26")
    names = []
    for _ in range(2):
        with engine.machine("first-run", base) as vm:
            names.append(vm.name)
    assert names[0] != names[1]
    assert all(name.startswith("wsrun-first-run-20260101000000-") for name in names)


@pytest.mark.parametrize(
    "url", ["http://example.test/tart.tar.gz", "file:///etc/hosts", "ftp://x/y"]
)
def test_a_download_from_a_non_https_address_is_refused_before_any_request(
    tmp_path: Path, url: str
) -> None:
    destination = tmp_path / "tart.tar.gz"
    with pytest.raises(ProofError, match="non-https"):
        download(url, destination)
    assert not destination.exists()


def test_a_corrupt_cached_tart_archive_is_downloaded_once_more(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeRunner([("--version", result_of(pins.TART_VERSION + "\n"))])
    engine = make_engine(tmp_path, runner)
    stage_unpacked_tart(engine, monkeypatch)
    archive = engine.release_dir / "tart.tar.gz"
    archive.write_bytes(b"half a download")
    downloads: list[str] = []

    def fetch(url: str, destination: Path) -> None:
        downloads.append(url)
        destination.write_bytes(b"stand-in")

    monkeypatch.setattr(engine_module, "download", fetch)
    assert engine.ensure_tart() == engine.tart
    assert downloads == [pins.TART_URL]


def test_a_tart_archive_that_is_still_wrong_after_a_fresh_download_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = make_engine(tmp_path, FakeRunner())
    stage_unpacked_tart(engine, monkeypatch)
    archive = engine.release_dir / "tart.tar.gz"
    archive.write_bytes(b"corrupt")
    downloads: list[str] = []

    def fetch(url: str, destination: Path) -> None:
        downloads.append(url)
        destination.write_bytes(b"also not the release")

    monkeypatch.setattr(engine_module, "download", fetch)
    with pytest.raises(ProofError, match="checksum mismatch"):
        engine.ensure_tart()
    assert len(downloads) == 1
