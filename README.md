# workstation-bootstrap

## What this is

`stevenaldinger/workstation-bootstrap` is the public starter for a private workstation setup repository. A new Mac has no GitHub credential, so it cannot clone the private repository directly. The starter runs first, sets up the machine's identity, and then clones the private repository and hands off to it.

The starter holds scripts only. It contains no account identifiers, email addresses, or package lists. The one personal value it needs, the 1Password sign-in address, is passed as a command argument.

This repository currently holds the starter's checks. The starter script, `start.sh`, is added here when it is released.

## Bootstrap command

A Mac runs the starter with one command. It downloads `start.sh` at one commit, checks the file's SHA-256, and runs it only when the hash matches:

```sh
curl -fsSL -o /tmp/ws-start.sh https://raw.githubusercontent.com/stevenaldinger/workstation-bootstrap/<commit>/start.sh \
  && echo "<sha256>  /tmp/ws-start.sh" | shasum -a 256 -c - \
  && /bin/bash /tmp/ws-start.sh --personal-account <sign-in address>
```

The private repository's README holds the pinned `<commit>` and `<sha256>`. Its checks download `start.sh` at that commit and compare hashes, so the starter cannot change without the pin changing too.

## Checks

`scripts/check.sh` runs shellcheck over every tracked `*.sh` file, the pytest suite through uv, and a gitleaks scan of the full history. GitHub Actions runs the same script on every push and pull request.

```sh
mise install
scripts/check.sh
```

`mise install` reads `mise.toml` and installs the pinned gitleaks, Python, shellcheck, and uv.
