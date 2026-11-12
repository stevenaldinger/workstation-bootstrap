# AGENTS.md

Claude Code reads this file at the start of every session, and it needs Claude Code 2.1.281 or later. Claude Code skips `AGENTS.md` when a `CLAUDE.md` exists, so this repository holds none. Skills are real files under `.claude/skills/`, never symlinks.

## What this is

`stevenaldinger/workstation-bootstrap` is the public starter for a private workstation setup repository. A new Mac or Windows computer has no GitHub credential, so the starter runs first: it checks the machine, installs a checksum-pinned mise, sets up the machine's identity, clones the private repository over SSH, and hands off to it. The repository also holds the proof engine that runs commands inside throwaway macOS virtual machines.

## Invariants

- The starter holds no account identifier, email address, token, or other private value; the one personal value it needs is passed as the `--personal-account` argument. Its own stage-1 package lists are `stage1.toml` (Mac) and `stage1-windows.toml` (Windows), and its settings are `start.toml`.
- `start.sh` needs only the `/bin/bash` 3.2 that every Mac has, and `start.ps1` needs only Windows PowerShell 5.1, which every Windows 11 has (keep it free of newer syntax; the `powershell` check enforces that). `start.py` and the proof engine use only the Python standard library.
- The starter cannot change without its pin changing: `start.sh` and `start.ps1` each pin the checksum of `start.sha256`, which lists the checksum of every other starter file, and each bootstrap command pins the commit and checksum of its script.
- Nothing trusts a host key on first use: ssh runs with `StrictHostKeyChecking=yes` and the keys in `github_known_hosts`.
- The tests never reach a real Homebrew, winget, 1Password, or GitHub; they run the starter against fake commands. On Windows the fakes are compiled `.exe` files, because Windows starts a command by file name only when it is an executable.
- Only `elevate` in `start_windows.py` asks Windows for administrator rights, and it states the reason first. On a Mac, only `start_macos.py` runs elevated commands (`sudo`), for the Command Line Tools, and it states the reason first and never opens the installer dialog. A restart happens only after the person chooses it.
- Every commit message is a Conventional Commit with a body (see the README).

## Layout

| Path | Holds |
|------|-------|
| `start.sh` | The only Bash: checks the Mac, installs mise, Python, uv, and gh, then runs `start.py` |
| `start.ps1` | The only PowerShell: the same for Windows, and the target of the sign-in restart entry |
| `start.py` | The entrypoint, with the sibling modules `start_base.py`, `start_config.py`, `start_packages.py`, `start_identity.py`, `start_clone.py`, `start_macos.py` (the Mac host step), and `start_windows.py` (the Windows host steps) |
| `start.toml`, `stage1.toml`, `stage1-windows.toml` | The starter's settings and its stage-1 package lists |
| `github_known_hosts` | GitHub's pinned SSH host keys |
| `start.sha256` | The checksum of each file `start.sh` downloads |
| `prove.py` | The proof engine's command line, with the `prove_engine/` package |
| `scripts/check.sh` | Every check: shellcheck, ruff, mypy, pytest, gitleaks, commit messages, agent files, and PSScriptAnalyzer; also the `bash32` (macOS) and `windows` (Windows) targets |
| `scripts/pins.sh` | Writes the manifest, points `start.sh` and `start.ps1` at it, and prints both pins |
| `PSScriptAnalyzerSettings.psd1` | The PSScriptAnalyzer rules for `start.ps1`, including Windows PowerShell 5.1 syntax compatibility |
| `tests/` | The pytest suite |
| `.claude/agents/`, `.claude/settings.json` | The `starter-maintainer` agent and the setting that makes it the default |
| `.claude/skills/` | The skill listed below |

## Commands

```sh
scripts/check.sh
scripts/check.sh tests
scripts/check.sh python
scripts/check.sh shellcheck
scripts/check.sh powershell
scripts/check.sh secrets
scripts/pins.sh show
```

`scripts/check.sh` with no argument runs every check, as hosted CI does; `mise install` provides the pinned tools first.

## Owner authorization

Ask the owner in the session before pushing to a remote, and before running `start.sh` or `start.ps1` on any machine the owner has not named as the target. The proof engine's virtual machines, and the `windows-2025` hosted job, are the places to run the starter instead. Never print git configuration, credential helper output, or a token.

## Releasing a starter change

A change to any file in `STARTER_FILES` in `scripts/pins.sh` is released in three commits: commit the change, run `scripts/pins.sh manifest` and commit `start.sha256` as `build(manifest): refresh starter manifest`, then run `scripts/pins.sh release` and commit `start.sh` and `start.ps1` as `chore(release): pin starter files`. After the push, `scripts/pins.sh show` prints the commit and both scripts' checksums for the bootstrap commands. The `change-starter` skill below runs this flow.

## Skills

Each skill is a real file at `.claude/skills/<name>/SKILL.md` with four sections: When to use, Steps, Done when, and Ask the owner.

| Skill | Use it to |
|-------|-----------|
| `change-starter` | Change the starter or its proof engine, run the checks, and report the new pin |
