"""Command line: argument parsing and dispatch."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from prove_engine.engine import Engine
from prove_engine.logic import parse_share
from prove_engine.model import ProofError
from prove_engine.pins import DEFAULT_COMMAND, RUN_PREFIX
from prove_engine.process import host_info


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="prove.py",
        description="Run commands in throwaway macOS virtual machines with a pinned Tart.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=Path.home() / ".cache" / "workstation-bootstrap",
        help="Folder for Tart, images, and logs (default: ~/.cache/workstation-bootstrap).",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("tart", help="Download, verify, and unpack the pinned Tart; print its path.")
    base = sub.add_parser("base", help="Pull the pinned image and clone the never-booted base.")
    base.add_argument("--target-major", type=int, help="macOS major version of the guest.")
    run = sub.add_parser("run", help="Boot a fresh clone, run a command, delete the clone.")
    run.add_argument("scenario", help="Scenario name: lowercase letters, digits, hyphens.")
    run.add_argument("--target-major", type=int, help="macOS major version of the guest.")
    what = run.add_mutually_exclusive_group()
    what.add_argument(
        "--command", dest="shell_command", default=DEFAULT_COMMAND, help="Shell command to run."
    )
    what.add_argument("--script", type=Path, help="Local script file to run with bash.")
    run.add_argument("--share", action="append", default=[], metavar="NAME=PATH[:ro]")
    run.add_argument("--timeout", type=float, default=600, help="Command timeout in seconds.")
    run.add_argument("--out", type=Path, help="Write env.json, timings.json, and output here.")
    run.add_argument("--keep-vm", action="store_true", help="Leave the clone running.")
    sub.add_parser("list", help="List run clones that still exist.")
    clean = sub.add_parser("cleanup", help="Delete leftover run clones.")
    clean.add_argument("--purge", action="store_true", help="Also delete the cache and images.")
    return parser.parse_args(argv)


def dispatch(args: argparse.Namespace, engine: Engine) -> int:
    if args.action == "tart":
        print(engine.ensure_tart())
    elif args.action == "base":
        base = engine.base(args.target_major)
        print(json.dumps({"name": base.name, **asdict(base.image)}, indent=2))
    elif args.action == "list":
        engine.ensure_tart()
        print("\n".join(n for n in engine.local_vms() if n.startswith(RUN_PREFIX)))
    elif args.action == "cleanup":
        print(json.dumps({"reaped": engine.cleanup(purge=args.purge), "purged": args.purge}))
    else:
        script = (
            args.script.read_text(encoding="utf-8") if args.script else args.shell_command + "\n"
        )
        outcome = engine.run(
            args.scenario,
            script=script,
            timeout=args.timeout,
            shares=[parse_share(spec) for spec in args.share],
            target_major=args.target_major,
            out_dir=args.out,
            keep_vm=args.keep_vm,
        )
        sys.stdout.write(outcome.command.stdout)
        sys.stderr.write(outcome.command.stderr)
        print(json.dumps({"env": outcome.env, "timings": outcome.timings}, indent=2))
        return 0 if outcome.command.exit_code == 0 else 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    engine = Engine(args.cache_root, host=host_info(), env=dict(os.environ))
    try:
        return dispatch(args, engine)
    except ProofError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
