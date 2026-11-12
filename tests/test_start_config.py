"""start.toml: the shipped settings load, an incomplete file names every gap, and settings act."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from starter_support import (
    PERSONAL_ACCOUNT,
    PERSONAL_EMAIL,
    Machine,
    build_machine,
    make_mirror,
    make_session,
    starter_config,
)

import start
import start_base as base
import start_config

COMPLETE = """
[identity]
item_title = "Another key"
vault = "Personal"
key_file_name = "another.pub"

[repository]
url = "git@example.test:me/setup.git"
clone_dir = "setup"
hooks_path = "hooks"
"""


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Machine:
    return build_machine(tmp_path, monkeypatch)


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "start.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_shipped_settings_load() -> None:
    config = starter_config()
    assert config.identity.item_title == "GitHub SSH (workstation)"
    assert config.identity.vault == "Private"
    assert config.repository.clone_dir == Path(".config/mise")
    assert config.repository.url.startswith("git@github.com:")


def test_a_complete_file_loads_every_value(tmp_path: Path) -> None:
    config = start_config.load_config(write(tmp_path, COMPLETE))
    assert config.identity.key_file_name == "another.pub"
    assert config.repository.hooks_path == "hooks"
    assert config.repository.clone_dir == Path("setup")


def test_an_incomplete_file_names_every_missing_setting_and_no_value(tmp_path: Path) -> None:
    text = '[identity]\nitem_title = "secret-looking-title"\n\n[repository]\nurl = ""\n'
    with pytest.raises(base.StageError) as raised:
        start_config.load_config(write(tmp_path, text))
    message = str(raised.value)
    for name in (
        "identity.vault",
        "identity.key_file_name",
        "repository.url",
        "repository.clone_dir",
        "repository.hooks_path",
    ):
        assert name in message
    assert "identity.item_title" not in message
    assert "secret-looking-title" not in message


@pytest.mark.parametrize("text", ["", "[identity]\nvault = 3\n", "not = [valid"])
def test_a_file_without_usable_settings_is_refused(tmp_path: Path, text: str) -> None:
    with pytest.raises(base.StageError):
        start_config.load_config(write(tmp_path, text))


def test_an_unreadable_file_is_refused_by_name(tmp_path: Path) -> None:
    with pytest.raises(base.StageError, match="cannot read the starter settings missing.toml"):
        start_config.load_config(tmp_path / "missing.toml")


def test_main_stops_before_any_step_when_the_settings_are_incomplete(
    machine: Machine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write(tmp_path, "[identity]\n")
    session = make_session()
    code = start.main(
        ["--personal-account", PERSONAL_EMAIL, "--config", str(config)],
        home=machine.home,
        console=session.console,
        environ={},
    )
    assert code == base.EXIT_FAILED
    assert "repository.url" in capsys.readouterr().err
    assert session.lines == []
    assert machine.call_log() == []


def test_the_settings_choose_the_clone_address_directory_hooks_and_key_item(
    machine: Machine, tmp_path: Path
) -> None:
    mirror = make_mirror(tmp_path)
    text = COMPLETE.replace("git@example.test:me/setup.git", str(mirror))
    config = write(tmp_path, text)
    machine.sign_in(PERSONAL_ACCOUNT)
    machine.integrations(on=True)
    stage1 = tmp_path / "stage1.toml"
    stage1.write_text("[bootstrap.packages]\n", encoding="utf-8")
    handed_off: list[str] = []
    code = start.main(
        ["--personal-account", PERSONAL_EMAIL, "--stage1", str(stage1), "--config", str(config)],
        home=machine.home,
        console=make_session().console,
        socket_ready=machine.socket_ready,
        execv=lambda path, argv: handed_off.append(path),
        environ={},
    )
    assert code == 0
    checkout = machine.home / "setup"
    assert handed_off == [str(checkout / "bin" / "ws")]
    hooks = subprocess.run(
        ["git", "-C", str(checkout), "config", "--get", "core.hooksPath"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert hooks == "hooks"
    assert (machine.home / ".ssh" / "another.pub").is_file()
    item_calls = [call for call in machine.calls("op") if call[:2] == ["item", "create"]]
    assert item_calls[0][item_calls[0].index("--title") + 1] == "Another key"
    assert item_calls[0][item_calls[0].index("--vault") + 1] == "Personal"


def test_the_environment_address_wins_over_the_settings(machine: Machine, tmp_path: Path) -> None:
    mirror = make_mirror(tmp_path)
    machine.sign_in(PERSONAL_ACCOUNT)
    machine.integrations(on=True)
    stage1 = tmp_path / "stage1.toml"
    stage1.write_text("[bootstrap.packages]\n", encoding="utf-8")
    code = start.main(
        ["--personal-account", PERSONAL_EMAIL, "--stage1", str(stage1)],
        home=machine.home,
        console=make_session().console,
        socket_ready=machine.socket_ready,
        execv=lambda path, argv: None,
        environ={start.REPO_URL_ENV: str(mirror)},
    )
    assert code == 0
    assert (machine.home / ".config" / "mise" / "bin" / "ws").is_file()
