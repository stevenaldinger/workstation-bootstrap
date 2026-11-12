"""The command line: the entrypoint script, argument parsing, and early validation."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from support import (
    REPO_ROOT,
)

from prove_engine import (
    pins,
)
from prove_engine.cli import main, parse_args


def test_entrypoint_script_prints_help(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "prove.py"), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "usage: prove.py" in result.stdout


# --- Argument parsing ----------------------------------------------------------------


def test_parse_args_run_defaults() -> None:
    args = parse_args(["run", "first-run"])
    assert args.action == "run"
    assert args.scenario == "first-run"
    assert (
        args.action == "run" and args.script is None and args.shell_command == pins.DEFAULT_COMMAND
    )
    assert args.timeout == 600
    assert args.share == []
    assert not args.keep_vm


def test_parse_args_run_options() -> None:
    args = parse_args(
        [
            "--cache-root", "/c",
            "run", "preview", "--target-major", "27", "--share", "a=/x:ro", "--share", "b=/y",
            "--timeout", "30", "--out", "/o", "--keep-vm", "--script", "/s.sh",
        ]
    )  # fmt: skip
    assert args.cache_root == Path("/c")
    assert (args.target_major, args.share, args.timeout) == (27, ["a=/x:ro", "b=/y"], 30)
    assert args.keep_vm and args.script == Path("/s.sh")


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["run"],
        ["bogus"],
        ["run", "x", "--command", "a", "--script", "b"],
        ["base", "--target-major", "x"],
    ],
)
def test_parse_args_rejects_invalid_invocations(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        parse_args(argv)
    assert exit_info.value.code == 2


def test_main_rejects_a_bad_scenario_before_any_download(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cache = tmp_path / "cache"
    assert main(["--cache-root", str(cache), "run", "Bad Name"]) == 2
    assert "scenario names" in capsys.readouterr().err
    assert not cache.exists()


def test_main_rejects_a_bad_share_before_any_download(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cache = tmp_path / "cache"
    assert main(["--cache-root", str(cache), "run", "ok", "--share", "bad"]) == 2
    assert "share must look like" in capsys.readouterr().err
    assert not cache.exists()
