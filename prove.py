#!/usr/bin/env python3
"""Run commands inside throwaway macOS virtual machines, using a checksum-pinned Tart.

The engine downloads one pinned Tart release into a cache folder (nothing is installed
system-wide), pulls a pinned vanilla macOS image, and keeps one never-booted base copy.
Every run clones that base, boots the clone headless, runs commands over SSH, and deletes
the clone in a `finally` block, so a failed run never leaves a machine behind.

Python API (`import prove_engine`; the test suite drives these):

    engine = Engine(cache_root, host=host_info(), env=dict(os.environ))
    engine.ensure_tart()                      # download, verify, unpack
    base = engine.base(target_major=26)       # pull and clone once
    with engine.machine("first-run", base) as vm:
        vm.start(shares=[Share("repo", Path("/some/dir"), read_only=True)])
        result = vm.run("sw_vers -productVersion && uname -m", timeout=60)
        vm.snapshot("clean"); vm.restore("clean")
    engine.run("first-run", script="echo hi", timeout=600, out_dir=Path("out"))
    engine.cleanup()                          # reap leftovers; purge=True removes the cache

Command line, run through uv:

    uv run prove.py tart
    uv run prove.py base [--target-major 26]
    uv run prove.py run SCENARIO [--command CMD | --script FILE] [--share NAME=PATH[:ro]]
                        [--timeout SECONDS] [--out DIR] [--keep-vm]
    uv run prove.py list
    uv run prove.py cleanup [--purge]

Exit codes: 0 success, 1 the guest command failed or timed out, 2 a hard error.
"""

from __future__ import annotations

from prove_engine.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
