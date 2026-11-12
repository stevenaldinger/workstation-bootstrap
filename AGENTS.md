# AGENTS.md

Claude Code reads this file at the start of every session, and it needs Claude Code 2.1.281 or later. Claude Code skips `AGENTS.md` when a `CLAUDE.md` exists, so this repository holds none. Skills are real files under `.claude/skills/`, never symlinks.

## What this is

`stevenaldinger/workstation-bootstrap` is the public starter for a private workstation setup repository. A new Mac has no GitHub credential, so the starter runs first: it checks the Mac, installs a checksum-pinned mise, sets up the machine's identity, clones the private repository over SSH, and hands off to it. The repository also holds the proof engine that runs commands inside throwaway macOS virtual machines.

## Invariants

- The starter holds no account identifier, email address, token, or other private value; the one personal value it needs is passed as the `--personal-account` argument. Its own stage-1 package list is `stage1.toml`, and its settings are `start.toml`.
- `start.sh` needs only the `/bin/bash` 3.2 that every Mac has. `start.py` and the proof engine use only the Python standard library.
- The starter cannot change without its pin changing: `start.sh` pins the checksum of `start.sha256`, which lists the checksum of every other starter file, and the bootstrap command pins the commit and checksum of `start.sh`.
- Nothing trusts a host key on first use: ssh runs with `StrictHostKeyChecking=yes` and the keys in `github_known_hosts`.
- The tests never reach a real Homebrew, 1Password, or GitHub; they run the starter against fake commands.
- Every commit message is a Conventional Commit with a body (see the README).

## Layout

| Path | Holds |
|------|-------|
| `start.sh` | The only Bash: checks the Mac, installs mise, Python, uv, and gh, then runs `start.py` |
| `start.py` | The entrypoint, with the sibling modules `start_base.py`, `start_config.py`, `start_packages.py`, `start_identity.py`, and `start_clone.py` |
| `start.toml`, `stage1.toml` | The starter's settings and its stage-1 package list |
| `github_known_hosts` | GitHub's pinned SSH host keys |
| `start.sha256` | The checksum of each file `start.sh` downloads |
| `prove.py` | The proof engine's command line, with the `prove_engine/` package |
| `scripts/check.sh` | Every check: shellcheck, ruff, mypy, pytest, gitleaks, commit messages, and agent files |
| `scripts/pins.sh` | Writes the manifest, points `start.sh` at it, and prints the pin |
| `tests/` | The pytest suite |
| `.claude/agents/`, `.claude/settings.json` | The `starter-maintainer` agent and the setting that makes it the default |
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

A change to any file in `STARTER_FILES` in `scripts/pins.sh` is released in three commits: commit the change, run `scripts/pins.sh manifest` and commit `start.sha256` as `build(manifest): refresh starter manifest`, then run `scripts/pins.sh release` and commit `start.sh` as `chore(release): pin starter files`. After the push, `scripts/pins.sh show` prints the commit and checksum for the bootstrap command. The `change-starter` skill below runs this flow.

## Skills

Each skill is a real file at `.claude/skills/<name>/SKILL.md` with four sections: When to use, Steps, Done when, and Ask the owner.

| Skill | Use it to |
|-------|-----------|
| `change-starter` | Change the starter or its proof engine, run the checks, and report the new pin |
