"""The ssh options the starter passes: pinned GitHub host keys, strict checking, quoted paths."""

from __future__ import annotations

import shlex
import shutil
import subprocess
from pathlib import Path

import pytest
from starter_support import (
    REPO_ROOT,
    Machine,
    build_machine,
    make_context,
    starter_files,
)

import start_clone
import start_identity as identity
from start_base import Context, StageError

# The SHA-256 fingerprints GitHub publishes for its host keys, from
# https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints
# and the `ssh_key_fingerprints` field of https://api.github.com/meta.
PUBLISHED_FINGERPRINTS = {
    "ED25519": "SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU",
    "ECDSA": "SHA256:p2QAMXNIC1TJYWeIOttrVc98/R1BUFWu3/LiyKgUfQM",
    "RSA": "SHA256:uNiVztksCsDhcc0u9e8BujQXVUpKZIDTMczCvj3tD2s",
}
SYSTEM_PATH = "/usr/bin:/bin"


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Machine:
    return build_machine(tmp_path, monkeypatch)


@pytest.fixture
def spaced_machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Machine:
    """A machine whose home folder has a space in its name, like many users' real ones."""
    parent = tmp_path / "with space"
    parent.mkdir()
    return build_machine(parent, monkeypatch)


def context_with_key(machine: Machine) -> Context:
    """A context whose key file exists, because ssh ignores an `-i` file it cannot find."""
    ctx = make_context(machine)
    ctx.key_file.parent.mkdir(parents=True, exist_ok=True)
    ctx.key_file.write_text("ssh-ed25519 AAAAexample\n", encoding="utf-8")
    return ctx


def real_ssh() -> str:
    found = shutil.which("ssh", path=SYSTEM_PATH)
    assert found, "the system ssh is needed to read back the options"
    return found


def parse_config(output: str) -> dict[str, list[str]]:
    """The lines of `ssh -G`, keyed by option; options such as identityfile repeat."""
    config: dict[str, list[str]] = {}
    for line in output.splitlines():
        key, _, value = line.partition(" ")
        config.setdefault(key, []).append(value)
    return config


def resolved(options: list[str]) -> dict[str, list[str]]:
    """What the system ssh makes of the options: `ssh -G` prints the config it would use."""
    result = subprocess.run(
        [real_ssh(), "-G", *options, "github.com"], capture_output=True, text=True, check=True
    )
    return parse_config(result.stdout)


def test_the_options_parse_under_the_real_ssh_with_strict_pinned_checking(
    machine: Machine,
) -> None:
    ctx = context_with_key(machine)
    config = resolved(identity.ssh_options(ctx))
    assert config["stricthostkeychecking"] == ["true"]
    assert config["userknownhostsfile"] == [str(identity.KNOWN_HOSTS)]
    assert config["globalknownhostsfile"] == ["/dev/null"]
    assert config["updatehostkeys"] == ["false"]
    assert config["identitiesonly"] == ["yes"]
    assert str(ctx.key_file) in config["identityfile"]


def test_a_home_folder_with_a_space_reaches_ssh_as_one_path(spaced_machine: Machine) -> None:
    ctx = context_with_key(spaced_machine)
    assert " " in str(ctx.agent_socket)
    config = resolved(identity.ssh_options(ctx))
    assert config["identityagent"] == [str(ctx.agent_socket)]
    assert str(ctx.key_file) in config["identityfile"]


def test_the_clone_command_survives_the_shell_that_git_runs_it_through(
    spaced_machine: Machine,
) -> None:
    ctx = make_context(spaced_machine)
    command = start_clone.clone_environment(ctx)["GIT_SSH_COMMAND"]
    words = shlex.split(command)
    assert words[0] == "ssh"
    script = f"{real_ssh()} -G {command.removeprefix('ssh ')} github.com"
    result = subprocess.run(["/bin/sh", "-c", script], capture_output=True, text=True, check=True)
    config = parse_config(result.stdout)
    assert config["identityagent"] == [str(ctx.agent_socket)]
    assert config["stricthostkeychecking"] == ["true"]
    assert config["userknownhostsfile"] == [str(identity.KNOWN_HOSTS)]


def test_the_handshake_runs_with_the_same_strict_options(machine: Machine) -> None:
    ctx = make_context(machine)
    identity.handshake(ctx)
    call = next(row for row in machine.calls("ssh") if "-T" in row)
    options = [part for part in call if part not in {"-T", "git@github.com"}]
    config = resolved(options)
    assert config["stricthostkeychecking"] == ["true"]
    assert config["batchmode"] == ["yes"]
    assert config["userknownhostsfile"] == [str(identity.KNOWN_HOSTS)]


def test_a_path_with_a_double_quote_is_refused_instead_of_misparsed() -> None:
    with pytest.raises(StageError, match="double quote"):
        identity.ssh_option("IdentityAgent", '/a"b')


def fingerprints(path: Path) -> dict[str, str]:
    listing = subprocess.run(
        ["ssh-keygen", "-l", "-f", str(path)], capture_output=True, text=True, check=True
    ).stdout
    found = {}
    for line in listing.splitlines():
        fields = line.split()
        found[fields[-1].strip("()")] = fields[1]
    return found


def test_the_pinned_host_keys_are_exactly_the_ones_github_publishes() -> None:
    assert fingerprints(identity.KNOWN_HOSTS) == PUBLISHED_FINGERPRINTS


def test_the_pinned_file_names_only_github_and_is_a_starter_file() -> None:
    lines = [
        line
        for line in identity.KNOWN_HOSTS.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert len(lines) == 3
    assert {line.split()[0] for line in lines} == {"github.com"}
    assert identity.KNOWN_HOSTS.name in starter_files()


def test_ssh_finds_all_three_pinned_keys_for_github() -> None:
    found = subprocess.run(
        ["ssh-keygen", "-F", "github.com", "-f", str(identity.KNOWN_HOSTS)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert {key for key in ("ssh-ed25519", "ecdsa-sha2-nistp256", "ssh-rsa") if key in found} == {
        "ssh-ed25519",
        "ecdsa-sha2-nistp256",
        "ssh-rsa",
    }


def test_no_starter_file_trusts_a_host_key_on_first_use() -> None:
    offenders = []
    for name in (*starter_files(), "start.sh", "README.md"):
        text = (REPO_ROOT / name).read_text(encoding="utf-8")
        if "accept-new" in text or "StrictHostKeyChecking=no" in text:
            offenders.append(name)
    assert offenders == []
