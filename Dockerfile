# syntax=docker/dockerfile:1
# Official images don't ship freethreaded 3.15t yet — use standard 3.15.
FROM python:3.15-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings.production

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Writable home outside the root-owned source tree (gunicorn writes ~/.gunicorn).
RUN groupadd --system app \
    && useradd --system --gid app --create-home --home-dir /home/app --shell /usr/sbin/nologin app

# Dependency layer (cached until lock/project metadata change)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY . .
RUN uv sync --frozen --no-dev \
    && DJANGO_SECRET_KEY=build-only DJANGO_ALLOWED_HOSTS=localhost \
       python manage.py collectstatic --noinput \
    && mkdir -p /app/staticfiles \
    && chown -R app:app /app/staticfiles \
    && chmod +x /app/docker/entrypoint.sh

# Entrypoint starts as root, then drops to app before migrate/gunicorn.
# Runtime requires reachable PostgreSQL (POSTGRES_* env vars).
EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "1", "--no-control-socket"]
