"""Shared starter plumbing: messages, command runners, the console, and the human-step loop."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from start_config import StartConfig

AGENT_SOCKET_RELATIVE = Path("Library/Group Containers/2BUA8C4S2C.com.1password/t/agent.sock")
TAIL_LINES = 3
# A captured command (op, gh, git) that has not finished by now is stuck, not slow.
COMMAND_TIMEOUT_SECONDS = 120.0
# Cloning the setup repository over a slow link takes longer than a status query.
CLONE_TIMEOUT_SECONDS = 900.0
EXIT_TIMED_OUT = 124
EXIT_NOT_EXECUTABLE = 126
EXIT_NOT_FOUND = 127
EXIT_FAILED = 1
EXIT_INPUT_ENDED = 130

MESSAGES: dict[str, str] = {
    "step.heading": "[{n}/{total}] {title}",
    "step.failed": 'Still waiting on "{step}": {detail}',
    "step.retry": "Press Enter to try again.",
    "choice.invalid": "Type a number from 1 to {count} and press Enter.",
    "input.ended": "Input ended before {step} finished; run the same command again to resume.",
    "stage1.title": "Install the password manager, Git, and the GitHub CLI",
    "stage1.failed": "Installing the stage-1 packages failed: {detail}",
    "sign-in.title": "Sign in to the personal 1Password account",
    "sign-in.prompt": (
        "In the 1Password app that just opened, sign in to the personal account {account}, "
        "then press Enter. The app's developer settings must also have 'Integrate with "
        "1Password CLI' turned on, or the command line cannot see the account."
    ),
    "sign-in.absent": "the personal account {account} is not listed by the 1Password command line",
    "integrations.title": "Turn on the 1Password command-line integration and SSH agent",
    "integrations.prompt": (
        "In the 1Password developer settings that just opened, turn on 'Integrate with "
        "1Password CLI' and 'Use the SSH agent', then press Enter."
    ),
    "integrations.vault-denied": "the command line could not list vaults: {detail}",
    "integrations.socket-missing": "the 1Password SSH agent socket does not exist at {path}",
    "key.title": "Create the GitHub SSH key in 1Password",
    "key.created": "Created the key {item} in the {vault} vault.",
    "key.unreadable": "The public key of {item} could not be read: {detail}",
    "register.title": "Register the key with GitHub",
    "register.registered": "Registered the key on GitHub.",
    "register.failed": "Registering the key failed: {detail}",
    "register.scope-kept": (
        "The {scope} scope is still on the GitHub token ({detail}). Remove it with: "
        "gh auth refresh --hostname github.com --remove-scopes {scope}, then run the same "
        "command again."
    ),
    "key.lookup-failed": "Could not look for the key {item} in the {vault} vault: {detail}",
    "register.handshake-failed": (
        "GitHub did not accept the key: {detail}. Lock and unlock 1Password so its agent offers "
        "the new key, then run the same command again."
    ),
    "approval.prompt": (
        "Approve the GitHub sign-in in the browser with the one-time code the next command prints."
    ),
    "approval.scope-missing": "the GitHub token lacks the {scope} scope",
    "clone.title": "Clone the setup repository",
    "clone.foreign": "{path} exists and is not a checkout of the setup repository; move it away.",
    "clone.failed": "Cloning the setup repository failed: {detail}",
    "handoff.title": "Hand off to the walkthrough",
    "handoff.missing": "The cloned repository has no {path}.",
    "handoff.failed": "Could not start {path} from the cloned repository: {detail}.",
}


class StageError(Exception):
    """A step failed in a way the owner must fix before running the command again."""


class InputEnded(Exception):
    """Standard input closed while a step was waiting for Enter."""


@dataclass(frozen=True)
class Completed:
    """Exit status and output of one command."""

    returncode: int
    stdout: str
    output: str


class Runner(Protocol):
    """Runs a command with stdin closed, captures its output, and gives up after `timeout`."""

    def __call__(
        self,
        argv: Sequence[str],
        env: Mapping[str, str] | None = None,
        *,
        timeout: float = COMMAND_TIMEOUT_SECONDS,
    ) -> Completed: ...


Streamer = Callable[[Sequence[str], Mapping[str, str] | None], int]
SocketCheck = Callable[[Path], bool]
Execv = Callable[[str, list[str]], object]


def failed_to_start(argv: Sequence[str], error: OSError) -> tuple[int, str]:
    """Exit status and message for a command that could not be started."""
    if isinstance(error, FileNotFoundError):
        return EXIT_NOT_FOUND, f"{argv[0]}: command not found"
    return EXIT_NOT_EXECUTABLE, f"{argv[0]}: cannot run: {error.strerror or error}"


def run_captured(
    argv: Sequence[str],
    env: Mapping[str, str] | None = None,
    *,
    timeout: float = COMMAND_TIMEOUT_SECONDS,
) -> Completed:
    """Run a command with stdin closed and capture its output.

    A missing command is status 127, one that cannot be run is 126, and one still running after
    `timeout` seconds is stopped and reported as 124.
    """
    try:
        proc = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            check=False,
            stdin=subprocess.DEVNULL,
            env=None if env is None else {**os.environ, **env},
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return Completed(EXIT_TIMED_OUT, "", f"{argv[0]}: timed out after {timeout:.0f}s")
    except OSError as error:
        code, message = failed_to_start(argv, error)
        return Completed(code, "", message)
    return Completed(proc.returncode, proc.stdout, proc.stdout + proc.stderr)


def run_streamed(argv: Sequence[str], env: Mapping[str, str] | None = None) -> int:
    """Run a command attached to the terminal and return its exit status.

    It has no time limit, because a person may be completing a step in it.
    """
    try:
        return subprocess.run(
            list(argv), check=False, env=None if env is None else {**os.environ, **env}
        ).returncode
    except OSError as error:
        code, message = failed_to_start(argv, error)
        print(message, file=sys.stderr)
        return code


class Console:
    """Prints messages by ID, records the IDs shown, and reads Enter confirmations."""

    def __init__(
        self,
        read: Callable[[str], str] = input,
        write: Callable[[str], None] = print,
    ) -> None:
        self._read = read
        self._write = write
        self.shown: list[str] = []

    def say(self, message_id: str, **values: str) -> None:
        self.shown.append(message_id)
        self._write(MESSAGES[message_id].format(**values))

    def wait(self) -> None:
        try:
            self._read("")
        except EOFError:
            raise InputEnded from None

    def choose(self, message_id: str, option_ids: Sequence[str], **values: str) -> int:
        """Show a question and its numbered options; return the zero-based index typed.

        Only a number in range counts, so a bare Enter never answers for the person.
        """
        self.say(message_id, **values)
        for number, option_id in enumerate(option_ids, start=1):
            self._write(f"  {number}. {MESSAGES[option_id]}")
        while True:
            try:
                answer = self._read("").strip()
            except EOFError:
                raise InputEnded from None
            if answer.isdigit() and 1 <= int(answer) <= len(option_ids):
                return int(answer) - 1
            self.say("choice.invalid", count=str(len(option_ids)))


@dataclass(frozen=True)
class Probe:
    """The result of a step's check: whether it passes and why not."""

    ok: bool
    detail: str = ""


Step = Callable[["Context"], None]
NamedStep = tuple[str, Step]


def mac_agent_probe(ctx: Context) -> Probe:
    """Whether the 1Password SSH agent's socket exists."""
    if ctx.socket_ready(ctx.agent_socket):
        return Probe(True)
    return Probe(False, MESSAGES["integrations.socket-missing"].format(path=str(ctx.agent_socket)))


def keep_environment(_ctx: Context) -> None:
    """Leave the process environment as it is."""


@dataclass(frozen=True)
class Platform:
    """What differs between the hosts the starter runs on; the steps themselves are shared.

    `open_app` and `open_settings` are the commands that show the password manager and its
    developer settings. `ssh_dir` is the folder that holds `ssh`, or None to find it on PATH.
    `steps_before_packages` and `steps_before_integrations` are host-only steps, each a message
    ID for its heading and a function of the context.
    """

    name: str
    open_app: tuple[str, ...]
    open_settings: tuple[str, ...]
    ssh_dir: Path | None
    ssh_suffix: str
    null_device: str
    ws_script: Path
    agent_option: bool
    sets_core_ssh_command: bool
    integrations_prompt: str
    stage1_notice: str
    agent_probe: Callable[[Context], Probe]
    refresh_environment: Callable[[Context], None]
    execv: Execv
    steps_before_packages: tuple[NamedStep, ...] = ()
    steps_before_integrations: tuple[NamedStep, ...] = ()

    def tool_path(self, name: str) -> str:
        """The command that runs `name` from the platform's ssh folder, in forward slashes."""
        if self.ssh_dir is None:
            return name
        return (self.ssh_dir / f"{name}{self.ssh_suffix}").as_posix()


MACOS = Platform(
    name="macos",
    open_app=("open", "-a", "1Password"),
    open_settings=("open", "onepassword://settings/developers"),
    ssh_dir=None,
    ssh_suffix="",
    null_device="/dev/null",
    ws_script=Path("bin/ws"),
    agent_option=True,
    sets_core_ssh_command=False,
    integrations_prompt="integrations.prompt",
    stage1_notice="",
    agent_probe=mac_agent_probe,
    refresh_environment=keep_environment,
    execv=os.execv,
)


@dataclass
class Context:
    """Everything the steps share: paths, the account, and the process seams."""

    home: Path
    personal: str
    console: Console
    config: StartConfig
    run: Runner = run_captured
    stream: Streamer = run_streamed
    socket_ready: SocketCheck = Path.is_socket
    platform: Platform = MACOS
    account_id: str = ""

    @property
    def ssh_dir(self) -> Path:
        return self.home / ".ssh"

    @property
    def key_file(self) -> Path:
        return self.ssh_dir / self.config.identity.key_file_name

    @property
    def agent_socket(self) -> Path:
        return self.home / AGENT_SOCKET_RELATIVE

    @property
    def clone_dir(self) -> Path:
        return self.home / self.config.repository.clone_dir


def tail(output: str) -> str:
    """The last few lines of command output on one line."""
    return " | ".join(output.strip().splitlines()[-TAIL_LINES:])


def run_human_step(
    ctx: Context,
    title_id: str,
    check: Callable[[], Probe],
    act: Callable[[], None],
    prompt: str,
    *,
    waits: bool = True,
    **values: str,
) -> None:
    """Skip silently when the check passes; otherwise act, prompt, wait, and re-check."""
    probe = check()
    attempts = 0
    while not probe.ok:
        if attempts > 0:
            ctx.console.say("step.failed", step=MESSAGES[title_id], detail=probe.detail)
            if not waits:
                ctx.console.say("step.retry")
                ctx.console.wait()
        attempts += 1
        ctx.console.say(prompt, **values)
        act()
        if waits:
            ctx.console.wait()
        probe = check()
