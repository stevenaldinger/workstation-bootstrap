#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Validate commit messages against the Conventional Commits format this repository uses.

A message is `type(scope): summary`, a blank line, and a body that explains why. The type is
one of feat, fix, docs, style, refactor, perf, test, build, ci, chore, or revert. The header
is at most 72 characters, the summary starts lowercase and has no trailing period, body lines
wrap at 72, and no AI or session attribution trailer appears.

Two modes, run through uv:

    uv run scripts/check_commits.py --message-file FILE      # the commit-msg hook
    uv run scripts/check_commits.py [--base SHA] [--head REV]  # a range of history

History mode reads every commit reachable from the head. Commits up to and including the
base only produce warnings; commits after it fail the run. The base is `--base`, else the
commit recorded in scripts/commit-lint-base.txt, else none (every commit is enforced). CI
passes the commit before the pushed range as `--base`.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

TYPES = (
    "feat",
    "fix",
    "docs",
    "style",
    "refactor",
    "perf",
    "test",
    "build",
    "ci",
    "chore",
    "revert",
)
MAX_HEADER = 72
MAX_BODY_LINE = 72
ZERO_SHA = "0" * 40
RECORDED_BASE = Path(__file__).with_name("commit-lint-base.txt")
HEADER_RE = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^()]*)\))?(?P<bang>!)?: (?P<summary>.*)$")
SCOPE_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*$")
ATTRIBUTION_RE = re.compile(
    r"^(co-authored-by|claude-session|generated-by)\s*:"
    r"|generated with .*claude|noreply@anthropic\.com|claude\.ai/",
    re.IGNORECASE | re.MULTILINE,
)
RECORD_SEPARATOR = "\x1e"
FIELD_SEPARATOR = "\x1f"


def strip_comments(text: str) -> str:
    """The message as git stores it: comment lines removed, trailing blank lines dropped."""
    lines = [line for line in text.splitlines() if not line.startswith("#")]
    return "\n".join(lines).strip("\n")


def check_header(header: str) -> list[str]:
    if header.startswith("Merge "):
        return []
    match = HEADER_RE.match(header)
    if match is None:
        return [f"header must look like 'type(scope): summary', got {header!r}"]
    problems: list[str] = []
    if match["type"] not in TYPES:
        problems.append(f"unknown type {match['type']!r}; use one of {', '.join(TYPES)}")
    scope = match["scope"]
    if scope is None or not SCOPE_RE.match(scope):
        problems.append("scope is required: lowercase letters, digits, and ._/- inside ()")
    summary = match["summary"]
    if not summary.strip():
        problems.append("summary is empty")
    elif summary[0].isupper():
        problems.append("summary must start lowercase")
    if summary.endswith("."):
        problems.append("summary must not end with a period")
    if len(header) > MAX_HEADER:
        problems.append(f"header is {len(header)} characters; the limit is {MAX_HEADER}")
    return problems


def check_body(lines: Sequence[str]) -> list[str]:
    if not lines:
        return ["add a body after a blank line that explains why the change is made"]
    problems: list[str] = []
    if lines[0].strip():
        problems.append("the second line must be blank")
    body = list(lines[1:])
    if not any(line.strip() for line in body):
        problems.append("add a body that explains why the change is made")
    for number, line in enumerate(body, start=3):
        # A line without whitespace is a URL or path that cannot wrap.
        if len(line) > MAX_BODY_LINE and " " in line.strip():
            problems.append(
                f"line {number} is {len(line)} characters; wrap body lines at {MAX_BODY_LINE}"
            )
    return problems


def check_message(text: str) -> list[str]:
    """Every rule the message breaks; an empty list means it passes."""
    message = strip_comments(text)
    if not message.strip():
        return ["the message is empty"]
    lines = message.splitlines()
    problems = check_header(lines[0])
    if not lines[0].startswith("Merge "):
        problems += check_body(lines[1:])
    if ATTRIBUTION_RE.search(message):
        problems.append("remove AI and session attribution trailers")
    return problems


def git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False, cwd=cwd)


def commit_exists(sha: str, cwd: Path | None) -> bool:
    return git("cat-file", "-e", f"{sha}^{{commit}}", cwd=cwd).returncode == 0


def read_recorded_base(path: Path = RECORDED_BASE) -> str | None:
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            return line.strip()
    return None


def resolve_base(argument: str | None, cwd: Path | None, recorded: Path) -> str | None:
    """The commit that bounds enforcement, or None when every commit is enforced."""
    if argument and argument != ZERO_SHA:
        if commit_exists(argument, cwd):
            return argument
        print(
            f"warning: base {argument} is not in this clone; using the recorded base",
            file=sys.stderr,
        )
    return read_recorded_base(recorded)


def history(head: str, cwd: Path | None) -> list[tuple[str, str]]:
    """(sha, message) for every commit reachable from head, oldest first."""
    fmt = f"%H{FIELD_SEPARATOR}%B{RECORD_SEPARATOR}"
    listing = git("log", "--reverse", f"--format={fmt}", head, cwd=cwd)
    if listing.returncode != 0:
        raise SystemExit(f"error: cannot read history from {head}: {listing.stderr.strip()}")
    entries = []
    for record in listing.stdout.split(RECORD_SEPARATOR):
        if record.strip():
            sha, _, message = record.strip("\n").partition(FIELD_SEPARATOR)
            entries.append((sha, message))
    return entries


def check_history(head: str, base: str | None, cwd: Path | None) -> tuple[int, int]:
    """Print every violation; return (enforced violations, report-only violations)."""
    enforced: set[str] | None = None
    if base is not None:
        if not commit_exists(base, cwd):
            raise SystemExit(
                f"error: the base {base} is not in this history; update or remove "
                f"{RECORDED_BASE.name} if it holds the base"
            )
        after = git("rev-list", f"{base}..{head}", cwd=cwd)
        enforced = set(after.stdout.split())
    failures = warnings = 0
    for sha, message in history(head, cwd):
        problems = check_message(message)
        if not problems:
            continue
        subject = message.splitlines()[0] if message.strip() else ""
        report_only = enforced is not None and sha not in enforced
        label = "WARN " if report_only else "ERROR"
        stream = sys.stdout if report_only else sys.stderr
        print(f"{label}  {sha[:9]} {subject}", file=stream)
        for problem in problems:
            print(f"        {problem}", file=stream)
        if report_only:
            warnings += 1
        else:
            failures += 1
    return failures, warnings


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="check_commits.py",
        description="Validate Conventional Commit messages.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--message-file", type=Path, help="Validate one message (commit-msg hook).")
    mode.add_argument("--base", help="Commit before the enforced range; older commits only warn.")
    parser.add_argument("--head", default="HEAD", help="Last commit to check (default: HEAD).")
    return parser.parse_args(list(argv))


def main(
    argv: Sequence[str] | None = None,
    cwd: Path | None = None,
    recorded_base: Path = RECORDED_BASE,
) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.message_file is not None:
        problems = check_message(args.message_file.read_text(encoding="utf-8"))
        for problem in problems:
            print(f"commit message: {problem}", file=sys.stderr)
        if problems:
            print(
                "format: type(scope): summary, blank line, body that explains why", file=sys.stderr
            )
        return 1 if problems else 0
    base = resolve_base(args.base, cwd, recorded_base)
    failures, warnings = check_history(args.head, base, cwd)
    if warnings:
        print(f"{warnings} commit(s) predate the base and are report-only")
    if failures:
        print(
            f"{failures} commit message(s) break the Conventional Commits format", file=sys.stderr
        )
        return 1
    print("commit messages: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
