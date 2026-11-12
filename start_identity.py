"""The SSH identity steps: sign in, integrations, key creation, and GitHub registration."""

from __future__ import annotations

import json
import re
import socket
from collections.abc import Callable
from pathlib import Path

from start_base import MESSAGES, Context, Probe, StageError, run_human_step, tail

PUBLIC_KEY_FIELD_LABEL = "public key"
KEY_PREFIX = "ssh-ed25519 "
SSH_DIR_MODE = 0o700
PUBLIC_KEY_MODE = 0o644
GITHUB_HOST = "github.com"
GITHUB_SSH_TARGET = "git@github.com"
KEY_SCOPE = "admin:public_key"
APP_NAME = "1Password"
SETTINGS_URL = "onepassword://settings/developers"
# GitHub's host keys, shipped beside this file and covered by the starter's checksum manifest.
KNOWN_HOSTS = Path(__file__).with_name("github_known_hosts")
GREETING_RE = re.compile(r"Hi (?P<login>[\w-]+)! You've successfully authenticated")


def personal_account(ctx: Context) -> dict[str, str] | None:
    """The 1Password account whose email, sign-in address, shorthand, or ID is the argument."""
    completed = ctx.run(["op", "account", "list", "--format", "json"], None)
    if completed.returncode != 0:
        return None
    try:
        accounts = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    wanted = ctx.personal.strip().lower().removeprefix("https://")
    for account in accounts:
        names = {
            str(account.get(key, "")).lower().removeprefix("https://")
            for key in ("email", "url", "shorthand", "account_uuid")
        }
        if wanted in names:
            return {key: str(value) for key, value in account.items()}
    return None


def check_signed_in(ctx: Context) -> Probe:
    account = personal_account(ctx)
    if account is None:
        return Probe(False, MESSAGES["sign-in.absent"].format(account=ctx.personal))
    ctx.account_id = account["account_uuid"]
    return Probe(True)


def check_integrations(ctx: Context) -> Probe:
    vaults = ctx.run(["op", "vault", "list", "--account", ctx.account_id, "--format", "json"], None)
    if vaults.returncode != 0:
        return Probe(
            False, MESSAGES["integrations.vault-denied"].format(detail=tail(vaults.output))
        )
    if not ctx.socket_ready(ctx.agent_socket):
        return Probe(
            False, MESSAGES["integrations.socket-missing"].format(path=str(ctx.agent_socket))
        )
    return Probe(True)


def open_command(ctx: Context, *argv: str) -> Callable[[], None]:
    """An action that runs `open`; its status is ignored because the check decides the outcome."""

    def act() -> None:
        ctx.stream(["open", *argv], None)

    return act


def sign_in(ctx: Context) -> None:
    run_human_step(
        ctx,
        "sign-in.title",
        lambda: check_signed_in(ctx),
        open_command(ctx, "-a", APP_NAME),
        "sign-in.prompt",
        account=ctx.personal,
    )


def enable_integrations(ctx: Context) -> None:
    run_human_step(
        ctx,
        "integrations.title",
        lambda: check_integrations(ctx),
        open_command(ctx, SETTINGS_URL),
        "integrations.prompt",
    )


def item_arguments(ctx: Context) -> list[str]:
    return ["--vault", ctx.config.identity.vault, "--account", ctx.account_id]


def public_key_from_item(text: str) -> str:
    """The public key field of an `op item get --format json` document, or an empty string."""
    try:
        fields = json.loads(text).get("fields", [])
    except (json.JSONDecodeError, AttributeError):
        return ""
    for entry in fields:
        label = str(entry.get("label", "")).lower()
        if entry.get("id") == "public_key" or label == PUBLIC_KEY_FIELD_LABEL:
            return str(entry.get("value", "")).strip()
    return ""


def read_public_key(ctx: Context) -> str:
    """The key through `op read`, falling back to the item's JSON field."""
    identity = ctx.config.identity
    reference = f"op://{identity.vault}/{identity.item_title}/{PUBLIC_KEY_FIELD_LABEL}"
    read = ctx.run(["op", "read", reference, "--account", ctx.account_id], None)
    key = read.stdout.strip()
    if read.returncode == 0 and key.startswith(KEY_PREFIX):
        return key
    item = ctx.run(
        ["op", "item", "get", identity.item_title, *item_arguments(ctx), "--format", "json"], None
    )
    key = public_key_from_item(item.stdout)
    if key.startswith(KEY_PREFIX):
        return key
    raise StageError(
        MESSAGES["key.unreadable"].format(
            item=identity.item_title, detail=tail(read.output or item.output)
        )
    )


def ensure_ssh_dir(ctx: Context) -> None:
    ctx.ssh_dir.mkdir(mode=SSH_DIR_MODE, parents=True, exist_ok=True)
    ctx.ssh_dir.chmod(SSH_DIR_MODE)


def create_key(ctx: Context) -> None:
    """Create the key item and write its public half, each only when missing or different."""
    identity = ctx.config.identity
    existing = ctx.run(
        ["op", "item", "get", identity.item_title, *item_arguments(ctx), "--format", "json"], None
    )
    if existing.returncode != 0:
        generate = ["--category", "ssh", "--ssh-generate-key", "ed25519"]
        created = ctx.run(
            ["op", "item", "create", *generate, "--title", identity.item_title]
            + [*item_arguments(ctx), "--format", "json"],
            None,
        )
        if created.returncode != 0:
            raise StageError(
                MESSAGES["key.unreadable"].format(
                    item=identity.item_title, detail=tail(created.output)
                )
            )
        ctx.console.say("key.created", item=identity.item_title, vault=identity.vault)
    ensure_ssh_dir(ctx)
    content = read_public_key(ctx) + "\n"
    current = ctx.key_file.read_text(encoding="utf-8") if ctx.key_file.is_file() else None
    if current != content:
        ctx.key_file.write_text(content, encoding="utf-8")
    ctx.key_file.chmod(PUBLIC_KEY_MODE)


def ssh_option(name: str, value: object) -> str:
    """An `-o` value whose argument may hold spaces, quoted the way ssh_config(5) allows."""
    text = str(value)
    if '"' in text:
        raise StageError(f"cannot pass a path containing a double quote to ssh: {text}")
    return f'{name}="{text}"'


def ssh_options(ctx: Context) -> list[str]:
    """ssh options for the 1Password agent, the workstation key, and GitHub's pinned host keys."""
    options = [
        ssh_option("IdentityAgent", ctx.agent_socket),
        "IdentitiesOnly=yes",
        "StrictHostKeyChecking=yes",
        ssh_option("UserKnownHostsFile", KNOWN_HOSTS),
        "GlobalKnownHostsFile=/dev/null",
        # A pinned file must not be rewritten when the server offers its other keys.
        "UpdateHostKeys=no",
    ]
    return ["-F", "/dev/null", *(arg for option in options for arg in ("-o", option))] + [
        "-i",
        str(ctx.key_file),
    ]


def handshake(ctx: Context) -> Probe:
    """Whether GitHub authenticates the workstation key through the agent."""
    command = ["ssh", "-T", *ssh_options(ctx), "-o", "BatchMode=yes", "-o", "ConnectTimeout=15"]
    result = ctx.run([*command, GITHUB_SSH_TARGET], None)
    if GREETING_RE.search(result.output):
        return Probe(True)
    return Probe(False, tail(result.output) or "no output from ssh")


def token_scopes(ctx: Context) -> tuple[bool, bool]:
    """(signed in to GitHub, token holds the key-registration scope)."""
    status = ctx.run(["gh", "auth", "status", "--hostname", GITHUB_HOST], None)
    return status.returncode == 0, KEY_SCOPE in status.output


def check_scope(ctx: Context) -> Probe:
    _, has_scope = token_scopes(ctx)
    if has_scope:
        return Probe(True)
    return Probe(False, MESSAGES["approval.scope-missing"].format(scope=KEY_SCOPE))


def approve_github(ctx: Context) -> None:
    signed_in, _ = token_scopes(ctx)
    if signed_in:
        command = ["gh", "auth", "refresh", "--hostname", GITHUB_HOST, "--scopes", KEY_SCOPE]
    else:
        login = ["--hostname", GITHUB_HOST, "--git-protocol", "ssh", "--web", "--skip-ssh-key"]
        command = ["gh", "auth", "login", *login, "--scopes", KEY_SCOPE]
    ctx.stream(command, None)


def key_is_registered(ctx: Context, key: str) -> bool:
    """Whether GitHub already lists the key (matched by its base64 body)."""
    listing = ctx.run(["gh", "ssh-key", "list"], None)
    parts = key.split()
    return listing.returncode == 0 and len(parts) > 1 and parts[1] in listing.output


def register_key(ctx: Context) -> None:
    """Make GitHub accept the key: sign in, add it, drop the extra scope, and verify."""
    if handshake(ctx).ok:
        return
    run_human_step(
        ctx,
        "register.title",
        lambda: check_scope(ctx),
        lambda: approve_github(ctx),
        "approval.prompt",
        waits=False,
    )
    key = ctx.key_file.read_text(encoding="utf-8").strip()
    if not key_is_registered(ctx, key):
        title = f"workstation ({socket.gethostname().split('.')[0]})"
        added = ctx.run(
            [
                "gh",
                "ssh-key",
                "add",
                str(ctx.key_file),
                "--type",
                "authentication",
                "--title",
                title,
            ],
            None,
        )
        if added.returncode != 0:
            raise StageError(MESSAGES["register.failed"].format(detail=tail(added.output)))
        ctx.console.say("register.registered")
    dropped = ctx.stream(
        ["gh", "auth", "refresh", "--hostname", GITHUB_HOST, "--remove-scopes", KEY_SCOPE], None
    )
    if dropped != 0:
        ctx.console.say("register.scope-kept", scope=KEY_SCOPE, detail=f"exit {dropped}")
    verdict = handshake(ctx)
    if not verdict.ok:
        raise StageError(MESSAGES["register.handshake-failed"].format(detail=verdict.detail))
