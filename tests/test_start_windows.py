"""The Windows host steps against fake commands: WSL, the restart, elevation, and the agent."""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from starter_support import (
    PERSONAL_ACCOUNT,
    PERSONAL_EMAIL,
    REPO_ROOT,
    FakeRegistry,
    Session,
    WindowsHost,
    build_windows_host,
    make_context,
    make_mirror,
    make_session,
    windows_only,
)

import start
import start_base as base
import start_windows as windows

COMMAND = (
    'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{script}" '
    "-PersonalAccount owner@example.com"
)


@pytest.fixture
def host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> WindowsHost:
    return build_windows_host(tmp_path, monkeypatch)


def context(host: WindowsHost, session: Session | None = None) -> base.Context:
    return make_context(host.machine, session, platform=host.platform)


def enable(host: WindowsHost, session: Session) -> None:
    windows.enable_wsl(context(host, session), host.registry, host.local_app_data)


def tools_called(host: WindowsHost) -> list[str]:
    return [row[0] for row in host.machine.call_log()]


def test_wsl_that_is_ready_changes_nothing_and_clears_a_stale_resume_entry(
    host: WindowsHost,
) -> None:
    host.machine.update(lambda world: world["wsl"].update(installed=True))
    host.registry.write_value(windows.HKCU, windows.RUN_ONCE_KEY, windows.RUN_ONCE_VALUE, "x")
    session = make_session()
    enable(host, session)
    assert session.console.shown == []
    assert "shutdown" not in tools_called(host)
    assert (
        host.registry.read_value(windows.HKCU, windows.RUN_ONCE_KEY, "!workstation-setup") is None
    )
    status = host.machine.call_log()[0]
    assert status[:2] == ["wsl", "--status"]
    assert status[-1]["env"]["WSL_UTF8"] == "1"


def test_turning_on_wsl_states_the_reason_before_the_approval_prompt(host: WindowsHost) -> None:
    session = make_session()
    enable(host, session)
    assert session.console.shown == ["wsl.reason"]
    elevated = next(row for row in host.machine.call_log() if "RunAs" in str(row[-2]))
    assert elevated[1:3] == ["-NoProfile", "-Command"]
    assert "-ArgumentList @('--install','--no-distribution') -Verb RunAs" in elevated[3]
    assert host.machine.world["wsl"]["installed"]


def test_wsl_install_needing_a_restart_registers_the_resume_and_schedules_it(
    host: WindowsHost,
) -> None:
    host.machine.update(lambda world: world["wsl"].update(install_exit=3010))
    session = make_session(lambda: "1")
    with pytest.raises(base.EarlyExit) as stop:
        enable(host, session)
    assert stop.value.status == 0
    assert session.console.shown == ["wsl.reason", "restart.needed", "restart.question"]
    command = host.registry.read_value(windows.HKCU, windows.RUN_ONCE_KEY, "!workstation-setup")
    assert command == COMMAND.format(
        script=host.local_app_data / "workstation-bootstrap" / "start.ps1"
    )
    assert len(command) <= windows.RUN_ONCE_LIMIT
    assert host.machine.world["shutdowns"] == [
        {"seconds": 15, "comment": base.MESSAGES["restart.comment"]}
    ]
    assert "restarting" in stop.value.message.lower()


def test_declining_the_restart_ends_the_run_with_the_remedy_and_changes_nothing(
    host: WindowsHost,
) -> None:
    host.machine.update(lambda world: world["wsl"].update(install_exit=3010))
    with pytest.raises(base.EarlyExit) as stop:
        enable(host, make_session(lambda: "2"))
    assert stop.value.status == base.EXIT_RESTART_DECLINED
    assert stop.value.message == base.MESSAGES["restart.declined"]
    assert host.registry.values == {}
    assert host.machine.world["shutdowns"] == []


def test_a_restart_that_was_already_pending_is_offered_even_when_wsl_is_on(
    host: WindowsHost,
) -> None:
    host.machine.update(lambda world: world["wsl"].update(installed=True))
    host.registry.keys.add((windows.HKLM, windows.REBOOT_PENDING_KEY))
    session = make_session(lambda: "2")
    with pytest.raises(base.EarlyExit):
        enable(host, session)
    assert session.console.shown == ["restart.needed", "restart.question"]
    assert not any("RunAs" in str(row[-2]) for row in host.machine.call_log())


def test_a_resumed_run_after_the_restart_carries_on_and_removes_the_resume_entry(
    host: WindowsHost,
) -> None:
    host.machine.update(lambda world: world["wsl"].update(install_exit=3010))
    with pytest.raises(base.EarlyExit):
        enable(host, make_session(lambda: "1"))
    host.machine.update(lambda world: world["wsl"].update(install_exit=0))
    enable(host, make_session())
    assert host.registry.values == {}


def test_a_declined_approval_is_a_stated_failure_and_wsl_stays_off(host: WindowsHost) -> None:
    host.machine.update(lambda world: world.update(uac="cancel"))
    with pytest.raises(base.StageError, match="Administrator approval was declined"):
        enable(host, make_session())
    assert not host.machine.world["wsl"]["installed"]


def test_a_failing_install_reports_its_exit_status(host: WindowsHost) -> None:
    host.machine.update(lambda world: world["wsl"].update(install_exit=5))
    with pytest.raises(base.StageError, match="turning on WSL failed with exit status 5"):
        enable(host, make_session())


def test_the_elevation_output_must_be_an_exit_status(host: WindowsHost) -> None:
    ctx = context(host)
    ctx.run = lambda argv, env=None, **kw: base.Completed(0, "garbage", "garbage")
    with pytest.raises(base.StageError, match="failed with exit status 0: garbage"):
        windows.elevate(ctx, ["wsl.exe", "--install"], "wsl.reason", "turning on WSL")


def test_a_turned_off_virtual_machine_platform_is_turned_on_even_when_wsl_answers(
    host: WindowsHost,
) -> None:
    def change(world: dict[str, Any]) -> None:
        world["wsl"]["installed"] = True
        world["vm_platform"] = "Disabled"

    host.machine.update(change)
    session = make_session()
    with pytest.raises(base.StageError, match="Virtual Machine Platform is turned off"):
        enable(host, session)
    assert session.console.shown == ["wsl.reason"], "the install ran; the platform stayed off"


def test_a_resume_command_over_the_windows_limit_is_refused_before_a_restart(
    host: WindowsHost,
) -> None:
    host.machine.update(lambda world: world["wsl"].update(installed=True))
    host.registry.keys.add((windows.HKLM, windows.REBOOT_PENDING_KEY))
    ctx = context(host)
    ctx.personal = "x" * 300
    with pytest.raises(base.StageError, match="over the 260"):
        windows.enable_wsl(ctx, host.registry, host.local_app_data)
    assert host.registry.values == {}


@pytest.mark.parametrize("argument", ["--install", "a b"])
def test_start_process_quotes_only_an_argument_with_whitespace(argument: str) -> None:
    quoted = windows.start_process_argument(argument)
    assert quoted == (f'"{argument}"' if " " in argument else argument)
    assert windows.ps_quote("it's") == "'it''s'"


def test_only_the_elevation_helper_asks_for_administrator_rights() -> None:
    offenders = [
        path.name
        for path in REPO_ROOT.glob("start*.py")
        if "RunAs" in path.read_text(encoding="utf-8") and path.name != "start_windows.py"
    ]
    assert offenders == []
    assert (REPO_ROOT / "start_windows.py").read_text(encoding="utf-8").count("-Verb RunAs") == 1


def set_service(host: WindowsHost, **state: object) -> None:
    host.machine.update(lambda world: world["agent_service"].update(state))


def free(host: WindowsHost, session: Session) -> None:
    windows.free_agent_pipe(context(host, session), host.environ)


def test_a_running_agent_service_is_stopped_and_disabled_after_saying_why(
    host: WindowsHost,
) -> None:
    set_service(host, running=True, start_type="AUTO_START")
    session = make_session()
    free(host, session)
    assert session.console.shown == ["agent.reason"]
    assert host.machine.world["agent_service"]["running"] is False
    assert host.machine.world["agent_service"]["start_type"] == "DISABLED"


def test_a_stopped_disabled_or_missing_service_is_left_alone(host: WindowsHost) -> None:
    set_service(host, running=False, start_type="DISABLED")
    free(host, make_session())
    set_service(host, present=False)
    free(host, make_session())
    assert "powershell" not in tools_called(host)


def test_the_proof_switch_keeps_the_agent_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kept = build_windows_host(tmp_path, monkeypatch, keep_service=True)
    set_service(kept, running=True)
    free(kept, make_session())
    assert kept.machine.world["agent_service"]["running"] is True
    assert kept.machine.calls("sc") == []


def test_a_service_that_stays_on_after_the_elevated_command_is_a_failure(
    host: WindowsHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_service(host, running=True, start_type="AUTO_START")
    monkeypatch.setattr(windows, "elevate", lambda *args, **kwargs: 0)
    with pytest.raises(base.StageError, match="still running or enabled"):
        free(host, make_session())


def test_a_missing_openssh_client_stops_with_the_remedy(host: WindowsHost, tmp_path: Path) -> None:
    (tmp_path / "windows" / "System32" / "OpenSSH" / "ssh.exe").unlink()
    with pytest.raises(base.StageError, match="OpenSSH Client"):
        free(host, make_session())


def test_the_agent_probe_needs_a_key_from_the_agent(host: WindowsHost) -> None:
    ctx = context(host)
    assert windows.windows_agent_probe(ctx).ok is False
    host.machine.integrations(on=True)
    assert windows.windows_agent_probe(ctx).ok is True


def test_the_path_gains_the_machine_user_and_winget_folders_once(host: WindowsHost) -> None:
    registry = FakeRegistry()
    machine_path = r"C:\Git\cmd;%SystemRoot%\system32"
    registry.write_value(windows.HKLM, windows.MACHINE_ENVIRONMENT_KEY, "Path", machine_path)
    registry.write_value(windows.HKCU, windows.USER_ENVIRONMENT_KEY, "Path", r"C:\GIT\cmd;C:\Tools")
    environ = {"PATH": r"C:\Windows\System32;", "SystemRoot": r"C:\Windows"}
    windows.refresh_path(registry, environ, Path("L"))
    assert environ["PATH"].split(";") == [
        r"C:\Windows\System32",
        r"C:\Git\cmd",
        r"C:\Tools",
        str(Path("L") / "Microsoft" / "WinGet" / "Links"),
    ]


def test_the_walkthrough_runs_to_completion_and_exits_with_its_status() -> None:
    script = [sys.executable, "-c", "raise SystemExit(7)"]
    with pytest.raises(SystemExit) as stop:
        windows.windows_exec("", script)
    assert stop.value.code == 7


def test_the_platform_names_the_windows_commands(host: WindowsHost) -> None:
    platform = host.platform
    assert platform.tool_path("ssh").endswith("System32/OpenSSH/ssh.exe")
    assert platform.null_device == "NUL"
    assert platform.ws_script == Path("bin") / "ws.cmd"
    assert platform.sets_core_ssh_command and not platform.agent_option


class Exec:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def __call__(self, path: str, argv: Sequence[str]) -> None:
        self.calls.append((path, list(argv)))


def run_windows(host: WindowsHost, session: Session, mirror: Path, handoff: Exec) -> int:
    stage1 = host.machine.home.parent / "stage1-windows.toml"
    stage1.write_text("[bootstrap.packages]\n", encoding="utf-8")
    return start.main(
        ["--personal-account", PERSONAL_EMAIL, "--stage1", str(stage1)],
        home=host.machine.home,
        console=session.console,
        execv=handoff,
        environ={start.REPO_URL_ENV: str(mirror)},
        platform=host.platform,
    )


def test_a_fresh_windows_host_runs_every_step_in_order_and_hands_off_to_ws_cmd(
    host: WindowsHost, tmp_path: Path
) -> None:
    mirror = make_mirror(tmp_path)
    session = make_session(
        lambda: host.machine.sign_in(PERSONAL_ACCOUNT),
        lambda: host.machine.integrations(on=True),
    )
    handoff = Exec()
    assert run_windows(host, session, mirror, handoff) == 0
    tools = tools_called(host)
    assert tools.index("wsl") < tools.index("mise") < tools.index("op")
    assert tools.index("sc") < tools.index("ssh-add") < tools.index("ssh")
    assert host.machine.world["gh"]["keys"]
    clone = host.machine.home / ".config" / "mise"
    sshcmd = subprocess.run(
        ["git", "-C", str(clone), "config", "--get", "core.sshCommand"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert sshcmd.endswith("System32/OpenSSH/ssh.exe")
    assert handoff.calls == [
        (str(clone / "bin" / "ws.cmd"), [str(clone / "bin" / "ws.cmd"), "bootstrap"])
    ]
    handshake = next(row for row in host.machine.calls("ssh") if "-T" in row)
    assert handshake[handshake.index("-F") + 1] == "NUL"
    assert not any(part.startswith("IdentityAgent") for part in handshake)
    headings = [line for line in session.lines if line.startswith("[")]
    assert headings[0].startswith("[1/9] ") and headings[-1].startswith("[9/9] ")


def test_the_run_that_follows_a_restart_finishes_the_setup(
    host: WindowsHost, tmp_path: Path
) -> None:
    mirror = make_mirror(tmp_path)
    host.machine.update(lambda world: world["wsl"].update(install_exit=3010))
    first = make_session(lambda: "1")
    assert run_windows(host, first, mirror, Exec()) == 0
    assert host.registry.values, "the resume entry is registered"
    host.machine.update(lambda world: world["wsl"].update(install_exit=0))
    host.machine.sign_in(PERSONAL_ACCOUNT)
    host.machine.integrations(on=True)
    handoff = Exec()
    assert run_windows(host, make_session(), mirror, handoff) == 0
    assert host.registry.values == {}
    assert len(handoff.calls) == 1
    assert len(host.machine.world["shutdowns"]) == 1


def test_a_declined_restart_exits_with_the_restart_status(
    host: WindowsHost, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    host.machine.update(lambda world: world["wsl"].update(install_exit=3010))
    status = run_windows(host, make_session(lambda: "2"), make_mirror(tmp_path), Exec())
    assert status == base.EXIT_RESTART_DECLINED
    assert base.MESSAGES["restart.declined"] in capsys.readouterr().err


@windows_only
def test_the_real_registry_writes_reads_checks_and_deletes_a_value() -> None:
    import winreg

    key = r"Software\workstation-bootstrap-tests"
    registry = windows.WindowsRegistry()
    try:
        assert registry.read_value(windows.HKCU, key, "x") is None
        registry.write_value(windows.HKCU, key, "x", "one")
        assert registry.key_exists(windows.HKCU, key)
        assert registry.read_value(windows.HKCU, key, "x") == "one"
        registry.delete_value(windows.HKCU, key, "x")
        assert registry.read_value(windows.HKCU, key, "x") is None
        registry.delete_value(windows.HKCU, key, "x")
    finally:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)  # type: ignore[attr-defined,unused-ignore]
    assert not registry.key_exists(windows.HKCU, key)
    machine_path = registry.read_value(windows.HKLM, windows.MACHINE_ENVIRONMENT_KEY, "Path")
    assert machine_path and "system32" in machine_path.lower()
    assert isinstance(registry.key_exists(windows.HKLM, windows.REBOOT_PENDING_KEY), bool)
