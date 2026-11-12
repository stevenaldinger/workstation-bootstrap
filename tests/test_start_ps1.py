"""start.ps1 under Windows PowerShell 5.1 against fake commands.

Each run starts `powershell.exe -File` on a wrapper that declares start.ps1's own parameters,
defines stand-ins for the cmdlets that would reach the network or change the machine, and then
runs a copy of start.ps1 whose pins match the fabricated downloads. The stand-ins declare only
the parameters the real cmdlet has, so a call with one it lacks fails the run:

- Invoke-WebRequest with -PassThru and -OutFile returns the response as well as writing the file:
  https://learn.microsoft.com/powershell/module/microsoft.powershell.utility/invoke-webrequest
- Add-AppxPackage -RegisterByFamilyName needs -MainPackage:
  https://learn.microsoft.com/powershell/module/appx/add-appxpackage
- Repair-WinGetPackageManager takes -Version and -Force:
  https://learn.microsoft.com/windows/package-manager/winget/powershell
- Win32_OperatingSystem.ProductType is 1 for a client edition and 3 for a server:
  https://learn.microsoft.com/windows/win32/cimwin32prov/win32-operatingsystem
- Win32_Processor.Architecture is 9 for x64 and 12 for Arm64:
  https://learn.microsoft.com/windows/win32/cimwin32prov/win32-processor
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from starter_support import (
    FAKE_TOOLS,
    REPO_ROOT,
    fake_exe,
    install_fake,
    starter_files,
    windows_only,
)

pytestmark = windows_only

START_PS1 = REPO_ROOT / "start.ps1"
ACCOUNT = "owner@example.com"
GOOD_HOST: dict[str, Any] = {
    "product_type": 1,
    "build": 26100,
    "hypervisor": True,
    "processors": [{"arch": 9, "firmware": False}],
    "appx": "unavailable",
    "module_repair": "fails",
}

TESTS = Path(__file__).resolve().parent
PRELUDE = (TESTS / "fake_cmdlets.ps1").read_text(encoding="utf-8")
CIM = (TESTS / "fake_cim.ps1").read_text(encoding="utf-8")

RUN = "\n& $env:FAKE_SCRIPT @PSBoundParameters @args\nexit $LASTEXITCODE\n"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pin(text: str, name: str) -> str:
    match = re.search(rf"^\${name} = '([^']*)'$", text, flags=re.M)
    assert match, name
    return match.group(1)


def mise_zip() -> bytes:
    """A release archive whose mise.exe is the logging fake."""
    body = fake_exe().read_bytes()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("mise/bin/mise.exe", body)
        archive.writestr("mise/bin/mise-shim.exe", body)
        archive.writestr("mise/LICENSE", "license")
    return buffer.getvalue()


@dataclass
class Sandbox:
    """A profile, fake commands, and a copy of start.ps1 whose pins match the downloads."""

    root: Path
    script: Path
    wrapper: Path
    fake_dir: Path
    bin_dir: Path
    profile: Path
    local: Path
    mise_url: str
    base: str
    starter: dict[str, bytes]
    downloads: dict[str, dict[str, str]] = field(default_factory=dict)
    host: dict[str, Any] = field(default_factory=lambda: dict(GOOD_HOST))

    @property
    def work(self) -> Path:
        return self.local / "workstation-bootstrap"

    def run(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        (self.fake_dir / "host.json").write_text(json.dumps(self.host), encoding="utf-8")
        (self.fake_dir / "downloads.json").write_text(json.dumps(self.downloads), encoding="utf-8")
        inherited = {k: v for k, v in os.environ.items() if not k.startswith("WS_")}
        environment = {
            **inherited,
            "USERPROFILE": str(self.profile),
            "LOCALAPPDATA": str(self.local),
            "FAKE_DIR": str(self.fake_dir),
            "FAKE_BIN": str(self.bin_dir),
            "FAKE_SCRIPT": str(self.script),
            "FAKE_PYTHON": sys.executable,
            "FAKE_TOOLS_PY": str(FAKE_TOOLS),
            "PATH": os.pathsep.join([str(self.bin_dir), os.environ["PATH"]]),
            **env,
        }
        return subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(self.wrapper), *args,
            ],
            capture_output=True, text=True, check=False, env=environment, cwd=self.root,
        )  # fmt: skip

    def serve(self, url: str, content: bytes, final: str = "") -> None:
        source = self.fake_dir / f"served-{sha256(url.encode())[:10]}"
        source.write_bytes(content)
        self.downloads[url] = {"source": str(source), "final": final}

    def serve_matching(self, suffix: str, content: bytes) -> None:
        for url in [url for url in self.downloads if url.endswith(suffix)]:
            self.serve(url, content)

    def calls(self, name: str) -> list[str]:
        log = self.fake_dir / "ps-calls.jsonl"
        if not log.exists():
            return []
        rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        return [row["detail"] for row in rows if row["name"] == name]

    def mise_rows(self) -> list[list[Any]]:
        log = self.fake_dir / "calls.jsonl"
        if not log.exists():
            return []
        rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        return [row for row in rows if row[0] == "mise"]


def build_sandbox(tmp_path: Path, *, real_cim: bool = False) -> Sandbox:
    root = tmp_path / "root"
    fake_dir, bin_dir = tmp_path / "fake", tmp_path / "bin"
    profile, local = tmp_path / "profile", tmp_path / "local"
    for directory in (root, fake_dir, bin_dir, profile, local):
        directory.mkdir()
    install_fake(fake_dir / "winget-stash", "winget.exe")
    install_fake(bin_dir, "winget.exe")
    archive = mise_zip()
    starter = {name: (REPO_ROOT / name).read_bytes() for name in starter_files()}
    manifest = "".join(f"{sha256(body)}  {name}\n" for name, body in starter.items()).encode()
    text = START_PS1.read_text(encoding="utf-8")
    for name, value in {"MiseSha256": sha256(archive), "ManifestSha256": sha256(manifest)}.items():
        text, count = re.subn(rf"^\${name} = '[^']*'$", f"${name} = '{value}'", text, flags=re.M)
        assert count == 1, name
    script = root / "start.ps1"
    script.write_text(text, encoding="utf-8", newline="\n")
    wrapper = root / "wrapper.ps1"
    wrapper.write_text(PRELUDE + ("" if real_cim else CIM) + RUN, encoding="utf-8", newline="\r\n")
    version = pin(text, "MiseVersion")
    mise_url = (
        f"https://github.com/jdx/mise/releases/download/v{version}/mise-v{version}-windows-x64.zip"
    )
    base = (
        "https://raw.githubusercontent.com/stevenaldinger/workstation-bootstrap/"
        f"{pin(text, 'FilesCommit')}"
    )
    box = Sandbox(root, script, wrapper, fake_dir, bin_dir, profile, local, mise_url, base, starter)
    box.serve(mise_url, archive)
    box.serve(f"{base}/start.sha256", manifest)
    for name, body in starter.items():
        box.serve(f"{base}/{name}", body)
    return box


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    return build_sandbox(tmp_path)


def test_a_supported_computer_installs_mise_then_hands_off_to_start_py(sandbox: Sandbox) -> None:
    result = sandbox.run("-PersonalAccount", ACCOUNT, "--verbose")
    assert result.returncode == 0, result.stderr + result.stdout
    handoff = sandbox.mise_rows()[-1]
    assert handoff[1] == "exec"
    assert handoff[handoff.index("--") + 1 :][:-1] == [
        "python", str(sandbox.work / "start.py"), "--stage1",
        str(sandbox.work / "stage1-windows.toml"), "--personal-account", ACCOUNT,
    ]  # fmt: skip
    assert handoff[-2] == "--verbose"
    assert (sandbox.profile / ".local" / "bin" / "mise.exe").is_file()
    assert (sandbox.work / "start.ps1").read_bytes() == sandbox.script.read_bytes()
    for name, body in sandbox.starter.items():
        assert (sandbox.work / name).read_bytes() == body


def test_the_runtimes_installed_are_the_ones_the_hand_off_runs_with(sandbox: Sandbox) -> None:
    assert sandbox.run("-PersonalAccount", ACCOUNT).returncode == 0
    install, handoff = (row[1:-1] for row in sandbox.mise_rows())
    assert install[0] == "install"
    assert {tool.split("@")[0] for tool in install[1:]} == {"python", "uv", "gh"}
    assert handoff[1 : handoff.index("--")] == install[1:]


def test_mise_trust_prompts_are_answered_only_for_the_install(sandbox: Sandbox) -> None:
    assert sandbox.run("-PersonalAccount", ACCOUNT).returncode == 0
    install, handoff = (row[-1]["env"] for row in sandbox.mise_rows())
    assert install["MISE_YES"] == "1"
    assert handoff["MISE_YES"] is None
    assert handoff["PYTHONUTF8"] == "1"


def test_every_download_is_https_and_the_hand_off_path_starts_with_mise(sandbox: Sandbox) -> None:
    assert sandbox.run("-PersonalAccount", ACCOUNT).returncode == 0
    urls = sandbox.calls("Invoke-WebRequest")
    assert urls[0] == sandbox.mise_url
    assert urls[1] == f"{sandbox.base}/start.sha256"
    assert all(url.startswith("https://") for url in urls)
    path = sandbox.mise_rows()[-1][-1]["env"]["PATH"].split(os.pathsep)
    assert path[0] == str(sandbox.profile / ".local" / "bin")


def test_a_second_run_reuses_the_checked_mise_archive(sandbox: Sandbox) -> None:
    assert sandbox.run("-PersonalAccount", ACCOUNT).returncode == 0
    (sandbox.fake_dir / "ps-calls.jsonl").unlink()
    assert sandbox.run("-PersonalAccount", ACCOUNT).returncode == 0
    assert sandbox.mise_url not in sandbox.calls("Invoke-WebRequest")


def test_a_second_run_restores_a_mise_binary_that_was_removed(sandbox: Sandbox) -> None:
    assert sandbox.run("-PersonalAccount", ACCOUNT).returncode == 0
    (sandbox.profile / ".local" / "bin" / "mise.exe").unlink()
    assert sandbox.run("-PersonalAccount", ACCOUNT).returncode == 0
    assert (sandbox.profile / ".local" / "bin" / "mise.exe").is_file()


@pytest.mark.parametrize(
    ("change", "cause"),
    [
        ({"product_type": 3}, "unsupported edition of Windows"),
        ({"product_type": 2}, "unsupported edition of Windows"),
        ({"build": 22621}, "unsupported Windows build 22621"),
        ({"processors": [{"arch": 12, "firmware": False}]}, "unsupported processor architecture"),
        (
            {"hypervisor": False, "processors": [{"arch": 9, "firmware": False}]},
            "virtualization is turned off",
        ),
    ],
)
def test_an_unsupported_computer_stops_before_changing_anything(
    sandbox: Sandbox, change: dict[str, Any], cause: str
) -> None:
    sandbox.host.update(change)
    result = sandbox.run("-PersonalAccount", ACCOUNT)
    assert result.returncode == 2
    assert cause in result.stderr
    assert sandbox.calls("Invoke-WebRequest") == []
    assert list(sandbox.local.iterdir()) == []
    assert list(sandbox.profile.iterdir()) == []


@pytest.mark.parametrize(
    ("change", "cause"),
    [
        ({"build": 22621}, "unsupported Windows build"),
        ({"processors": [{"arch": 12, "firmware": False}]}, "unsupported processor architecture"),
        ({"hypervisor": False}, "virtualization is turned off"),
    ],
)
def test_the_server_switch_relaxes_only_the_edition_test(
    sandbox: Sandbox, change: dict[str, Any], cause: str
) -> None:
    sandbox.host.update({"product_type": 3, **change})
    result = sandbox.run("-PersonalAccount", ACCOUNT, WS_ALLOW_SERVER_EDITION="1")
    assert result.returncode == 2
    assert cause in result.stderr
    assert "edition" not in result.stderr


def test_the_server_switch_lets_a_server_that_passes_the_other_tests_run(
    sandbox: Sandbox,
) -> None:
    sandbox.host["product_type"] = 3
    assert sandbox.run("-PersonalAccount", ACCOUNT, WS_ALLOW_SERVER_EDITION="1").returncode == 0


def test_the_skip_switch_skips_the_whole_support_test(sandbox: Sandbox) -> None:
    sandbox.host.update(processors=[{"arch": 12, "firmware": False}], build=1, product_type=3)
    assert sandbox.run("-PersonalAccount", ACCOUNT, WS_SKIP_SUPPORT_CHECK="1").returncode == 0


def test_virtualization_in_the_firmware_alone_passes(sandbox: Sandbox) -> None:
    sandbox.host.update(hypervisor=False, processors=[{"arch": 9, "firmware": True}])
    assert sandbox.run("-PersonalAccount", ACCOUNT).returncode == 0


@pytest.mark.parametrize(
    ("args", "cause"),
    [
        ([], "-PersonalAccount is required"),
        (["--verbose"], "-PersonalAccount is required"),
        (["-PersonalAccount", ""], "-PersonalAccount is required"),
        (["-PersonalAccount", "--verbose"], "needs an email or sign-in address"),
        (["-PersonalAccount", "two words"], "needs an email or sign-in address"),
    ],
)
def test_a_bad_account_argument_stops_before_anything_is_installed(
    sandbox: Sandbox, args: list[str], cause: str
) -> None:
    result = sandbox.run(*args)
    assert result.returncode == 2
    assert cause in result.stderr
    assert sandbox.calls("Invoke-WebRequest") == []
    assert list(sandbox.local.iterdir()) == []


def test_a_second_run_while_one_is_in_progress_stops(sandbox: Sandbox) -> None:
    import ctypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined,unused-ignore]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel.CreateMutexW(None, True, "Local\\workstation-setup")
    assert handle
    try:
        result = sandbox.run("-PersonalAccount", ACCOUNT)
    finally:
        kernel.ReleaseMutex(ctypes.c_void_p(handle))
        kernel.CloseHandle(ctypes.c_void_p(handle))
    assert result.returncode == 3
    assert "another run of the workstation setup is in progress" in result.stderr
    assert sandbox.calls("Invoke-WebRequest") == []


def test_a_mise_download_that_fails_its_checksum_is_refused(sandbox: Sandbox) -> None:
    sandbox.serve(sandbox.mise_url, b"tampered archive")
    result = sandbox.run("-PersonalAccount", ACCOUNT)
    assert result.returncode == 1
    assert "checksum mismatch" in result.stderr
    assert sandbox.mise_rows() == []
    assert not (sandbox.profile / ".local" / "bin" / "mise.exe").exists()
    assert not (sandbox.work / "mise.zip").exists()


@pytest.mark.parametrize("name", starter_files())
def test_a_starter_file_that_fails_its_checksum_is_refused(sandbox: Sandbox, name: str) -> None:
    sandbox.serve_matching(f"/{name}", b"tampered")
    result = sandbox.run("-PersonalAccount", ACCOUNT)
    assert result.returncode == 1
    assert "checksum mismatch in the starter files" in result.stderr
    assert not any(row[1] == "exec" for row in sandbox.mise_rows())


def test_a_tampered_manifest_is_refused_before_any_file_is_fetched(sandbox: Sandbox) -> None:
    sandbox.serve_matching("/start.sha256", b"0" * 64 + b"  start.py\n")
    result = sandbox.run("-PersonalAccount", ACCOUNT)
    assert result.returncode == 1
    assert "checksum mismatch" in result.stderr
    assert sandbox.calls("Invoke-WebRequest")[-1].endswith("/start.sha256")


def test_a_manifest_line_the_script_cannot_read_is_refused(sandbox: Sandbox) -> None:
    manifest = b"not a checksum line\n"
    text = sandbox.script.read_text(encoding="utf-8")
    text = re.sub(
        r"^\$ManifestSha256 = '[^']*'$", f"$ManifestSha256 = '{sha256(manifest)}'", text, flags=re.M
    )
    sandbox.script.write_text(text, encoding="utf-8", newline="\n")
    sandbox.serve_matching("/start.sha256", manifest)
    result = sandbox.run("-PersonalAccount", ACCOUNT)
    assert result.returncode == 1
    assert "manifest has a line this script cannot read" in result.stderr


def test_a_download_that_redirects_away_from_https_is_refused(sandbox: Sandbox) -> None:
    sandbox.serve(sandbox.mise_url, b"anything", final="http://example.com/mise.zip")
    result = sandbox.run("-PersonalAccount", ACCOUNT)
    assert result.returncode == 1
    assert "download failed" in result.stderr
    assert sandbox.mise_rows() == []


def test_a_failed_download_is_tried_three_times_then_reported(sandbox: Sandbox) -> None:
    sandbox.downloads.clear()
    result = sandbox.run("-PersonalAccount", ACCOUNT)
    assert result.returncode == 1
    assert "download failed" in result.stderr
    assert sandbox.calls("Invoke-WebRequest") == [sandbox.mise_url] * 3
    assert sandbox.calls("Start-Sleep") == ["2", "4"]


def test_a_working_winget_is_left_alone(sandbox: Sandbox) -> None:
    assert sandbox.run("-PersonalAccount", ACCOUNT).returncode == 0
    for name in ("Add-AppxPackage", "Install-Module", "Repair-WinGetPackageManager"):
        assert sandbox.calls(name) == []


def test_a_missing_winget_is_registered_from_the_app_installer(sandbox: Sandbox) -> None:
    (sandbox.bin_dir / "winget.exe").unlink()
    sandbox.host["appx"] = "registers"
    assert sandbox.run("-PersonalAccount", ACCOUNT).returncode == 0
    assert sandbox.calls("Add-AppxPackage") == ["Microsoft.DesktopAppInstaller_8wekyb3d8bbwe"]
    assert sandbox.calls("Install-Module") == []


def test_a_missing_winget_that_registration_cannot_fix_gets_the_pinned_client(
    sandbox: Sandbox,
) -> None:
    (sandbox.bin_dir / "winget.exe").unlink()
    sandbox.host["module_repair"] = "works"
    assert sandbox.run("-PersonalAccount", ACCOUNT).returncode == 0
    version = pin(sandbox.script.read_text(encoding="utf-8"), "WinGetVersion")
    assert sandbox.calls("Install-Module") == [f"Microsoft.WinGet.Client {version}"]
    assert sandbox.calls("Import-Module") == [f"Microsoft.WinGet.Client {version}"]
    assert sandbox.calls("Repair-WinGetPackageManager") == [version]


@pytest.mark.parametrize(
    ("outcome", "cause"),
    [
        ("silent", "winget is not available and could not be repaired"),
        ("fails", "The package manager could not be repaired."),
    ],
)
def test_a_winget_that_cannot_be_repaired_stops_before_any_runtime_is_installed(
    sandbox: Sandbox, outcome: str, cause: str
) -> None:
    (sandbox.bin_dir / "winget.exe").unlink()
    sandbox.host["module_repair"] = outcome
    result = sandbox.run("-PersonalAccount", ACCOUNT)
    assert result.returncode == 1
    assert cause in result.stderr
    assert sandbox.mise_rows() == []


def test_a_failing_runtime_install_ends_the_run_with_its_status(sandbox: Sandbox) -> None:
    result = sandbox.run("-PersonalAccount", ACCOUNT, FAKE_MISE_INSTALL_FAILS="7")
    assert result.returncode == 7
    assert "failed with exit status 7" in result.stderr
    assert [row[1] for row in sandbox.mise_rows()] == ["install"]


def test_the_real_computer_answers_the_support_queries_with_the_properties_the_script_reads(
    tmp_path: Path,
) -> None:
    box = build_sandbox(tmp_path, real_cim=True)
    box.downloads.clear()
    result = box.run("-PersonalAccount", ACCOUNT, WS_ALLOW_SERVER_EDITION="1")
    assert "unsupported" not in result.stderr
    assert "virtualization is turned off" not in result.stderr
    assert "download failed" in result.stderr


def run_documented_command(
    box: Sandbox, served: bytes, claimed: str
) -> subprocess.CompletedProcess[str]:
    """The README's PowerShell block, serving `served` with TEMP inside the sandbox."""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Bootstrap command", 1)[1].split("\n## ", 1)[0]
    block = re.search(r"```powershell\n(.*?)```", section, flags=re.S)
    assert block
    command = (
        block.group(1)
        .replace("<commit>", "0" * 40)
        .replace("<sha256>", claimed)
        .replace("<email or sign-in address>", ACCOUNT)
        .replace("& powershell.exe", "& $env:FAKE_POWERSHELL")
    )
    served_file = box.fake_dir / "documented-start.ps1"
    served_file.write_bytes(served)
    serve = (
        "function Invoke-WebRequest {\n"
        "  param([switch]$UseBasicParsing, [Parameter(Mandatory = $true)][string]$Uri,\n"
        "        [Parameter(Mandatory = $true)][string]$OutFile)\n"
        f"  Copy-Item -LiteralPath '{served_file}' -Destination $OutFile\n"
        "}\n"
    )
    wrapper = box.root / "documented.ps1"
    wrapper.write_text(serve + command, encoding="utf-8", newline="\n")
    marker = box.root / "marker.txt"
    environment = {
        **os.environ,
        "TEMP": str(box.root / "temp"),
        "MARKER": str(marker),
        "FAKE_POWERSHELL": str(box.root / "marker-runner.cmd"),
    }
    (box.root / "temp").mkdir(exist_ok=True)
    (box.root / "marker-runner.cmd").write_text('@echo %* > "%MARKER%"\r\n', encoding="ascii")
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper)],
        capture_output=True, text=True, check=False, env=environment, cwd=box.root,
    )  # fmt: skip


def test_the_documented_command_runs_the_script_that_matches_the_hash(sandbox: Sandbox) -> None:
    body = b"Write-Output 'hello'\n"
    result = run_documented_command(sandbox, body, sha256(body).upper())
    assert result.returncode == 0, result.stderr
    arguments = (sandbox.root / "marker.txt").read_text(encoding="utf-8")
    assert f"-PersonalAccount {ACCOUNT}" in arguments
    assert "-ExecutionPolicy Bypass" in arguments
    match = re.search(r"-File (\S+)", arguments)
    assert match
    ran = Path(match.group(1))
    assert ran.parent.parent == sandbox.root / "temp"
    assert ran.name == "start.ps1"
    assert ran.read_bytes() == body


def test_the_documented_command_refuses_a_script_that_does_not_match_the_hash(
    sandbox: Sandbox,
) -> None:
    body = b"Write-Output 'hello'\n"
    result = run_documented_command(sandbox, body + b"# tampered\n", sha256(body))
    assert result.returncode != 0
    assert "does not match its SHA-256" in result.stderr
    assert not (sandbox.root / "marker.txt").exists()
