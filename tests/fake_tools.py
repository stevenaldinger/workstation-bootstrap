"""Fake `op`, `gh`, `ssh`, `open`, `mise`, `sudo`, `curl`, `uname`, and `sw_vers` for starter tests.

Each wrapper script runs `python fake_tools.py <tool> <arguments>`. A fake appends its argument
vector to `$FAKE_DIR/calls.jsonl` and reads and writes its machine state in
`$FAKE_DIR/world.json`, so the starter's real command lines run against a model of
1Password, GitHub, and mise instead of the machine's own tools.

A fake answers only the commands and flags the real tool has and the starter uses. Anything
else exits 64 with `fake <tool>: unsupported ...`, and a flag the real command lacks exits 1 the
way the real one does, so a typo in the starter's command line fails a test instead of passing
against a permissive stand-in. Output formats follow the real producers:

- `op account list --format json` prints url (a sign-in address with no scheme, as in
  https://developer.1password.com/docs/cli/reference/management-commands/account), email,
  user_uuid, and account_uuid.
- `op item create --category ssh --ssh-generate-key ed25519` and the item JSON follow
  https://developer.1password.com/docs/cli/ssh-keys (the public key is the `public_key` field).
- `gh auth status` prints a host heading, then `Logged in to <host> account <login> (<source>)`,
  `Active account`, `Git operations protocol`, `Token`, and `Token scopes` lines
  (cli/cli v2.103.3, pkg/cmd/auth/status/status.go); with no login it prints
  "You are not logged into any GitHub hosts." and exits 1.
- `gh ssh-key list`, when its output is not a terminal, prints tab-separated title, key, added,
  id, and type (cli/cli v2.103.3, pkg/cmd/ssh-key/list/list.go).
- `gh auth login`, `gh auth refresh`, and `gh ssh-key add` accept the flags in
  https://cli.github.com/manual/gh_auth_login, gh_auth_refresh, and gh_ssh-key_add.
- `mise bootstrap packages status|apply` follow https://mise.jdx.dev/cli/bootstrap/packages.html.
- `wsl.exe --status` and `wsl.exe --install --no-distribution` follow
  https://learn.microsoft.com/windows/wsl/basic-commands; installing needs elevation, and an
  exit status of 3010 (ERROR_SUCCESS_REBOOT_REQUIRED) means a restart is required.
- `winget.exe --version` prints the client version, as `winget --version` does
  (https://learn.microsoft.com/windows/package-manager/winget/); no other winget command is
  modeled, because mise's `winget:` installs are faked at the `mise` boundary.
- `shutdown.exe /r /t <seconds> /c <comment>` follows
  https://learn.microsoft.com/windows-server/administration/windows-commands/shutdown; the
  comment is at most 512 characters.
- `sc.exe query|qc <service>` prints the STATE and START_TYPE lines as sc.exe does, and exits
  1060 for a service that is not installed.
- `ssh-add.exe -l` exits 0 with a key line, 1 with "The agent has no identities.", and 2 when
  the agent cannot be reached (ssh-add(1)).
- `powershell.exe -NoProfile -Command <script>` runs only the three scripts the starter sends;
  `Start-Process ... -Verb RunAs` runs its target with elevation, and a declined approval
  fails with "The operation was canceled by the user."
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
import time
from pathlib import Path
from typing import Any

World = dict[str, Any]

FAKE_DIR = Path(os.environ.get("FAKE_DIR", "."))
WORLD_FILE = FAKE_DIR / "world.json"
CALLS_FILE = FAKE_DIR / "calls.jsonl"
GREETING = "Hi stevenaldinger! You've successfully authenticated, but GitHub gives no shell."
EXIT_UNSUPPORTED = 64
EXIT_REBOOT_REQUIRED = 3010
SHUTDOWN_COMMENT_LIMIT = 512
# Set while a command runs as the target of an elevated Start-Process.
ELEVATED = {"on": False}
# Environment variables a call records, so tests can see what a command ran with.
LOGGED_ENV = ("MISE_YES", "PATH", "WSL_UTF8", "PYTHONUTF8")

DEFAULT_WORLD: World = {
    "app_accounts": [],
    "integration": False,
    "item": None,
    "read_mode": "read",
    "key_counter": 0,
    "item_list_error": "",
    "gh": {
        "logged_in": False,
        "scopes": [],
        "keys": [],
        "login_failures": 0,
        "remove_scope_fails": False,
    },
    "agent": False,
    "agent_offers": True,
    "wsl": {"installed": False, "install_exit": 0},
    "vm_platform": "Unknown",
    "uac": "approve",
    "shutdowns": [],
    "agent_service": {"present": True, "running": False, "start_type": "DEMAND_START"},
    "stage1_installed": False,
    "mise_apply_fails": False,
    # The Command Line Tools: installed, the labels `softwareupdate -l` lists while the marker
    # file exists, the exit status of `softwareupdate -i`, and whether a clean install leaves
    # the tools in place.
    "clt": {
        "installed": True,
        "marker": False,
        "labels": [],
        "install_exit": 0,
        "installs": True,
    },
}
CLT_MARKER = "/tmp/.com.apple.dt.CommandLineTools.installondemand.in-progress"
CLT_FOLDER = "/Library/Developer/CommandLineTools"

GH_LOGIN_FLAGS = {
    "--hostname": 1,
    "-h": 1,
    "--git-protocol": 1,
    "-p": 1,
    "--web": 0,
    "-w": 0,
    "--skip-ssh-key": 0,
    "--scopes": 1,
    "-s": 1,
}
GH_REFRESH_FLAGS = {"--hostname": 1, "-h": 1, "--scopes": 1, "-s": 1, "--remove-scopes": 1, "-r": 1}
GH_KEY_ADD_FLAGS = {"--title": 1, "-t": 1, "--type": 1}
OP_ITEM_CREATE_FLAGS = {
    "--category": 1,
    "--ssh-generate-key": 1,
    "--title": 1,
    "--vault": 1,
    "--account": 1,
    "--format": 1,
}


def stdin_is_null() -> bool:
    """Whether standard input is the null device, as it is when the caller closes stdin."""
    if sys.platform == "win32":
        import ctypes
        import msvcrt

        handle = msvcrt.get_osfhandle(0)  # type: ignore[attr-defined,unused-ignore]
        kernel = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]
        is_character_device = kernel.GetFileType(handle) == 2
        return bool(
            is_character_device and not kernel.GetConsoleMode(handle, ctypes.byref(ctypes.c_uint()))
        )
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


def unsupported(tool: str, argv: list[str]) -> int:
    return fail(f"fake {tool}: unsupported {argv}", EXIT_UNSUPPORTED)


def unknown_flag(argv: list[str], allowed: dict[str, int], positionals: int = 0) -> str:
    """The first flag not in `allowed`, or an empty string; values and positionals are skipped."""
    index, seen = 0, 0
    while index < len(argv):
        word = argv[index]
        if word in allowed:
            index += 1 + allowed[word]
        elif word.startswith("-"):
            return word
        else:
            seen += 1
            index += 1
    return "" if seen <= positionals else argv[-1]


def matching_account(world: World, wanted: str) -> bool:
    return any(
        wanted in (a["account_uuid"], a["url"], a.get("shorthand", ""))
        for a in world["app_accounts"]
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
        print(json.dumps([{"id": "vaultid0000000000000000000", "name": "Private"}]))
        return 0
    return op_item(argv, world)


def op_item(argv: list[str], world: World) -> int:
    item = world["item"]
    if argv[:2] == ["item", "list"]:
        if world["item_list_error"]:
            return fail(world["item_list_error"])
        listed = []
        if item is not None:
            listed.append({"id": "itemid00000000000000000000", "title": "GitHub SSH (workstation)"})
        print(json.dumps(listed))
        return 0
    if argv[:2] == ["item", "get"]:
        if item is None:
            return fail('[ERROR] "GitHub SSH (workstation)" isn\'t an item in any vault')
        field = {"id": "public_key", "label": "public key", "value": item}
        print(json.dumps({"title": argv[2], "fields": [field]}))
        return 0
    if argv[:2] == ["item", "create"]:
        bad = unknown_flag(argv[2:], OP_ITEM_CREATE_FLAGS)
        if bad:
            return fail(f'[ERROR] unknown flag: "{bad}"')
        if option(argv, "--category") != "ssh" or option(argv, "--ssh-generate-key") != "ed25519":
            return fail("[ERROR] the fake creates only a generated Ed25519 SSH key item", 64)
        world["key_counter"] += 1
        world["item"] = f"ssh-ed25519 AAAAC3Fake{world['key_counter']:04d} generated"
        save(world)
        print(json.dumps({"id": "itemid00000000000000000000", "title": option(argv, "--title")}))
        return 0
    if argv[0] == "read":
        if item is None or world["read_mode"] != "read":
            return fail("[ERROR] could not read secret: field not found")
        print(item)
        return 0
    return unsupported("op", argv)


def gh_auth(argv: list[str], world: World) -> int:
    state = world["gh"]
    if argv[0] == "status":
        if not state["logged_in"]:
            return fail("You are not logged into any GitHub hosts. To log in, run: gh auth login")
        scopes = ", ".join(f"'{scope}'" for scope in state["scopes"]) or "none"
        print("github.com")
        print("  \u2713 Logged in to github.com account stevenaldinger (keyring)")
        print("  - Active account: true")
        print("  - Git operations protocol: ssh")
        print("  - Token: gho_************************************")
        print(f"  - Token scopes: {scopes}")
        return 0
    if argv[0] in ("login", "refresh"):
        allowed = GH_LOGIN_FLAGS if argv[0] == "login" else GH_REFRESH_FLAGS
        bad = unknown_flag(argv[1:], allowed)
        if bad:
            return fail(f"unknown flag: {bad}")
        return gh_auth_change(argv, world)
    return unsupported("gh", ["auth", *argv])


def gh_auth_change(argv: list[str], world: World) -> int:
    state = world["gh"]
    if "--remove-scopes" in argv:
        if state["remove_scope_fails"]:
            return fail("refresh failed")
        state["scopes"] = [s for s in state["scopes"] if s != option(argv, "--remove-scopes")]
    else:
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
        for number, key in enumerate(state["keys"], start=1):
            print(f"workstation\t{key}\t2026-11-11T00:00:00Z\t{number}\tauthentication")
        return 0
    if argv[:2] == ["ssh-key", "add"]:
        bad = unknown_flag(argv[2:], GH_KEY_ADD_FLAGS, positionals=1)
        if bad:
            return fail(f"unknown flag: {bad}")
        if "admin:public_key" not in state["scopes"]:
            return fail("HTTP 404: Not Found (needs admin:public_key)")
        body = Path(argv[2]).read_text(encoding="utf-8").split()[1]
        state["keys"].append(body)
        save(world)
        return 0
    return unsupported("gh", argv)


def ssh(argv: list[str], world: World) -> int:
    """Only the GitHub handshake (`ssh -T ... git@github.com`) is modeled."""
    if "-T" not in argv or argv[-1] != "git@github.com":
        return unsupported("ssh", argv)
    pub = option(argv, "-i")
    body = Path(pub).read_text(encoding="utf-8").split()[1] if pub and Path(pub).is_file() else ""
    if body and body in world["gh"]["keys"] and world["agent_offers"]:
        print(GREETING, file=sys.stderr)
        return 1
    return fail("git@github.com: Permission denied (publickey).", 255)


def mise(argv: list[str], world: World) -> int:
    if argv[:1] == ["exec"] and os.environ.get("FAKE_EXEC_SECONDS"):
        time.sleep(float(os.environ["FAKE_EXEC_SECONDS"]))
    if argv[:3] == ["bootstrap", "packages", "status"]:
        return 0 if world["stage1_installed"] else 1
    if argv[:3] == ["bootstrap", "packages", "apply"]:
        if world["mise_apply_fails"]:
            return fail("brew-cask:1password refused by policy")
        world["stage1_installed"] = True
        save(world)
        return 0
    if argv[:1] == ["install"] and os.environ.get("FAKE_MISE_INSTALL_FAILS"):
        return int(os.environ["FAKE_MISE_INSTALL_FAILS"])
    if argv[:1] in (["install"], ["exec"]):
        return 0
    return unsupported("mise", argv)


def open_(argv: list[str], _world: World) -> int:
    """macOS `open` with an application (`-a NAME`) or a URL; its effects are not modeled."""
    return 0 if argv and (argv[0] == "-a" or "://" in argv[0]) else unsupported("open", argv)


def sudo(argv: list[str], world: World) -> int:
    """`sudo -v`, `sudo -n true`, and the elevated commands that install the Command Line Tools.

    FAKE_SUDO_REFRESH_FAILS makes the refresh fail. Only the marker file, `softwareupdate -i`,
    and `xcode-select --switch` are modeled; each runs as its own call in the log.
    """
    if argv == ["-v"]:
        return 1 if os.environ.get("FAKE_SUDO_FAILS") else 0
    if argv == ["-n", "true"]:
        return 1 if os.environ.get("FAKE_SUDO_REFRESH_FAILS") else 0
    if argv and argv[0] in ("touch", "rm", "softwareupdate", "xcode-select"):
        return elevated(argv, world)
    return unsupported("sudo", argv)


def elevated(argv: list[str], world: World) -> int:
    """A command run through sudo, logged as its own call."""
    with CALLS_FILE.open("a", encoding="utf-8") as log:
        log.write(json.dumps([*argv, {"elevated": True}]) + "\n")
    clt = world["clt"]
    if argv == ["touch", CLT_MARKER]:
        clt["marker"] = True
    elif argv == ["rm", "-f", CLT_MARKER]:
        clt["marker"] = False
    elif argv[:2] == ["softwareupdate", "-i"] and len(argv) == 3:
        if argv[2] not in clt["labels"]:
            return fail(f"No such update: {argv[2]}")
        if clt["install_exit"]:
            return fail("Install failed", clt["install_exit"])
        clt["installed"] = clt["installs"]
    elif argv == ["xcode-select", "--switch", CLT_FOLDER]:
        if not clt["installed"]:
            return fail("xcode-select: error: invalid developer directory")
    else:
        return unsupported("sudo", argv)
    save(world)
    return 0


def xcode_select(argv: list[str], world: World) -> int:
    """`xcode-select -p`: the developer folder, or the real tool's failure when none is set."""
    if argv != ["-p"]:
        return unsupported("xcode-select", argv)
    if world["clt"]["installed"]:
        print(CLT_FOLDER)
        return 0
    return fail("xcode-select: error: unable to get active developer directory", 2)


def softwareupdate(argv: list[str], world: World) -> int:
    """`softwareupdate -l`; like the real tool it lists Command Line Tools only while the marker
    file exists."""
    if argv != ["-l"]:
        return unsupported("softwareupdate", argv)
    print("Software Update Tool\n\nFinding available software")
    labels = world["clt"]["labels"] if world["clt"]["marker"] else []
    if not labels:
        print("No new software available.")
        return 0
    print("Software Update found the following new or updated software:")
    for label in labels:
        print(f"* Label: {label}")
        print(f"\tTitle: {label.rsplit('-', 1)[0]}, Version: 1.0, Size: 1KiB, Recommended: YES,")
    return 0


# Short options may be combined, as in `-fsSL`.
SHORT_RUN = re.compile(r"^-[A-Za-z]{2,}$")
CURL_FLAGS = {
    "--fail": 0,
    "--silent": 0,
    "--show-error": 0,
    "--location": 0,
    "--proto": 1,
    "--proto-redir": 1,
    "--tlsv1.2": 0,
    "--connect-timeout": 1,
    "--max-time": 1,
    "--retry": 1,
    "-o": 1,
    "-f": 0,
    "-s": 0,
    "-S": 0,
    "-L": 0,
}


def curl(argv: list[str], _world: World) -> int:
    """Serve the file that `downloads.json` maps the URL to; any other URL is a 404.

    Only the curl options start.sh uses are accepted, as https://curl.se/docs/manpage.html
    defines them; any other option exits 2 like curl's "unknown option".
    """
    words = [
        flag
        for word in argv
        for flag in ([f"-{letter}" for letter in word[1:]] if SHORT_RUN.match(word) else [word])
    ]
    bad = unknown_flag(words, CURL_FLAGS, positionals=1)
    if bad:
        return fail(f"curl: option {bad}: is unknown", 2)
    downloads = json.loads((FAKE_DIR / "downloads.json").read_text(encoding="utf-8"))
    source = downloads.get(argv[-1])
    if source is None:
        return fail(f"curl: (22) The requested URL returned error: 404 {argv[-1]}", 22)
    Path(option(argv, "-o")).write_bytes(Path(source).read_bytes())
    return 0


def uname(argv: list[str], _world: World) -> int:
    if argv != ["-m"]:
        return unsupported("uname", argv)
    print(os.environ["FAKE_ARCH"])
    return 0


def sw_vers(argv: list[str], _world: World) -> int:
    if argv != ["-productVersion"]:
        return unsupported("sw_vers", argv)
    print(os.environ["FAKE_MACOS"])
    return 0


def wsl(argv: list[str], world: World) -> int:
    state = world["wsl"]
    if argv == ["--status"]:
        if not state["installed"]:
            return fail("The Windows Subsystem for Linux is not installed.")
        print("Default Version: 2")
        return 0
    if argv[:1] == ["--install"]:
        bad = unknown_flag(argv[1:], {"--no-distribution": 0})
        if bad or "--no-distribution" not in argv:
            return fail(f"Invalid command line option: {bad or argv}")
        if not ELEVATED["on"]:
            return fail("Elevated administrator rights are required.")
        state["installed"] = True
        save(world)
        return int(state["install_exit"])
    return unsupported("wsl", argv)


def winget(argv: list[str], _world: World) -> int:
    if argv == ["--version"]:
        print("v1.12.470")
        return 0
    return unsupported("winget", argv)


def shutdown(argv: list[str], world: World) -> int:
    allowed = {"/r": 0, "/t": 1, "/c": 1}
    if "/r" not in argv or unknown_flag(argv, allowed):
        return fail("The system could not be shut down: invalid arguments.")
    seconds, comment = option(argv, "/t"), option(argv, "/c")
    if not seconds.isdigit() or len(comment) > SHUTDOWN_COMMENT_LIMIT:
        return fail("The system could not be shut down: invalid arguments.")
    world["shutdowns"].append({"seconds": int(seconds), "comment": comment})
    save(world)
    return 0


def sc(argv: list[str], world: World) -> int:
    service = world["agent_service"]
    if len(argv) != 2 or argv[0] not in ("query", "qc") or argv[1] != "ssh-agent":
        return unsupported("sc", argv)
    if not service["present"]:
        print("[SC] EnumQueryServicesStatus:OpenService FAILED 1060:")
        print("The specified service does not exist as an installed service.")
        return 1060
    if argv[0] == "query":
        code, name = (4, "RUNNING") if service["running"] else (1, "STOPPED")
        print("SERVICE_NAME: ssh-agent")
        print(f"        STATE              : {code}  {name}")
        return 0
    codes = {"AUTO_START": 2, "DEMAND_START": 3, "DISABLED": 4}
    print("[SC] QueryServiceConfig SUCCESS")
    print("SERVICE_NAME: ssh-agent")
    print(f"        START_TYPE         : {codes[service['start_type']]}   {service['start_type']}")
    return 0


def ssh_add(argv: list[str], world: World) -> int:
    if argv != ["-l"]:
        return unsupported("ssh-add", argv)
    if not world["agent"]:
        print("The agent has no identities.")
        return 1
    print("256 SHA256:AAAAfakefingerprint GitHub SSH (workstation) (ED25519)")
    return 0


def cmd(argv: list[str], _world: World) -> int:
    """`cmd.exe /c start "" <url>`, which opens a protocol handler; its effects are not modeled."""
    if len(argv) == 4 and argv[:3] == ["/c", "start", ""] and "://" in argv[3]:
        return 0
    return unsupported("cmd", argv)


def powershell(argv: list[str], world: World) -> int:
    if len(argv) != 3 or argv[:2] != ["-NoProfile", "-Command"]:
        return unsupported("powershell", argv)
    script = argv[2]
    started = re.fullmatch(
        r"Start-Process -FilePath '(?P<exe>(?:[^']|'')*)' -ArgumentList @\((?P<args>.*)\) "
        r"-Verb RunAs -Wait -PassThru \| ForEach-Object ExitCode",
        script,
    )
    if started:
        if world["uac"] != "approve":
            return fail(
                "Start-Process : This command cannot be run due to the error: "
                "The operation was canceled by the user."
            )
        quoted = re.findall(r"'((?:[^']|'')*)'", started["args"])
        target = [text.replace("''", "'") for text in quoted]
        exe = Path(started["exe"].replace("''", "'")).name.removesuffix(".exe")
        ELEVATED["on"] = True
        try:
            code = HANDLERS[exe](shlex.split(" ".join(target)), load())
        finally:
            ELEVATED["on"] = False
        print(code)
        return 0
    if "VirtualMachinePlatform" in script:
        print(world["vm_platform"])
        return 0
    if script == "Set-Service ssh-agent -StartupType Disabled; Stop-Service ssh-agent -Force":
        if not ELEVATED["on"]:
            return fail("Set-Service : Access is denied.")
        world["agent_service"].update(running=False, start_type="DISABLED")
        save(world)
        return 0
    return unsupported("powershell", argv)


HANDLERS = {
    "op": op,
    "gh": gh,
    "ssh": ssh,
    "mise": mise,
    "open": open_,
    "sudo": sudo,
    "xcode-select": xcode_select,
    "softwareupdate": softwareupdate,
    "curl": curl,
    "uname": uname,
    "sw_vers": sw_vers,
    "wsl": wsl,
    "winget": winget,
    "shutdown": shutdown,
    "sc": sc,
    "ssh-add": ssh_add,
    "cmd": cmd,
    "powershell": powershell,
}


def main(tool: str, argv: list[str]) -> int:
    with CALLS_FILE.open("a", encoding="utf-8") as log:
        seen = {"stdin_null": stdin_is_null(), "env": {k: os.environ.get(k) for k in LOGGED_ENV}}
        log.write(json.dumps([tool, *argv, seen]) + "\n")
    world = load() if WORLD_FILE.exists() else {}
    if tool not in HANDLERS:
        return unsupported(tool, argv)
    return HANDLERS[tool](argv, world)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1], sys.argv[2:]))
