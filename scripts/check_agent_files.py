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
- an absolute path or a `../` path inside a .claude/skills/*/SKILL.md or .claude/agents/*.md file;
- a `.claude/settings.json` `agent` setting whose agent file `.claude/agents/<name>.md` is
  missing, is a symlink, or declares a different `name`, and a settings file that is not a JSON
  object.

Run it through uv:

    uv run scripts/check_agent_files.py [--root DIR]
"""

from __future__ import annotations

import argparse
import json
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


def instruction_files(root: Path) -> list[Path]:
    """The skill and agent definition files, which are read from any machine."""
    claude = root / ".claude"
    return sorted([*claude.glob("skills/*/SKILL.md"), *claude.glob("agents/*.md")])


def skill_path_problems(root: Path) -> list[str]:
    problems = []
    for definition in instruction_files(root):
        if definition.is_symlink():
            continue
        for number, line in enumerate(definition.read_text(encoding="utf-8").splitlines(), start=1):
            if ABSOLUTE_PATH_RE.search(line):
                problems.append(
                    f"{relative(definition, root)}:{number}: absolute path; "
                    "use a path under ${CLAUDE_PROJECT_DIR} or relative to the repository"
                )
            if PARENT_PATH_RE.search(line):
                problems.append(
                    f"{relative(definition, root)}:{number}: '../' path leaves the repository"
                )
    return problems


def declared_name(path: Path) -> str | None:
    """The `name` in the agent file's frontmatter, or None when it has none."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            return None
        key, _, value = line.partition(":")
        if key.strip() == "name":
            return value.strip().strip("\"'")
    return None


def default_agent_problems(root: Path) -> list[str]:
    """The `agent` setting must name an agent file that exists, is real, and matches."""
    settings = root / ".claude" / "settings.json"
    if not settings.is_file():
        return []
    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
    except ValueError as error:
        return [f".claude/settings.json: not valid JSON ({error})"]
    if not isinstance(data, dict):
        return [".claude/settings.json: must hold a JSON object"]
    if "agent" not in data:
        return []
    name = data["agent"]
    if not isinstance(name, str) or not name:
        return [".claude/settings.json: 'agent' must be the name of an agent"]
    path = root / ".claude" / "agents" / f"{name}.md"
    if path.is_symlink():
        return [f"{relative(path, root)}: is a symlink; the default agent must be a real file"]
    if not path.is_file():
        return [f".claude/settings.json: 'agent' is {name!r} but {relative(path, root)} is missing"]
    if declared_name(path) != name:
        return [f"{relative(path, root)}: frontmatter 'name' must be {name!r}, the default agent"]
    return []


def check(root: Path) -> list[str]:
    """Every problem found under root; an empty list means the files are in order."""
    return (
        claude_md_problems(root)
        + symlink_problems(root)
        + skill_path_problems(root)
        + default_agent_problems(root)
    )


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
