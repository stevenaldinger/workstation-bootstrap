"""A temporary home, fake commands, and a scripted person for the starter tests."""

from __future__ import annotations

import atexit
import copy
import functools
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import fake_tools
import pytest

import start_base as base
from start_config import StartConfig, load_config

REPO_ROOT = Path(__file__).resolve().parents[1]
FAKE_TOOLS = Path(fake_tools.__file__).resolve()
PERSONAL_EMAIL = "owner@example.com"
PERSONAL_ACCOUNT = {
    "url": "my.example.1password.com",
    "email": PERSONAL_EMAIL,
    "user_uuid": "USERPERSONAL000000000000000",
    "account_uuid": "ACCOUNTPERSONAL0000000000000",
    "shorthand": "personal",
}
WORK_ACCOUNT = {
    "url": "work.example.1password.com",
    "email": "owner@work.example",
    "user_uuid": "USERWORK0000000000000000000",
    "account_uuid": "ACCOUNTWORK0000000000000000",
    "shorthand": "work",
}


@functools.cache
def fake_exe() -> Path:
    """The stand-in Windows command, compiled once from tests/fake_exe.cs with the compiler
    that ships with .NET Framework, so no binary is committed."""
    folder = Path(tempfile.mkdtemp(prefix="fake-exe-"))
    atexit.register(shutil.rmtree, folder, ignore_errors=True)
    compiler = (
        Path(os.environ["WINDIR"]) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    )
    target = folder / "fake.exe"
    subprocess.run(
        [str(compiler), "/nologo", f"/out:{target}", str(REPO_ROOT / "tests" / "fake_exe.cs")],
        check=True,
        capture_output=True,
    )
    return target


# Windows-only tests run on a Windows host; WS_REQUIRE_WINDOWS makes any other host fail them
# instead of skipping, so a Windows job cannot pass while silently running nothing.
windows_only = pytest.mark.skipif(
    sys.platform != "win32" and not os.environ.get("WS_REQUIRE_WINDOWS"),
    reason="needs a Windows host",
)


def install_fake(bin_dir: Path, tool: str) -> None:
    """A command that runs the tool's behavior from fake_tools.py.

    `tool` may carry an `.exe` suffix, as a Windows command does; the behavior is keyed by the
    name without it. On Windows the command is a compiled copy of the stand-in executable.
    """
    name = tool.removesuffix(".exe")
    bin_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        shutil.copyfile(fake_exe(), bin_dir / f"{name}.exe")
        return
    target = bin_dir / tool
    target.write_text(
        f"#!/bin/sh\nexec '{sys.executable}' '{FAKE_TOOLS}' {name} \"$@\"\n", encoding="utf-8"
    )
    target.chmod(target.stat().st_mode | stat.S_IXUSR)


@dataclass
class Machine:
    """A temporary home whose commands are fakes backed by one world file."""

    home: Path
    bin_dir: Path
    fake_dir: Path

    @property
    def world(self) -> dict[str, Any]:
        return json.loads((self.fake_dir / "world.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]

    def update(self, change: Callable[[dict[str, Any]], None]) -> None:
        world = self.world
        change(world)
        (self.fake_dir / "world.json").write_text(json.dumps(world), encoding="utf-8")

    def calls(self, tool: str) -> list[list[Any]]:
        log = self.fake_dir / "calls.jsonl"
        if not log.exists():
            return []
        rows = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        return [row[1:-1] for row in rows if row[0] == tool]

    def call_log(self) -> list[list[Any]]:
        log = self.fake_dir / "calls.jsonl"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]

    def sign_in(self, *accounts: dict[str, str]) -> None:
        self.update(lambda world: world.update(app_accounts=list(accounts)))

    def integrations(self, *, on: bool) -> None:
        self.update(lambda world: world.update(integration=on, agent=on))

    def socket_ready(self, _path: Path) -> bool:
        return bool(self.world["agent"])


def build_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tools: Sequence[str] = ()
) -> Machine:
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = tmp_path / "bin"
    fake_dir = tmp_path / "fake"
    fake_dir.mkdir()
    for tool in ("op", "gh", "ssh", "open", "mise", *tools):
        install_fake(bin_dir, tool)
    (fake_dir / "world.json").write_text(json.dumps(fake_tools.DEFAULT_WORLD), encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("FAKE_DIR", str(fake_dir))
    monkeypatch.setenv("FAKE_PYTHON", sys.executable)
    monkeypatch.setenv("FAKE_TOOLS_PY", str(FAKE_TOOLS))
    system_path = os.environ["PATH"] if sys.platform == "win32" else "/usr/bin:/bin"
    monkeypatch.setenv("PATH", os.pathsep.join([str(bin_dir), system_path]))
    monkeypatch.delenv("WS_REPO_URL", raising=False)
    return Machine(home, bin_dir, fake_dir)


class FakeRegistry:
    """An in-memory registry; reading a hive the starter never uses fails the test."""

    HIVES = ("HKCU", "HKLM")

    def __init__(self) -> None:
        self.values: dict[tuple[str, str, str], str] = {}
        self.keys: set[tuple[str, str]] = set()

    def key_exists(self, hive: str, path: str) -> bool:
        assert hive in self.HIVES, hive
        return (hive, path) in self.keys

    def read_value(self, hive: str, path: str, name: str) -> str | None:
        assert hive in self.HIVES, hive
        return self.values.get((hive, path, name))

    def write_value(self, hive: str, path: str, name: str, value: str) -> None:
        assert hive in self.HIVES, hive
        self.keys.add((hive, path))
        self.values[(hive, path, name)] = value

    def delete_value(self, hive: str, path: str, name: str) -> None:
        assert hive in self.HIVES, hive
        self.values.pop((hive, path, name), None)


@dataclass
class WindowsHost:
    """A machine with the Windows platform, fake Windows commands, and an in-memory registry."""

    machine: Machine
    platform: base.Platform
    registry: FakeRegistry
    local_app_data: Path
    environ: dict[str, str]


WINDOWS_COMMANDS = ("wsl.exe", "shutdown.exe", "sc.exe", "cmd.exe", "powershell.exe")


def build_windows_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, keep_service: bool = False
) -> WindowsHost:
    """Fake `op`, `gh`, `git` stay real or fake as on the Mac; ssh lives in the system folder."""
    import start_windows

    machine = build_machine(tmp_path, monkeypatch, WINDOWS_COMMANDS)
    system_root = tmp_path / "windows"
    for name in ("ssh.exe", "ssh-add.exe"):
        install_fake(system_root / "System32" / "OpenSSH", name)
    local = tmp_path / "local"
    registry = FakeRegistry()
    environ = {"PATH": os.environ["PATH"]}
    if keep_service:
        environ[start_windows.KEEP_AGENT_SERVICE_ENV] = "1"
    platform = start_windows.windows_platform(
        registry=registry, system_root=system_root, local_app_data=local, environ=environ
    )
    return WindowsHost(machine, platform, registry, local, environ)


@dataclass
class Person:
    """Stands in for the owner: each line typed runs the next action and returns its text."""

    actions: list[Callable[[], str | None]] = field(default_factory=list)
    enters: int = 0

    def __call__(self, _prompt: str) -> str:
        if not self.actions:
            raise EOFError
        self.enters += 1
        return self.actions.pop(0)() or ""


@dataclass
class Session:
    """The console, its output lines, and the person driving it."""

    console: base.Console
    lines: list[str]
    person: Person

    def text(self, message_id: str, **values: str) -> str:
        return base.MESSAGES[message_id].format(**values)


def make_session(*actions: Callable[[], str | None]) -> Session:
    person = Person(list(actions))
    lines: list[str] = []
    return Session(base.Console(read=person, write=lines.append), lines, person)


def make_mirror(root: Path) -> Path:
    """A real Git repository standing in for the setup repository, with a recording bin/ws."""
    mirror = root / "mirror"
    (mirror / "bin").mkdir(parents=True)
    ws = mirror / "bin" / "ws"
    ws.write_text('#!/bin/sh\necho "$@"\n', encoding="utf-8")
    ws.chmod(0o755)
    (mirror / "bin" / "ws.cmd").write_text("@echo off\n", encoding="utf-8")
    (mirror / ".githooks").mkdir()
    (mirror / ".githooks" / "pre-commit").write_text("#!/bin/sh\n", encoding="utf-8")
    git = ["git", "-C", str(mirror), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "mirror"], check=True)
    return mirror


def starter_files() -> tuple[str, ...]:
    """The files `scripts/pins.sh` pins and `start.sh` downloads: its STARTER_FILES list."""
    script = (REPO_ROOT / "scripts" / "pins.sh").read_text(encoding="utf-8")
    match = re.search(r"^STARTER_FILES=\((.*)\)$", script, flags=re.M)
    assert match, "scripts/pins.sh has no STARTER_FILES list"
    return tuple(match.group(1).split())


def starter_config() -> StartConfig:
    """The settings the starter ships with."""
    return load_config(REPO_ROOT / "start.toml")


def make_context(
    machine: Machine,
    session: Session | None = None,
    *,
    config: StartConfig | None = None,
    platform: base.Platform | None = None,
) -> base.Context:
    """A starter context on the machine's home, with the session's console."""
    return base.Context(
        home=machine.home,
        personal=PERSONAL_EMAIL,
        console=(session or make_session()).console,
        config=config or starter_config(),
        socket_ready=machine.socket_ready,
        platform=platform or base.MACOS,
    )


def fresh_world() -> dict[str, Any]:
    return copy.deepcopy(fake_tools.DEFAULT_WORLD)
