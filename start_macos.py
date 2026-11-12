"""The Mac host: the Command Line Tools, installed headless before any build.

`start.py` selects this module's platform when it runs on a Mac. Standard library only. Apple's
Command Line Tools supply the compiler and Git that later builds shell out to; without them, the
first program that calls `clang` opens a dialog that needs a click. The tools are installed
through Apple's own `softwareupdate`, the way the Homebrew installer does it, so no dialog
opens: the check passes when `xcode-select -p` names a developer folder, and nothing runs then.
"""

from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from start_base import MACOS, MESSAGES, Context, Platform, StageError

# Reading `softwareupdate -l` and installing the label follow the Homebrew installer's
# `install.sh` (github.com/Homebrew/install, commit 7986efac25016e41eed7e471666140c06f9d97cf,
# the `should_install_command_line_tools` block): a marker file makes `softwareupdate` list the
# Command Line Tools, the newest label is installed, the developer folder is selected, and the
# marker is removed on every exit path.
# The path is fixed by Apple: `softwareupdate` looks for this exact file.
MARKER = Path("/tmp/.com.apple.dt.CommandLineTools.installondemand.in-progress")  # noqa: S108
TOOLS_FOLDER = "/Library/Developer/CommandLineTools"
# Finding updates can take longer than an ordinary status query.
LIST_TIMEOUT_SECONDS = 300.0
# `grep -B 1 -E 'Command Line Tools'` keeps a line that names the tools and the line before it.
TOOLS_NAME = "Command Line Tools"
# `awk -F'*' '/^ *\*/ {print $2}'` then `sed -e 's/^ *Label: //' -e 's/^ *//'`.
LABEL_LINE = re.compile(r"^ *\*([^*]*)")
LABEL_PREFIX = re.compile(r"^ *Label: ")
LEADING_SPACES = re.compile(r"^ *")
DIGITS = re.compile(r"(\d+)")


def newest_label(listing: str) -> str | None:
    """The Command Line Tools label that `sort -V | tail -n1` picks from `softwareupdate -l`."""
    lines = listing.splitlines()
    labels: list[str] = []
    for number, line in enumerate(lines):
        following = lines[number + 1] if number + 1 < len(lines) else ""
        if TOOLS_NAME not in line and TOOLS_NAME not in following:
            continue
        found = LABEL_LINE.match(line)
        if found:
            label = LEADING_SPACES.sub("", LABEL_PREFIX.sub("", found[1])).strip()
            if label:
                labels.append(label)
    return max(labels, key=_version_key) if labels else None


def _version_key(label: str) -> tuple[object, ...]:
    """Numbers compare as numbers and text as text, as `sort -V` orders version strings."""
    return tuple(int(part) if part.isdigit() else part for part in DIGITS.split(label))


def tools_present(ctx: Context) -> bool:
    """Whether `xcode-select -p` names a developer folder."""
    return ctx.run(["xcode-select", "-p"]).returncode == 0


def install_command_line_tools(ctx: Context) -> None:
    """Install the Command Line Tools through `softwareupdate` unless they are already there."""
    if tools_present(ctx):
        ctx.console.say("clt.present")
        return
    ctx.console.say("clt.reason")
    try:
        _install(ctx)
    finally:
        ctx.run(["sudo", "rm", "-f", str(MARKER)])
    if not tools_present(ctx):
        raise StageError(MESSAGES["clt.still-missing"])


def _install(ctx: Context) -> None:
    placed = ctx.run(["sudo", "touch", str(MARKER)])
    if placed.returncode != 0:
        raise StageError(MESSAGES["clt.failed"].format(detail=placed.output.strip()))
    listing = ctx.run(["softwareupdate", "-l"], timeout=LIST_TIMEOUT_SECONDS)
    label = newest_label(listing.stdout)
    if label is None:
        raise StageError(MESSAGES["clt.not-listed"])
    code = ctx.stream(["sudo", "softwareupdate", "-i", label], None)
    if code != 0:
        raise StageError(MESSAGES["clt.install-failed"].format(label=label, code=str(code)))
    # Select the installed folder; `tools_present` then decides whether it worked.
    ctx.run(["sudo", "xcode-select", "--switch", TOOLS_FOLDER])


def macos_platform() -> Platform:
    """The Mac host: the shared platform plus the step that installs the Command Line Tools."""
    return dataclasses.replace(
        MACOS, steps_before_packages=(("clt.title", install_command_line_tools),)
    )
