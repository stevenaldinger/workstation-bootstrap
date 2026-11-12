"""Pure logic: pins, selection, validation, shares, command construction, guest facts."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest
from support import (
    APPLE_SILICON,
)

from prove_engine import (
    HostInfo,
    ProofError,
    Share,
    VncAddress,
    logic,
    parse_share,
    pins,
    select_image,
)

# --- Pins ----------------------------------------------------------------------------


def test_pins_are_well_formed() -> None:
    assert re.fullmatch(r"[0-9a-f]{64}", pins.TART_SHA256)
    assert pins.TART_VERSION in pins.TART_URL
    assert len({image.major for image in pins.IMAGES}) == len(pins.IMAGES)
    for image in pins.IMAGES:
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", image.digest)
        assert image.tag.startswith(f"{image.major}.")
        assert image.ref == f"{image.repository}@{image.digest}"


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


def test_tart_run_argv_serves_the_screen_over_vnc_only_when_asked() -> None:
    argv = logic.tart_run_argv(Path("/c/tart"), "wsrun-x-1", [], vnc=True)
    assert "--vnc-experimental" in argv
    assert argv.index("--vnc-experimental") < argv.index("wsrun-x-1")
    assert "--vnc-experimental" not in logic.tart_run_argv(Path("/c/tart"), "wsrun-x-1", [])


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "VNC server is running at vnc://:spike-minimum-school-wolf@127.0.0.1:53327\n",
            VncAddress("127.0.0.1", 53327, "spike-minimum-school-wolf"),
        ),
        (
            "noise\nVNC server is running at vnc://:a-b@127.0.0.1:5900\nmore\n",
            VncAddress("127.0.0.1", 5900, "a-b"),
        ),
        ("vnc://:pw@localhost:1", VncAddress("localhost", 1, "pw")),
    ],
)
def test_parse_vnc_address_reads_the_address_tart_prints(text: str, expected: VncAddress) -> None:
    assert logic.parse_vnc_address(text) == expected


@pytest.mark.parametrize(
    "text",
    ["", "tart run exited", "vnc://127.0.0.1:5900", "vnc://:pw@127.0.0.1", "vnc://:pw@h:notaport"],
)
def test_parse_vnc_address_returns_nothing_without_a_full_address(text: str) -> None:
    assert logic.parse_vnc_address(text) is None


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
