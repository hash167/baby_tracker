# syntax=docker/dockerfile:1
# Official images don't ship freethreaded 3.15t yet — use standard 3.15.
FROM python:3.15-slim-bookworm

COPY --from=ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app \
    DJANGO_SETTINGS_MODULE=config.settings.production

WORKDIR /app

# Bookworm's postgresql-client is 15. pg_dump 15 cannot dump a PostgreSQL 16 server.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl gnupg \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
        https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && . /etc/os-release \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt ${VERSION_CODENAME}-pgdg main" \
        > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends libpq5 postgresql-client-16 openssl \
    && rm -rf /var/lib/apt/lists/*

# Writable home outside the root-owned source tree (gunicorn writes ~/.gunicorn).
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --create-home --home-dir /home/app --shell /usr/sbin/nologin app

# Dependency layer (cached until lock/project metadata change)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY . .
RUN uv sync --frozen --no-dev \
    && DJANGO_SECRET_KEY=build-only DJANGO_ALLOWED_HOSTS=localhost \
       POSTGRES_HOST=localhost POSTGRES_DB=build \
       POSTGRES_USER=build POSTGRES_PASSWORD=build \
       python manage.py collectstatic --noinput \
    && mkdir -p /app/staticfiles \
    && chown -R app:app /app/staticfiles \
    && chmod +x /app/docker/entrypoint.sh /app/docker/migrate.py

# Entrypoint starts as root, prepares data directories, then drops to app.
# Runtime requires reachable PostgreSQL (POSTGRES_* env vars).
# Run `python /app/docker/migrate.py` before serving traffic.
EXPOSE 8000

# Migrations are not run here. Use `python /app/docker/migrate.py` first.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).read()"

ENTRYPOINT ["/app/docker/entrypoint.sh"]
# Access logs are off. A default access line would include invite tokens in the path.
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--no-control-socket"]
