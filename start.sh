#!/bin/bash
set -euo pipefail

# =============================================================================
# Workstation bootstrap, stage 1
# =============================================================================
# Prepares a fresh Mac to run start.py: checks the Mac, installs a checksum-pinned mise,
# installs the pinned Python, uv, and GitHub CLI through mise, downloads the starter files
# named in start.sha256, verifies them, and hands off to start.py.
#
# Usage:
#   /bin/bash start.sh --personal-account <email or sign-in address> [start.py options]
#   /bin/bash start.sh -h
#
# What it does:
#   1. Checks the Mac is Apple silicon on macOS 26 or 27 and validates the arguments.
#   2. Asks for administrator access once and keeps it alive while the script runs.
#   3. Downloads mise, checks it against MISE_SHA256, and installs it.
#   4. Has mise install the pinned Python, uv, and gh (mise verifies those downloads).
#   5. Downloads start.sha256, checks it against MANIFEST_SHA256, then downloads and checks
#      every file it lists.
#   6. Runs start.py with the pinned Python, uv, and gh on PATH.
#
# Design decisions:
#   - The shebang is /bin/bash, not env bash: a fresh Mac has only the system bash 3.2, so
#     nothing here may need a newer bash.
#   - Only the mise archive and the manifest are pinned by checksum in this file. The
#     manifest pins every starter file, and mise checks what it downloads (see the README's
#     trust boundary section).
#   - Homebrew is not required. mise installs the brew and brew-cask packages itself and
#     /opt/homebrew/bin is added to PATH for the commands it links there.
# =============================================================================

# MISE_SHA256 is the macOS arm64 archive's line in the release's SHASUMS256.txt.
MISE_VERSION="2026.11.7"
MISE_SHA256="92f1b9f68b6031a9f77938a0ff8dfd7a77fe9cd918c5b09d0ed434f06ebbd3f2"
PYTHON_VERSION="3.15.2"
UV_VERSION="0.13.7"
GH_VERSION="2.103.3"
SUPPORTED_MAJORS=" 26 27 "
BREW_BIN="/opt/homebrew/bin"
KEEPALIVE_SECONDS=50
# The starter files are listed, with their checksums, in start.sha256 at the commit that last
# changed them. Only that manifest's own checksum is pinned here.
FILES_COMMIT="4dfed37797e9f4bddd4184e8dfacf325c7aa3455"
MANIFEST_SHA256="99ed1cd052ec2d8f6624b239ce109322f38cdaaf4ced46cecfc8482dcc6ffe32"
RAW_URL="https://raw.githubusercontent.com/stevenaldinger/workstation-bootstrap/${FILES_COMMIT}"
# https only, including redirects, and no older TLS than 1.2; a stalled download times out.
CURL_OPTIONS=(--fail --silent --show-error --location --proto '=https' --proto-redir '=https'
  --tlsv1.2 --connect-timeout 15 --max-time 300 --retry 3)

WORK="${HOME}/.cache/workstation-bootstrap"

# =============================================================================
# Output
# =============================================================================

die() {
  echo "start.sh: $1" >&2
  exit "${2:-1}"
}

show_help() {
  cat <<'USAGE'
usage: /bin/bash start.sh --personal-account <email or sign-in address> [start.py options]

Prepares this Mac and runs the workstation starter. Options other than -h are passed to
start.py, which passes the ones it does not know on to `ws bootstrap`.

  --personal-account VALUE  email or sign-in address of the personal 1Password account
  -h, --help                show this help and exit
USAGE
}

# =============================================================================
# Checks
# =============================================================================

# Reads the arguments before anything is installed, so a typo costs nothing.
validate_args() {
  local account=""
  while [ "$#" -gt 0 ]; do
    case "$1" in
      -h | --help)
        show_help
        exit 0
        ;;
      --personal-account)
        [ "$#" -ge 2 ] || die "--personal-account needs a value" 2
        account="$2"
        shift 2
        ;;
      --personal-account=*)
        account="${1#*=}"
        shift
        ;;
      *) shift ;;
    esac
  done
  [ -n "${account}" ] || die "--personal-account is required; see -h" 2
  case "${account}" in
    -* | *[[:space:]]*) die "--personal-account needs an email or sign-in address, got: ${account}" 2 ;;
  esac
}

check_mac() {
  [ "$(uname -m)" = "arm64" ] || die "unsupported architecture $(uname -m); an Apple silicon Mac is required" 2
  local major
  major="$(sw_vers -productVersion | cut -d. -f1)"
  case "${SUPPORTED_MAJORS}" in
    *" ${major} "*) ;;
    *) die "unsupported macOS ${major}; macOS 26 or 27 is required" 2 ;;
  esac
}

# =============================================================================
# Steps
# =============================================================================

# Asks for administrator access, then refreshes it until this script exits or a refresh fails.
keep_sudo_alive() {
  sudo -v || die "administrator access is required"
  (
    while kill -0 "$$" 2>/dev/null; do
      sudo -n true || exit 0
      sleep "${KEEPALIVE_SECONDS}"
    done
  ) >/dev/null 2>&1 </dev/null &
}

download() { # url destination
  curl "${CURL_OPTIONS[@]}" -o "$2" "$1" || die "download failed: $1"
}

fetch() { # url destination sha256
  download "$1" "$2"
  echo "$3  $2" | shasum -a 256 -c - >/dev/null 2>&1 || {
    rm -f "$2"
    die "checksum mismatch: $1"
  }
}

install_mise() {
  mkdir -p "${WORK}" "${HOME}/.local/bin"
  fetch "https://github.com/jdx/mise/releases/download/v${MISE_VERSION}/mise-v${MISE_VERSION}-macos-arm64.tar.gz" \
    "${WORK}/mise.tar.gz" "${MISE_SHA256}"
  tar -xzf "${WORK}/mise.tar.gz" -C "${WORK}"
  install -m 755 "${WORK}/mise/bin/mise" "${HOME}/.local/bin/mise"
}

install_runtimes() {
  export PATH="${HOME}/.local/bin:${BREW_BIN}:${PATH}"
  # MISE_YES answers mise's trust prompts for this install only, not for start.py.
  MISE_YES=1 mise install "python@${PYTHON_VERSION}" "uv@${UV_VERSION}" "gh@${GH_VERSION}"
}

fetch_starter_files() {
  local name
  fetch "${RAW_URL}/start.sha256" "${WORK}/start.sha256" "${MANIFEST_SHA256}"
  while read -r _ name; do
    download "${RAW_URL}/${name}" "${WORK}/${name}"
  done <"${WORK}/start.sha256"
  (cd "${WORK}" && shasum -a 256 -c start.sha256 >/dev/null 2>&1) || die "checksum mismatch in the starter files"
}

# =============================================================================
# Main
# =============================================================================

main() {
  validate_args "$@"
  check_mac
  keep_sudo_alive
  install_mise
  install_runtimes
  fetch_starter_files
  exec mise exec "python@${PYTHON_VERSION}" "uv@${UV_VERSION}" "gh@${GH_VERSION}" -- \
    python "${WORK}/start.py" --stage1 "${WORK}/stage1.toml" "$@"
}

main "$@"
