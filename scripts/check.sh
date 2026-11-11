#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Repository checks
# =============================================================================
# Runs the same checks locally and in GitHub Actions.
#
# Usage:
#   scripts/check.sh               # Every check
#   scripts/check.sh shellcheck    # Lint every tracked *.sh file
#   scripts/check.sh tests         # Run the pytest suite through uv
#   scripts/check.sh secrets       # Scan the full Git history with gitleaks
#
# Design decisions:
#   - shellcheck, uv, and gitleaks come from the path; mise.toml pins them.
#   - The pytest version is pinned here, so tests need no project or lock file.
# =============================================================================

PYTEST_VERSION="9.2.0"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  echo "usage: scripts/check.sh [shellcheck|tests|secrets|all]" >&2
}

run_shellcheck() {
  local files=()
  while IFS= read -r file; do
    files+=("${file}")
  done < <(git -C "${REPO_ROOT}" ls-files '*.sh')
  if [[ ${#files[@]} -eq 0 ]]; then
    echo "error: no tracked *.sh files found" >&2
    return 1
  fi
  (cd "${REPO_ROOT}" && shellcheck -x "${files[@]}")
}

run_tests() {
  (cd "${REPO_ROOT}" && uv run --no-project --with "pytest==${PYTEST_VERSION}" pytest -q tests)
}

run_secrets() {
  (cd "${REPO_ROOT}" && gitleaks git . --redact --no-banner --config .gitleaks.toml)
}

main() {
  local target="${1:-all}"
  case "${target}" in
    shellcheck) run_shellcheck ;;
    tests) run_tests ;;
    secrets) run_secrets ;;
    all)
      run_shellcheck
      run_tests
      run_secrets
      ;;
    *)
      usage
      return 2
      ;;
  esac
}

main "$@"
