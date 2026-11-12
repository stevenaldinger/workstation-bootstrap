"""The engine behind prove.py (import it as `prove_engine`).

- model: typed records and ProofError
- pins: the pinned Tart release, image digests, and limits
- logic: pure selection, validation, parsing, and command construction
- process: command execution and host facts
- toolchain: download and unpack the pinned Tart
- machine: one cloned guest (boot, SSH, copy, snapshot, teardown)
- engine: Tart acquisition, the base image, per-run clones, and evidence
- cli: argument parsing and dispatch
"""

from __future__ import annotations

from prove_engine.engine import Engine, write_evidence
from prove_engine.logic import parse_share, select_image
from prove_engine.machine import Machine
from prove_engine.model import (
    Base,
    CommandResult,
    HostInfo,
    Image,
    ProofError,
    RunResult,
    Share,
    VncAddress,
)
from prove_engine.process import host_info, run_command

__all__ = [
    "Base",
    "CommandResult",
    "Engine",
    "HostInfo",
    "Image",
    "Machine",
    "ProofError",
    "RunResult",
    "Share",
    "VncAddress",
    "host_info",
    "parse_share",
    "run_command",
    "select_image",
    "write_evidence",
]
