"""The pins in start.sh match the files they pin, and scripts/pins.sh keeps them that way."""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from starter_support import starter_files

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "start.sha256"
STARTER_FILES = starter_files()


def pin(text: str, name: str) -> str:
    """A pinned value from start.sh (`NAME="value"`) or start.ps1 (`$Name = 'value'`)."""
    match = re.search(rf"""^\$?{name}(?:=| = )["']([^"']*)["']$""", text, flags=re.M)
    assert match, name
    return match.group(1)


def read(name: str) -> str:
    return (REPO_ROOT / name).read_text(encoding="utf-8")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest_entries(text: str) -> dict[str, str]:
    return {name: digest for digest, name in (line.split() for line in text.splitlines())}


def test_the_manifest_lists_every_starter_file_with_its_current_checksum() -> None:
    entries = manifest_entries((REPO_ROOT / MANIFEST).read_text(encoding="utf-8"))
    assert sorted(entries) == sorted(STARTER_FILES)
    for name, digest in entries.items():
        actual = sha256((REPO_ROOT / name).read_bytes())
        assert digest == actual, f"{name} changed: run scripts/pins.sh manifest"


@pytest.mark.parametrize(
    ("script", "commit_name", "manifest_name"),
    [
        ("start.sh", "FILES_COMMIT", "MANIFEST_SHA256"),
        ("start.ps1", "FilesCommit", "ManifestSha256"),
    ],
)
def test_each_script_pins_the_manifest_and_a_commit_that_holds_it(
    script: str, commit_name: str, manifest_name: str
) -> None:
    text = read(script)
    commit = pin(text, commit_name)
    assert re.fullmatch(r"[0-9a-f]{40}", commit), f"{commit_name} must be a full commit hash"
    pinned = pin(text, manifest_name)
    assert pinned == sha256((REPO_ROOT / MANIFEST).read_bytes()), "run scripts/pins.sh release"
    shown = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "show", f"{commit}:{MANIFEST}"],
        capture_output=True,
        check=True,
    ).stdout
    assert sha256(shown) == pinned


def test_every_file_at_the_pinned_commit_matches_the_manifest() -> None:
    commit = pin((REPO_ROOT / "start.sh").read_text(encoding="utf-8"), "FILES_COMMIT")
    entries = manifest_entries((REPO_ROOT / MANIFEST).read_text(encoding="utf-8"))
    for name, digest in entries.items():
        shown = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show", f"{commit}:{name}"],
            capture_output=True,
            check=True,
        ).stdout
        assert sha256(shown) == digest, name


def test_the_runtime_pins_match_the_tool_versions_in_mise_toml() -> None:
    mise_toml = read("mise.toml")
    for script, names in (
        ("start.sh", {"python": "PYTHON_VERSION", "uv": "UV_VERSION"}),
        ("start.ps1", {"python": "PythonVersion", "uv": "UvVersion"}),
    ):
        for tool, name in names.items():
            declared = re.search(rf'^{tool}\s*=\s*"([^"]+)"', mise_toml, flags=re.M)
            assert declared and declared.group(1) == pin(read(script), name), (script, tool)


def test_both_scripts_pin_the_same_mise_and_gh_versions() -> None:
    sh, ps1 = read("start.sh"), read("start.ps1")
    assert pin(sh, "MISE_VERSION") == pin(ps1, "MiseVersion")
    assert pin(sh, "GH_VERSION") == pin(ps1, "GhVersion")


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def test_the_release_script_pins_the_head_commit_and_the_manifest(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    for name in (*STARTER_FILES, "start.sh", "start.ps1", "scripts/pins.sh"):
        shutil.copy2(REPO_ROOT / name, root / name)
    git(root, "init", "-q", "-b", "main")
    (root / "start.py").write_text("print('changed')\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "files")
    script = root / "scripts" / "pins.sh"

    stale = subprocess.run([str(script), "release"], capture_output=True, text=True, check=False)
    assert stale.returncode == 1
    assert "stale" in stale.stderr or "commit the starter files" in stale.stderr

    subprocess.run([str(script), "manifest"], check=True)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "manifest")
    subprocess.run([str(script), "release"], check=True, capture_output=True)
    released = (root / "start.sh").read_text(encoding="utf-8")
    released_ps1 = (root / "start.ps1").read_text(encoding="utf-8")
    head = git(root, "rev-parse", "HEAD").strip()
    manifest_sha = sha256((root / MANIFEST).read_bytes())
    assert pin(released, "FILES_COMMIT") == head
    assert pin(released, "MANIFEST_SHA256") == manifest_sha
    assert pin(released_ps1, "FilesCommit") == head
    assert pin(released_ps1, "ManifestSha256") == manifest_sha
    assert released_ps1.count("$FilesCommit =") == 1, "release changes only the two pins"
    assert (root / "start.sh").stat().st_mode & 0o111, "release keeps start.sh executable"

    unknown = subprocess.run([str(script), "nonsense"], capture_output=True, text=True, check=False)
    assert unknown.returncode == 2
    assert "usage:" in unknown.stderr
