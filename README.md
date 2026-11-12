# workstation-bootstrap

## What this is

`stevenaldinger/workstation-bootstrap` is the public starter for a private workstation setup repository. A new Mac has no GitHub credential, so it cannot clone the private repository directly. The starter runs first, sets up the machine's identity, and then clones the private repository and hands off to it.

The starter holds scripts only. It contains no account identifiers, email addresses, or package lists. The one personal value it needs, the 1Password sign-in address, is passed as a command argument.

The repository also holds the proof engine and the checks for both.

## Bootstrap command

A Mac runs the starter with one command. It downloads `start.sh` at one commit, checks the file's SHA-256, and runs it only when the hash matches:

```sh
curl -fsSL -o /tmp/ws-start.sh https://raw.githubusercontent.com/stevenaldinger/workstation-bootstrap/<commit>/start.sh \
  && echo "<sha256>  /tmp/ws-start.sh" | shasum -a 256 -c - \
  && /bin/bash /tmp/ws-start.sh --personal-account <email or sign-in address>
```

The private repository's README holds the pinned `<commit>` and `<sha256>`. Its checks download `start.sh` at that commit and compare hashes, so the starter cannot change without the pin changing too. `--personal-account` is the one personal value the starter needs: the email or sign-in address of the personal 1Password account. Any other argument goes to the private repository's `ws bootstrap`.

## What the starter does

`start.sh` is the only Bash, and it needs only the `/bin/bash` 3.2 that every Mac has. In order, it:

1. stops with exit status 2, before changing anything, unless the Mac is Apple silicon running macOS 26 or 27;
2. asks for administrator access once and keeps it alive;
3. downloads a pinned mise release, checks its SHA-256, and installs it into `~/.local/bin`;
4. installs the pinned Python and uv through mise;
5. downloads `start.sha256` and checks it against the checksum pinned in `start.sh`, downloads each file it lists, and checks every one;
6. runs `start.py` under that Python.

`start.py` uses only the standard library. Every step checks the machine first and does nothing when the check already passes, so a re-run resumes at the first step that is not yet satisfied:

1. Install the packages in `stage1.toml`: the 1Password app and command line, Git, and the GitHub CLI.
2. Sign in to the personal 1Password account (H1).
3. Turn on the 1Password command-line integration and the SSH agent (H3).
4. Create the SSH key `GitHub SSH (workstation)` in the personal Private vault, and write its public half to `~/.ssh/github_workstation.pub`. No private key file is ever written.
5. Register the key with GitHub through the GitHub CLI, after the one-time-code approval (H4), then drop the key-registration scope from the token and verify the SSH handshake through the 1Password agent.
6. Clone the private repository to `~/.config/mise` over SSH and set its hook directory. An existing checkout of the same remote is fast-forwarded; any other non-empty directory stops the run with its path.
7. Hand off to `~/.config/mise/bin/ws bootstrap`.

A human step runs only when its check fails. The starter then opens the app or settings pane, states the action, waits for Enter, checks again, and repeats until the check passes:

| ID | Step | Check |
|----|------|-------|
| H1 | Sign in to the personal 1Password account | `op account list` lists the account |
| H3 | Turn on the command-line integration and the SSH agent | `op vault list` succeeds without a prompt and the agent socket exists |
| H4 | Approve the GitHub sign-in with a one-time code | `gh auth status` shows the key-registration scope |

`WS_REPO_URL` replaces the clone address, which the clean-machine proof uses to clone from a host mirror.

## Releasing a starter change

`start.sh` pins one checksum, the one of `start.sha256`, which lists the checksum of every other starter file. A change is released in three commits:

```sh
# 1. Commit the changed starter files, then rewrite the manifest and commit it
scripts/pins.sh manifest
git add start.sha256 && git commit -m "Update the starter manifest"
# 2. Point start.sh at that commit and the manifest, then commit it
scripts/pins.sh release
git add start.sh && git commit -m "Release the starter"
# 3. After the push, print the commit and checksum that the private README pins
scripts/pins.sh show
```

The tests fail while any pin and its file disagree.

## Proof engine

`prove.py` runs commands inside throwaway macOS virtual machines, so the bootstrap can be proven on a clean machine before it runs on a real one. It uses [Tart](https://tart.run/) and a vanilla macOS image from Cirrus Labs. The Python is standard library only.

How it works:

- Tart 2.41.1 is downloaded from its GitHub release, checked against a pinned SHA-256, and unpacked into the cache folder. Nothing is installed system-wide.
- The image is pinned by digest. macOS 26 uses `macos-tahoe-vanilla` and macOS 27 uses `macos-golden-gate-vanilla`.
- The image is cloned once to a base that is never booted. Each run clones the base, boots the clone headless, runs commands over SSH, and deletes the clone, including when the command fails or times out.

Prerequisites:

- An Apple Silicon Mac. A macOS 15 host boots a macOS 26 guest.
- About 60 GB of free disk for the first image pull, and 15 GB for each run. The base machine's disk is grown to 100 GB (the shipped image leaves too little room to install a full workstation): `Engine.base()` moves the image's recovery partition to the end of the raw disk file while the machine is stopped, then resizes the system container from the host. The disk file is sparse, so this takes no space until the guest writes to it. `Engine.base()` reads the result from the partition table, so a grow that stopped half way is finished by the next call.
- `uv`, which `mise install` provides.

Everything lives under `~/.cache/workstation-bootstrap/prove` (`--cache-root` changes it), and VM images land in its `tart-home` folder, not in `~/.tart`. The folder above it is where `start.sh` keeps the starter files, so the engine never uses it. The first run after an upgrade moves an older engine cache out of that shared folder.

```sh
# Download and verify Tart, then pull the image and make the base (about 24 GB, once).
uv run prove.py tart
uv run prove.py base --target-major 26

# Boot a fresh clone, print the guest's macOS version and architecture, delete the clone.
uv run prove.py run smoke --target-major 26

# Run a script with a read-only share, a writable share, and a time limit.
uv run prove.py run first-run --script scenario.sh --timeout 1800 \
  --share repo=/path/to/repo:ro --share out=/path/to/out --out /path/to/evidence

# Leave the clone running to debug it; the command prints the SSH login.
uv run prove.py run debug --script scenario.sh --keep-vm

# List and delete leftover clones, or remove the engine's cache, images, and Tart.
uv run prove.py list
uv run prove.py cleanup
uv run prove.py cleanup --purge
```

Every Tart command has a time limit, and `tart pull` prints a still-running line every 30 seconds. A cached Tart archive that fails its checksum is deleted and downloaded once more. `prove.py run --script FILE` exits 2 with a message when the file cannot be read. Only one VM runs at a time on a host. A run takes the lock folder `~/.cache/workstation-bootstrap/vm.lock` (an atomic `mkdir`, with the owner's process id inside) before it clones, and a second run fails at once, naming the run that holds it. A lock whose process is gone is taken over. A guest left running by `--keep-vm` keeps the lock until its process ends. If a folder with no owner file blocks a run and no VM is running, remove it with `rmdir`.

Shares appear in the guest under `/Volumes/My Shared Files/<name>`. Every run enables Gatekeeper first, because the vanilla image ships with it off. The run stops with an engine error if `spctl --global-enable` fails or `spctl --status` does not then report `assessments enabled`, and `env.json` records `gatekeeper`. `--out` writes `env.json` (guest macOS version and architecture, image digest, Tart version, host version, Gatekeeper state), `timings.json`, `result.json`, and the command's output. `prove.py` exits 0 when the guest command succeeds, 1 when it fails or times out, and 2 on an engine error.

The engine is also a Python API for test suites: `from prove_engine import Engine, Share, host_info`. `Engine.base()`, `Engine.machine()` (a context manager that always deletes its clone), and `Engine.run()` cover the lifecycle. A `Machine` offers `start`, `run`, `snapshot`, `restore`, and `destroy`. A snapshot flushes the guest's disk, stops the machine, copies its disk, and boots it again, so it includes a reboot. Share a folder with `--share` to move files in or out.

To change a pin, edit `prove_engine/pins.py`. Take the Tart checksum from the release's published asset digest, and take an image digest from the registry's manifest for its tag.

Tart is under the Functional Source License 1.1, which permits internal use. To remove everything the engine created, run `uv run prove.py cleanup --purge`. A purge removes only the `tart`, `tart-home`, and `logs` folders and the `askpass.sh` file, and only from a folder that holds the `.prove-engine` marker the engine wrote, so a mistyped `--cache-root` is refused instead of deleted.

## Checks

`scripts/check.sh` runs shellcheck over every tracked `*.sh` file, ruff and mypy over the Python, the pytest suite through uv, and a gitleaks scan of the full history. The unit tests cover the engine's pure logic and need neither Tart nor a virtual machine. GitHub Actions runs the same script on every push and pull request.

```sh
mise install
scripts/check.sh
```

`mise install` reads `mise.toml` and installs the pinned gitleaks, Python, shellcheck, and uv. Ruff, mypy, and pytest versions are pinned in `scripts/check.sh` and run through `uv run --no-project`.

## Commit messages

Every commit message is a Conventional Commit:

```text
type(scope): imperative lowercase summary

Body that explains why, wrapped at 72 characters.
```

The type is one of `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, or `revert`. The scope is required, the header is at most 72 characters with no trailing period, and no AI or session attribution trailer appears. Enable the `commit-msg` hook once per clone:

```sh
git config core.hooksPath .githooks
```

`scripts/check.sh commits` runs the same rules over history. Commits up to the one recorded in `scripts/commit-lint-base.txt` only produce warnings; later commits fail the check. CI passes the commit before the pushed range as `CHECK_BASE_SHA`, which replaces the recorded base.

## Agent instructions

Claude Code reads `AGENTS.md` and the `change-starter` skill under `.claude/skills/`; there is no `CLAUDE.md`, and no skill is a symlink, because either one breaks how Claude Code loads them. The skill covers the checks above, the release flow, and the hand-off of the new pin to the private repository. The private repository's `ws check --online` verifies these files at the commit its README pins.
