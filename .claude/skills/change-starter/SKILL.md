---
name: change-starter
description: Use when changing the public starter or its proof engine, meaning start.sh, start.py, the stage 1 package list, prove.py, or their tests, for requests like "fix the starter" or "change the first-run step". Runs the checks, keeps private data out, and hands the new pin to the private repository; not for editing the private setup repository.
allowed-tools: Bash(${CLAUDE_PROJECT_DIR}/scripts/check.sh) Bash(${CLAUDE_PROJECT_DIR}/scripts/check.sh *) Bash(${CLAUDE_PROJECT_DIR}/scripts/pins.sh show) Bash(git status *) Bash(git diff *) Bash(git log *)
---

# Change the starter

## When to use

A new Mac runs the starter before it can reach the private repository, so a starter change affects every first run. The starter files are `start.sh`, `start.py`, `start_base.py`, `start_identity.py`, and `stage1.toml`; the proof engine is `prove.py` and the `prove_engine/` package. `start.py`, `start_base.py`, `start_identity.py`, and `stage1.toml` are pinned by checksum in `start.sha256`, so changing one means a release.

## Steps

1. Write the failing test first in `${CLAUDE_PROJECT_DIR}/tests/test_start_flow.py` or the test file for the module you change, and run it with `${CLAUDE_PROJECT_DIR}/scripts/check.sh tests` to see it fail for the reason you expect.
2. Make the change in `${CLAUDE_PROJECT_DIR}/start.py` or the file it belongs to. Keep `${CLAUDE_PROJECT_DIR}/start.sh` to Bash 3.2, keep Python to the standard library, and keep every module under 500 lines.
3. Keep private data out: no account identifier, email address, package list, or token in any file or test fixture. The personal 1Password address stays a command argument.
4. Run `${CLAUDE_PROJECT_DIR}/scripts/check.sh` and fix every finding.
5. When a pinned starter file changed (`start.py`, `start_base.py`, `start_identity.py`, or `stage1.toml`), commit the change, run `${CLAUDE_PROJECT_DIR}/scripts/pins.sh manifest` and commit `start.sha256`, then run `${CLAUDE_PROJECT_DIR}/scripts/pins.sh release` and commit `start.sh`.
6. **Owner authorization:** push the commits with `git push origin main` only after the owner agrees in this session.
7. After the push, run `${CLAUDE_PROJECT_DIR}/scripts/pins.sh show` and give the commit and SHA-256 it prints to the private repository's `release-starter` skill.

## Done when

- `${CLAUDE_PROJECT_DIR}/scripts/check.sh` passes on the committed change.
- The new test failed before the change and passes after it.
- For a pinned-file change, `scripts/pins.sh show` printed a commit and checksum for the pushed release.

## Ask the owner

- Whether a change to a prompt or a human-only step is wanted, because the owner sees it on the next new Mac.
- Whether a change to the starter's package list or its proof image is intended.
- Permission to push.
