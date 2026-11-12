"""Stage 1 of the workstation bootstrap: tools, SSH identity, clone, and hand-off.

`start.sh` installs a pinned mise, Python, and uv, then runs this file under that Python.
Standard library only. Each step checks the machine first and does nothing when its check
already passes, so a re-run resumes at the first step that is not yet satisfied. Steps that
need a person (signing in to 1Password, turning on its integrations, approving GitHub) run
only when their check fails: the walkthrough opens the app or pane, states the action,
waits for Enter, checks again, and repeats until the check passes.

    python start.py --personal-account <email or sign-in address> [--stage1 stage1.toml]

Any argument this file does not recognize is passed on to `ws bootstrap`. Set WS_REPO_URL to
clone the setup repository from somewhere other than GitHub. The identity steps live in
`start_identity.py` and the shared plumbing in `start_base.py`; `start.sh` fetches all three.
"""

from __future__ import annotations

import argparse
import os
import shlex
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from start_base import (
    EXIT_FAILED,
    EXIT_INPUT_ENDED,
    MESSAGES,
    Console,
    Context,
    Execv,
    InputEnded,
    Runner,
    SocketCheck,
    StageError,
    Streamer,
    run_captured,
    run_streamed,
    tail,
)
from start_identity import (
    create_key,
    enable_integrations,
    register_key,
    sign_in,
    ssh_options,
)

DEFAULT_REPO_URL = "git@github.com:stevenaldinger/workstation-setup.git"
REPO_URL_ENV = "WS_REPO_URL"
HOOKS_PATH = ".githooks"
WS_RELATIVE = Path("bin/ws")


def clone_environment(ctx: Context) -> dict[str, str]:
    """The environment that makes git's ssh use the agent and the workstation key."""
    return {"GIT_SSH_COMMAND": shlex.join(["ssh", *ssh_options(ctx)])}


def directory_is_empty(path: Path) -> bool:
    return path.is_dir() and not any(path.iterdir())


def clone_repository(ctx: Context, url: str) -> None:
    """Clone to the mise config directory, or fast-forward an existing checkout of the remote."""
    target = ctx.clone_dir
    environment = clone_environment(ctx)
    if (target / ".git").exists():
        origin = ctx.run(["git", "-C", str(target), "config", "--get", "remote.origin.url"], None)
        if origin.stdout.strip() != url:
            raise StageError(MESSAGES["clone.foreign"].format(path=str(target)))
        command = ["git", "-C", str(target), "pull", "--ff-only"]
    elif target.exists() and not directory_is_empty(target):
        raise StageError(MESSAGES["clone.foreign"].format(path=str(target)))
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        command = ["git", "clone", url, str(target)]
    result = ctx.run(command, environment)
    if result.returncode != 0:
        raise StageError(MESSAGES["clone.failed"].format(detail=tail(result.output)))
    hooks = ctx.run(["git", "-C", str(target), "config", "core.hooksPath", HOOKS_PATH], None)
    if hooks.returncode != 0:
        raise StageError(MESSAGES["clone.failed"].format(detail=tail(hooks.output)))


def install_stage1(ctx: Context, stage1: Path) -> None:
    """Install the stage-1 packages unless `mise bootstrap packages status` finds none missing."""
    # Mise merges every file in its config directory with the global file, so once the clone
    # exists a re-run would see all declared packages. An empty directory keeps this stage to
    # the stage-1 list.
    with tempfile.TemporaryDirectory(prefix="stage1-") as empty:
        environment = {"MISE_GLOBAL_CONFIG_FILE": str(stage1), "MISE_CONFIG_DIR": empty}
        status = ctx.run(["mise", "bootstrap", "packages", "status", "--missing"], environment)
        if status.returncode == 0:
            return
        code = ctx.stream(["mise", "bootstrap", "packages", "apply", "--yes"], environment)
        if code != 0:
            raise StageError(MESSAGES["stage1.failed"].format(detail=f"exit {code}"))


def hand_off(ctx: Context, extra: Sequence[str], execv: Execv) -> None:
    """Replace this process with the cloned repository's `ws bootstrap`."""
    ws = ctx.clone_dir / WS_RELATIVE
    if not ws.is_file():
        raise StageError(MESSAGES["handoff.missing"].format(path=str(WS_RELATIVE)))
    execv(str(ws), [str(ws), "bootstrap", *extra])


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
    return parser.parse_known_args(list(argv))


def main(
    argv: Sequence[str] | None = None,
    *,
    home: Path | None = None,
    console: Console | None = None,
    run: Runner = run_captured,
    stream: Streamer = run_streamed,
    socket_ready: SocketCheck = Path.is_socket,
    execv: Execv = os.execv,
    environ: Mapping[str, str] | None = None,
) -> int:
    """Run every step in order; return the exit status when a step fails."""
    args, extra = parse_args(sys.argv[1:] if argv is None else argv)
    env = os.environ if environ is None else environ
    ctx = Context(
        home=home or Path.home(),
        personal=args.personal_account,
        console=console or Console(),
        run=run,
        stream=stream,
        socket_ready=socket_ready,
    )
    url = env.get(REPO_URL_ENV) or DEFAULT_REPO_URL
    steps: list[tuple[str, Callable[[], None]]] = [
        ("stage1.title", lambda: install_stage1(ctx, args.stage1)),
        ("h1.title", lambda: sign_in(ctx)),
        ("h3.title", lambda: enable_integrations(ctx)),
        ("key.title", lambda: create_key(ctx)),
        ("register.title", lambda: register_key(ctx)),
        ("clone.title", lambda: clone_repository(ctx, url)),
        ("handoff.title", lambda: hand_off(ctx, extra, execv)),
    ]
    try:
        for number, (title_id, step) in enumerate(steps, start=1):
            title = MESSAGES[title_id]
            ctx.console.say("step.heading", n=str(number), total=str(len(steps)), title=title)
            step()
    except StageError as error:
        print(error, file=sys.stderr)
        return EXIT_FAILED
    except InputEnded:
        ctx.console.say("input.ended", step=title)
        return EXIT_INPUT_ENDED
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
