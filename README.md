# workstation-bootstrap

## What this is

`stevenaldinger/workstation-bootstrap` is the public starter for a private workstation setup repository. A new Mac or Windows computer has no GitHub credential, so it cannot clone the private repository directly. The starter runs first, sets up the machine's identity, and then clones the private repository and hands off to it.

The starter holds no account identifiers or email addresses. The one personal value it needs, the 1Password sign-in address, is passed as a command argument. It does carry a short package list for its own stage (`stage1.toml` on a Mac and `stage1-windows.toml` on Windows: the 1Password app, its command line, and Git) and the settings for the identity and the clone (`start.toml`: the key's item title and vault, the private repository's address, its checkout folder, and its hook folder).

The repository also holds the proof engine and the checks for both.

## Bootstrap command

### Mac

A Mac runs the starter with one command. It downloads `start.sh` at one commit into a private temporary folder, checks the file's SHA-256, and runs it only when the hash matches:

```sh
dir="$(mktemp -d)" \
  && curl -fsSL --proto '=https' --tlsv1.2 -o "$dir/start.sh" https://raw.githubusercontent.com/stevenaldinger/workstation-bootstrap/<commit>/start.sh \
  && echo "<sha256>  $dir/start.sh" | shasum -a 256 -c - \
  && /bin/bash "$dir/start.sh" --personal-account <email or sign-in address>
```

`mktemp -d` makes a folder only the current user can enter, so no other process can swap the file between the check and the run. `scripts/pins.sh show` prints the `<commit>` and `<sha256>` of a released `start.sh`; take them from the source that sent you here, not from a file you downloaded alongside the script. `--personal-account` is the one personal value the starter needs: the email or sign-in address of the personal 1Password account. `/bin/bash "$dir/start.sh" -h` prints the usage. Any other argument goes to the private repository's `ws bootstrap`.

### Windows

A Windows 11 computer (x64, 23H2 or later, with virtualization turned on) runs the starter from Windows PowerShell 5.1, which every Windows 11 has. Paste the block into a PowerShell window that is not elevated. It downloads `start.ps1` at one commit into a new folder under the user's own `%TEMP%`, checks the file's SHA-256, and runs it only when the hash matches:

```powershell
& {
  $ErrorActionPreference = 'Stop'
  [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
  $dir = New-Item -ItemType Directory -Path (Join-Path $env:TEMP ([guid]::NewGuid()))
  $file = Join-Path $dir 'start.ps1'
  Invoke-WebRequest -UseBasicParsing -Uri 'https://raw.githubusercontent.com/stevenaldinger/workstation-bootstrap/<commit>/start.ps1' -OutFile $file
  if ((Get-FileHash -Algorithm SHA256 -LiteralPath $file).Hash -ne '<sha256>') { throw 'start.ps1 does not match its SHA-256; nothing was run' }
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $file -PersonalAccount <email or sign-in address>
}
```

The new folder inherits the access list of `%TEMP%`, which only the user, administrators, and the system can enter, so no other user can swap the file between the check and the run. `-ExecutionPolicy Bypass` applies to that one run, because a Windows 11 client refuses to run any script file under its default policy. `scripts/pins.sh show` prints the `<commit>` and `<sha256>` of a released `start.ps1`; take them from the source that sent you here. `-PersonalAccount` comes first and takes the email or sign-in address of the personal 1Password account. Any other argument goes to the private repository's `ws bootstrap`.

The first run can end in a restart: Windows Subsystem for Linux must be turned on before Ubuntu can be installed. The starter asks first. If you choose to restart, it registers `start.ps1` with the same `-PersonalAccount` to run again when you sign in, and running the command again by hand does the same.

## Trust boundary

The command pins the bytes of `start.sh` or `start.ps1`. From there, each download is either checked by this repository or handed to a tool that does its own checking.

Pinned and checked here:

- `start.sh` and `start.ps1`, by the SHA-256 in the command above.
- The mise archive, by `MISE_SHA256` in `start.sh` and `$MiseSha256` in `start.ps1`. Each is the platform's line in the release's `SHASUMS256.txt`.
- `start.sha256`, by `MANIFEST_SHA256` in `start.sh` and `$ManifestSha256` in `start.ps1`, and through it every starter file (`start.py`, its sibling modules, `start.toml`, `stage1.toml`, `stage1-windows.toml`, and `github_known_hosts`), each downloaded at `FILES_COMMIT` and checked against the manifest.
- GitHub's SSH host keys. `github_known_hosts` holds the three keys GitHub publishes in the `ssh_keys` field of [its meta API](https://api.github.com/meta), which match [GitHub's published fingerprints](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints). ssh runs with `StrictHostKeyChecking=yes` and only that file, so a different key stops the handshake and the clone.

Pinned by version here, downloaded and verified by mise:

- Python, uv, and the GitHub CLI. `start.sh` and `start.ps1` name the exact versions; mise downloads them and applies whatever verification its backend provides for each tool, such as a checksum, an attestation, or a signature. This repository does not check those bytes again.

Delegated, not pinned:

- On a Mac, the 1Password app, the 1Password command line, and Git, listed in `stage1.toml` at `latest`. mise installs them as a Homebrew cask and formulae. It checks a formula's download against Homebrew's published sha256 and a cask's when the cask provides one, and the versions are whatever Homebrew serves that day.
- On Windows, the same three, listed in `stage1-windows.toml` with exact winget versions. mise runs `winget install --exact --version`, and winget checks each installer against the SHA-256 in its package manifest. The installers ask Windows for administrator approval, because they install for every user.
- On Windows, the winget client itself, only on a computer that lacks it (a Windows 11 Pro computer ships winget, so a normal client never takes this path). `start.ps1` first registers the App Installer that Windows already holds, and otherwise installs the `Microsoft.WinGet.Client` module from the PowerShell Gallery at a pinned version and lets it repair winget. That is pinned by version, with the signature checked by Windows.
- The private repository's contents, which the owner trusts by cloning it over the pinned host key into `~/.config/mise`.

Homebrew is not required. mise's `brew:` and `brew-cask:` entries in `stage1.toml` install into the standard Homebrew prefix without Homebrew present, according to [the mise documentation](https://mise.jdx.dev/bootstrap/packages/brew.html), and `start.sh` adds `/opt/homebrew/bin` to `PATH` for the commands they link there.

## What the starter does

### On a Mac

`start.sh` is the only Bash, and it needs only the `/bin/bash` 3.2 that every Mac has. In order, it:

1. stops with exit status 2, before changing anything, if `--personal-account` is missing or malformed or the Mac is not Apple silicon running macOS 26 or 27;
2. asks for administrator access once and refreshes it until the script ends or a refresh fails;
3. downloads a pinned mise release, checks its SHA-256, and installs it into `~/.local/bin`;
4. installs the pinned Python, uv, and GitHub CLI through mise;
5. downloads `start.sha256` and checks it against the checksum pinned in `start.sh`, downloads each file it lists, and checks every one;
6. runs `start.py` under that Python.

Every download uses `curl` with `--proto '=https' --tlsv1.2`, connection and total time limits, and retries.

`start.py` uses only the standard library. Every step checks the machine first and does nothing when the check already passes, so a re-run resumes at the first step that is not yet satisfied:

1. Install the packages in `stage1.toml`: the 1Password app and command line, and Git.
2. Sign in to the personal 1Password account.
3. Turn on the 1Password command-line integration and the SSH agent.
4. Create the SSH key `GitHub SSH (workstation)` in the personal Private vault, and write its public half to `~/.ssh/github_workstation.pub`. The vault is searched by exact title first, and a failed search stops the run instead of creating a second key. No private key file is ever written.
5. Register the key with GitHub through the GitHub CLI, after the one-time-code approval, then drop the key-registration scope from the token and verify the SSH handshake through the 1Password agent. A token that keeps the scope stops the run with the command to remove it, and a repeated run finishes the removal.
6. Clone the private repository to `~/.config/mise` over SSH and set its hook directory. An existing checkout of the same remote is fast-forwarded; any other non-empty directory stops the run with its path.
7. Hand off to `~/.config/mise/bin/ws bootstrap`.

Commands the starter runs on its own have time limits, so a stuck `op`, `gh`, or `git` ends the run with a message. A command a person is completing, such as the GitHub sign-in, has none.

A human step runs only when its check fails. The starter then opens the app or settings pane, states the action, waits for Enter, checks again, and repeats until the check passes:

| Step | Check |
|------|-------|
| Sign in to the personal 1Password account | `op account list` lists the account |
| Turn on the command-line integration and the SSH agent | `op vault list` succeeds without a prompt and the agent socket exists |
| Approve the GitHub sign-in with a one-time code | `gh auth status` shows the key-registration scope |

`WS_REPO_URL` replaces the clone address, which the clean-machine proof uses to clone from a host mirror.

### On Windows

`start.ps1` is the only PowerShell, and it needs only Windows PowerShell 5.1. In order, it:

1. stops with exit status 2, before changing anything, if `-PersonalAccount` is missing or malformed or the computer is not an x64 Windows 11 client edition (build 22631 or later) with virtualization turned on;
2. takes the named lock `Local\workstation-setup`, so two runs never overlap, and stops with exit status 3 if another run holds it;
3. downloads a pinned mise release, checks its SHA-256, and installs it into `%USERPROFILE%\.local\bin`;
4. makes sure winget works, repairing it if it is missing;
5. installs the pinned Python, uv, and GitHub CLI through mise;
6. downloads `start.sha256` and checks it against the checksum pinned in `start.ps1`, downloads each file it lists, and checks every one;
7. runs `start.py` under that Python.

Every download goes over HTTPS with at least TLS 1.2, a time limit, and three tries, and a redirect to another scheme is refused. `start.ps1` keeps a copy of itself in `%LOCALAPPDATA%\workstation-bootstrap`, where the restart entry runs it from.

`start.py` takes the same steps as on a Mac, with these differences:

1. **Windows Subsystem for Linux comes first.** If it is off, Windows asks for administrator approval after the starter says why, and the starter turns it on. When Windows needs a restart, the starter offers a numbered choice. Choosing to restart registers a `RunOnce` entry that runs `start.ps1` again at the next sign-in and schedules `shutdown.exe` after 15 seconds. Declining ends the run with exit status 4 and the instruction to restart and run the same command again.
2. **The Windows OpenSSH agent service is stopped and disabled**, after the starter says why and Windows asks for administrator approval, because 1Password's SSH agent needs the pipe that service holds. The Windows OpenSSH client must be present.
3. **The key check is `ssh-add -l`** against that pipe, and every `ssh` and `git` call uses the Windows OpenSSH client with the same pinned host keys and `NUL` as the null device.
4. **The hand-off is `bin\ws.cmd`** in the cloned repository.

Two environment variables exist for the clean-machine proof, which runs on a hosted Windows Server image: `WS_ALLOW_SERVER_EDITION=1` relaxes only the client-edition test, and `WS_KEEP_SSH_AGENT_SERVICE=1` keeps the agent service so a stand-in key can use it. `WS_SKIP_SUPPORT_CHECK=1` skips the whole support test, for the Arm job.

## Layout

| Path | Holds |
|------|-------|
| `start.sh` | The only Bash: checks the Mac and installs mise, Python, uv, and gh, then runs `start.py` |
| `start.ps1` | The only PowerShell: the same for Windows, and the restart entry's target |
| `start.py` | The entrypoint: argument parsing and the order of the steps |
| `start_base.py`, `start_config.py`, `start_packages.py`, `start_identity.py`, `start_clone.py` | Shared plumbing, the settings loader, the package install, the identity steps, and the clone and hand-off |
| `start_windows.py` | The Windows host: WSL and the restart, elevation, the SSH agent service, and PATH |
| `start.toml`, `stage1.toml`, `stage1-windows.toml` | The starter's settings and its package lists |
| `github_known_hosts` | GitHub's pinned SSH host keys |
| `start.sha256` | The checksum of each file `start.sh` downloads |
| `prove.py` | The proof engine's command line, with the `prove_engine/` package |
| `scripts/` | `check.sh` (every check), `pins.sh` (the release pins), and the commit and agent-file checks |
| `tests/` | The pytest suite |

The starter modules are flat siblings of `start.py`, not a package, because `start.sh` and `start.ps1` download exactly the files in `start.sha256` into one folder and runs `start.py` from it, where Python finds the siblings next to it. A new starter module must be added to `STARTER_FILES` in `scripts/pins.sh`, and a test fails when it is not.

## Releasing a starter change

`start.sh` and `start.ps1` each pin one checksum, the one of `start.sha256`, which lists the checksum of every other starter file. A change is released in three commits, each a [Conventional Commit](#commit-messages) with a body:

```sh
# 1. Commit the changed starter files, e.g. "fix(start): ...", then rewrite the manifest
scripts/pins.sh manifest
git add start.sha256
git commit -m "build(manifest): refresh starter manifest" \
  -m "The starter files changed, so their checksums in the manifest changed."
# 2. Point start.sh and start.ps1 at that commit and the manifest
scripts/pins.sh release
git add start.sh start.ps1
git commit -m "chore(release): pin starter files" \
  -m "start.sh and start.ps1 now pin the commit that holds the refreshed manifest."
# 3. After the push, print the commit and checksums to use in the bootstrap commands
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

`scripts/check.sh` runs shellcheck over every tracked `*.sh` file, PSScriptAnalyzer over `start.ps1`, ruff and mypy over the Python, the pytest suite through uv, and a gitleaks scan of the full history. PSScriptAnalyzer runs through a local `pwsh` when there is one and otherwise through the pinned PowerShell container image, so a Mac needs only Docker. The unit tests cover the engine's pure logic and need neither Tart nor a virtual machine. GitHub Actions runs the same script on every push and pull request: every check on Linux, `scripts/check.sh bash32` on macOS, where `/bin/bash` is the 3.2 that `start.sh` must run under, and `scripts/check.sh windows` on `windows-2025`, which runs `start.ps1` under Windows PowerShell 5.1 and the Windows host steps against real Windows commands and the real registry, with stand-ins for the network and the commands that would change the machine.

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

Claude Code reads `AGENTS.md`, runs sessions as the `starter-maintainer` agent in `.claude/agents/` (the `agent` key in `.claude/settings.json` makes it the default), and loads the `change-starter` skill under `.claude/skills/`; there is no `CLAUDE.md`, and no agent or skill is a symlink, because either one breaks how Claude Code loads them. `scripts/check.sh agent-files` fails when the default agent names a file that is missing or a symlink. The skill covers the checks above, the release flow, and reporting the new pin.
