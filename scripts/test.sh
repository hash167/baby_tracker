#!/usr/bin/env sh
# One-command local test run against PostgreSQL (issue #8).
set -eu
cd "$(dirname "$0")/.."

# Homebrew libpq is keg-only; help the dynamic linker find it on macOS.
if [ "$(uname -s)" = "Darwin" ] && command -v brew >/dev/null 2>&1; then
  LIBPQ_PREFIX="$(brew --prefix libpq 2>/dev/null || true)"
  if [ -n "${LIBPQ_PREFIX}" ] && [ -d "${LIBPQ_PREFIX}/lib" ]; then
    export DYLD_FALLBACK_LIBRARY_PATH="${LIBPQ_PREFIX}/lib${DYLD_FALLBACK_LIBRARY_PATH:+:$DYLD_FALLBACK_LIBRARY_PATH}"
  fi
fi

docker compose up -d --wait db
uv sync
uv run pytest "$@"
