"""The starter's command runners: bounded time, and a command that cannot start is a status."""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest
from starter_support import Machine, build_machine, make_context

import start_base as base
import start_clone


def test_a_command_that_outlives_its_timeout_is_stopped_and_reported() -> None:
    result = base.run_captured([sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.5)
    assert result.returncode == base.EXIT_TIMED_OUT
    assert "timed out after" in result.output


def test_a_missing_command_is_status_127(tmp_path: Path) -> None:
    result = base.run_captured([str(tmp_path / "nope")])
    assert result.returncode == base.EXIT_NOT_FOUND
    assert "command not found" in result.output


def test_a_command_without_permission_to_run_is_status_126_not_a_traceback(tmp_path: Path) -> None:
    script = tmp_path / "tool"
    script.write_text("#!/bin/sh\n", encoding="utf-8")
    script.chmod(stat.S_IRUSR)
    captured = base.run_captured([str(script)])
    assert captured.returncode == base.EXIT_NOT_EXECUTABLE
    assert "cannot run" in captured.output
    assert base.run_streamed([str(script)]) == base.EXIT_NOT_EXECUTABLE


def test_a_missing_streamed_command_is_status_127_with_a_message(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert base.run_streamed([str(tmp_path / "nope")]) == base.EXIT_NOT_FOUND
    assert "command not found" in capsys.readouterr().err


def test_a_finished_command_keeps_its_streams_and_status() -> None:
    result = base.run_captured([sys.executable, "-c", "import sys; print('out'); sys.exit(3)"])
    assert result.returncode == 3
    assert result.stdout.strip() == "out"


def test_a_directory_in_place_of_a_command_is_status_126(tmp_path: Path) -> None:
    assert base.run_captured([str(tmp_path)]).returncode == base.EXIT_NOT_EXECUTABLE
    assert os.path.isdir(tmp_path)


def test_the_clone_runs_with_the_long_timeout_and_other_commands_with_the_short_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    machine: Machine = build_machine(tmp_path, monkeypatch)
    ctx = make_context(machine)
    seen: list[tuple[list[str], float]] = []

    def run(argv, env=None, *, timeout=base.COMMAND_TIMEOUT_SECONDS):  # type: ignore[no-untyped-def]
        seen.append((list(argv), timeout))
        return base.Completed(1, "", "stop here")

    ctx.run = run
    with pytest.raises(base.StageError):
        start_clone.clone_repository(ctx, "git@example.test:me/setup.git")
    assert seen[0][0][:2] == ["git", "clone"]
    assert seen[0][1] == base.CLONE_TIMEOUT_SECONDS
    assert base.CLONE_TIMEOUT_SECONDS > base.COMMAND_TIMEOUT_SECONDS


def test_a_handoff_that_cannot_exec_is_a_stage_error(tmp_path: Path, machine_home: Machine) -> None:
    ctx = make_context(machine_home)
    ws = ctx.clone_dir / start_clone.WS_RELATIVE
    ws.parent.mkdir(parents=True)
    ws.write_text("#!/bin/sh\n", encoding="utf-8")

    def execv(path: str, argv: list[str]) -> None:
        raise PermissionError(13, "Permission denied")

    with pytest.raises(base.StageError, match="Could not start .* Permission denied"):
        start_clone.hand_off(ctx, [], execv)


@pytest.fixture
def machine_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Machine:
    return build_machine(tmp_path, monkeypatch)
