"""The platform seam: what a host changes, and the numbered choice a step can ask for."""

from __future__ import annotations

import dataclasses
import shlex
import subprocess
import sys
from pathlib import Path

import pytest
from starter_support import (
    PERSONAL_ACCOUNT,
    PERSONAL_EMAIL,
    Machine,
    build_machine,
    make_context,
    make_mirror,
    make_session,
)

import start
import start_base as base
import start_clone
import start_identity as identity
import start_packages

WINDOWS_LIKE = dataclasses.replace(
    base.MACOS,
    name="windows-like",
    open_app=("opener", "app"),
    open_settings=("opener", "settings"),
    ssh_dir=Path("C:/Windows/System32/OpenSSH"),
    ssh_suffix=".exe",
    null_device="NUL",
    ws_script=Path("bin/ws.cmd"),
    agent_option=False,
    sets_core_ssh_command=True,
    integrations_prompt="integrations.prompt",
    stage1_notice="step.retry",
)


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Machine:
    return build_machine(tmp_path, monkeypatch)


def test_choose_returns_the_zero_based_number_typed() -> None:
    session = make_session(lambda: "2")
    assert session.console.choose("step.retry", ["step.retry", "input.ended"], step="x") == 1
    assert session.lines[1:] == [
        f"  1. {base.MESSAGES['step.retry']}",
        f"  2. {base.MESSAGES['input.ended']}",
    ]


@pytest.mark.parametrize("typed", ["", "3"])
def test_choose_asks_again_until_a_number_in_range_is_typed(typed: str) -> None:
    session = make_session(lambda: typed, lambda: "1")
    assert session.console.choose("step.retry", ["step.retry", "step.retry"]) == 0
    assert session.console.shown == ["step.retry", "choice.invalid"]
    assert session.person.enters == 2


def test_choose_reports_input_that_ends() -> None:
    with pytest.raises(base.InputEnded):
        make_session().console.choose("step.retry", ["step.retry"])


def test_the_mac_platform_keeps_the_mac_commands_and_ssh_options(machine: Machine) -> None:
    ctx = make_context(machine)
    assert ctx.platform is base.MACOS
    options = identity.ssh_options(ctx)
    assert options[:2] == ["-F", "/dev/null"]
    assert any(option.startswith("IdentityAgent=") for option in options)
    assert "GlobalKnownHostsFile=/dev/null" in options
    assert base.MACOS.tool_path("ssh") == "ssh"


def test_a_platform_without_an_agent_socket_option_names_its_ssh_and_null_device(
    machine: Machine,
) -> None:
    ctx = make_context(machine)
    ctx.platform = WINDOWS_LIKE
    options = identity.ssh_options(ctx)
    assert options[:2] == ["-F", "NUL"]
    assert not any(option.startswith("IdentityAgent=") for option in options)
    assert "GlobalKnownHostsFile=NUL" in options
    assert options[-1] == ctx.key_file.as_posix()
    command = shlex.split(start_clone.clone_environment(ctx)["GIT_SSH_COMMAND"])
    assert command[0] == "C:/Windows/System32/OpenSSH/ssh.exe"


def test_the_sign_in_and_settings_steps_open_what_the_platform_names(machine: Machine) -> None:
    ctx = make_context(machine)
    ctx.platform = dataclasses.replace(WINDOWS_LIKE, open_app=("open", "-a", "Other"))
    identity.open_command(ctx, ctx.platform.open_app)()
    assert machine.calls("open") == [["-a", "Other"]]


def test_a_platform_that_sets_the_ssh_command_sets_it_in_the_clone(
    machine: Machine, tmp_path: Path
) -> None:
    mirror = make_mirror(tmp_path)
    ctx = make_context(machine)
    ctx.platform = WINDOWS_LIKE
    start_clone.clone_repository(ctx, str(mirror))
    shown = subprocess.run(
        ["git", "-C", str(ctx.clone_dir), "config", "--get", "core.sshCommand"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert shown == "C:/Windows/System32/OpenSSH/ssh.exe"


def test_the_hand_off_runs_the_platforms_walkthrough_script(machine: Machine) -> None:
    ctx = make_context(machine)
    ctx.platform = WINDOWS_LIKE
    script = ctx.clone_dir / "bin" / "ws.cmd"
    script.parent.mkdir(parents=True)
    script.write_text("@echo off\n", encoding="utf-8")
    calls: list[tuple[str, list[str]]] = []
    start_clone.hand_off(ctx, ["--x"], lambda path, argv: calls.append((path, argv)))
    assert calls == [(str(script), [str(script), "bootstrap", "--x"])]
    script.unlink()
    with pytest.raises(base.StageError, match=r"no bin.ws\.cmd"):
        start_clone.hand_off(ctx, [], lambda path, argv: None)


def test_stage_one_announces_the_installers_only_when_it_installs_and_always_refreshes(
    machine: Machine, tmp_path: Path
) -> None:
    refreshed: list[int] = []
    platform = dataclasses.replace(
        WINDOWS_LIKE, refresh_environment=lambda _ctx: refreshed.append(1)
    )
    session = make_session()
    ctx = make_context(machine, session)
    ctx.platform = platform
    stage1 = tmp_path / "stage1.toml"
    stage1.write_text("[bootstrap.packages]\n", encoding="utf-8")
    start_packages.install_stage1(ctx, stage1)
    assert session.console.shown == ["step.retry"]
    start_packages.install_stage1(ctx, stage1)
    assert session.console.shown == ["step.retry"]
    assert refreshed == [1, 1]


def test_host_steps_run_in_their_slots_around_the_shared_steps(
    machine: Machine, tmp_path: Path
) -> None:
    order: list[str] = []
    platform = dataclasses.replace(
        base.MACOS,
        steps_before_packages=(("key.title", lambda _ctx: order.append("before packages")),),
        steps_before_integrations=(
            ("register.title", lambda _ctx: order.append("before integrations")),
        ),
    )
    machine.sign_in(PERSONAL_ACCOUNT)
    session = make_session()
    stage1 = tmp_path / "stage1.toml"
    stage1.write_text("[bootstrap.packages]\n", encoding="utf-8")
    status = start.main(
        ["--personal-account", PERSONAL_EMAIL, "--stage1", str(stage1)],
        home=machine.home,
        console=session.console,
        socket_ready=machine.socket_ready,
        platform=platform,
    )
    assert status == base.EXIT_INPUT_ENDED
    assert order == ["before packages", "before integrations"]
    titles = [line.split("] ", 1)[1] for line in session.lines if line.startswith("[")]
    assert titles == [
        base.MESSAGES[title_id]
        for title_id in (
            "key.title",
            "stage1.title",
            "sign-in.title",
            "register.title",
            "integrations.title",
        )
    ]


def test_the_default_platform_follows_the_operating_system(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    assert start.select_platform().name == "macos"
