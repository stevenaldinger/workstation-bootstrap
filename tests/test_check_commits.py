"""Conventional Commit validation: message rules, history ranges, and the commit-msg hook."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import check_commits
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOK = REPO_ROOT / ".githooks" / "commit-msg"
BODY = "The reason this change is made, in a sentence."
VALID = f"feat(start): add the thing\n\n{BODY}\n"


def message(header: str, body: str = BODY) -> str:
    return f"{header}\n\n{body}\n"


@pytest.mark.parametrize(
    "text",
    [
        VALID,
        message("fix(engine): refuse a purge outside the cache root"),
        message("chore(release): pin starter files"),
        message("build(manifest): refresh starter manifest"),
        message("refactor(start-sh)!: drop the brew path override"),
        message("docs(readme): state the trust boundary"),
        message("ci(check): add a macos job"),
        message("test(disk): cover a resumed grow", "A line.\n\nhttps://example.com/" + "a" * 90),
        "Merge branch 'main' into feature\n",
        f"fix(x): y\n\n{BODY}\n# Please enter the commit message\n# lines starting with #\n",
        message("fix(a.b/c-d): scope characters"),
        message("feat(start): " + "x" * 59),
    ],
    ids=lambda text: text.splitlines()[0][:40],
)
def test_valid_messages_pass(text: str) -> None:
    assert check_commits.check_message(text) == []


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("", "message is empty"),
        ("# only a comment\n", "message is empty"),
        (message("Add the thing"), "type(scope): summary"),
        (message("added: the thing"), "unknown type"),
        (message("Feat(start): add the thing"), "type(scope): summary"),
        (message("feat: add the thing"), "scope is required"),
        (message("feat(): add the thing"), "scope is required"),
        (message("feat(Start): add the thing"), "scope is required"),
        (message("feat(start):add the thing"), "type(scope): summary"),
        (message("feat(start): Add the thing"), "start lowercase"),
        (message("feat(start): add the thing."), "end with a period"),
        (message("feat(start): " + "x" * 60), "header is 73 characters"),
        ("feat(start): add the thing\n", "add a body"),
        ("feat(start): add the thing\n\n", "add a body"),
        ("feat(start): add the thing\nbody right below\n", "second line must be blank"),
        (message("feat(start): add", "word " * 20), "wrap body lines at 72"),
        (
            message("feat(start): add", f"{BODY}\n\nCo-Authored-By: Claude <noreply@example.com>"),
            "attribution",
        ),
        (
            message("feat(start): add", f"{BODY}\n\nClaude-Session: https://example.test/s"),
            "attribution",
        ),
        (message("feat(start): add", f"{BODY}\n\nGenerated with Claude Code"), "attribution"),
    ],
)
def test_invalid_messages_name_the_broken_rule(text: str, fragment: str) -> None:
    problems = check_commits.check_message(text)
    assert any(fragment in problem for problem in problems), problems


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def commit(root: Path, subject: str, body: str = BODY) -> str:
    (root / f"{os.urandom(4).hex()}.txt").write_text(subject, encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", f"{subject}\n\n{body}" if body else subject)
    return git(root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    return root


def run(root: Path, *args: str, recorded: Path | None = None) -> int:
    absent = root / "no-recorded-base.txt"
    return check_commits.main(list(args), cwd=root, recorded_base=recorded or absent)


def test_a_clean_history_passes(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    commit(repo, "feat(a): first")
    commit(repo, "fix(a): second")
    assert run(repo) == 0
    assert "ok" in capsys.readouterr().out


def test_without_a_base_every_commit_is_enforced(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    commit(repo, "Add the starter")
    commit(repo, "feat(a): second")
    assert run(repo) == 1
    assert "Add the starter" in capsys.readouterr().err


def test_commits_up_to_the_base_only_warn(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    commit(repo, "Add the starter")
    base = commit(repo, "Refresh the manifest")
    commit(repo, "feat(a): after the base")
    assert run(repo, "--base", base) == 0
    out = capsys.readouterr()
    assert "WARN" in out.out
    assert "predate the base" in out.out
    assert out.err == ""


def test_a_bad_commit_after_the_base_fails(repo: Path, capsys: pytest.CaptureFixture[str]) -> None:
    base = commit(repo, "Add the starter")
    commit(repo, "feat(a): fine")
    commit(repo, "Fix the thing")
    assert run(repo, "--base", base) == 1
    err = capsys.readouterr().err
    assert "Fix the thing" in err
    assert "feat(a): fine" not in err


def test_a_commit_without_a_body_fails_after_the_base(repo: Path) -> None:
    base = commit(repo, "Add the starter")
    commit(repo, "feat(a): fine", body="")
    assert run(repo, "--base", base) == 1


def test_the_zero_sha_of_a_new_branch_falls_back_to_the_recorded_base(
    repo: Path, tmp_path: Path
) -> None:
    old = commit(repo, "Add the starter")
    commit(repo, "feat(a): after")
    recorded = tmp_path / "recorded.txt"
    recorded.write_text(f"# comment\n{old}\n", encoding="utf-8")
    assert run(repo, "--base", "0" * 40, recorded=recorded) == 0
    assert run(repo, "--base", "0" * 40) == 1


def test_an_unknown_base_falls_back_to_the_recorded_base(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old = commit(repo, "Add the starter")
    commit(repo, "feat(a): after")
    recorded = tmp_path / "recorded.txt"
    recorded.write_text(old + "\n", encoding="utf-8")
    assert run(repo, "--base", "1" * 40, recorded=recorded) == 0
    assert "not in this clone" in capsys.readouterr().err


def test_a_recorded_base_missing_from_the_history_is_an_error(repo: Path, tmp_path: Path) -> None:
    commit(repo, "feat(a): only")
    recorded = tmp_path / "recorded.txt"
    recorded.write_text("2" * 40 + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="not in this history"):
        run(repo, recorded=recorded)


def test_a_merge_commit_is_not_judged(repo: Path) -> None:
    commit(repo, "feat(a): first")
    git(repo, "checkout", "-q", "-b", "topic")
    commit(repo, "feat(a): topic work")
    git(repo, "checkout", "-q", "main")
    commit(repo, "feat(a): main work")
    git(repo, "merge", "-q", "--no-ff", "-m", "Merge branch 'topic'", "topic")
    assert run(repo) == 0


def test_the_head_option_limits_the_range(repo: Path) -> None:
    good = commit(repo, "feat(a): good")
    commit(repo, "bad subject")
    assert run(repo, "--head", good) == 0
    assert run(repo) == 1


def test_the_commit_msg_hook_is_executable() -> None:
    assert HOOK.is_file()
    assert os.access(HOOK, os.X_OK)


def test_a_real_commit_is_rejected_or_accepted_by_the_hook(repo: Path) -> None:
    git(repo, "config", "core.hooksPath", str(HOOK.parent))
    (repo / "a.txt").write_text("a\n", encoding="utf-8")
    git(repo, "add", "-A")
    rejected = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@example.com",
            "commit",
            "-q",
            "-m",
            "Add the thing",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert rejected.returncode != 0
    assert "type(scope): summary" in rejected.stderr
    git(repo, "commit", "-q", "-m", f"feat(a): add the thing\n\n{BODY}")
    assert git(repo, "log", "-1", "--format=%s") == "feat(a): add the thing"


def test_message_mode_reports_each_problem_and_the_expected_format(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "COMMIT_EDITMSG"
    path.write_text("Fix.\n", encoding="utf-8")
    assert check_commits.main(["--message-file", str(path)]) == 1
    err = capsys.readouterr().err
    assert "commit message:" in err
    assert "format: type(scope): summary" in err
