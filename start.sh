#!/bin/bash
set -euo pipefail

# Stage 1 entrypoint. A fresh Mac has only /bin/bash 3.2, so this script does the least that
# must happen before a runtime exists: check the Mac, install a checksum-pinned mise, install
# mise's Python and uv, and hand off to start.py. Every download is verified before it is used.

MISE_VERSION="2026.11.6"
MISE_SHA256="74cf55e92f6c6b1f166781c36db35db9420a4cbf40d62d85c1b9d5dc76e0ba72"
PYTHON_VERSION="3.15.2"
UV_VERSION="0.13.7"
SUPPORTED_MAJORS=" 26 27 "
# The starter files are listed, with their checksums, in start.sha256 at the commit that last
# changed them. Only that manifest's own checksum is pinned here.
FILES_COMMIT="0000000000000000000000000000000000000000"
MANIFEST_SHA256="0000000000000000000000000000000000000000000000000000000000000000"
RAW_URL="https://raw.githubusercontent.com/stevenaldinger/workstation-bootstrap/${FILES_COMMIT}"

die() { echo "start.sh: $1" >&2; exit "${2:-1}"; }

fetch() { # url destination sha256
  curl -fsSL -o "$2" "$1" || die "download failed: $1"
  echo "$3  $2" | shasum -a 256 -c - >/dev/null 2>&1 || { rm -f "$2"; die "checksum mismatch: $1"; }
}

[ "$(uname -m)" = "arm64" ] || die "unsupported architecture $(uname -m); an Apple silicon Mac is required" 2
major="$(sw_vers -productVersion | cut -d. -f1)"
case "${SUPPORTED_MAJORS}" in *" ${major} "*) ;; *) die "unsupported macOS ${major}; macOS 26 or 27 is required" 2 ;; esac

sudo -v || die "administrator access is required"
(while kill -0 "$$" 2>/dev/null; do sudo -n true; sleep 50; done) >/dev/null 2>&1 </dev/null &

work="${HOME}/.cache/workstation-bootstrap"
mkdir -p "${work}" "${HOME}/.local/bin"
fetch "https://github.com/jdx/mise/releases/download/v${MISE_VERSION}/mise-v${MISE_VERSION}-macos-arm64.tar.gz" \
  "${work}/mise.tar.gz" "${MISE_SHA256}"
tar -xzf "${work}/mise.tar.gz" -C "${work}"
install -m 755 "${work}/mise/bin/mise" "${HOME}/.local/bin/mise"

export PATH="${HOME}/.local/bin:${WS_BREW_BIN:-/opt/homebrew/bin}:${PATH}" MISE_YES=1
mise install "python@${PYTHON_VERSION}" "uv@${UV_VERSION}"
fetch "${RAW_URL}/start.sha256" "${work}/start.sha256" "${MANIFEST_SHA256}"
while read -r _ name; do
  curl -fsSL -o "${work}/${name}" "${RAW_URL}/${name}" || die "download failed: ${name}"
done < "${work}/start.sha256"
(cd "${work}" && shasum -a 256 -c start.sha256 >/dev/null 2>&1) || die "checksum mismatch in the starter files"
exec mise exec "python@${PYTHON_VERSION}" "uv@${UV_VERSION}" -- python "${work}/start.py" --stage1 "${work}/stage1.toml" "$@"
