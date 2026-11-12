"""Human steps H1, H3, and H4: skip when satisfied, otherwise prompt, wait, and re-check."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from starter_support import (
    PERSONAL_ACCOUNT,
    PERSONAL_EMAIL,
    WORK_ACCOUNT,
    Machine,
    Session,
    build_machine,
    make_session,
)

import start_base as base
import start_identity as identity


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Machine:
    return build_machine(tmp_path, monkeypatch)


def context(machine: Machine, session: Session) -> base.Context:
    return base.Context(
        home=machine.home,
        personal=PERSONAL_EMAIL,
        console=session.console,
        socket_ready=machine.socket_ready,
    )


def open_calls(machine: Machine) -> list[list[str]]:
    return machine.calls("open")


def test_h1_is_skipped_silently_when_the_personal_account_is_listed(machine: Machine) -> None:
    machine.sign_in(PERSONAL_ACCOUNT)
    session = make_session()
    ctx = context(machine, session)
    identity.sign_in(ctx)
    assert session.console.shown == []
    assert open_calls(machine) == []
    assert ctx.account_id == PERSONAL_ACCOUNT["account_uuid"]


def test_h1_prompts_then_passes_once_the_account_is_listed(machine: Machine) -> None:
    session = make_session(lambda: machine.sign_in(PERSONAL_ACCOUNT))
    identity.sign_in(context(machine, session))
    assert session.console.shown == ["h1.prompt"]
    assert open_calls(machine) == [["-a", "1Password"]]
    assert session.person.enters == 1
    assert PERSONAL_EMAIL in session.lines[0]


def test_h1_names_the_failure_and_prompts_again(machine: Machine) -> None:
    session = make_session(lambda: None, lambda: machine.sign_in(PERSONAL_ACCOUNT))
    identity.sign_in(context(machine, session))
    assert session.console.shown == ["h1.prompt", "step.failed", "h1.prompt"]
    assert session.text("h1.absent", account=PERSONAL_EMAIL) in session.lines[1]
    assert session.person.enters == 2


def test_h1_ignores_the_work_account(machine: Machine) -> None:
    machine.sign_in(WORK_ACCOUNT)
    session = make_session(lambda: machine.sign_in(WORK_ACCOUNT, PERSONAL_ACCOUNT))
    identity.sign_in(context(machine, session))
    assert session.console.shown == ["h1.prompt"]


def test_h1_matches_the_account_by_sign_in_address_as_well(machine: Machine) -> None:
    machine.sign_in(PERSONAL_ACCOUNT)
    session = make_session()
    ctx = context(machine, session)
    ctx.personal = "my.example.1password.com"
    identity.sign_in(ctx)
    assert session.console.shown == []


def test_h1_reports_input_that_ends_instead_of_looping(machine: Machine) -> None:
    session = make_session()
    with pytest.raises(base.InputEnded):
        identity.sign_in(context(machine, session))


def prepared(machine: Machine) -> None:
    machine.sign_in(PERSONAL_ACCOUNT)


def test_h3_is_skipped_silently_when_the_integrations_work(machine: Machine) -> None:
    prepared(machine)
    machine.integrations(on=True)
    session = make_session()
    ctx = context(machine, session)
    identity.sign_in(ctx)
    identity.enable_integrations(ctx)
    assert session.console.shown == []
    assert open_calls(machine) == []


def test_h3_opens_the_settings_pane_then_passes(machine: Machine) -> None:
    prepared(machine)
    session = make_session(lambda: machine.integrations(on=True))
    ctx = context(machine, session)
    identity.sign_in(ctx)
    identity.enable_integrations(ctx)
    assert session.console.shown == ["h3.prompt"]
    assert open_calls(machine) == [[identity.SETTINGS_URL]]


def test_h3_checks_the_agent_socket_as_well_as_the_vault_listing(machine: Machine) -> None:
    prepared(machine)
    machine.update(lambda world: world.update(integration=True, agent=False))
    session = make_session(lambda: machine.integrations(on=True))
    ctx = context(machine, session)
    identity.sign_in(ctx)
    identity.enable_integrations(ctx)
    assert session.console.shown == ["h3.prompt"]


def test_h3_shows_the_cause_and_prompts_again_when_still_failing(machine: Machine) -> None:
    prepared(machine)
    session = make_session(lambda: None, lambda: machine.integrations(on=True))
    ctx = context(machine, session)
    identity.sign_in(ctx)
    identity.enable_integrations(ctx)
    assert session.console.shown == ["h3.prompt", "step.failed", "h3.prompt"]
    assert "could not list vaults" in session.lines[1]


def test_h3_lists_vaults_with_stdin_closed_and_an_explicit_account(machine: Machine) -> None:
    prepared(machine)
    machine.integrations(on=True)
    ctx = context(machine, make_session())
    identity.sign_in(ctx)
    identity.enable_integrations(ctx)
    vault_rows = [row for row in machine.call_log() if row[:3] == ["op", "vault", "list"]]
    assert vault_rows
    assert all(row[-1]["stdin_null"] for row in vault_rows)
    assert all(PERSONAL_ACCOUNT["account_uuid"] in row for row in vault_rows)


def keyed(machine: Machine, session: Session) -> base.Context:
    """A context with the personal account resolved and the key written to the home."""
    prepared(machine)
    machine.integrations(on=True)
    ctx = context(machine, session)
    identity.sign_in(ctx)
    identity.create_key(ctx)
    return ctx


def test_h4_is_skipped_silently_when_the_key_already_authenticates(machine: Machine) -> None:
    session = make_session()
    ctx = keyed(machine, session)
    key = ctx.key_file.read_text(encoding="utf-8").split()[1]
    machine.update(lambda world: world["gh"]["keys"].append(key))
    session.console.shown.clear()
    identity.register_key(ctx)
    assert session.console.shown == []
    assert machine.calls("gh") == []


def test_h4_prompts_signs_in_registers_then_drops_the_scope(machine: Machine) -> None:
    session = make_session()
    ctx = keyed(machine, session)
    identity.register_key(ctx)
    gh_calls = machine.calls("gh")
    assert session.console.shown == ["key.created", "h4.prompt", "register.registered"]
    assert [
        "auth",
        "login",
        "--hostname",
        "github.com",
        "--git-protocol",
        "ssh",
        "--web",
        "--skip-ssh-key",
        "--scopes",
        "admin:public_key",
    ] in gh_calls
    adds = [call for call in gh_calls if call[:2] == ["ssh-key", "add"]]
    assert len(adds) == 1
    assert adds[0][2:5] == [str(ctx.key_file), "--type", "authentication"]
    assert [
        "auth",
        "refresh",
        "--hostname",
        "github.com",
        "--remove-scopes",
        "admin:public_key",
    ] in (gh_calls)
    assert machine.world["gh"]["scopes"] == []


def test_h4_refreshes_a_token_that_lacks_only_the_scope(machine: Machine) -> None:
    session = make_session()
    ctx = keyed(machine, session)
    machine.update(lambda world: world["gh"].update(logged_in=True, scopes=["repo"]))
    identity.register_key(ctx)
    gh_calls = machine.calls("gh")
    assert ["auth", "refresh", "--hostname", "github.com", "--scopes", "admin:public_key"] in (
        gh_calls
    )
    assert not any(call[:2] == ["auth", "login"] for call in gh_calls)


def test_h4_names_the_failure_and_asks_for_enter_before_retrying(machine: Machine) -> None:
    session = make_session(lambda: None)
    ctx = keyed(machine, session)
    machine.update(lambda world: world["gh"].update(login_failures=1))
    identity.register_key(ctx)
    assert session.console.shown == [
        "key.created",
        "h4.prompt",
        "step.failed",
        "step.retry",
        "h4.prompt",
        "register.registered",
    ]
    assert session.person.enters == 1


def test_an_existing_registration_is_not_added_again(machine: Machine) -> None:
    session = make_session()
    ctx = keyed(machine, session)
    key = ctx.key_file.read_text(encoding="utf-8").split()[1]

    def registered_but_not_offered(world: dict[str, Any]) -> None:
        world["gh"]["keys"].append(key)
        world["agent_offers"] = False

    machine.update(registered_but_not_offered)
    with pytest.raises(base.StageError, match="Lock and unlock 1Password"):
        identity.register_key(ctx)
    assert not any(call[:2] == ["ssh-key", "add"] for call in machine.calls("gh"))


def test_a_scope_that_cannot_be_dropped_is_reported_but_not_fatal(machine: Machine) -> None:
    session = make_session()
    ctx = keyed(machine, session)
    machine.update(lambda world: world["gh"].update(remove_scope_fails=True))
    identity.register_key(ctx)
    assert "register.scope-kept" in session.console.shown


def test_a_rejected_key_names_the_agent_remedy(machine: Machine) -> None:
    session = make_session()
    ctx = keyed(machine, session)
    machine.update(lambda world: world.update(agent_offers=False))
    with pytest.raises(base.StageError) as raised:
        identity.register_key(ctx)
    assert "Permission denied" in str(raised.value)
