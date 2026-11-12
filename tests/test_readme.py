"""The README's bootstrap command runs as written, and its stated pins match the code."""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from starter_support import FAKE_TOOLS, REPO_ROOT, install_fake, starter_files

from prove_engine import pins

README = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
SHELLS = [shell for shell in ("/bin/sh", "/bin/zsh", "/bin/bash") if Path(shell).exists()]
COMMIT = "0123456789abcdef0123456789abcdef01234567"
ACCOUNT = "owner@example.com"
# Records where it ran from and its arguments.
MARKER_SCRIPT = b'#!/bin/bash\nprintf "%s\\n" "$0" "$@" > "$MARKER"\n'


def bootstrap_command() -> str:
    section = README.split("## Bootstrap command", 1)[1].split("\n## ", 1)[0]
    match = re.search(r"```sh\n(.*?)```", section, flags=re.S)
    assert match, "the README has no bootstrap command block"
    return match.group(1)


def filled(command: str, sha256: str) -> str:
    return (
        command.replace("<commit>", COMMIT)
        .replace("<sha256>", sha256)
        .replace("<email or sign-in address>", ACCOUNT)
    )


class Run:
    """The documented command run in a shell with a fake curl that serves a marker script."""

    def __init__(self, root: Path, shell: str, served: bytes, claimed_sha256: str) -> None:
        self.root = root
        self.shell = shell
        self.marker = root / "marker.txt"
        self.temporary = root / "tmp"
        bin_dir, fake_dir = root / "bin", root / "fake"
        for directory in (self.temporary, bin_dir, fake_dir):
            directory.mkdir(parents=True)
        install_fake(bin_dir, "curl")
        source = fake_dir / "served"
        source.write_bytes(served)
        base = "https://raw.githubusercontent.com/stevenaldinger/workstation-bootstrap"
        url = f"{base}/{COMMIT}/start.sh"
        (fake_dir / "downloads.json").write_text(json.dumps({url: str(source)}), encoding="utf-8")
        self.env = {
            "PATH": os.pathsep.join([str(bin_dir), "/usr/bin", "/bin"]),
            "FAKE_DIR": str(fake_dir),
            "SANDBOX_TMP": str(self.temporary),
            "MARKER": str(self.marker),
            "HOME": str(root),
        }
        # Real mktemp, but under the sandbox: macOS mktemp -d ignores TMPDIR.
        relocate = 'mktemp() { command mktemp -d "$SANDBOX_TMP/tmp.XXXXXXXX"; }\n'
        self.command = relocate + filled(bootstrap_command(), claimed_sha256)

    def run(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.shell, "-c", self.command],
            capture_output=True, text=True, check=False, env=self.env, cwd=self.root,
        )  # fmt: skip


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@pytest.mark.parametrize("shell", SHELLS)
def test_the_documented_command_runs_the_script_that_matches_the_hash(
    tmp_path: Path, shell: str
) -> None:
    run = Run(tmp_path, shell, MARKER_SCRIPT, sha256(MARKER_SCRIPT))
    result = run.run()
    assert result.returncode == 0, result.stderr
    ran_from, *arguments = run.marker.read_text(encoding="utf-8").splitlines()
    assert arguments == ["--personal-account", ACCOUNT]
    assert Path(ran_from).name == "start.sh"
    assert FAKE_TOOLS.exists()


@pytest.mark.parametrize("shell", SHELLS)
def test_the_documented_command_refuses_a_script_that_does_not_match_the_hash(
    tmp_path: Path, shell: str
) -> None:
    run = Run(tmp_path, shell, MARKER_SCRIPT + b"# tampered\n", sha256(MARKER_SCRIPT))
    result = run.run()
    assert result.returncode != 0
    assert not run.marker.exists()


def test_the_documented_command_downloads_into_a_fresh_private_folder_each_time(
    tmp_path: Path,
) -> None:
    folders = []
    for attempt in range(2):
        run = Run(tmp_path / f"run{attempt}", SHELLS[0], MARKER_SCRIPT, sha256(MARKER_SCRIPT))
        assert run.run().returncode == 0
        script = Path(run.marker.read_text(encoding="utf-8").splitlines()[0])
        assert script.parent.parent == run.temporary
        folders.append(script.parent)
    assert folders[0] != folders[1]
    for folder in folders:
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700
        assert folder.stat().st_uid == os.getuid()
    assert "/tmp/" not in bootstrap_command()


def windows_command() -> str:
    section = README.split("## Bootstrap command", 1)[1].split("\n## ", 1)[0]
    match = re.search(r"```powershell\n(.*?)```", section, flags=re.S)
    assert match, "the README has no Windows bootstrap command block"
    return match.group(1)


def test_the_documented_windows_command_checks_the_hash_before_it_runs_anything() -> None:
    command = windows_command()
    assert command.index("Get-FileHash") < command.index("-File $file")
    assert "-ne '<sha256>'" in command
    assert "throw" in command
    assert "$ErrorActionPreference = 'Stop'" in command
    assert "<commit>/start.ps1" in command
    assert command.count("https://") == 1 and "http://" not in command
    assert "Tls12" in command
    assert "$env:TEMP" in command
    assert "[guid]::NewGuid()" in command


def test_the_documented_download_refuses_plain_http_and_old_tls() -> None:
    command = bootstrap_command()
    assert "--proto '=https'" in command
    assert "--tlsv1.2" in command


def test_the_readme_states_the_same_pins_as_the_proof_engine() -> None:
    assert f"Tart {pins.TART_VERSION}" in README
    for image in pins.IMAGES:
        assert image.repository.rsplit("/", 1)[1] in README
    assert f"{pins.DISK_GB} GB" in README
    assert f"{pins.FREE_GB_FIRST_PULL} GB" in README
    assert f"{pins.FREE_GB_PER_RUN} GB" in README
    assert f"every {pins.HEARTBEAT_SECONDS} seconds" in README


def test_the_readme_states_the_same_versions_and_settings_as_the_starter() -> None:
    script = (REPO_ROOT / "start.sh").read_text(encoding="utf-8")
    assert re.search(r'^SUPPORTED_MAJORS=" 26 27 "$', script, flags=re.M)
    assert "macOS 26 or 27" in README
    assert "/opt/homebrew/bin" in script
    for name in starter_files():
        assert f"`{name}`" in README, f"the README layout does not mention {name}"


def imported_siblings(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return {name for name in names if (REPO_ROOT / f"{name}.py").is_file()}


def test_every_module_the_starter_imports_is_a_downloaded_starter_file() -> None:
    shipped = set(starter_files())
    pending = ["start"]
    seen: set[str] = set()
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        seen.add(module)
        pending.extend(imported_siblings(REPO_ROOT / f"{module}.py"))
    missing = sorted(f"{name}.py" for name in seen if f"{name}.py" not in shipped)
    assert missing == [], f"add these to STARTER_FILES in scripts/pins.sh: {missing}"
    assert shutil.which("git")
