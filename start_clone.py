"""The last two steps: clone the setup repository, then hand off to its walkthrough."""

from __future__ import annotations

import shlex
from collections.abc import Sequence
from pathlib import Path

from start_base import CLONE_TIMEOUT_SECONDS, MESSAGES, Context, Execv, StageError, tail
from start_identity import ssh_options


def clone_environment(ctx: Context) -> dict[str, str]:
    """The environment that makes git's ssh use the agent and the workstation key."""
    return {"GIT_SSH_COMMAND": shlex.join([ctx.platform.tool_path("ssh"), *ssh_options(ctx)])}


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
    result = ctx.run(command, environment, timeout=CLONE_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise StageError(MESSAGES["clone.failed"].format(detail=tail(result.output)))
    settings = {"core.hooksPath": ctx.config.repository.hooks_path}
    if ctx.platform.sets_core_ssh_command:
        # Git for Windows' bundled ssh cannot reach the 1Password agent, so later pulls use
        # the system ssh and the options in the owner's ssh config.
        settings["core.sshCommand"] = ctx.platform.tool_path("ssh")
    for key, value in settings.items():
        configured = ctx.run(["git", "-C", str(target), "config", key, value], None)
        if configured.returncode != 0:
            raise StageError(MESSAGES["clone.failed"].format(detail=tail(configured.output)))


def hand_off(ctx: Context, extra: Sequence[str], execv: Execv) -> None:
    """Replace this process with the cloned repository's `ws bootstrap`."""
    relative = ctx.platform.ws_script
    ws = ctx.clone_dir / relative
    if not ws.is_file():
        raise StageError(MESSAGES["handoff.missing"].format(path=str(relative)))
    try:
        execv(str(ws), [str(ws), "bootstrap", *extra])
    except OSError as error:
        reason = error.strerror or str(error)
        raise StageError(
            MESSAGES["handoff.failed"].format(path=str(relative), detail=reason)
        ) from error
