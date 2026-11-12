"""Stage 1 package install: the password manager app and command line, and Git."""

from __future__ import annotations

import tempfile
from pathlib import Path

from start_base import MESSAGES, Context, StageError


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
