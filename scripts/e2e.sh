#!/usr/bin/env sh
# Playwright browser tests. The app must already be serving BASE_URL.
# Seed synthetic users first: uv run python manage.py seed_e2e
# For the hidden-tab poll check, start the app with TIMELINE_POLL_MS=1000.
set -eu
cd "$(dirname "$0")/.."

base="${BASE_URL:-http://127.0.0.1:8000}"
major="$(node -p 'process.versions.node.split(".")[0]')"
if [ "$major" -lt 22 ]; then
  echo "Playwright needs Node.js 22 or newer (found $(node --version))." >&2
  exit 1
fi

if ! curl -fsS "$base/healthz" >/dev/null; then
  echo "App is not reachable at $base. Start PostgreSQL, migrate, seed_e2e, and runserver first." >&2
  exit 1
fi

cd e2e
if [ ! -d node_modules/@playwright/test ]; then
  npm ci
fi
npx playwright install chromium
BASE_URL="$base" npx playwright test
