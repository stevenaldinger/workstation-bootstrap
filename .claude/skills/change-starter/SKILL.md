---
name: change-starter
description: Use when changing the public starter or its proof engine, meaning start.sh, start.py, the stage 1 package list, prove.py, or their tests, for requests like "fix the starter" or "change the first-run step". Runs the checks, keeps private data out, and reports the new pin; not for editing the private setup repository.
allowed-tools: Bash(${CLAUDE_PROJECT_DIR}/scripts/check.sh) Bash(${CLAUDE_PROJECT_DIR}/scripts/check.sh *) Bash(${CLAUDE_PROJECT_DIR}/scripts/pins.sh show) Bash(git status *) Bash(git diff *) Bash(git log *)
---

# Change the starter

## When to use

A new Mac runs the starter before it can reach the private repository, so a starter change affects every first run. The starter files are the ones listed in `STARTER_FILES` in `${CLAUDE_PROJECT_DIR}/scripts/pins.sh` (`start.py` and its sibling modules, `start.toml`, `stage1.toml`, and `github_known_hosts`), plus `start.sh`; the proof engine is `prove.py` and the `prove_engine/` package. Every file in `STARTER_FILES` is pinned by checksum in `start.sha256`, so changing one means a release.

## Steps

1. Write the failing test first in the test file for the module you change, and run it with `${CLAUDE_PROJECT_DIR}/scripts/check.sh tests` to see it fail for the reason you expect.
2. Make the change in the file it belongs to. Keep `${CLAUDE_PROJECT_DIR}/start.sh` to Bash 3.2 and keep Python to the standard library. A new starter module goes in `STARTER_FILES` in `${CLAUDE_PROJECT_DIR}/scripts/pins.sh`.
3. Keep private data out: no account identifier, email address, or token in any file or test fixture. The personal 1Password address stays a command argument.
4. Run `${CLAUDE_PROJECT_DIR}/scripts/check.sh` and fix every finding.
5. Commit with a Conventional Commit and a body, for example `fix(start): stop on a failed key lookup`.
6. When a starter file changed, run `${CLAUDE_PROJECT_DIR}/scripts/pins.sh manifest` and commit `start.sha256` as `build(manifest): refresh starter manifest`, then run `${CLAUDE_PROJECT_DIR}/scripts/pins.sh release` and commit `start.sh` as `chore(release): pin starter files`.
7. **Owner authorization:** push the commits with `git push origin main` only after the owner agrees in this session.
8. After the push, run `${CLAUDE_PROJECT_DIR}/scripts/pins.sh show` and report the commit and SHA-256 it prints, which are the values for the bootstrap command.

## Done when

- `${CLAUDE_PROJECT_DIR}/scripts/check.sh` passes on the committed change.
- The new test failed before the change and passes after it.
- For a starter-file change, `scripts/pins.sh show` printed a commit and checksum for the pushed release.

## Ask the owner

- Whether a change to a prompt or a human-only step is wanted, because the owner sees it on the next new Mac.
- Whether a change to the starter's package list or its proof image is intended.
- Permission to push.
