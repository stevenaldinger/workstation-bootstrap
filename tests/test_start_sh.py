"""start.sh against fake commands: the Mac check, argument validation, and the checksum gates."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from starter_support import FAKE_TOOLS, REPO_ROOT, install_fake, starter_files

START_SH = REPO_ROOT / "start.sh"
MISE_URL = re.compile(r'fetch "(https://github\.com/jdx/mise[^"]+)"')
ACCOUNT = "owner@example.com"
SPACED_HOME = "home with space"


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


def script_constant(text: str, name: str) -> str:
    match = re.search(rf'^{name}="([^"]*)"$', text, flags=re.M)
    assert match, name
    return match.group(1)


@dataclass
class Sandbox:
    """A home, fake commands, and a copy of start.sh whose pins match the fabricated downloads."""

    home: Path
    script: Path
    fake_dir: Path
    bin_dir: Path
    env: dict[str, str]
    downloads: dict[str, str]
    starter: dict[str, bytes]

    def run(
        self, *args: str, arch: str = "arm64", macos: str = "26.1", **extra: str
    ) -> subprocess.CompletedProcess[str]:
        env = {**self.env, "FAKE_ARCH": arch, "FAKE_MACOS": macos, **extra}
        return subprocess.run(
            ["/bin/bash", str(self.script), *args],
            capture_output=True, text=True, check=False, env=env, cwd=self.home,
        )  # fmt: skip

    def serve(self, url: str, content: bytes) -> None:
        source = self.fake_dir / f"served-{sha256(url.encode())[:10]}"
        source.write_bytes(content)
        self.downloads[url] = str(source)
        (self.fake_dir / "downloads.json").write_text(json.dumps(self.downloads), encoding="utf-8")

    def serve_matching(self, suffix: str, content: bytes) -> None:
        for url in [url for url in self.downloads if url.endswith(suffix)]:
            self.serve(url, content)

    def rows(self, tool: str) -> list[list[Any]]:
        log = self.fake_dir / "calls.jsonl"
        if not log.exists():
            return []
        rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        return [row for row in rows if row[0] == tool]

    def calls(self, tool: str) -> list[list[str]]:
        return [row[1:-1] for row in self.rows(tool)]


def build_sandbox(tmp_path: Path, home_name: str = "home") -> Sandbox:
    home, fake_dir, bin_dir = tmp_path / home_name, tmp_path / "fake", tmp_path / "bin"
    for directory in (home, fake_dir, bin_dir):
        directory.mkdir()
    for tool in ("curl", "uname", "sw_vers", "sudo"):
        install_fake(bin_dir, tool)
    tarball = fake_mise_tarball()
    starter = {name: (REPO_ROOT / name).read_bytes() for name in starter_files()}
    manifest = "".join(f"{sha256(body)}  {name}\n" for name, body in starter.items()).encode()
    text = START_SH.read_text(encoding="utf-8")
    # A sandbox sees only fakes: no Homebrew directory, and a short refresh interval.
    replacements = {
        "MISE_SHA256": f'"{sha256(tarball)}"',
        "MANIFEST_SHA256": f'"{sha256(manifest)}"',
        "BREW_BIN": f'"{tmp_path / "no-brew"}"',
        "KEEPALIVE_SECONDS": "1",
    }
    for name, value in replacements.items():
        text, count = re.subn(rf"^{name}=.*$", f"{name}={value}", text, flags=re.M)
        assert count == 1, name
    script = tmp_path / "start.sh"
    script.write_text(text, encoding="utf-8")
    commit = script_constant(text, "FILES_COMMIT")
    base = f"https://raw.githubusercontent.com/stevenaldinger/workstation-bootstrap/{commit}"
    mise_url = MISE_URL.search(text)
    assert mise_url
    env = {
        "HOME": str(home),
        "FAKE_DIR": str(fake_dir),
        "PATH": os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"]),
    }
    box = Sandbox(home, script, fake_dir, bin_dir, env, {}, starter)
    box.serve(
        mise_url.group(1).replace("${MISE_VERSION}", script_constant(text, "MISE_VERSION")), tarball
    )
    box.serve(f"{base}/start.sha256", manifest)
    for name, body in starter.items():
        box.serve(f"{base}/{name}", body)
    return box


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    return build_sandbox(tmp_path)


def installed(box: Sandbox) -> list[str]:
    return sorted(path.name for path in box.home.iterdir())


def test_a_supported_mac_installs_mise_then_hands_off_to_start_py(sandbox: Sandbox) -> None:
    result = sandbox.run("--personal-account", ACCOUNT, "--verbose")
    assert result.returncode == 0, result.stderr
    assert sandbox.calls("sudo")[0] == ["-v"]
    work = sandbox.home / ".cache" / "workstation-bootstrap"
    handoff = sandbox.calls("mise")[-1]
    assert handoff[0] == "exec"
    assert handoff[handoff.index("--") + 1 :] == [
        "python", str(work / "start.py"), "--stage1", str(work / "stage1.toml"),
        "--personal-account", ACCOUNT, "--verbose",
    ]  # fmt: skip
    assert os.access(sandbox.home / ".local" / "bin" / "mise", os.X_OK)
    for name, body in sandbox.starter.items():
        assert (work / name).read_bytes() == body


def test_the_runtimes_installed_are_the_ones_the_hand_off_runs_with(sandbox: Sandbox) -> None:
    assert sandbox.run("--personal-account", ACCOUNT).returncode == 0
    install, handoff = sandbox.calls("mise")
    assert install[0] == "install"
    tools = install[1:]
    assert {tool.split("@")[0] for tool in tools} == {"python", "uv", "gh"}
    assert handoff[1 : handoff.index("--")] == tools


def test_mise_trust_prompts_are_answered_only_for_the_install(sandbox: Sandbox) -> None:
    assert sandbox.run("--personal-account", ACCOUNT).returncode == 0
    install, handoff = (row[-1]["env"] for row in sandbox.rows("mise"))
    assert install["MISE_YES"] == "1"
    assert handoff["MISE_YES"] is None


def test_the_hand_off_sees_the_homebrew_commands_after_mise(sandbox: Sandbox) -> None:
    assert sandbox.run("--personal-account", ACCOUNT).returncode == 0
    path = sandbox.rows("mise")[-1][-1]["env"]["PATH"].split(os.pathsep)
    local = str(sandbox.home / ".local" / "bin")
    assert path[0] == local
    assert str(sandbox.script.parent / "no-brew") in path


def test_the_homebrew_directory_is_the_prefix_mise_documents() -> None:
    # https://mise.jdx.dev/bootstrap/packages/brew.html: linked commands go in /opt/homebrew/bin.
    assert script_constant(START_SH.read_text(encoding="utf-8"), "BREW_BIN") == "/opt/homebrew/bin"


@pytest.mark.parametrize("macos", ["26.0", "26.4.1", "27.0"])
def test_supported_macos_majors_proceed(sandbox: Sandbox, macos: str) -> None:
    assert sandbox.run("--personal-account", ACCOUNT, macos=macos).returncode == 0


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
    result = sandbox.run("--personal-account", ACCOUNT, arch=arch, macos=macos)
    assert result.returncode == 2
    assert cause in result.stderr
    assert sandbox.calls("sudo") == []
    assert sandbox.calls("curl") == []
    assert installed(sandbox) == []


@pytest.mark.parametrize(
    ("args", "cause"),
    [
        ([], "--personal-account is required"),
        (["--verbose"], "--personal-account is required"),
        (["--personal-account"], "needs a value"),
        (["--personal-account", ""], "--personal-account is required"),
        (["--personal-account=", "--verbose"], "--personal-account is required"),
        (["--personal-account", "--verbose"], "needs an email or sign-in address"),
        (["--personal-account", "two words"], "needs an email or sign-in address"),
    ],
)
def test_a_bad_account_argument_stops_before_anything_is_installed(
    sandbox: Sandbox, args: list[str], cause: str
) -> None:
    result = sandbox.run(*args)
    assert result.returncode == 2
    assert cause in result.stderr
    assert sandbox.calls("sudo") == []
    assert sandbox.calls("curl") == []
    assert installed(sandbox) == []


def test_the_equals_form_of_the_account_argument_is_accepted(sandbox: Sandbox) -> None:
    assert sandbox.run(f"--personal-account={ACCOUNT}").returncode == 0


def test_help_prints_the_usage_and_does_nothing_else(sandbox: Sandbox) -> None:
    for flag in ("-h", "--help"):
        result = sandbox.run(flag)
        assert result.returncode == 0
        assert "--personal-account" in result.stdout
    assert sandbox.calls("sudo") == []
    assert installed(sandbox) == []


def test_a_denied_administrator_prompt_stops_before_any_download(sandbox: Sandbox) -> None:
    result = sandbox.run("--personal-account", ACCOUNT, FAKE_SUDO_FAILS="1")
    assert result.returncode == 1
    assert "administrator access is required" in result.stderr
    assert sandbox.calls("curl") == []


def test_a_mise_download_that_fails_its_checksum_is_refused(sandbox: Sandbox) -> None:
    for url in list(sandbox.downloads):
        if "jdx/mise" in url:
            sandbox.serve(url, b"tampered archive")
    result = sandbox.run("--personal-account", ACCOUNT)
    assert result.returncode == 1
    assert "checksum mismatch" in result.stderr
    assert sandbox.calls("mise") == []
    assert not (sandbox.home / ".local" / "bin" / "mise").exists()


@pytest.mark.parametrize("name", starter_files())
def test_a_starter_file_that_fails_its_checksum_is_refused(sandbox: Sandbox, name: str) -> None:
    sandbox.serve_matching(f"/{name}", b"tampered")
    result = sandbox.run("--personal-account", ACCOUNT)
    assert result.returncode == 1
    assert "checksum mismatch in the starter files" in result.stderr
    assert not any(call[0] == "exec" for call in sandbox.calls("mise"))


def test_a_tampered_manifest_is_refused_before_any_file_is_fetched(sandbox: Sandbox) -> None:
    sandbox.serve_matching("/start.sha256", b"0" * 64 + b"  start.py\n")
    result = sandbox.run("--personal-account", ACCOUNT)
    assert result.returncode == 1
    assert "checksum mismatch" in result.stderr
    assert not any(call[0] == "exec" for call in sandbox.calls("mise"))
    assert len(sandbox.calls("curl")) == 2


def test_a_failed_download_is_reported(sandbox: Sandbox) -> None:
    sandbox.downloads.clear()
    (sandbox.fake_dir / "downloads.json").write_text("{}", encoding="utf-8")
    result = sandbox.run("--personal-account", ACCOUNT)
    assert result.returncode == 1
    assert "download failed" in result.stderr
    assert sandbox.calls("mise") == []


def test_every_download_is_https_only_with_a_modern_tls_floor_and_time_limits(
    sandbox: Sandbox,
) -> None:
    assert sandbox.run("--personal-account", ACCOUNT).returncode == 0
    calls = sandbox.calls("curl")
    assert len(calls) > 2
    for call in calls:
        assert call[call.index("--proto") + 1] == "=https"
        assert call[call.index("--proto-redir") + 1] == "=https"
        assert "--tlsv1.2" in call
        assert "--connect-timeout" in call
        assert "--max-time" in call
        assert "--retry" in call
        assert call[-1].startswith("https://")


def test_a_home_folder_with_a_space_runs_end_to_end(tmp_path: Path) -> None:
    box = build_sandbox(tmp_path, home_name=SPACED_HOME)
    result = box.run("--personal-account", ACCOUNT)
    assert result.returncode == 0, result.stderr
    work = box.home / ".cache" / "workstation-bootstrap"
    assert box.calls("mise")[-1][-5] == str(work / "start.py")
    assert (work / "start.py").is_file()


def sudo_refreshes(box: Sandbox, **extra: str) -> int:
    """How many `sudo -n true` refreshes ran while the hand-off command took 3 seconds."""
    result = box.run("--personal-account", ACCOUNT, FAKE_EXEC_SECONDS="3", **extra)
    assert result.returncode == 0, result.stderr
    time.sleep(1.5)
    return sum(1 for call in box.calls("sudo") if call == ["-n", "true"])


def test_the_administrator_session_is_refreshed_while_the_hand_off_runs(sandbox: Sandbox) -> None:
    assert sudo_refreshes(sandbox) >= 2


def test_the_refresh_loop_stops_at_the_first_failed_refresh(sandbox: Sandbox) -> None:
    assert sudo_refreshes(sandbox, FAKE_SUDO_REFRESH_FAILS="1") == 1


@pytest.mark.skipif(sys.platform != "darwin", reason="the system bash 3.2 ships with macOS")
def test_the_runner_executes_start_sh_under_the_bash_that_macos_ships() -> None:
    version = subprocess.run(
        ["/bin/bash", "--version"], capture_output=True, text=True, check=True
    ).stdout
    assert "version 3.2" in version
