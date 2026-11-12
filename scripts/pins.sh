#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Starter pins
# =============================================================================
# start.sh pins one checksum: the one of start.sha256, which lists the checksum of every
# other starter file. A starter change is released in three commits:
#
#   1. Commit the changed files.
#   2. scripts/pins.sh manifest   # rewrites start.sha256; commit it
#   3. scripts/pins.sh release    # sets FILES_COMMIT and MANIFEST_SHA256 in start.sh; commit it
#
# After the push, `show` prints the commit and start.sh checksum for the bootstrap command.
#
# Usage:
#   scripts/pins.sh manifest   # Rewrite start.sha256 from the working files
#   scripts/pins.sh release    # Point start.sh at HEAD and the committed manifest
#   scripts/pins.sh show       # Print the commit and checksum for the bootstrap command
# =============================================================================

STARTER_FILES=(start.py start_base.py start_clone.py start_config.py start_identity.py start_packages.py start_windows.py start.toml github_known_hosts stage1.toml stage1-windows.toml)
MANIFEST="start.sha256"
SCRIPT="start.sh"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  echo "usage: scripts/pins.sh [manifest|release|show]" >&2
}

write_manifest() {
  (cd "${REPO_ROOT}" && shasum -a 256 "${STARTER_FILES[@]}" > "${MANIFEST}")
}

release() {
  cd "${REPO_ROOT}"
  if ! git diff --quiet HEAD -- "${STARTER_FILES[@]}" "${MANIFEST}"; then
    echo "error: commit the starter files and ${MANIFEST} before releasing" >&2
    return 1
  fi
  shasum -a 256 -c "${MANIFEST}" > /dev/null || {
    echo "error: ${MANIFEST} is stale; run scripts/pins.sh manifest and commit it" >&2
    return 1
  }
  local commit manifest_sha temporary
  commit="$(git rev-parse HEAD)"
  manifest_sha="$(shasum -a 256 "${MANIFEST}" | cut -d' ' -f1)"
  temporary="$(mktemp)"
  awk -v commit="${commit}" -v sha="${manifest_sha}" '
    /^FILES_COMMIT=/ { print "FILES_COMMIT=\"" commit "\""; next }
    /^MANIFEST_SHA256=/ { print "MANIFEST_SHA256=\"" sha "\""; next }
    { print }
  ' "${SCRIPT}" > "${temporary}"
  cat "${temporary}" > "${SCRIPT}"
  rm -f "${temporary}"
  echo "start.sh now pins ${commit} and manifest ${manifest_sha}; commit it, then run: scripts/pins.sh show"
}

show() {
  cd "${REPO_ROOT}"
  echo "commit:  $(git rev-parse HEAD)"
  echo "sha256:  $(git show "HEAD:${SCRIPT}" | shasum -a 256 | cut -d' ' -f1)"
}

main() {
  case "${1:-}" in
    manifest) write_manifest ;;
    release) release ;;
    show) show ;;
    *)
      usage
      return 2
      ;;
  esac
}

main "$@"
