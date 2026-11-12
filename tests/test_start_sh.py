"""start.sh: the Mac check, the checksum gates, and the hand-off, against fake commands."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
from dataclasses import dataclass
from pathlib import Path

import pytest
from starter_support import FAKE_TOOLS, install_fake

REPO_ROOT = Path(__file__).resolve().parents[1]
START_SH = REPO_ROOT / "start.sh"
MISE_URL = re.compile(r'fetch "(https://github\.com/jdx/mise[^"]+)"')
PINS = ("MISE_SHA256", "MANIFEST_SHA256")
STARTER_FILES = {
    "start.py": b"print(1)\n",
    "start_base.py": b"X = 1\n",
    "stage1.toml": b"[bootstrap.packages]\n",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fake_mise_tarball() -> bytes:
    """A release archive whose mise binary is the logging fake."""
    script = f"#!/bin/sh\nexec '{sys.executable}' '{FAKE_TOOLS}' mise \"$@\"\n".encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo("mise/bin/mise")
        info.size, info.mode = len(script), 0o755
        archive.addfile(info, io.BytesIO(script))
    return buffer.getvalue()


@dataclass
class Sandbox:
    """A home, fake commands, and a copy of start.sh whose pins match the fabricated downloads."""

    home: Path
    script: Path
    fake_dir: Path
    bin_dir: Path
    env: dict[str, str]
    downloads: dict[str, str]

    def run(
        self, *args: str, arch: str = "arm64", macos: str = "26.1"
    ) -> subprocess.CompletedProcess[str]:
        env = {**self.env, "FAKE_ARCH": arch, "FAKE_MACOS": macos}
        return subprocess.run(
            ["/bin/bash", str(self.script), *args],
            capture_output=True, text=True, check=False, env=env, cwd=self.home,
        )  # fmt: skip

    def serve(self, url: str, content: bytes) -> None:
        source = self.fake_dir / f"served-{sha256(url.encode())[:10]}"
        source.write_bytes(content)
        self.downloads[url] = str(source)
        (self.fake_dir / "downloads.json").write_text(json.dumps(self.downloads), encoding="utf-8")

    def calls(self, tool: str) -> list[list[str]]:
        log = self.fake_dir / "calls.jsonl"
        if not log.exists():
            return []
        rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        return [row[1:-1] for row in rows if row[0] == tool]


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    home, fake_dir, bin_dir = tmp_path / "home", tmp_path / "fake", tmp_path / "bin"
    for directory in (home, fake_dir, bin_dir):
        directory.mkdir()
    for tool in ("curl", "uname", "sw_vers", "sudo"):
        install_fake(bin_dir, tool)
    tarball = fake_mise_tarball()
    manifest = "".join(f"{sha256(body)}  {name}\n" for name, body in STARTER_FILES.items()).encode()
    text = START_SH.read_text(encoding="utf-8")
    for name, content in zip(PINS, (tarball, manifest), strict=True):
        text, count = re.subn(
            rf'^{name}="[0-9a-f]{{64}}"$', f'{name}="{sha256(content)}"', text, flags=re.M
        )
        assert count == 1, name
    script = tmp_path / "start.sh"
    script.write_text(text, encoding="utf-8")
    raw = re.search(r'^RAW_URL="(.*)"$', text, flags=re.M)
    commit = re.search(r'^FILES_COMMIT="(.*)"$', text, flags=re.M)
    assert raw and commit
    base = raw.group(1).replace("${FILES_COMMIT}", commit.group(1))
    mise_url = MISE_URL.search(text)
    assert mise_url
    resolved = mise_url.group(1).replace("${MISE_VERSION}", "2026.11.6")
    env = {
        "HOME": str(home),
        "FAKE_DIR": str(fake_dir),
        "PATH": os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"]),
        "WS_BREW_BIN": str(tmp_path / "no-brew"),
    }
    box = Sandbox(home, script, fake_dir, bin_dir, env, {})
    box.serve(resolved, tarball)
    box.serve(f"{base}/start.sha256", manifest)
    for name, body in STARTER_FILES.items():
        box.serve(f"{base}/{name}", body)
    return box


def test_a_supported_mac_installs_mise_then_hands_off_to_start_py(sandbox: Sandbox) -> None:
    result = sandbox.run("--personal-account", "owner@example.com")
    assert result.returncode == 0, result.stderr
    assert sandbox.calls("sudo")[0] == ["-v"]
    assert sandbox.calls("mise")[0] == ["install", "python@3.15.2", "uv@0.13.7"]
    work = sandbox.home / ".cache" / "workstation-bootstrap"
    assert sandbox.calls("mise")[1] == [
        "exec", "python@3.15.2", "uv@0.13.7", "--", "python", str(work / "start.py"),
        "--stage1", str(work / "stage1.toml"), "--personal-account", "owner@example.com",
    ]  # fmt: skip
    assert os.access(sandbox.home / ".local" / "bin" / "mise", os.X_OK)


@pytest.mark.parametrize("macos", ["26.0", "26.4.1", "27.0"])
def test_supported_macos_majors_proceed(sandbox: Sandbox, macos: str) -> None:
    assert sandbox.run(macos=macos).returncode == 0


@pytest.mark.parametrize(
    ("arch", "macos", "cause"),
    [
        ("x86_64", "26.1", "unsupported architecture x86_64"),
        ("arm64", "25.6", "unsupported macOS 25"),
        ("arm64", "28.0", "unsupported macOS 28"),
        ("arm64", "15.7", "unsupported macOS 15"),
    ],
)
def test_an_unsupported_mac_stops_before_changing_anything(
    sandbox: Sandbox, arch: str, macos: str, cause: str
) -> None:
    result = sandbox.run(arch=arch, macos=macos)
    assert result.returncode == 2
    assert cause in result.stderr
    assert sandbox.calls("sudo") == []
    assert sandbox.calls("curl") == []
    assert list(sandbox.home.iterdir()) == []


def test_a_mise_download_that_fails_its_checksum_is_refused(sandbox: Sandbox) -> None:
    for url in list(sandbox.downloads):
        if "jdx/mise" in url:
            sandbox.serve(url, b"tampered archive")
    result = sandbox.run()
    assert result.returncode == 1
    assert "checksum mismatch" in result.stderr
    assert sandbox.calls("mise") == []
    assert not (sandbox.home / ".local" / "bin" / "mise").exists()


@pytest.mark.parametrize("name", ["start.py", "start_base.py", "stage1.toml"])
def test_a_starter_file_that_fails_its_checksum_is_refused(sandbox: Sandbox, name: str) -> None:
    for url in list(sandbox.downloads):
        if url.endswith(f"/{name}"):
            sandbox.serve(url, b"tampered")
    result = sandbox.run()
    assert result.returncode == 1
    assert "checksum mismatch in the starter files" in result.stderr
    assert not any(call[0] == "exec" for call in sandbox.calls("mise"))


def test_a_tampered_manifest_is_refused_before_any_file_is_fetched(sandbox: Sandbox) -> None:
    for url in list(sandbox.downloads):
        if url.endswith("/start.sha256"):
            sandbox.serve(url, b"0" * 64 + b"  start.py\n")
    result = sandbox.run()
    assert result.returncode == 1
    assert "checksum mismatch" in result.stderr
    assert not any(call[0] == "exec" for call in sandbox.calls("mise"))
    assert len(sandbox.calls("curl")) == 2


def test_a_failed_download_is_reported(sandbox: Sandbox) -> None:
    sandbox.downloads.clear()
    (sandbox.fake_dir / "downloads.json").write_text("{}", encoding="utf-8")
    result = sandbox.run()
    assert result.returncode == 1
    assert "download failed" in result.stderr
    assert sandbox.calls("mise") == []


def test_start_sh_stays_small() -> None:
    lines = START_SH.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 60
    assert lines[0] == "#!/bin/bash"
    assert "set -euo pipefail" in lines
