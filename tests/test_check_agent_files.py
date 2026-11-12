"""Agent instruction files: no CLAUDE.md, no symlinks, and no machine-specific skill paths."""

from __future__ import annotations

from pathlib import Path

import check_agent_files
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL = "---\nname: demo\n---\n\n# Demo\n\n{line}\n"


def build(root: Path, skill_line: str = "Run scripts/check.sh.") -> Path:
    (root / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
    skill = root / ".claude" / "skills" / "demo" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(SKILL.format(line=skill_line), encoding="utf-8")
    return root


def test_the_repository_itself_is_clean() -> None:
    assert check_agent_files.check(REPO_ROOT) == []


def test_a_clean_tree_passes(tmp_path: Path) -> None:
    assert check_agent_files.check(build(tmp_path)) == []


@pytest.mark.parametrize(
    "relative",
    ["CLAUDE.md", ".claude/CLAUDE.md", "docs/CLAUDE.md", "claude.md", "a/b/c/CLAUDE.md"],
)
def test_a_claude_md_anywhere_is_rejected(tmp_path: Path, relative: str) -> None:
    root = build(tmp_path)
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x\n", encoding="utf-8")
    problems = check_agent_files.check(root)
    assert any(
        "CLAUDE.md" in problem and relative.lower() in problem.lower() for problem in problems
    )


def test_a_symlinked_agents_md_is_rejected(tmp_path: Path) -> None:
    root = build(tmp_path)
    (root / "AGENTS.md").unlink()
    (root / "README.md").write_text("x\n", encoding="utf-8")
    (root / "AGENTS.md").symlink_to("README.md")
    assert any(
        "AGENTS.md" in problem and "symlink" in problem for problem in check_agent_files.check(root)
    )


def test_a_dangling_agents_md_symlink_is_rejected(tmp_path: Path) -> None:
    root = build(tmp_path)
    (root / "AGENTS.md").unlink()
    (root / "AGENTS.md").symlink_to("missing.md")
    assert any("symlink" in problem for problem in check_agent_files.check(root))


@pytest.mark.parametrize("kind", ["skill-file", "skill-directory", "claude-entry", "dangling"])
def test_a_symlink_under_claude_is_rejected(tmp_path: Path, kind: str) -> None:
    root = build(tmp_path)
    skills = root / ".claude" / "skills"
    other = root / "elsewhere"
    other.mkdir()
    (other / "SKILL.md").write_text(SKILL.format(line="ok"), encoding="utf-8")
    if kind == "skill-file":
        (skills / "demo" / "SKILL.md").unlink()
        (skills / "demo" / "SKILL.md").symlink_to(other / "SKILL.md")
    elif kind == "skill-directory":
        (skills / "linked").symlink_to(other, target_is_directory=True)
    elif kind == "claude-entry":
        (root / ".claude" / "settings.json").symlink_to(other / "SKILL.md")
    else:
        (root / ".claude" / "gone").symlink_to("nowhere")
    assert any(
        ".claude" in problem and "symlink" in problem for problem in check_agent_files.check(root)
    )


@pytest.mark.parametrize(
    "line",
    [
        "Edit /Users/someone/project/start.py.",
        "Open `/etc/hosts`.",
        "Run /bin/bash start.sh.",
        "(see /opt/homebrew/bin/brew)",
        'cd "/tmp/work"',
        "Read ../other/README.md first.",
        "Read the file at ../../x.",
        "Path=/srv/data",
    ],
)
def test_an_absolute_or_parent_path_in_a_skill_is_rejected(tmp_path: Path, line: str) -> None:
    root = build(tmp_path, line)
    problems = check_agent_files.check(root)
    assert any("SKILL.md" in problem and "path" in problem for problem in problems), problems


@pytest.mark.parametrize(
    "line",
    [
        "Run ${CLAUDE_PROJECT_DIR}/scripts/check.sh.",
        "Bash(${CLAUDE_PROJECT_DIR}/scripts/check.sh *)",
        "See https://example.com/a/b and http://example.com/c.",
        "Run scripts/check.sh or tests/test_x.py.",
        "Choose read/write, and/or both.",
        "Link [docs](./README.md) and [more](docs/x.md#anchor).",
        "A date 2026/11/12 and a fraction 1/2.",
    ],
)
def test_project_relative_references_in_a_skill_pass(tmp_path: Path, line: str) -> None:
    assert check_agent_files.check(build(tmp_path, line)) == []


def test_every_problem_is_reported_in_one_run(tmp_path: Path) -> None:
    root = build(tmp_path, "Edit /Users/x/y and ../z")
    (root / "CLAUDE.md").write_text("x\n", encoding="utf-8")
    (root / ".claude" / "link").symlink_to("AGENTS.md")
    assert len(check_agent_files.check(root)) >= 4


def test_main_reports_problems_and_returns_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = build(tmp_path)
    (root / "CLAUDE.md").write_text("x\n", encoding="utf-8")
    assert check_agent_files.main(["--root", str(root)]) == 1
    assert "CLAUDE.md" in capsys.readouterr().err
    (root / "CLAUDE.md").unlink()
    assert check_agent_files.main(["--root", str(root)]) == 0
