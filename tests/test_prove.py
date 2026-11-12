"""Unit tests for the proof engine's pure logic; none of them needs Tart or a virtual machine."""

from __future__ import annotations

import hashlib
import io
import json
import re
import subprocess
import sys
import tarfile
import time
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

import prove_engine.engine as engine_module
from prove_engine import (
    CommandResult,
    Engine,
    HostInfo,
    Machine,
    ProofError,
    Share,
    logic,
    parse_share,
    pins,
    run_command,
    select_image,
    toolchain,
    write_evidence,
)
from prove_engine.cli import main, parse_args
from prove_engine.model import Base, RunResult

REPO_ROOT = Path(__file__).resolve().parents[1]
APPLE_SILICON = HostInfo(15, "arm64", "Darwin")


class FakeRunner:
    """Records every command and answers from a script of (prefix, result) rules."""

    def __init__(self, rules: Sequence[tuple[str, CommandResult]] = ()) -> None:
        self.rules = list(rules)
        self.calls: list[tuple[str, ...]] = []
        self.timeouts: list[float | None] = []

    def __call__(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
        stdin: str | None = None,
    ) -> CommandResult:
        self.calls.append(tuple(argv))
        self.timeouts.append(timeout)
        line = " ".join(str(part) for part in argv)
        for fragment, result in self.rules:
            if fragment in line:
                return result
        return result_of("")

    def tart_calls(self) -> list[str]:
        return [" ".join(call[1:]) for call in self.calls if call[0].endswith("/tart")]


def result_of(stdout: str, code: int = 0, stderr: str = "") -> CommandResult:
    return CommandResult(("x",), code, stdout, stderr)


def make_engine(tmp_path: Path, runner: FakeRunner) -> Engine:
    return Engine(tmp_path / "cache", host=APPLE_SILICON, env={"PATH": "/usr/bin"}, runner=runner)


# --- Pins ----------------------------------------------------------------------------


def test_pins_are_well_formed() -> None:
    assert re.fullmatch(r"[0-9a-f]{64}", pins.TART_SHA256)
    assert pins.TART_VERSION in pins.TART_URL
    assert len({image.major for image in pins.IMAGES}) == len(pins.IMAGES)
    for image in pins.IMAGES:
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", image.digest)
        assert image.tag.startswith(f"{image.major}.")
        assert image.ref == f"{image.repository}@{image.digest}"


def test_entrypoint_script_prints_help(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "prove.py"), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "usage: prove.py" in result.stdout


# --- Pin verification ----------------------------------------------------------------


@pytest.mark.parametrize("content", [b"", b"x", b"a" * 3_000_000], ids=["empty", "tiny", "large"])
def test_verify_sha256_accepts_matching_content(tmp_path: Path, content: bytes) -> None:
    target = tmp_path / "f.bin"
    target.write_bytes(content)
    logic.verify_sha256(target, hashlib.sha256(content).hexdigest())


@pytest.mark.parametrize(
    "expected",
    ["0" * 64, "", hashlib.sha256(b"other").hexdigest().upper()],
    ids=["wrong", "empty", "uppercase"],
)
def test_verify_sha256_rejects_other_digests(tmp_path: Path, expected: str) -> None:
    target = tmp_path / "f.bin"
    target.write_bytes(b"payload")
    with pytest.raises(ProofError, match="checksum mismatch"):
        logic.verify_sha256(target, expected)


def test_ensure_tart_rejects_a_tampered_archive_before_unpacking(tmp_path: Path) -> None:
    runner = FakeRunner()
    engine = make_engine(tmp_path, runner)
    engine.release_dir.mkdir(parents=True)
    (engine.release_dir / "tart.tar.gz").write_bytes(b"not the pinned release")
    with pytest.raises(ProofError, match="checksum mismatch"):
        engine.ensure_tart()
    assert not engine.tart.exists()
    assert runner.calls == []


def stage_unpacked_tart(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> None:
    """Leave a stand-in archive that matches the (patched) pin and an already-unpacked binary."""
    engine.release_dir.mkdir(parents=True)
    (engine.release_dir / "tart.tar.gz").write_bytes(b"stand-in")
    engine.tart.parent.mkdir(parents=True)
    engine.tart.write_text("", encoding="utf-8")
    monkeypatch.setattr(engine_module, "TART_SHA256", hashlib.sha256(b"stand-in").hexdigest())


def test_ensure_tart_rejects_a_binary_reporting_another_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = make_engine(tmp_path, FakeRunner([("--version", result_of("2.0.0\n"))]))
    stage_unpacked_tart(engine, monkeypatch)
    with pytest.raises(ProofError, match="expected 2.41.1"):
        engine.ensure_tart()


# --- Selection and validation --------------------------------------------------------


@pytest.mark.parametrize(
    ("host_major", "expected"), [(14, 26), (15, 26), (26, 26), (27, 27), (28, 27)]
)
def test_default_target_major(host_major: int, expected: int) -> None:
    assert logic.default_target_major(host_major) == expected


def test_select_image_returns_the_pinned_image_for_each_major() -> None:
    assert "macos-tahoe-vanilla" in select_image(26).repository
    assert "macos-golden-gate-vanilla" in select_image(27).repository


@pytest.mark.parametrize("major", [0, 15, 25, 28, -1])
def test_select_image_refuses_unpinned_majors(major: int) -> None:
    with pytest.raises(ProofError, match="no pinned image"):
        select_image(major)


@pytest.mark.parametrize(
    ("host", "ok"),
    [
        (HostInfo(15, "arm64", "Darwin"), True),
        (HostInfo(15, "x86_64", "Darwin"), False),
        (HostInfo(0, "arm64", "Linux"), False),
        (HostInfo(0, "x86_64", "Linux"), False),
    ],
)
def test_check_host_requires_apple_silicon_macos(host: HostInfo, ok: bool) -> None:
    if ok:
        logic.check_host(host)
    else:
        with pytest.raises(ProofError, match="Apple Silicon"):
            logic.check_host(host)


@pytest.mark.parametrize(
    ("free_gb", "required_gb", "ok"),
    [(60, 60, True), (59, 60, False), (0, 15, False), (1000, 15, True)],
)
def test_check_free_space_boundary(free_gb: int, required_gb: int, ok: bool) -> None:
    if ok:
        logic.check_free_space(free_gb * 10**9, required_gb)
    else:
        with pytest.raises(ProofError, match="GB free"):
            logic.check_free_space(free_gb * 10**9, required_gb)


@pytest.mark.parametrize(
    "scenario",
    ["first-run", "a", "re-run-2", "x" * 41],
)
def test_machine_name_accepts_simple_scenarios(scenario: str) -> None:
    assert logic.machine_name(scenario, "20260101000000") == (f"wsrun-{scenario}-20260101000000")


@pytest.mark.parametrize(
    "scenario",
    ["", "First", "has space", "../escape", "a/b", "-lead", "x" * 42, "semi;colon", "ünï", "ok\n"],
)
def test_machine_name_rejects_unsafe_scenarios(scenario: str) -> None:
    with pytest.raises(ProofError, match="scenario names"):
        logic.machine_name(scenario, "20260101000000")


# --- Shares --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("spec", "name", "path", "read_only"),
    [
        ("repo=/srv/repo", "repo", Path("/srv/repo"), False),
        ("repo=/srv/repo:ro", "repo", Path("/srv/repo"), True),
        ("out_1=/tmp/out", "out_1", Path("/tmp/out"), False),
    ],
)
def test_parse_share(spec: str, name: str, path: Path, read_only: bool) -> None:
    share = parse_share(spec)
    assert (share.name, share.path, share.read_only) == (name, path, read_only)


@pytest.mark.parametrize(
    "spec", ["", "repo", "=/x", "repo=", "bad name=/x", "../x=/x", "r:epo=/x", "repo\n=/x"]
)
def test_parse_share_rejects_malformed_specs(spec: str) -> None:
    with pytest.raises(ProofError, match="share must look like"):
        parse_share(spec)


def test_share_argument_encodes_the_read_only_option() -> None:
    assert Share("repo", Path("/srv/repo"), True).to_arg() == "repo:/srv/repo:ro"
    assert Share("out", Path("/srv/out")).to_arg() == "out:/srv/out"


# --- Command construction ------------------------------------------------------------


def test_tart_run_argv_is_headless_and_orders_shares_before_the_name() -> None:
    shares = [Share("repo", Path("/r"), True), Share("out", Path("/o"))]
    argv = logic.tart_run_argv(Path("/c/tart"), "wsrun-x-1", shares)
    assert argv[:3] == ["/c/tart", "run", "--no-graphics"]
    assert argv[-1] == "wsrun-x-1"
    assert argv[argv.index("--dir") + 1] == "repo:/r:ro"
    assert argv.count("--dir") == 2


def test_tart_run_argv_without_shares_has_no_dir_flag() -> None:
    assert "--dir" not in logic.tart_run_argv(Path("/c/tart"), "n", [])


def test_ssh_argv_ignores_host_config_and_keys_and_ends_with_the_command() -> None:
    argv = logic.ssh_argv("192.168.64.9", "echo 'a b'")
    assert argv[0] == "ssh"
    assert argv[argv.index("-F") + 1] == "/dev/null"
    assert "PubkeyAuthentication=no" in argv
    assert "StrictHostKeyChecking=no" in argv
    assert argv[-2:] == ["admin@192.168.64.9", "echo 'a b'"]


def test_scp_argv_places_source_and_destination_last() -> None:
    argv = logic.scp_argv("10.0.0.2", "/local/a", logic.guest_endpoint("10.0.0.2", "/tmp/a"))
    assert argv[:3] == ["scp", "-q", "-r"]
    assert argv[-2:] == ["/local/a", "admin@10.0.0.2:/tmp/a"]


# --- Guest facts ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("output", "version", "major", "machine"),
    [
        ("26.6.2\narm64\n", "26.6.2", 26, "arm64"),
        ("27.0\narm64", "27.0", 27, "arm64"),
        ("\n  26.1  \n  arm64  \n\n", "26.1", 26, "arm64"),
    ],
)
def test_parse_guest_facts(output: str, version: str, major: int, machine: str) -> None:
    facts = logic.parse_guest_facts(output)
    assert facts == {
        "guest_macos_version": version,
        "guest_macos_major": major,
        "guest_machine": machine,
    }


@pytest.mark.parametrize("output", ["", "arm64", "26.6.2", "unknown\narm64", "ssh: refused\n"])
def test_parse_guest_facts_rejects_unexpected_output(output: str) -> None:
    with pytest.raises(ProofError, match="unexpected guest fact output"):
        logic.parse_guest_facts(output)


def test_env_facts_records_digest_tool_version_and_host() -> None:
    image = select_image(26)
    facts = logic.env_facts({"guest_macos_major": 26}, image, APPLE_SILICON)
    assert facts["base_digest"] == image.digest
    assert facts["tart_version"] == pins.TART_VERSION
    assert (facts["host_macos_major"], facts["host_machine"]) == (15, "arm64")


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


# --- Engine orchestration against a recording runner ---------------------------------


def test_base_pulls_and_clones_once_then_reuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeRunner([("--version", result_of(pins.TART_VERSION + "\n"))])
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


def test_base_refuses_a_host_that_is_not_apple_silicon(tmp_path: Path) -> None:
    engine = Engine(tmp_path, host=HostInfo(0, "x86_64", "Linux"), env={}, runner=FakeRunner())
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


def test_cleanup_purge_removes_the_cache(tmp_path: Path) -> None:
    engine = make_engine(tmp_path, FakeRunner())
    engine.cache_root.mkdir(parents=True)
    (engine.cache_root / "marker").write_text("x", encoding="utf-8")
    assert engine.cleanup(purge=True) == []
    assert not engine.cache_root.exists()


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


# --- Argument parsing ----------------------------------------------------------------


def test_parse_args_run_defaults() -> None:
    args = parse_args(["run", "first-run"])
    assert args.action == "run"
    assert args.scenario == "first-run"
    assert (
        args.action == "run" and args.script is None and args.shell_command == pins.DEFAULT_COMMAND
    )
    assert args.timeout == 600
    assert args.share == []
    assert not args.keep_vm


def test_parse_args_run_options() -> None:
    args = parse_args(
        [
            "--cache-root", "/c",
            "run", "preview", "--target-major", "27", "--share", "a=/x:ro", "--share", "b=/y",
            "--timeout", "30", "--out", "/o", "--keep-vm", "--script", "/s.sh",
        ]
    )  # fmt: skip
    assert args.cache_root == Path("/c")
    assert (args.target_major, args.share, args.timeout) == (27, ["a=/x:ro", "b=/y"], 30)
    assert args.keep_vm and args.script == Path("/s.sh")


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["run"],
        ["bogus"],
        ["run", "x", "--command", "a", "--script", "b"],
        ["base", "--target-major", "x"],
    ],
)
def test_parse_args_rejects_invalid_invocations(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        parse_args(argv)
    assert exit_info.value.code == 2


def test_main_rejects_a_bad_scenario_before_any_download(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cache = tmp_path / "cache"
    assert main(["--cache-root", str(cache), "run", "Bad Name"]) == 2
    assert "scenario names" in capsys.readouterr().err
    assert not cache.exists()


def test_main_rejects_a_bad_share_before_any_download(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cache = tmp_path / "cache"
    assert main(["--cache-root", str(cache), "run", "ok", "--share", "bad"]) == 2
    assert "share must look like" in capsys.readouterr().err
    assert not cache.exists()


# --- Snapshots -----------------------------------------------------------------------


def test_snapshot_flushes_the_guest_before_copying_its_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeRunner()
    engine = make_engine(tmp_path, runner)
    engine.cache_root.mkdir(parents=True)
    vm = Machine(engine, "wsrun-x-1", Base(select_image(26), "ws-base-26"))
    vm.ip = "10.0.0.3"
    monkeypatch.setattr(Machine, "start", lambda self, shares=(): {})
    vm.snapshot("clean")
    sequence = [call[-1] if call[0] == "ssh" else " ".join(call[1:]) for call in runner.calls]
    assert sequence == ["sync", "clone wsrun-x-1 wsrun-x-1-snap-clean --overwrite"]


def test_restore_replaces_the_machine_with_its_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = FakeRunner()
    engine = make_engine(tmp_path, runner)
    vm = Machine(engine, "wsrun-x-1", Base(select_image(26), "ws-base-26"))
    monkeypatch.setattr(Machine, "start", lambda self, shares=(): {})
    vm.restore("clean")
    assert runner.tart_calls() == ["delete wsrun-x-1", "clone wsrun-x-1-snap-clean wsrun-x-1"]


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
