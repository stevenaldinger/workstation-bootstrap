"""The starter end to end against fake commands: order, key creation, resume, clone, hand-off."""

from __future__ import annotations

import re
import stat
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from starter_support import (
    PERSONAL_ACCOUNT,
    PERSONAL_EMAIL,
    Machine,
    Session,
    build_machine,
    make_context,
    make_mirror,
    make_session,
    starter_config,
    starter_files,
)

import start
import start_base as base
import start_clone
import start_identity as identity

HEADING_RE = re.compile(r"^\[[0-9]+/[0-9]+\] ")


class Recorder:
    """Runs commands for real and remembers each one, in order, across both seams."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []
        self.environments: list[Mapping[str, str]] = []

    def run(
        self,
        argv: Sequence[str],
        env: Mapping[str, str] | None = None,
        *,
        timeout: float = base.COMMAND_TIMEOUT_SECONDS,
    ) -> base.Completed:
        self.commands.append(list(argv))
        self.environments.append(dict(env or {}))
        return base.run_captured(argv, env, timeout=timeout)

    def stream(self, argv: Sequence[str], env: Mapping[str, str] | None = None) -> int:
        self.commands.append(list(argv))
        self.environments.append(dict(env or {}))
        return base.run_streamed(argv, env)

    def index(self, *prefix: str) -> int:
        for position, command in enumerate(self.commands):
            if command[: len(prefix)] == list(prefix):
                return position
        raise AssertionError(f"no command started with {prefix}")

    def count(self, *prefix: str) -> int:
        return sum(1 for command in self.commands if command[: len(prefix)] == list(prefix))


class Exec:
    """Records the hand-off instead of replacing the test process."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def __call__(self, path: str, argv: Sequence[str]) -> None:
        self.calls.append((path, list(argv)))


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Machine:
    return build_machine(tmp_path, monkeypatch)


@pytest.fixture
def mirror(tmp_path: Path) -> Path:
    return make_mirror(tmp_path)


def satisfied(machine: Machine) -> None:
    machine.sign_in(PERSONAL_ACCOUNT)
    machine.integrations(on=True)


def run_starter(
    machine: Machine,
    session: Session,
    mirror: Path,
    recorder: Recorder,
    handoff: Exec,
    extra: Sequence[str] = (),
) -> int:
    stage1 = machine.home.parent / "stage1.toml"
    stage1.write_text("[bootstrap.packages]\n", encoding="utf-8")
    return start.main(
        ["--personal-account", PERSONAL_EMAIL, "--stage1", str(stage1), *extra],
        home=machine.home,
        console=session.console,
        run=recorder.run,
        stream=recorder.stream,
        socket_ready=machine.socket_ready,
        execv=handoff,
        environ={start.REPO_URL_ENV: str(mirror)},
    )


def test_a_fresh_machine_reaches_the_identity_before_the_clone(
    machine: Machine, mirror: Path
) -> None:
    session = make_session(
        lambda: machine.sign_in(PERSONAL_ACCOUNT),
        lambda: machine.integrations(on=True),
    )
    recorder, handoff = Recorder(), Exec()
    assert run_starter(machine, session, mirror, recorder, handoff) == 0
    handshake = recorder.index("ssh", "-T")
    clone = recorder.index("git", "clone")
    assert recorder.index("mise", "bootstrap", "packages", "apply") < recorder.index("open")
    assert recorder.index("op", "item", "create") < handshake < clone
    assert machine.world["gh"]["keys"], "the key is registered before the clone"
    clone_dir = machine.home / ".config" / "mise"
    assert (clone_dir / "bin" / "ws").is_file()
    assert handoff.calls == [
        (str(clone_dir / "bin" / "ws"), [str(clone_dir / "bin" / "ws"), "bootstrap"])
    ]


def test_stage_one_reads_only_its_own_package_list(machine: Machine, mirror: Path) -> None:
    # A config directory holding the cloned repository's declarations would widen stage 1 to
    # every declared package on a re-run, so the directory it reads is empty.
    session = make_session(
        lambda: machine.sign_in(PERSONAL_ACCOUNT),
        lambda: machine.integrations(on=True),
    )
    recorder = Recorder()
    run_starter(machine, session, mirror, recorder, Exec())
    stage1 = [
        env
        for command, env in zip(recorder.commands, recorder.environments, strict=True)
        if command[:3] == ["mise", "bootstrap", "packages"]
    ]
    assert len(stage1) == 2
    for env in stage1:
        assert env["MISE_GLOBAL_CONFIG_FILE"].endswith("stage1.toml")
        assert env["MISE_CONFIG_DIR"]
    assert not Path(stage1[0]["MISE_CONFIG_DIR"]).exists(), "the empty directory is removed"


def test_every_step_prints_its_position_out_of_the_total(machine: Machine, mirror: Path) -> None:
    satisfied(machine)
    session = make_session()
    run_starter(machine, session, mirror, Recorder(), Exec())
    headings = [line for line in session.lines if HEADING_RE.match(line)]
    assert len(headings) == 8
    assert headings[0].startswith("[1/8] ")
    assert headings[-1].startswith("[8/8] ")


def test_the_clone_uses_the_agent_and_sets_the_hooks_path(machine: Machine, mirror: Path) -> None:
    satisfied(machine)
    run_starter(machine, make_session(), mirror, Recorder(), Exec())
    clone_dir = machine.home / ".config" / "mise"
    hooks = subprocess.run(
        ["git", "-C", str(clone_dir), "config", "--get", "core.hooksPath"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert hooks == ".githooks"


def test_the_clone_environment_pins_the_agent_socket_and_key(machine: Machine) -> None:
    ctx = make_context(machine)
    command = start_clone.clone_environment(ctx)["GIT_SSH_COMMAND"]
    assert "IdentitiesOnly=yes" in command
    assert str(ctx.key_file) in command
    assert "agent.sock" in command


def test_flags_the_starter_does_not_know_are_passed_to_the_walkthrough(
    machine: Machine, mirror: Path
) -> None:
    satisfied(machine)
    handoff = Exec()
    run_starter(machine, make_session(), mirror, Recorder(), handoff, extra=["--verbose"])
    assert handoff.calls[0][1][1:] == ["bootstrap", "--verbose"]


def test_a_rerun_on_a_satisfied_machine_prompts_and_changes_nothing(
    machine: Machine, mirror: Path
) -> None:
    satisfied(machine)
    first = Recorder()
    run_starter(machine, make_session(), mirror, first, Exec())
    assert first.count("op", "item", "create") == 1
    machine.update(lambda world: world.update(stage1_installed=True))
    session, second = make_session(), Recorder()
    assert run_starter(machine, session, mirror, second, Exec()) == 0
    assert session.person.enters == 0
    assert {
        "sign-in.prompt",
        "integrations.prompt",
        "approval.prompt",
        "key.created",
        "register.registered",
    }.isdisjoint(session.console.shown)
    assert second.count("op", "item", "create") == 0
    assert second.count("mise", "bootstrap", "packages", "apply") == 0
    assert second.count("gh") == second.count("gh", "auth", "status")
    assert any("pull" in command for command in second.commands)


def test_an_interrupted_run_resumes_at_the_first_unsatisfied_step(
    machine: Machine, mirror: Path
) -> None:
    session = make_session()
    interrupted = Recorder()
    assert run_starter(machine, session, mirror, interrupted, Exec()) == base.EXIT_INPUT_ENDED
    assert session.console.shown[-2:] == ["sign-in.prompt", "input.ended"]
    assert interrupted.count("mise", "bootstrap", "packages", "apply") == 1
    assert interrupted.count("git") == 0
    satisfied(machine)
    resumed, handoff = Recorder(), Exec()
    assert run_starter(machine, make_session(), mirror, resumed, handoff) == 0
    assert resumed.count("mise", "bootstrap", "packages", "apply") == 0
    assert len(handoff.calls) == 1


def test_a_directory_that_is_not_the_setup_checkout_stops_the_clone(
    machine: Machine, mirror: Path
) -> None:
    satisfied(machine)
    clone_dir = machine.home / ".config" / "mise"
    clone_dir.mkdir(parents=True)
    (clone_dir / "notes.txt").write_text("mine\n", encoding="utf-8")
    handoff = Exec()
    assert run_starter(machine, make_session(), mirror, Recorder(), handoff) == base.EXIT_FAILED
    assert handoff.calls == []
    assert (clone_dir / "notes.txt").read_text(encoding="utf-8") == "mine\n"


def test_a_checkout_of_another_remote_stops_the_clone(
    machine: Machine, mirror: Path, tmp_path: Path
) -> None:
    satisfied(machine)
    clone_dir = machine.home / ".config" / "mise"
    subprocess.run(["git", "clone", "-q", str(mirror), str(clone_dir)], check=True)
    other = make_mirror(tmp_path / "elsewhere")
    handoff = Exec()
    assert run_starter(machine, make_session(), other, Recorder(), handoff) == base.EXIT_FAILED
    assert handoff.calls == []


def test_an_empty_existing_directory_is_cloned_into(machine: Machine, mirror: Path) -> None:
    satisfied(machine)
    (machine.home / ".config" / "mise").mkdir(parents=True)
    handoff = Exec()
    assert run_starter(machine, make_session(), mirror, Recorder(), handoff) == 0
    assert len(handoff.calls) == 1


def test_a_checkout_without_the_walkthrough_stops_the_hand_off(
    machine: Machine, tmp_path: Path
) -> None:
    satisfied(machine)
    bare = tmp_path / "bare"
    bare.mkdir()
    git = ["git", "-C", str(bare), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "init", "-q", "-b", "main"], check=True)
    (bare / "README").write_text("x\n", encoding="utf-8")
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "x"], check=True)
    handoff = Exec()
    assert run_starter(machine, make_session(), bare, Recorder(), handoff) == base.EXIT_FAILED
    assert handoff.calls == []


def test_a_failing_package_install_stops_before_any_identity_step(
    machine: Machine, mirror: Path
) -> None:
    machine.update(lambda world: world.update(mise_apply_fails=True))
    session, recorder = make_session(), Recorder()
    assert run_starter(machine, session, mirror, recorder, Exec()) == base.EXIT_FAILED
    assert recorder.count("op") == 0


def test_the_key_file_is_public_only_and_the_directory_is_private(
    machine: Machine, mirror: Path
) -> None:
    satisfied(machine)
    run_starter(machine, make_session(), mirror, Recorder(), Exec())
    ssh_dir = machine.home / ".ssh"
    assert stat.S_IMODE(ssh_dir.stat().st_mode) == 0o700
    assert [path.name for path in ssh_dir.iterdir() if path.suffix != ".pub"] == []
    public = ssh_dir / starter_config().identity.key_file_name
    assert public.read_text(encoding="utf-8").startswith("ssh-ed25519 ")
    assert stat.S_IMODE(public.stat().st_mode) == 0o644


def test_the_key_item_is_created_once_and_read_from_the_private_vault(machine: Machine) -> None:
    satisfied(machine)
    ctx = make_context(machine)
    identity.sign_in(ctx)
    identity.create_key(ctx)
    identity.create_key(ctx)
    creates = [call for call in machine.calls("op") if call[:2] == ["item", "create"]]
    assert len(creates) == 1
    create = creates[0]
    assert create[create.index("--category") + 1] == "ssh"
    assert create[create.index("--ssh-generate-key") + 1] == "ed25519"
    assert create[create.index("--vault") + 1] == "Private"
    assert create[create.index("--account") + 1] == PERSONAL_ACCOUNT["account_uuid"]


def test_every_item_command_names_the_account_explicitly(machine: Machine) -> None:
    satisfied(machine)
    ctx = make_context(machine)
    identity.sign_in(ctx)
    identity.create_key(ctx)
    item_calls = [call for call in machine.calls("op") if call[0] in ("item", "read", "vault")]
    assert item_calls
    assert all("--account" in call for call in item_calls)


def test_an_existing_key_item_is_used_not_recreated(machine: Machine) -> None:
    satisfied(machine)
    machine.update(lambda world: world.update(item="ssh-ed25519 AAAAexisting comment"))
    ctx = make_context(machine)
    identity.sign_in(ctx)
    identity.create_key(ctx)
    assert not any(call[:2] == ["item", "create"] for call in machine.calls("op"))
    assert ctx.key_file.read_text(encoding="utf-8") == "ssh-ed25519 AAAAexisting comment\n"


def test_a_failed_item_lookup_stops_instead_of_creating_a_second_key(machine: Machine) -> None:
    satisfied(machine)
    machine.update(lambda world: world.update(item_list_error="[ERROR] 1Password is locked"))
    ctx = make_context(machine)
    identity.sign_in(ctx)
    with pytest.raises(base.StageError, match="Could not look for the key.*1Password is locked"):
        identity.create_key(ctx)
    assert not any(call[:2] == ["item", "create"] for call in machine.calls("op"))
    assert not ctx.key_file.exists()


@pytest.mark.parametrize("output", ["not json", '{"title": "x"}', "[1]", "[{}]"])
def test_an_unreadable_item_list_is_a_lookup_error_not_a_missing_key(
    machine: Machine, output: str
) -> None:
    satisfied(machine)
    ctx = make_context(machine)
    identity.sign_in(ctx)
    ctx.run = lambda argv, env=None, **kw: base.Completed(0, output, output)
    with pytest.raises(base.StageError, match="Could not look for the key"):
        identity.key_item_exists(ctx)


def test_an_item_with_a_similar_title_does_not_count_as_the_key(machine: Machine) -> None:
    satisfied(machine)
    ctx = make_context(machine)
    identity.sign_in(ctx)
    listing = '[{"title": "GitHub SSH (workstation) copy"}, {"title": "other"}]'
    ctx.run = lambda argv, env=None, **kw: base.Completed(0, listing, listing)
    assert identity.key_item_exists(ctx) is False


def test_the_public_key_falls_back_to_the_item_field(machine: Machine) -> None:
    satisfied(machine)
    machine.update(lambda world: world.update(item="ssh-ed25519 AAAAfield comment", read_mode="x"))
    ctx = make_context(machine)
    identity.sign_in(ctx)
    identity.create_key(ctx)
    assert ctx.key_file.read_text(encoding="utf-8").startswith("ssh-ed25519 AAAAfield")


def test_an_unreadable_public_key_stops_with_the_cause(machine: Machine) -> None:
    satisfied(machine)
    machine.update(lambda world: world.update(item="not-a-key", read_mode="x"))
    ctx = make_context(machine)
    identity.sign_in(ctx)
    with pytest.raises(base.StageError, match="could not be read"):
        identity.create_key(ctx)


def test_public_key_from_item_ignores_documents_without_the_field() -> None:
    assert identity.public_key_from_item("not json") == ""
    assert identity.public_key_from_item('{"fields": []}') == ""
    assert identity.public_key_from_item("[1]") == ""
    labelled = '{"fields": [{"label": "Public Key", "value": " ssh-ed25519 AAAA "}]}'
    assert identity.public_key_from_item(labelled) == "ssh-ed25519 AAAA"


def test_the_starter_holds_no_email_address_or_account_data() -> None:
    root = Path(__file__).resolve().parents[1]
    emails = re.compile(r"[\w.+-]+@(?!github\.com)[\w-]+\.[a-z]{2,}")
    for name in (*starter_files(), "start.sh"):
        text = (root / name).read_text(encoding="utf-8")
        assert emails.findall(text) == [], name
        assert "1password.com" not in text, name
