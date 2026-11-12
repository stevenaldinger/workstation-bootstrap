"""The last two steps: clone the setup repository, then hand off to its walkthrough."""

from __future__ import annotations

import shlex
from collections.abc import Sequence
from pathlib import Path

from start_base import MESSAGES, Context, Execv, StageError, tail
from start_identity import ssh_options

WS_RELATIVE = Path("bin/ws")


def clone_environment(ctx: Context) -> dict[str, str]:
    """The environment that makes git's ssh use the agent and the workstation key."""
    return {"GIT_SSH_COMMAND": shlex.join(["ssh", *ssh_options(ctx)])}


def directory_is_empty(path: Path) -> bool:
    return path.is_dir() and not any(path.iterdir())


def clone_repository(ctx: Context, url: str) -> None:
    """Clone to the configured directory, or fast-forward an existing checkout of the remote."""
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
    hooks_path = ctx.config.repository.hooks_path
    hooks = ctx.run(["git", "-C", str(target), "config", "core.hooksPath", hooks_path], None)
    if hooks.returncode != 0:
        raise StageError(MESSAGES["clone.failed"].format(detail=tail(hooks.output)))


def hand_off(ctx: Context, extra: Sequence[str], execv: Execv) -> None:
    """Replace this process with the cloned repository's `ws bootstrap`."""
    ws = ctx.clone_dir / WS_RELATIVE
    if not ws.is_file():
        raise StageError(MESSAGES["handoff.missing"].format(path=str(WS_RELATIVE)))
    execv(str(ws), [str(ws), "bootstrap", *extra])
