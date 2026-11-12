"""Pure functions: selection, validation, parsing, and command construction."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from prove_engine.model import HostInfo, Image, ProofError, Share, VncAddress
from prove_engine.pins import (
    GUEST_USER,
    IMAGES,
    RUN_PREFIX,
    SCENARIO_RE,
    SHARE_NAME_RE,
    SSH_OPTIONS,
    TART_VERSION,
)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path: Path, expected: str) -> None:
    actual = sha256_of(path)
    if actual != expected:
        raise ProofError(f"checksum mismatch for {path.name}: expected {expected}, got {actual}")


def default_target_major(host_major: int) -> int:
    return 27 if host_major >= 27 else 26


def select_image(target_major: int) -> Image:
    for image in IMAGES:
        if image.major == target_major:
            return image
    supported = ", ".join(str(image.major) for image in IMAGES)
    raise ProofError(f"no pinned image for macOS {target_major}; supported: {supported}")


def check_host(host: HostInfo) -> None:
    if host.system != "Darwin" or host.machine != "arm64":
        raise ProofError(f"requires Apple Silicon macOS; found {host.system} {host.machine}")


def check_free_space(free_bytes: int, required_gb: int) -> None:
    if free_bytes < required_gb * 10**9:
        raise ProofError(
            f"need {required_gb} GB free, found {free_bytes // 10**9} GB; free space and retry"
        )


def validate_scenario(scenario: str) -> str:
    if not SCENARIO_RE.fullmatch(scenario):
        raise ProofError(f"scenario names are lowercase letters, digits, and hyphens: {scenario!r}")
    return scenario


def machine_name(scenario: str, stamp: str) -> str:
    return f"{RUN_PREFIX}{validate_scenario(scenario)}-{stamp}"


def parse_share(spec: str) -> Share:
    """Parse NAME=PATH or NAME=PATH:ro."""
    name, sep, rest = spec.partition("=")
    if not sep or not rest or not SHARE_NAME_RE.fullmatch(name):
        raise ProofError(f"share must look like NAME=PATH[:ro]: {spec!r}")
    read_only = rest.endswith(":ro")
    path = Path(rest[:-3] if read_only else rest).expanduser()
    return Share(name, path, read_only)


VNC_ADDRESS_RE = re.compile(r"vnc://:(?P<password>[^@\s]+)@(?P<host>[^:\s]+):(?P<port>\d+)")


def tart_run_argv(
    tart: Path, name: str, shares: Sequence[Share], *, vnc: bool = False
) -> list[str]:
    """The headless `tart run` command; `vnc` also serves the screen over Tart's VNC server."""
    argv = [str(tart), "run", "--no-graphics", "--no-audio", "--no-clipboard"]
    if vnc:
        argv.append("--vnc-experimental")
    for share in shares:
        argv += ["--dir", share.to_arg()]
    return [*argv, name]


def parse_vnc_address(text: str) -> VncAddress | None:
    """The address in the line Tart prints once its VNC server runs, or None before that."""
    found = VNC_ADDRESS_RE.search(text)
    if found is None:
        return None
    return VncAddress(found["host"], int(found["port"]), found["password"])


def ssh_argv(ip: str, remote: str) -> list[str]:
    return ["ssh", *SSH_OPTIONS, f"{GUEST_USER}@{ip}", remote]


def scp_argv(ip: str, source: str, destination: str) -> list[str]:
    return ["scp", "-q", "-r", *SSH_OPTIONS, source, destination]


def guest_endpoint(ip: str, remote_path: str) -> str:
    return f"{GUEST_USER}@{ip}:{remote_path}"


def parse_guest_facts(output: str) -> dict[str, Any]:
    """Parse the `sw_vers -productVersion && uname -m` output into guest facts."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if len(lines) < 2 or not re.match(r"^\d+(\.\d+)*$", lines[0]):
        raise ProofError(f"unexpected guest fact output: {output!r}")
    return {
        "guest_macos_version": lines[0],
        "guest_macos_major": int(lines[0].split(".")[0]),
        "guest_machine": lines[1],
    }


def env_facts(guest: Mapping[str, Any], image: Image, host: HostInfo) -> dict[str, Any]:
    return {
        **guest,
        "base_image": image.repository,
        "base_tag": image.tag,
        "base_digest": image.digest,
        "tart_version": TART_VERSION,
        "host_macos_major": host.major,
        "host_machine": host.machine,
    }
