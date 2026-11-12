# AGENTS.md

instructions-marker: ws-agents-4bc9642a

Claude Code reads this file at the start of every session, and it needs Claude Code 2.1.281 or later. Claude Code skips `AGENTS.md` when a `CLAUDE.md` exists, so this repository holds none. Skills are real files under `.claude/skills/`, never symlinks. The private setup repository's `ws check --online` verifies both against the commit its README pins.

## What this is

`stevenaldinger/workstation-bootstrap` is the public starter for a private workstation setup repository. A new Mac has no GitHub credential, so the starter runs first: it checks the Mac, installs a checksum-pinned mise, sets up the machine's identity, clones the private repository over SSH, and hands off to it. The repository also holds the proof engine that runs commands inside throwaway macOS virtual machines.

## Invariants

- The starter holds scripts only. No account identifier, email address, package list, token, or other private value is ever committed; the one personal value it needs is passed as the `--personal-account` argument.
- `start.sh` needs only the `/bin/bash` 3.2 that every Mac has. `start.py` and the proof engine use only the Python standard library.
- The starter cannot change without its pin changing: `start.sh` pins the checksum of `start.sha256`, which lists the checksum of every other starter file, and the private repository's README pins the commit and checksum of `start.sh`.
- Every Python module stays under 500 lines, and the tests never reach a real Homebrew, 1Password, or GitHub.

## Layout

| Path | Holds |
|------|-------|
| `start.sh` | The only Bash: checks the Mac and installs mise, then runs `start.py` |
| `start.py` | The staged walkthrough, with `start_base.py` and `start_identity.py` |
| `stage1.toml` | The packages stage 1 installs |
| `start.sha256` | The checksum of each file `start.sh` downloads |
| `prove.py` | The proof engine's command line, with the `prove_engine/` package |
| `scripts/check.sh` | Every check: shellcheck, ruff, mypy, pytest, and gitleaks |
| `scripts/pins.sh` | Writes the manifest, points `start.sh` at it, and prints the README pin |
| `tests/` | The pytest suite |
| `.claude/skills/` | The skill listed below |

## Commands

```sh
scripts/check.sh
scripts/check.sh tests
scripts/check.sh python
scripts/check.sh shellcheck
scripts/check.sh secrets
scripts/pins.sh show
```

`scripts/check.sh` with no argument runs every check, as hosted CI does; `mise install` provides the pinned tools first.

## Owner authorization

Ask the owner in the session before pushing to a remote, and before running `start.sh` on any machine the owner has not named as the target. The proof engine's virtual machines are the place to run the starter instead. Never print git configuration, credential helper output, or a token.

## Releasing a starter change

A change to `start.py`, `start_base.py`, `start_identity.py`, or `stage1.toml` is released in three commits with `scripts/pins.sh`: commit the change, run `scripts/pins.sh manifest` and commit `start.sha256`, run `scripts/pins.sh release` and commit `start.sh`. After the push, `scripts/pins.sh show` prints the commit and checksum that the private repository's README pins; the private repository's `release-starter` skill moves that pin. The `change-starter` skill below runs this flow.

## Skills

Each skill is a real file at `.claude/skills/<name>/SKILL.md` with four sections: When to use, Steps, Done when, and Ask the owner.

| Skill | Use it to |
|-------|-----------|
| `change-starter` | Change the starter or its proof engine, run the checks, and hand the new pin to the private repository |
