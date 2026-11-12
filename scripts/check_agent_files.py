#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Check the agent instruction files for the layouts that break how Claude Code loads them.

Claude Code skips AGENTS.md when a CLAUDE.md exists, and it does not load a skill reached
through a symlink, so this repository holds neither. A skill that names an absolute path or a
parent-relative path only works on the machine that wrote it. The check reports:

- any file named CLAUDE.md, in any directory;
- AGENTS.md, or anything under .claude/, that is a symlink (a dangling one included);
- an absolute path or a `../` path inside a .claude/skills/*/SKILL.md file.

Run it through uv:

    uv run scripts/check_agent_files.py [--root DIR]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path

SKIPPED_DIRECTORIES = frozenset(
    {".git", ".venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
)
# A slash that starts a token (after whitespace, a quote, a bracket, or `=`) and is followed
# by a path character. URLs, `a/b`, `and/or`, and `${VAR}/x` do not start a token with a slash.
ABSOLUTE_PATH_RE = re.compile(r"(?<![^\s`'\"(\[<=,;])/[\w.~-]")
PARENT_PATH_RE = re.compile(r"\.\./")


def walk_files(root: Path) -> Iterator[Path]:
    """Every file and directory entry under root, without following symlinks."""
    for current, directories, files in os.walk(root, followlinks=False):
        directories[:] = [name for name in directories if name not in SKIPPED_DIRECTORIES]
        for name in (*directories, *files):
            yield Path(current) / name


def relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def claude_md_problems(root: Path) -> list[str]:
    return [
        f"{relative(path, root)}: CLAUDE.md makes Claude Code skip AGENTS.md; remove it"
        for path in walk_files(root)
        if path.name.lower() == "claude.md"
    ]


def symlink_problems(root: Path) -> list[str]:
    problems = []
    agents = root / "AGENTS.md"
    if agents.is_symlink():
        problems.append("AGENTS.md: is a symlink; Claude Code needs a regular file")
    claude = root / ".claude"
    if claude.is_symlink():
        problems.append(".claude: is a symlink; Claude Code needs a real directory")
    elif claude.is_dir():
        problems += [
            f"{relative(path, root)}: is a symlink; Claude Code does not load through symlinks"
            for path in walk_files(claude)
            if path.is_symlink()
        ]
    return problems


def skill_path_problems(root: Path) -> list[str]:
    problems = []
    for skill in sorted((root / ".claude" / "skills").glob("*/SKILL.md")):
        if skill.is_symlink():
            continue
        for number, line in enumerate(skill.read_text(encoding="utf-8").splitlines(), start=1):
            if ABSOLUTE_PATH_RE.search(line):
                problems.append(
                    f"{relative(skill, root)}:{number}: absolute path; "
                    "use a path under ${CLAUDE_PROJECT_DIR} or relative to the repository"
                )
            if PARENT_PATH_RE.search(line):
                problems.append(
                    f"{relative(skill, root)}:{number}: '../' path leaves the repository"
                )
    return problems


def check(root: Path) -> list[str]:
    """Every problem found under root; an empty list means the files are in order."""
    return claude_md_problems(root) + symlink_problems(root) + skill_path_problems(root)


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check_agent_files.py", description="Check the agent instruction files."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="Repository root (default: the repository holding this script).",
    )
    return parser.parse_args(list(argv))


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    problems = check(args.root)
    for problem in problems:
        print(f"ERROR  {problem}", file=sys.stderr)
    if problems:
        return 1
    print("agent files: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
