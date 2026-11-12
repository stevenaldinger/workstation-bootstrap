#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Refuse real email addresses in tracked files.

This repository is public, so a tracked file may name only addresses at domains reserved for
examples: `example.com`, `example.org`, `example.net` (and their subdomains), and any `.test`,
`.invalid`, `.localhost`, or `.example` domain. `git@github.com:` remotes are allowed. Each other
address is reported as `file:line` with the address itself left out, so the report does not
leak what it finds. Files that are not UTF-8 text are skipped.

Run it through uv:

    uv run scripts/check_emails.py [--root DIR]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

EMAIL_RE = re.compile(r"(?P<local>[\w.%+-]+)@(?P<domain>(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,})")
RESERVED_DOMAINS = ("example.com", "example.org", "example.net")
RESERVED_SUFFIXES = ("test", "invalid", "localhost", "example")
GIT_REMOTE = ("git", "github.com")


def reserved(local: str, domain: str) -> bool:
    """Whether an address is safe to commit: an example domain or a GitHub remote."""
    domain = domain.lower()
    return (
        (local, domain) == GIT_REMOTE
        or domain.rpartition(".")[2] in RESERVED_SUFFIXES
        or any(domain == name or domain.endswith(f".{name}") for name in RESERVED_DOMAINS)
    )


def check(root: Path) -> list[str]:
    """One `file:line` entry for each line of a tracked file that holds a real address."""
    listed = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    problems = []
    for name in filter(None, listed.split("\0")):
        try:
            lines = (root / name).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(lines, start=1):
            if any(not reserved(m["local"], m["domain"]) for m in EMAIL_RE.finditer(line)):
                problems.append(
                    f"{name}:{number}: email address at a domain not reserved for examples"
                )
    return problems


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check_emails.py", description="Refuse real email addresses in tracked files."
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
    print("emails: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
