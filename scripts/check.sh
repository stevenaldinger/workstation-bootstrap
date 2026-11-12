#!/usr/bin/env bash
set -euo pipefail

# =============================================================================
# Repository checks
# =============================================================================
# Owns the check inventory. Local runs and GitHub Actions both call this script, so
# a check is added here once and runs everywhere.
#
# Usage:
#   scripts/check.sh               # Every check, reporting each failure
#   scripts/check.sh shellcheck    # Lint every tracked shell script and hook
#   scripts/check.sh python        # ruff, ruff format, and mypy through uv
#   scripts/check.sh tests         # Run the pytest suite through uv
#   scripts/check.sh secrets       # Scan the full Git history with gitleaks
#   scripts/check.sh -h            # Show help
#
# What it does:
#   1. Runs the named check, or every check in ALL_CHECKS for `all`.
#   2. For `all`, runs the remaining checks after a failure and lists every failed check.
#
# Design decisions:
#   - shellcheck, uv, and gitleaks come from the path; mise.toml pins them.
#   - The ruff, mypy, and pytest versions are pinned here, so Python checks need no
#     project or lock file; tool settings live in pyproject.toml.
# =============================================================================

PYTEST_VERSION="9.2.0"
RUFF_VERSION="0.17.4"
MYPY_VERSION="2.4.1"
ALL_CHECKS=(shellcheck python tests secrets)

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

show_help() {
  cat <<EOF
Usage: $(basename "$0") [shellcheck|python|tests|secrets|all]

Runs the repository checks. With no argument, runs every check and reports each failure.

Options:
  -h, --help    Show this help message and exit
EOF
}

run_shellcheck() {
  local files=()
  while IFS= read -r file; do
    files+=("${file}")
  done < <(git -C "${REPO_ROOT}" ls-files '*.sh' '.githooks/*')
  if [[ ${#files[@]} -eq 0 ]]; then
    echo "error: no tracked shell scripts found" >&2
    return 1
  fi
  (cd "${REPO_ROOT}" && shellcheck -x "${files[@]}")
}

# Each command runs even after an earlier one fails, so one run reports everything.
run_python() {
  local status=0
  (
    cd "${REPO_ROOT}"
    uv run --no-project --with "ruff==${RUFF_VERSION}" ruff check . || status=1
    uv run --no-project --with "ruff==${RUFF_VERSION}" ruff format --check . || status=1
    uv run --no-project --with "mypy==${MYPY_VERSION}" --with "pytest==${PYTEST_VERSION}" mypy || status=1
    exit "${status}"
  )
}

run_tests() {
  (cd "${REPO_ROOT}" && uv run --no-project --with "pytest==${PYTEST_VERSION}" pytest -q tests)
}

run_secrets() {
  (cd "${REPO_ROOT}" && gitleaks git . --redact --no-banner --config .gitleaks.toml)
}

run_named() {
  case "$1" in
    shellcheck) run_shellcheck ;;
    python) run_python ;;
    tests) run_tests ;;
    secrets) run_secrets ;;
    *) return 2 ;;
  esac
}

run_all() {
  local failed=()
  local name
  for name in "${ALL_CHECKS[@]}"; do
    echo "==> ${name}"
    if ! run_named "${name}"; then
      failed+=("${name}")
    fi
  done
  if [[ ${#failed[@]} -gt 0 ]]; then
    echo "FAILED: ${failed[*]}" >&2
    return 1
  fi
  echo "All checks passed: ${ALL_CHECKS[*]}"
}

main() {
  local target="${1:-all}"
  case "${target}" in
    --help | -h)
      show_help
      ;;
    all)
      run_all
      ;;
    shellcheck | python | tests | secrets)
      run_named "${target}"
      ;;
    *)
      echo "usage: scripts/check.sh [shellcheck|python|tests|secrets|all]" >&2
      return 2
      ;;
  esac
}

main "$@"
