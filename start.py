#!/usr/bin/env python3
"""Stage 1 of the workstation bootstrap: tools, SSH identity, clone, and hand-off.

`start.sh` on a Mac, or `start.ps1` on Windows, installs a pinned mise, then Python, uv, and
the GitHub CLI through it, and runs this file under that Python. Standard library only. Each
step checks the machine first and does nothing when its check already passes, so a re-run
resumes at the first step that is not yet satisfied. Steps that need a person (signing in to
1Password, turning on its integrations, approving GitHub) run only when their check fails: the
walkthrough opens the app or pane, states the action, waits for Enter, checks again, and
repeats until the check passes.

    python start.py --personal-account <email or sign-in address> [--stage1 stage1.toml]

Any argument this file does not recognize is passed on to `ws bootstrap`. Settings live in
`start.toml`; set WS_REPO_URL to clone the setup repository from somewhere other than the
configured address.

The modules sit side by side, not in a package, because `start.sh` downloads exactly the files
listed in `start.sha256` into one folder and runs this file from it, so Python finds its
siblings there. `start_base.py` holds the shared plumbing, `start_config.py` loads
`start.toml`, `start_packages.py` installs the stage-1 packages, `start_identity.py` holds the
identity steps, `start_clone.py` clones and hands off, and `start_windows.py` holds the
Windows host's own steps: WSL, the restart, elevation, and the SSH agent service. The
`WS_KEEP_SSH_AGENT_SERVICE=1` environment variable keeps that service running; the clean-machine
proof sets it because its stand-in SSH agent is that service.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from start_base import (
    EXIT_FAILED,
    EXIT_INPUT_ENDED,
    MACOS,
    MESSAGES,
    Console,
    Context,
    EarlyExit,
    Execv,
    InputEnded,
    NamedStep,
    Platform,
    Runner,
    SocketCheck,
    StageError,
    Streamer,
    run_captured,
    run_streamed,
)
from start_clone import clone_repository, hand_off
from start_config import CONFIG_NAME, load_config
from start_identity import create_key, enable_integrations, register_key, sign_in
from start_packages import install_stage1

REPO_URL_ENV = "WS_REPO_URL"


def select_platform() -> Platform:
    """The platform of the machine this process runs on."""
    if sys.platform == "win32":
        from start_windows import windows_platform

        return windows_platform()
    return MACOS


def host_steps(ctx: Context, steps: Sequence[NamedStep]) -> list[tuple[str, Callable[[], None]]]:
    """A platform's own steps, bound to the context."""
    return [(title_id, lambda step=step: step(ctx)) for title_id, step in steps]  # type: ignore[misc]


def parse_args(argv: Sequence[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(
        prog="start.py", description="Stage 1 of the workstation bootstrap."
    )
    parser.add_argument(
        "--personal-account",
        required=True,
        help="Email or sign-in address of the personal 1Password account.",
    )
    parser.add_argument(
        "--stage1",
        type=Path,
        default=Path(__file__).with_name("stage1.toml"),
        help="Package list to install first (default: stage1.toml next to this file).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path(__file__).with_name(CONFIG_NAME),
        help=f"Starter settings (default: {CONFIG_NAME} next to this file).",
    )
    return parser.parse_known_args(list(argv))


def main(
    argv: Sequence[str] | None = None,
    *,
    home: Path | None = None,
    console: Console | None = None,
    run: Runner = run_captured,
    stream: Streamer = run_streamed,
    socket_ready: SocketCheck = Path.is_socket,
    execv: Execv | None = None,
    environ: Mapping[str, str] | None = None,
    platform: Platform | None = None,
) -> int:
    """Run every step in order; return the exit status when a step fails."""
    args, extra = parse_args(sys.argv[1:] if argv is None else argv)
    env = os.environ if environ is None else environ
    console = console or Console()
    try:
        config = load_config(args.config)
    except StageError as error:
        print(error, file=sys.stderr)
        return EXIT_FAILED
    ctx = Context(
        home=home or Path.home(),
        personal=args.personal_account,
        console=console,
        config=config,
        run=run,
        stream=stream,
        socket_ready=socket_ready,
        platform=platform or select_platform(),
    )
    url = env.get(REPO_URL_ENV) or config.repository.url
    steps: list[tuple[str, Callable[[], None]]] = [
        *host_steps(ctx, ctx.platform.steps_before_packages),
        ("stage1.title", lambda: install_stage1(ctx, args.stage1)),
        ("sign-in.title", lambda: sign_in(ctx)),
        *host_steps(ctx, ctx.platform.steps_before_integrations),
        ("integrations.title", lambda: enable_integrations(ctx)),
        ("key.title", lambda: create_key(ctx)),
        ("register.title", lambda: register_key(ctx)),
        ("clone.title", lambda: clone_repository(ctx, url)),
        ("handoff.title", lambda: hand_off(ctx, extra, execv or ctx.platform.execv)),
    ]
    title = ""
    try:
        for number, (title_id, step) in enumerate(steps, start=1):
            title = MESSAGES[title_id]
            ctx.console.say("step.heading", n=str(number), total=str(len(steps)), title=title)
            step()
    except StageError as error:
        print(error, file=sys.stderr)
        return EXIT_FAILED
    except EarlyExit as stop:
        print(stop.message, file=sys.stderr if stop.status else sys.stdout)
        return stop.status
    except InputEnded:
        ctx.console.say("input.ended", step=title)
        return EXIT_INPUT_ENDED
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
