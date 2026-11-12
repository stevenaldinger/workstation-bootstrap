# workstation-bootstrap

## What this is

`stevenaldinger/workstation-bootstrap` is the public starter for a private workstation setup repository. A new Mac has no GitHub credential, so it cannot clone the private repository directly. The starter runs first, sets up the machine's identity, and then clones the private repository and hands off to it.

The starter holds scripts only. It contains no account identifiers, email addresses, or package lists. The one personal value it needs, the 1Password sign-in address, is passed as a command argument.

This repository currently holds the proof engine and the starter's checks. The starter script, `start.sh`, is added here when it is released.

## Bootstrap command

A Mac runs the starter with one command. It downloads `start.sh` at one commit, checks the file's SHA-256, and runs it only when the hash matches:

```sh
curl -fsSL -o /tmp/ws-start.sh https://raw.githubusercontent.com/stevenaldinger/workstation-bootstrap/<commit>/start.sh \
  && echo "<sha256>  /tmp/ws-start.sh" | shasum -a 256 -c - \
  && /bin/bash /tmp/ws-start.sh --personal-account <sign-in address>
```

The private repository's README holds the pinned `<commit>` and `<sha256>`. Its checks download `start.sh` at that commit and compare hashes, so the starter cannot change without the pin changing too.

## Proof engine

`prove.py` runs commands inside throwaway macOS virtual machines, so the bootstrap can be proven on a clean machine before it runs on a real one. It uses [Tart](https://tart.run/) and a vanilla macOS image from Cirrus Labs. The Python is standard library only.

How it works:

- Tart 2.41.1 is downloaded from its GitHub release, checked against a pinned SHA-256, and unpacked into the cache folder. Nothing is installed system-wide.
- The image is pinned by digest. macOS 26 uses `macos-tahoe-vanilla` and macOS 27 uses `macos-golden-gate-vanilla`.
- The image is cloned once to a base that is never booted. Each run clones the base, boots the clone headless, runs commands over SSH, and deletes the clone, including when the command fails or times out.

Prerequisites:

- An Apple Silicon Mac. A macOS 15 host boots a macOS 26 guest.
- About 60 GB of free disk for the first image pull, and 15 GB for each run.
- `uv`, which `mise install` provides.

Everything lives under `~/.cache/workstation-bootstrap` (`--cache-root` changes it), and VM images land in its `tart-home` folder, not in `~/.tart`.

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

# List and delete leftover clones, or remove the cache, images, and Tart.
uv run prove.py list
uv run prove.py cleanup
uv run prove.py cleanup --purge
```

Shares appear in the guest under `/Volumes/My Shared Files/<name>`. Every run enables Gatekeeper first, because the vanilla image ships with it off. `--out` writes `env.json` (guest macOS version and architecture, image digest, Tart version, host version), `timings.json`, `result.json`, and the command's output. `prove.py` exits 0 when the guest command succeeds, 1 when it fails or times out, and 2 on an engine error.

The engine is also a Python API for test suites: `from prove_engine import Engine, Share, host_info`. `Engine.base()`, `Engine.machine()` (a context manager that always deletes its clone), and `Engine.run()` cover the lifecycle. A `Machine` offers `start`, `run`, `put`, `get`, `snapshot`, `restore`, and `destroy`. A snapshot flushes the guest's disk, stops the machine, copies its disk, and boots it again, so it includes a reboot.

To change a pin, edit `prove_engine/pins.py`. Take the Tart checksum from the release's published asset digest, and take an image digest from the registry's manifest for its tag.

Tart is under the Functional Source License 1.1, which permits internal use. To remove everything the engine created, run `uv run prove.py cleanup --purge`.

## Checks

`scripts/check.sh` runs shellcheck over every tracked `*.sh` file, ruff and mypy over the Python, the pytest suite through uv, and a gitleaks scan of the full history. The unit tests cover the engine's pure logic and need neither Tart nor a virtual machine. GitHub Actions runs the same script on every push and pull request.

```sh
mise install
scripts/check.sh
```

`mise install` reads `mise.toml` and installs the pinned gitleaks, Python, shellcheck, and uv. Ruff, mypy, and pytest versions are pinned in `scripts/check.sh` and run through `uv run --no-project`.
