"""The Windows host: WSL with an announced restart, elevation, the SSH agent, and the PATH.

`start.py` selects this module's platform when it runs on Windows. Standard library only.
Every command that needs administrator rights goes through `elevate`, which states the reason
first and checks the outcome after. A restart is offered as a numbered choice and happens
only after the person picks it; the setup then resumes from a RunOnce entry that runs
`start.ps1` again at the next sign-in, and running the same command by hand does the same.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from collections.abc import MutableMapping, Sequence
from pathlib import Path
from typing import Protocol

from start_base import (
    EXIT_RESTART_DECLINED,
    MESSAGES,
    Context,
    EarlyExit,
    Platform,
    Probe,
    StageError,
    tail,
)

HKCU = "HKCU"
HKLM = "HKLM"
RUN_ONCE_KEY = r"Software\Microsoft\Windows\CurrentVersion\RunOnce"
# The leading "!" keeps the entry until its command has finished, so a resume that fails to
# start is not silently dropped.
RUN_ONCE_VALUE = "!workstation-setup"
# Windows rejects a RunOnce command longer than this.
RUN_ONCE_LIMIT = 260
REBOOT_PENDING_KEY = (
    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Component Based Servicing\RebootPending"
)
MACHINE_ENVIRONMENT_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
USER_ENVIRONMENT_KEY = "Environment"
RESTART_DELAY_SECONDS = 15
# Installing WSL downloads components, and the person may be slow to approve the prompt.
ELEVATED_TIMEOUT_SECONDS = 1800.0
EXIT_RESTART_REQUIRED = 3010
SSH_AGENT_SERVICE = "ssh-agent"
# Proof-only switch: keep the Windows agent service, which the proof uses to hold a stand-in key.
KEEP_AGENT_SERVICE_ENV = "WS_KEEP_SSH_AGENT_SERVICE"
STARTER_SCRIPT = Path("workstation-bootstrap") / "start.ps1"
WSL_ENVIRONMENT = {"WSL_UTF8": "1"}
ONEPASSWORD_SETTINGS_URL = "onepassword://settings/developers"
VM_PLATFORM_SCRIPT = (
    "$f = Get-CimInstance -ClassName Win32_OptionalFeature "
    "-Filter \"Name='VirtualMachinePlatform'\" -ErrorAction SilentlyContinue; "
    "if ($null -eq $f) { 'Unknown' } elseif ($f.InstallState -eq 1) { 'Enabled' } "
    "else { 'Disabled' }"
)
DISABLE_AGENT_SCRIPT = (
    f"Set-Service {SSH_AGENT_SERVICE} -StartupType Disabled; "
    f"Stop-Service {SSH_AGENT_SERVICE} -Force"
)


class Registry(Protocol):
    """The slice of the Windows registry the setup reads and writes."""

    def key_exists(self, hive: str, path: str) -> bool: ...

    def read_value(self, hive: str, path: str, name: str) -> str | None: ...

    def write_value(self, hive: str, path: str, name: str, value: str) -> None: ...

    def delete_value(self, hive: str, path: str, name: str) -> None: ...


class WindowsRegistry:
    """The real registry, through `winreg`."""

    @staticmethod
    def _root(hive: str) -> int:
        if sys.platform != "win32":
            raise NotImplementedError
        import winreg

        return {HKCU: winreg.HKEY_CURRENT_USER, HKLM: winreg.HKEY_LOCAL_MACHINE}[hive]

    def key_exists(self, hive: str, path: str) -> bool:
        if sys.platform != "win32":
            raise NotImplementedError
        import winreg

        try:
            winreg.CloseKey(winreg.OpenKey(self._root(hive), path))
        except FileNotFoundError:
            return False
        return True

    def read_value(self, hive: str, path: str, name: str) -> str | None:
        if sys.platform != "win32":
            raise NotImplementedError
        import winreg

        try:
            with winreg.OpenKey(self._root(hive), path) as key:
                value, _kind = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            return None
        return str(value)

    def write_value(self, hive: str, path: str, name: str, value: str) -> None:
        if sys.platform != "win32":
            raise NotImplementedError
        import winreg

        with winreg.CreateKeyEx(self._root(hive), path, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)

    def delete_value(self, hive: str, path: str, name: str) -> None:
        if sys.platform != "win32":
            raise NotImplementedError
        import winreg

        try:
            with winreg.OpenKey(self._root(hive), path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            return


def tool(name: str) -> str:
    """The first `name` on PATH, so a stand-in ahead of the real command is the one that runs."""
    return shutil.which(name) or name


def ps_quote(text: str) -> str:
    """A PowerShell single-quoted string."""
    return "'" + text.replace("'", "''") + "'"


def start_process_argument(argument: str) -> str:
    """An argument for Start-Process, which joins its list with spaces and does not quote."""
    return f'"{argument}"' if re.search(r"\s", argument) else argument


def elevate(ctx: Context, argv: Sequence[str], reason_id: str, step: str) -> int:
    """Run a command with administrator rights after saying why; return its exit status.

    This is the only place the setup asks Windows for elevation. A declined approval is a
    stated failure, and the caller checks the outcome of the step afterwards.
    """
    ctx.console.say(reason_id)
    executable, *arguments = argv
    listed = ",".join(ps_quote(start_process_argument(argument)) for argument in arguments)
    script = (
        f"Start-Process -FilePath {ps_quote(executable)} -ArgumentList @({listed}) "
        "-Verb RunAs -Wait -PassThru | ForEach-Object ExitCode"
    )
    result = ctx.run(
        [tool("powershell.exe"), "-NoProfile", "-Command", script],
        None,
        timeout=ELEVATED_TIMEOUT_SECONDS,
    )
    if result.returncode != 0 and "canceled by the user" in result.output:
        raise StageError(MESSAGES["elevate.cancelled"].format(step=step))
    lines = result.stdout.split()
    if result.returncode != 0 or not lines or not lines[-1].lstrip("-").isdigit():
        values = {"step": step, "code": result.returncode, "detail": tail(result.output)}
        raise StageError(MESSAGES["elevate.failed"].format(**values))
    return int(lines[-1])


def wsl_ready(ctx: Context) -> Probe:
    """Whether WSL answers and the Virtual Machine Platform is not known to be off."""
    status = ctx.run([tool("wsl.exe"), "--status"], WSL_ENVIRONMENT)
    if status.returncode != 0:
        return Probe(False, tail(status.output))
    platform = ctx.run([tool("powershell.exe"), "-NoProfile", "-Command", VM_PLATFORM_SCRIPT], None)
    if platform.stdout.strip() == "Disabled":
        return Probe(False, "the Virtual Machine Platform is turned off")
    return Probe(True)


def restart_command(personal: str, local_app_data: Path) -> str:
    """The command Windows runs at the next sign-in to resume the setup."""
    script = local_app_data / STARTER_SCRIPT
    return (
        f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{script}" '
        f"-PersonalAccount {personal}"
    )


def offer_restart(ctx: Context, registry: Registry, local_app_data: Path, why_id: str) -> None:
    """Ask for a restart, and end the run either way: scheduled, or left for the person."""
    command = restart_command(ctx.personal, local_app_data)
    if len(command) > RUN_ONCE_LIMIT:
        raise StageError(
            MESSAGES["restart.too-long"].format(length=len(command), limit=RUN_ONCE_LIMIT)
        )
    ctx.console.say("restart.needed", why=MESSAGES[why_id])
    choice = ctx.console.choose("restart.question", ["restart.now", "restart.later"])
    if choice == 1:
        raise EarlyExit(EXIT_RESTART_DECLINED, MESSAGES["restart.declined"])
    registry.write_value(HKCU, RUN_ONCE_KEY, RUN_ONCE_VALUE, command)
    delay = str(RESTART_DELAY_SECONDS)
    ctx.stream([tool("shutdown.exe"), "/r", "/t", delay, "/c", MESSAGES["restart.comment"]], None)
    raise EarlyExit(0, MESSAGES["restart.scheduled"].format(seconds=RESTART_DELAY_SECONDS))


def enable_wsl(ctx: Context, registry: Registry, local_app_data: Path) -> None:
    """Turn on WSL while the person is present, so the one restart comes before long installs."""
    restart_why = ""
    if not wsl_ready(ctx).ok:
        code = elevate(
            ctx, [tool("wsl.exe"), "--install", "--no-distribution"], "wsl.reason", "turning on WSL"
        )
        if code not in (0, EXIT_RESTART_REQUIRED):
            raise StageError(
                MESSAGES["elevate.failed"].format(step="turning on WSL", code=code, detail="")
            )
        if code == EXIT_RESTART_REQUIRED:
            restart_why = "restart.why-enable"
    if not restart_why and registry.key_exists(HKLM, REBOOT_PENDING_KEY):
        restart_why = "restart.why-pending"
    if restart_why:
        offer_restart(ctx, registry, local_app_data, restart_why)
    verdict = wsl_ready(ctx)
    if not verdict.ok:
        raise StageError(MESSAGES["wsl.failed"].format(detail=verdict.detail))
    # The resume entry has done its job once nothing is waiting on a restart.
    registry.delete_value(HKCU, RUN_ONCE_KEY, RUN_ONCE_VALUE)


def agent_service_state(ctx: Context) -> tuple[bool, bool] | None:
    """(stopped, disabled) for the Windows agent service, or None when it is not installed."""
    query = ctx.run([tool("sc.exe"), "query", SSH_AGENT_SERVICE], None)
    if query.returncode != 0:
        return None
    config = ctx.run([tool("sc.exe"), "qc", SSH_AGENT_SERVICE], None)
    return "STOPPED" in query.stdout, "DISABLED" in config.stdout


def free_agent_pipe(ctx: Context, environ: MutableMapping[str, str]) -> None:
    """Stop and disable the Windows agent service, so 1Password's agent owns the pipe."""
    ssh = Path(ctx.platform.tool_path("ssh"))
    if not ssh.is_file():
        raise StageError(MESSAGES["agent.client-missing"].format(path=str(ssh)))
    if environ.get(KEEP_AGENT_SERVICE_ENV) == "1":
        return
    state = agent_service_state(ctx)
    if state is None or state == (True, True):
        return
    elevate(
        ctx,
        [tool("powershell.exe"), "-NoProfile", "-Command", DISABLE_AGENT_SCRIPT],
        "agent.reason",
        "stopping the OpenSSH Authentication Agent service",
    )
    after = agent_service_state(ctx)
    if after != (True, True):
        raise StageError(MESSAGES["agent.failed"].format(detail=f"(stopped, disabled) = {after}"))


def windows_agent_probe(ctx: Context) -> Probe:
    """Whether the agent on the OpenSSH pipe offers at least one key."""
    listed = ctx.run([ctx.platform.tool_path("ssh-add"), "-l"], None)
    if listed.returncode == 0:
        return Probe(True)
    return Probe(False, MESSAGES["integrations.agent-missing"].format(detail=tail(listed.output)))


def expand_variables(value: str, environ: MutableMapping[str, str]) -> str:
    """Replace %NAME% with the variable's value; an unknown name stays as written."""
    known = {name.lower(): text for name, text in environ.items()}
    return re.sub(r"%([^%]+)%", lambda m: known.get(m.group(1).lower(), m.group(0)), value)


def refresh_path(
    registry: Registry, environ: MutableMapping[str, str], local_app_data: Path
) -> None:
    """Add the machine and user PATH entries and winget's link folder to this process's PATH.

    The package installers changed those after this process started, and Git and the 1Password
    command line are found through them.
    """
    entries = environ.get("PATH", "").split(";")
    for hive, key in ((HKLM, MACHINE_ENVIRONMENT_KEY), (HKCU, USER_ENVIRONMENT_KEY)):
        stored = registry.read_value(hive, key, "Path") or ""
        entries += [expand_variables(entry, environ) for entry in stored.split(";")]
    entries.append(str(local_app_data / "Microsoft" / "WinGet" / "Links"))
    seen: set[str] = set()
    unique = []
    for entry in entries:
        if entry and entry.lower() not in seen:
            seen.add(entry.lower())
            unique.append(entry)
    environ["PATH"] = ";".join(unique)


def windows_exec(_path: str, argv: list[str]) -> None:
    """Run the walkthrough and exit with its status.

    `os.execv` on Windows starts a new process and ends this one at once, which would hand the
    console back before the walkthrough finishes.
    """
    raise SystemExit(subprocess.run(argv, check=False).returncode)


def windows_platform(
    *,
    registry: Registry | None = None,
    system_root: Path | None = None,
    local_app_data: Path | None = None,
    environ: MutableMapping[str, str] | None = None,
) -> Platform:
    """The Windows host, with the machine's registry, folders, and environment by default."""
    env = os.environ if environ is None else environ
    reg = registry or WindowsRegistry()
    root = system_root or Path(env.get("SystemRoot", r"C:\Windows"))
    local = local_app_data or Path(env.get("LOCALAPPDATA", ""))
    opener = (tool("cmd.exe"), "/c", "start", "", ONEPASSWORD_SETTINGS_URL)
    return Platform(
        name="windows",
        open_app=opener,
        open_settings=opener,
        ssh_dir=root / "System32" / "OpenSSH",
        ssh_suffix=".exe",
        null_device="NUL",
        ws_script=Path("bin") / "ws.cmd",
        agent_option=False,
        sets_core_ssh_command=True,
        integrations_prompt="integrations.prompt-windows",
        stage1_notice="stage1.notice-windows",
        agent_probe=windows_agent_probe,
        refresh_environment=lambda _ctx: refresh_path(reg, env, local),
        execv=windows_exec,
        steps_before_packages=(("wsl.title", lambda ctx: enable_wsl(ctx, reg, local)),),
        steps_before_integrations=(("agent.title", lambda ctx: free_agent_pipe(ctx, env)),),
    )
