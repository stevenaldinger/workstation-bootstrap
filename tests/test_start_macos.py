"""The Command Line Tools step: skipped when present, installed headless when missing."""

from __future__ import annotations

from pathlib import Path

import pytest
from fake_tools import CLT_FOLDER, CLT_MARKER
from starter_support import Machine, build_machine, make_context

import start
import start_base as base
import start_macos

OLD = "Command Line Tools for Xcode-9.9"
NEW = "Command Line Tools for Xcode-26.2"


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Machine:
    return build_machine(tmp_path, monkeypatch)


def missing(machine: Machine, *labels: str, install_exit: int = 0) -> None:
    machine.update(
        lambda world: world["clt"].update(
            installed=False, labels=list(labels), install_exit=install_exit
        )
    )


def run_step(machine: Machine) -> base.Context:
    ctx = make_context(machine)
    start_macos.install_command_line_tools(ctx)
    return ctx


def test_the_marker_path_and_folder_are_the_ones_the_fakes_model() -> None:
    assert str(start_macos.MARKER) == CLT_MARKER
    assert start_macos.TOOLS_FOLDER == CLT_FOLDER


def test_installed_tools_skip_the_step_and_run_nothing_elevated(machine: Machine) -> None:
    ctx = run_step(machine)
    assert machine.calls("xcode-select") == [["-p"]]
    assert machine.calls("softwareupdate") == []
    assert machine.calls("sudo") == []
    assert ctx.console.shown == ["clt.present"]


def commands(machine: Machine) -> list[list[object]]:
    """Every call in order, each without the record of what it ran with."""
    return [row[:-1] for row in machine.call_log()]


def test_missing_tools_are_installed_headless_with_the_newest_label(machine: Machine) -> None:
    missing(machine, OLD, NEW)
    ctx = run_step(machine)
    assert commands(machine) == [
        ["xcode-select", "-p"],
        ["sudo", "touch", CLT_MARKER],
        ["touch", CLT_MARKER],
        ["softwareupdate", "-l"],
        ["sudo", "softwareupdate", "-i", NEW],
        ["softwareupdate", "-i", NEW],
        ["sudo", "xcode-select", "--switch", CLT_FOLDER],
        ["xcode-select", "--switch", CLT_FOLDER],
        ["sudo", "rm", "-f", CLT_MARKER],
        ["rm", "-f", CLT_MARKER],
        ["xcode-select", "-p"],
    ]
    assert machine.world["clt"]["installed"] is True
    assert machine.world["clt"]["marker"] is False
    assert ctx.console.shown == ["clt.reason"]


def test_a_listing_without_tools_stops_with_a_remedy_and_removes_the_marker(
    machine: Machine,
) -> None:
    missing(machine)
    with pytest.raises(base.StageError, match="lists no Command Line Tools"):
        run_step(machine)
    assert machine.world["clt"]["marker"] is False
    assert machine.calls("softwareupdate") == [["-l"]]


def test_a_failed_install_stops_with_the_label_and_status_and_removes_the_marker(
    machine: Machine,
) -> None:
    missing(machine, NEW, install_exit=7)
    with pytest.raises(base.StageError) as caught:
        run_step(machine)
    assert NEW in str(caught.value)
    assert "exit status 7" in str(caught.value)
    assert machine.world["clt"]["marker"] is False
    assert machine.world["clt"]["installed"] is False


def test_tools_still_missing_after_a_clean_install_are_reported(machine: Machine) -> None:
    missing(machine, NEW)
    machine.update(lambda world: world["clt"].update(installs=False))
    with pytest.raises(base.StageError, match="still reports no developer tools"):
        run_step(machine)
    assert machine.world["clt"]["marker"] is False


@pytest.mark.parametrize(
    ("listing", "expected"),
    [
        ("", None),
        ("* Label: macOS Tahoe 26.2-25C56\n\tTitle: macOS Tahoe 26.2, Version: 26.2\n", None),
        (
            f"* Label: {OLD}\n\tTitle: x\n* Label: {NEW}\n\tTitle: x\n",
            NEW,
        ),
        (
            (
                "* Label: Command Line Tools for Xcode-10.1\n"
                "* Label: Command Line Tools for Xcode-9.9\n"
            ),
            "Command Line Tools for Xcode-10.1",
        ),
        (
            "* Label: Update-1\n\tTitle: Command Line Tools for Xcode, Version: 26.2\n",
            "Update-1",
        ),
    ],
)
def test_the_newest_command_line_tools_label_is_chosen_like_homebrew_does(
    listing: str, expected: str | None
) -> None:
    assert start_macos.newest_label(listing) == expected


def test_the_mac_platform_installs_the_tools_before_any_package() -> None:
    assert start.select_platform().steps_before_packages[0][0] == "clt.title"
