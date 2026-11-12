"""Fake `op`, `gh`, `ssh`, `open`, `mise`, `sudo`, and `curl` for tests of the starter.

Each wrapper script runs `python fake_tools.py <tool> <arguments>`. A fake appends its argument
vector to `$FAKE_DIR/calls.jsonl` and reads and writes its machine state in
`$FAKE_DIR/world.json`, so the starter's real command lines run against a model of
1Password, GitHub, and mise instead of the machine's own tools.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

World = dict[str, Any]

FAKE_DIR = Path(os.environ.get("FAKE_DIR", "."))
WORLD_FILE = FAKE_DIR / "world.json"
CALLS_FILE = FAKE_DIR / "calls.jsonl"
GREETING = "Hi stevenaldinger! You've successfully authenticated, but GitHub gives no shell."

DEFAULT_WORLD: World = {
    "app_accounts": [],
    "integration": False,
    "item": None,
    "read_mode": "read",
    "key_counter": 0,
    "gh": {
        "logged_in": False,
        "scopes": [],
        "keys": [],
        "login_failures": 0,
        "remove_scope_fails": False,
    },
    "agent": False,
    "agent_offers": True,
    "stage1_installed": False,
    "mise_apply_fails": False,
}


def stdin_is_null() -> bool:
    """Whether standard input is the null device, as it is when the caller closes stdin."""
    try:
        return os.fstat(0).st_rdev == os.stat(os.devnull).st_rdev
    except OSError:
        return False


def load() -> World:
    return json.loads(WORLD_FILE.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def save(world: World) -> None:
    WORLD_FILE.write_text(json.dumps(world), encoding="utf-8")


def option(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1] if flag in argv else ""


def fail(message: str, code: int = 1) -> int:
    print(message, file=sys.stderr)
    return code


def matching_account(world: World, wanted: str) -> bool:
    return any(
        wanted in (a["account_uuid"], a["url"], a["shorthand"]) for a in world["app_accounts"]
    )


def op(argv: list[str], world: World) -> int:
    if argv[:2] == ["account", "list"]:
        print(json.dumps(world["app_accounts"]))
        return 0
    account = option(argv, "--account")
    if argv[:2] == ["vault", "list"]:
        if not matching_account(world, account):
            return fail(f'[ERROR] found no accounts for filter "{account}"')
        if not world["integration"]:
            return fail("[ERROR] You are not currently signed in.")
        print(json.dumps([{"name": "Private"}]))
        return 0
    item = world["item"]
    if argv[:2] == ["item", "get"]:
        if item is None:
            return fail('[ERROR] "GitHub SSH (workstation)" isn\'t an item')
        field = {"id": "public_key", "label": "public key", "value": item}
        print(json.dumps({"title": argv[2], "fields": [field]}))
        return 0
    if argv[:2] == ["item", "create"]:
        world["key_counter"] += 1
        world["item"] = f"ssh-ed25519 AAAAC3Fake{world['key_counter']:04d} generated"
        save(world)
        print(json.dumps({"title": option(argv, "--title")}))
        return 0
    if argv[0] == "read":
        if item is None or world["read_mode"] != "read":
            return fail("[ERROR] could not read secret: field not found")
        print(item)
        return 0
    return fail(f"fake op: unsupported {argv}", 64)


def gh_auth(argv: list[str], world: World) -> int:
    state = world["gh"]
    if argv[0] == "status":
        if not state["logged_in"]:
            return fail("You are not logged into any GitHub hosts.")
        scopes = ", ".join(f"'{scope}'" for scope in state["scopes"])
        print(f"github.com\n  - Token scopes: {scopes}")
        return 0
    if argv[0] in ("login", "refresh") and "--remove-scopes" in argv:
        if state["remove_scope_fails"]:
            return fail("refresh failed")
        state["scopes"] = [s for s in state["scopes"] if s != option(argv, "--remove-scopes")]
    elif argv[0] in ("login", "refresh"):
        if state["login_failures"] > 0:
            state["login_failures"] -= 1
            save(world)
            return fail("the one-time code was not approved")
        state["logged_in"] = True
        state["scopes"] = sorted({*state["scopes"], option(argv, "--scopes")})
    save(world)
    return 0


def gh(argv: list[str], world: World) -> int:
    state = world["gh"]
    if argv[0] == "auth":
        return gh_auth(argv[1:], world)
    if argv[:2] == ["ssh-key", "list"]:
        for key in state["keys"]:
            print(f"workstation\t{key}\tauthentication\t2026-11-11")
        return 0
    if argv[:2] == ["ssh-key", "add"]:
        if "admin:public_key" not in state["scopes"]:
            return fail("HTTP 404: Not Found (needs admin:public_key)")
        body = Path(argv[2]).read_text(encoding="utf-8").split()[1]
        state["keys"].append(body)
        save(world)
        return 0
    return fail(f"fake gh: unsupported {argv}", 64)


def ssh(argv: list[str], world: World) -> int:
    pub = option(argv, "-i")
    body = Path(pub).read_text(encoding="utf-8").split()[1] if pub and Path(pub).is_file() else ""
    if body and body in world["gh"]["keys"] and world["agent_offers"]:
        print(GREETING, file=sys.stderr)
        return 1
    return fail("git@github.com: Permission denied (publickey).", 255)


def mise(argv: list[str], world: World) -> int:
    if argv[:3] == ["bootstrap", "packages", "status"]:
        return 0 if world["stage1_installed"] else 1
    if argv[:3] == ["bootstrap", "packages", "apply"]:
        if world["mise_apply_fails"]:
            return fail("brew-cask:1password refused by policy")
        world["stage1_installed"] = True
        save(world)
        return 0
    return 0


def curl(argv: list[str], _world: World) -> int:
    """Serve the file that `downloads.json` maps the URL to; any other URL is a 404."""
    downloads = json.loads((FAKE_DIR / "downloads.json").read_text(encoding="utf-8"))
    source = downloads.get(argv[-1])
    if source is None:
        return fail(f"curl: (22) The requested URL returned error: 404 {argv[-1]}", 22)
    Path(option(argv, "-o")).write_bytes(Path(source).read_bytes())
    return 0


def uname(_argv: list[str], _world: World) -> int:
    print(os.environ["FAKE_ARCH"])
    return 0


def sw_vers(_argv: list[str], _world: World) -> int:
    print(os.environ["FAKE_MACOS"])
    return 0


def main(tool: str, argv: list[str]) -> int:
    with CALLS_FILE.open("a", encoding="utf-8") as log:
        log.write(json.dumps([tool, *argv, {"stdin_null": stdin_is_null()}]) + "\n")
    world = load() if WORLD_FILE.exists() else {}
    handlers = {
        "op": op,
        "gh": gh,
        "ssh": ssh,
        "mise": mise,
        "curl": curl,
        "uname": uname,
        "sw_vers": sw_vers,
    }
    if tool in handlers:
        return handlers[tool](argv, world)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2:]))
