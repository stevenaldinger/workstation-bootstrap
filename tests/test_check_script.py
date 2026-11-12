"""scripts/check.sh: target dispatch and the report-every-failure behavior of `all`."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECK_SH = REPO_ROOT / "scripts" / "check.sh"

# Stands in for shellcheck, uv, and gitleaks. It logs its command line and fails when any
# comma-separated entry of $FAKE_FAIL appears in it, so one stub can fail a single command of
# a multi-command check.
STUB = """#!/bin/sh
echo "$(basename "$0") $*" >> "$FAKE_LOG"
args="$(basename "$0") $*"
IFS=,
for word in $FAKE_FAIL; do
  case "$args" in *"$word"*) exit 1 ;; esac
done
exit 0
"""


class Sandbox:
    """A throwaway Git repository holding a copy of check.sh and stub tools."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.log = root / "calls.log"
        scripts = root / "scripts"
        scripts.mkdir(parents=True)
        shutil.copy2(CHECK_SH, scripts / "check.sh")
        (root / "tool.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        bin_dir = root / "bin"
        bin_dir.mkdir()
        for tool in ("shellcheck", "uv", "gitleaks"):
            stub = bin_dir / tool
            stub.write_text(STUB, encoding="utf-8")
            stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com"]
        subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
        subprocess.run([*git, "add", "-A"], check=True)
        subprocess.run([*git, "commit", "-q", "-m", "chore(test): seed"], check=True)

    def run(self, *args: str, fail: str = "") -> subprocess.CompletedProcess[str]:
        env = {
            "PATH": os.pathsep.join([str(self.root / "bin"), "/usr/bin", "/bin"]),
            "FAKE_LOG": str(self.log),
            "FAKE_FAIL": fail,
            "HOME": str(self.root),
        }
        if "CHECK_BASE_SHA" in os.environ:
            env["CHECK_BASE_SHA"] = os.environ["CHECK_BASE_SHA"]
        return subprocess.run(
            [str(self.root / "scripts" / "check.sh"), *args],
            capture_output=True,
            text=True,
            check=False,
            env=env,
            cwd=self.root,
        )

    def commands(self) -> str:
        return self.log.read_text(encoding="utf-8") if self.log.exists() else ""


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    return Sandbox(tmp_path / "repo")


def test_all_passes_when_every_check_passes(sandbox: Sandbox) -> None:
    result = sandbox.run()
    assert result.returncode == 0, result.stderr
    assert "All checks passed" in result.stdout


@pytest.mark.parametrize(
    ("fail", "failed_checks"),
    [
        ("shellcheck", "shellcheck"),
        ("ruff check", "python"),
        ("mypy", "python"),
        ("pytest -q", "tests"),
        ("gitleaks", "secrets"),
        ("check_commits", "commits"),
        ("shellcheck,gitleaks", "shellcheck secrets"),
        ("ruff format,pytest -q", "python tests"),
    ],
)
def test_all_runs_every_check_and_names_each_failure(
    sandbox: Sandbox, fail: str, failed_checks: str
) -> None:
    result = sandbox.run(fail=fail)
    assert result.returncode == 1
    assert f"FAILED: {failed_checks}" in result.stderr
    calls = sandbox.commands()
    tools = (
        "shellcheck",
        "ruff check",
        "ruff format",
        "mypy",
        "pytest",
        "gitleaks",
        "check_commits",
    )
    for tool in tools:
        assert tool in calls, f"{tool} did not run after an earlier failure"


def test_the_python_check_runs_all_three_commands_after_the_first_fails(
    sandbox: Sandbox,
) -> None:
    result = sandbox.run("python", fail="ruff check")
    assert result.returncode == 1
    calls = sandbox.commands()
    assert "ruff format --check" in calls
    assert "mypy" in calls


def test_the_commits_check_receives_the_base_sha_from_the_environment(
    sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CHECK_BASE_SHA", "abc123")
    assert sandbox.run("commits").returncode == 0
    assert "check_commits.py --base abc123" in sandbox.commands()


def test_the_commits_check_runs_without_a_base_when_none_is_given(
    sandbox: Sandbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CHECK_BASE_SHA", raising=False)
    assert sandbox.run("commits").returncode == 0
    assert "--base" not in sandbox.commands()


def test_a_single_target_runs_only_that_check(sandbox: Sandbox) -> None:
    result = sandbox.run("shellcheck")
    assert result.returncode == 0
    assert sandbox.commands().startswith("shellcheck ")
    assert "gitleaks" not in sandbox.commands()


@pytest.mark.parametrize("target", ["nonsense", "--bogus"])
def test_an_unknown_target_is_a_usage_error(sandbox: Sandbox, target: str) -> None:
    result = sandbox.run(target)
    assert result.returncode == 2
    assert "usage:" in result.stderr
    assert sandbox.commands() == ""


def test_help_lists_the_targets(sandbox: Sandbox) -> None:
    result = sandbox.run("-h")
    assert result.returncode == 0
    assert "shellcheck" in result.stdout
    assert sandbox.commands() == ""
