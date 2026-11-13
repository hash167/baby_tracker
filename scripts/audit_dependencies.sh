#!/bin/sh
# Audit the locked Python dependencies with pip-audit.
# Usage: scripts/audit_dependencies.sh [project-dir]
# Exits non-zero when a known vulnerability is reported.
# Does not install the audited packages.
set -eu
cd "$(dirname "$0")/.."
if [ "${1:-}" != "" ]; then
  cd "$1"
fi

if [ ! -f uv.lock ]; then
  echo "uv.lock not found in $(pwd)" >&2
  exit 2
fi

req="$(mktemp)"
trap 'rm -f "$req"' EXIT
# Hashes come from uv.lock. --disable-pip audits that file without resolving
# or installing. --strict fails the run if any requirement cannot be audited.
uv export \
  --frozen \
  --all-groups \
  --no-emit-project \
  --format requirements-txt \
  --no-header \
  --output-file "$req" \
  >/dev/null

uvx --python 3.15 --from pip-audit==2.10.1 pip-audit \
  --strict \
  --disable-pip \
  --progress-spinner off \
  -r "$req"
