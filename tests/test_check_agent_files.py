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


def default_agent(
    root: Path, setting: str = '{"agent": "maintainer"}', name: str = "maintainer"
) -> None:
    (root / ".claude" / "settings.json").write_text(setting, encoding="utf-8")
    agent = root / ".claude" / "agents" / f"{name}.md"
    agent.parent.mkdir(parents=True, exist_ok=True)
    agent.write_text(
        f"---\nname: {name}\ndescription: Maintains it.\n---\n\nBody.\n", encoding="utf-8"
    )


def test_a_default_agent_with_a_real_matching_file_passes(tmp_path: Path) -> None:
    root = build(tmp_path)
    default_agent(root)
    assert check_agent_files.check(root) == []


def test_a_default_agent_whose_file_is_missing_fails(tmp_path: Path) -> None:
    root = build(tmp_path)
    default_agent(root, '{"agent": "ghost"}')
    problems = check_agent_files.check(root)
    assert any("'ghost'" in problem and "missing" in problem for problem in problems), problems


def test_a_default_agent_file_that_is_a_symlink_fails(tmp_path: Path) -> None:
    root = build(tmp_path)
    default_agent(root)
    agent = root / ".claude" / "agents" / "maintainer.md"
    real = root / "elsewhere.md"
    real.write_text(agent.read_text(encoding="utf-8"), encoding="utf-8")
    agent.unlink()
    agent.symlink_to(real)
    problems = check_agent_files.check(root)
    assert any("maintainer.md" in problem and "symlink" in problem for problem in problems)


def test_a_default_agent_whose_frontmatter_name_differs_fails(tmp_path: Path) -> None:
    root = build(tmp_path)
    default_agent(root, '{"agent": "maintainer"}', name="maintainer")
    agent = root / ".claude" / "agents" / "maintainer.md"
    agent.write_text("---\nname: other\ndescription: x\n---\n", encoding="utf-8")
    assert any("frontmatter 'name'" in p for p in check_agent_files.check(root))


@pytest.mark.parametrize(
    ("setting", "fragment"),
    [
        ("not json", "not valid JSON"),
        ("[1]", "JSON object"),
        ('{"agent": ""}', "name of an agent"),
        ('{"agent": 3}', "name of an agent"),
    ],
)
def test_a_malformed_settings_file_fails(tmp_path: Path, setting: str, fragment: str) -> None:
    root = build(tmp_path)
    (root / ".claude" / "settings.json").write_text(setting, encoding="utf-8")
    assert any(fragment in problem for problem in check_agent_files.check(root))


def test_settings_without_an_agent_key_are_fine(tmp_path: Path) -> None:
    root = build(tmp_path)
    (root / ".claude" / "settings.json").write_text('{"permissions": {}}', encoding="utf-8")
    assert check_agent_files.check(root) == []


def test_an_absolute_path_in_an_agent_file_is_rejected(tmp_path: Path) -> None:
    root = build(tmp_path)
    default_agent(root)
    agent = root / ".claude" / "agents" / "maintainer.md"
    agent.write_text(agent.read_text(encoding="utf-8") + "Edit /Users/x/y.\n", encoding="utf-8")
    assert any(
        "agents/maintainer.md" in p and "absolute path" in p for p in check_agent_files.check(root)
    )
