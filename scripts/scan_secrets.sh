#!/bin/sh
# Secret scan of the working tree and of every commit in the clone.
# Usage: scripts/scan_secrets.sh [repo-dir]
# Findings are summarized with secret values redacted. This script does not
# print match text.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
root="$(cd "${here}/.." && pwd)"
if [ "${1:-}" != "" ]; then
  root="$(cd "$1" && pwd)"
fi
export PATH="${HOME}/.local/bin:${PATH}"

if ! command -v gitleaks >/dev/null 2>&1 || [ "$(gitleaks version 2>/dev/null || true)" != "8.30.1" ]; then
  "${here}/install_gitleaks.sh"
  export PATH="${HOME}/.local/bin:${PATH}"
fi

hist="$(mktemp)"
tree="$(mktemp)"
trap 'rm -f "$hist" "$tree"' EXIT

set +e
# No --log-opts: gitleaks' default is `git log -p --full-history --all`,
# which walks every commit in the clone. A custom log opt replaces that.
gitleaks detect \
  --source "$root" \
  --redact \
  --report-format json \
  --report-path "$hist" \
  --exit-code 1 \
  >/dev/null 2>&1
hist_status=$?
gitleaks detect \
  --source "$root" \
  --no-git \
  --redact \
  --report-format json \
  --report-path "$tree" \
  --exit-code 1 \
  >/dev/null 2>&1
tree_status=$?
set -e

python3 "${here}/summarize_gitleaks.py" \
  --label history --status "$hist_status" --report "$hist"
python3 "${here}/summarize_gitleaks.py" \
  --label working-tree --status "$tree_status" --report "$tree"

if [ "$hist_status" -ne 0 ] || [ "$tree_status" -ne 0 ]; then
  exit 1
fi
