#!/bin/sh
# Django deployment checks against production settings.
#
# Default: DJANGO_BEHIND_PROXY=true (TLS terminated in front of gunicorn).
# That is the configuration `check --deploy` must pass.
# --without-proxy is the plain-HTTP container mode and is expected to fail.
set -eu
cd "$(dirname "$0")/.."

mode="${1:-}"
if [ "$mode" = "--without-proxy" ]; then
  unset DJANGO_BEHIND_PROXY
elif [ -n "$mode" ]; then
  echo "usage: scripts/deploy_check.sh [--without-proxy]" >&2
  exit 2
else
  export DJANGO_BEHIND_PROXY=true
fi

export DJANGO_SETTINGS_MODULE=config.settings.production
export DJANGO_ALLOWED_HOSTS="${DJANGO_ALLOWED_HOSTS:-localhost,127.0.0.1}"
export POSTGRES_HOST="${POSTGRES_HOST:-127.0.0.1}"
export POSTGRES_PORT="${POSTGRES_PORT:-5432}"
export POSTGRES_DB="${POSTGRES_DB:-baby_tracker}"
export POSTGRES_USER="${POSTGRES_USER:-baby_tracker}"
export POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-baby_tracker}"
# Long enough for Django's SECRET_KEY check. Not a credential.
export DJANGO_SECRET_KEY="$(printf 'deploy-check-placeholder-%.0s' 1 2 3 4 5 6)"

uv run python manage.py check --deploy --fail-level WARNING
