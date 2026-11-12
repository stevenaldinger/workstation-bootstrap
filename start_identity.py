"""The SSH identity steps: sign in, integrations, key creation, and GitHub registration."""

from __future__ import annotations

import json
import re
import socket
from collections.abc import Callable

from start_base import MESSAGES, Context, Probe, StageError, run_human_step, tail

ITEM_TITLE = "GitHub SSH (workstation)"
VAULT = "Private"
PUBLIC_KEY_FIELD_LABEL = "public key"
KEY_PREFIX = "ssh-ed25519 "
SSH_DIR_MODE = 0o700
PUBLIC_KEY_MODE = 0o644
GITHUB_HOST = "github.com"
GITHUB_SSH_TARGET = "git@github.com"
KEY_SCOPE = "admin:public_key"
APP_NAME = "1Password"
SETTINGS_URL = "onepassword://settings/developers"
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
        return Probe(False, MESSAGES["h1.absent"].format(account=ctx.personal))
    ctx.account_id = account["account_uuid"]
    return Probe(True)


def check_integrations(ctx: Context) -> Probe:
    vaults = ctx.run(["op", "vault", "list", "--account", ctx.account_id, "--format", "json"], None)
    if vaults.returncode != 0:
        return Probe(False, MESSAGES["h3.vault-denied"].format(detail=tail(vaults.output)))
    if not ctx.socket_ready(ctx.agent_socket):
        return Probe(False, MESSAGES["h3.socket-missing"].format(path=str(ctx.agent_socket)))
    return Probe(True)


def open_command(ctx: Context, *argv: str) -> Callable[[], None]:
    """An action that runs `open`; its status is ignored because the check decides the outcome."""

    def act() -> None:
        ctx.stream(["open", *argv], None)

    return act


def sign_in(ctx: Context) -> None:
    run_human_step(
        ctx,
        "H1",
        lambda: check_signed_in(ctx),
        open_command(ctx, "-a", APP_NAME),
        "h1.prompt",
        account=ctx.personal,
    )


def enable_integrations(ctx: Context) -> None:
    run_human_step(
        ctx,
        "H3",
        lambda: check_integrations(ctx),
        open_command(ctx, SETTINGS_URL),
        "h3.prompt",
    )


def item_arguments(ctx: Context) -> list[str]:
    return ["--vault", VAULT, "--account", ctx.account_id]


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
    reference = f"op://{VAULT}/{ITEM_TITLE}/{PUBLIC_KEY_FIELD_LABEL}"
    read = ctx.run(["op", "read", reference, "--account", ctx.account_id], None)
    key = read.stdout.strip()
    if read.returncode == 0 and key.startswith(KEY_PREFIX):
        return key
    item = ctx.run(
        ["op", "item", "get", ITEM_TITLE, *item_arguments(ctx), "--format", "json"], None
    )
    key = public_key_from_item(item.stdout)
    if key.startswith(KEY_PREFIX):
        return key
    raise StageError(
        MESSAGES["key.unreadable"].format(item=ITEM_TITLE, detail=tail(read.output or item.output))
    )


def ensure_ssh_dir(ctx: Context) -> None:
    ctx.ssh_dir.mkdir(mode=SSH_DIR_MODE, parents=True, exist_ok=True)
    ctx.ssh_dir.chmod(SSH_DIR_MODE)


def create_key(ctx: Context) -> None:
    """Create the key item and write its public half, each only when missing or different."""
    existing = ctx.run(
        ["op", "item", "get", ITEM_TITLE, *item_arguments(ctx), "--format", "json"], None
    )
    if existing.returncode != 0:
        created = ctx.run(
            [
                "op", "item", "create", "--category", "ssh", "--ssh-generate-key", "ed25519",
                "--title", ITEM_TITLE, *item_arguments(ctx), "--format", "json",
            ],
            None,
        )  # fmt: skip
        if created.returncode != 0:
            raise StageError(
                MESSAGES["key.unreadable"].format(item=ITEM_TITLE, detail=tail(created.output))
            )
        ctx.console.say("key.created", item=ITEM_TITLE, vault=VAULT)
    ensure_ssh_dir(ctx)
    content = read_public_key(ctx) + "\n"
    current = ctx.key_file.read_text(encoding="utf-8") if ctx.key_file.is_file() else None
    if current != content:
        ctx.key_file.write_text(content, encoding="utf-8")
    ctx.key_file.chmod(PUBLIC_KEY_MODE)


def ssh_options(ctx: Context) -> list[str]:
    """Options that make ssh use the 1Password agent and offer only the workstation key."""
    return [
        "-F", "/dev/null",
        "-o", f"IdentityAgent={ctx.agent_socket}",
        "-o", "IdentitiesOnly=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-i", str(ctx.key_file),
    ]  # fmt: skip


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
    return Probe(False, MESSAGES["h4.scope-missing"].format(scope=KEY_SCOPE))


def approve_github(ctx: Context) -> None:
    signed_in, _ = token_scopes(ctx)
    if signed_in:
        command = ["gh", "auth", "refresh", "--hostname", GITHUB_HOST, "--scopes", KEY_SCOPE]
    else:
        command = [
            "gh", "auth", "login", "--hostname", GITHUB_HOST, "--git-protocol", "ssh",
            "--web", "--skip-ssh-key", "--scopes", KEY_SCOPE,
        ]  # fmt: skip
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
        "H4",
        lambda: check_scope(ctx),
        lambda: approve_github(ctx),
        "h4.prompt",
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
