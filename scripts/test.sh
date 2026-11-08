#!/usr/bin/env sh
# One-command local test run against PostgreSQL (issue #8).
set -eu
cd "$(dirname "$0")/.."

docker compose up -d --wait db
uv sync
uv run pytest "$@"
