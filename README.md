# Baby Tracker

Baby Tracker is a mobile-friendly web app for shared baby care. Parents and caregivers will use one site to record diapers and breastfeeding and to read a shared timeline. Accounts are planned to be invite-only, with an admin who invites people, changes roles, and deactivates accounts.

The running code is the Django skeleton from [Django project skeleton, settings and container image](https://github.com/hash167/baby_tracker/issues/7), merged in [pull request #43](https://github.com/hash167/baby_tracker/pull/43), plus local PostgreSQL, pytest, and GitHub Actions CI. The app serves a home page and `/healthz`. Accounts, diaper logging, feeding timers, the shared timeline, CSV import, and deployment are still planned. They are specified in the epics below and are not part of the app you can run today.

## What the app will do

These behaviors are the product. They are not implemented yet.

- **Accounts.** Invite-only accounts for parents and caregivers. An admin manages invitations, roles, and deactivation. Authorization is checked on the server. The default policy is that parents manage every entry, and caregivers can view the shared history and add or edit their own entries.
- **Diapers.** Each diaper event has a type (wet, dirty, both, or dry) and a timestamp.
- **Breastfeeding.** A session records left and right sides. Start, pause, switch side, and finish are saved on the server. The on-screen timer is computed from those timestamps, so a phone lock or reload keeps the same session. A feed can also be entered by hand as time on each side.
- **Shared timeline.** The household sees one daily history of these events.
- **Nara Baby CSV import.** An export can be staged, validated, previewed, and committed, with duplicates skipped by source activity id. Files used in this repository will be synthetic. A real family export stays out of git, issues, and CI.

The first version does not include full offline writing. The interface is server-rendered HTML, HTMX, and plain JavaScript.

## Roadmap

Work is grouped into three stages. The stage names match the `release:` labels on the issues. Detail lives in the epics; this is only the map.

**Accounts and diapers.** Set up the project, then add invite-only accounts and diaper events on a shared timeline. This stage also starts deployment: build and deliver an image, run the app with a private PostgreSQL database on k3s, publish HTTPS through a Cloudflare Tunnel, and export redacted OpenTelemetry. The skeleton in this repository is the beginning of that stage. The other tickets are open.

**Feeding.** Add breastfeeding: server-persisted left and right timers, manual per-side entries, and feeding summaries on the timeline. A check on a real phone is planned once the timer and the public HTTPS route exist, together with an installable page.

**Import and launch.** Add the Nara Baby CSV import (preview, deduplication, and a batch rollback), then the launch work: Honeycomb dashboards and alarms, an external availability check, encrypted backups with a restore drill, and a release smoke-check list.

Deployment and telemetry start during accounts and diapers. They are not deferred until launch. The phone check sits with feeding. Dashboards, the external check, backups, and the launch list sit with import and launch. That split is described in the deployment epic.

## Epics

| Epic | Stage |
| --- | --- |
| [Epic: Project foundation & CI](https://github.com/hash167/baby_tracker/issues/1) | Accounts and diapers. Project skeleton, planned PostgreSQL for tests and deployment, core models, CI, security scanning, and baseline telemetry. |
| [Epic: Accounts & access](https://github.com/hash167/baby_tracker/issues/2) | Accounts and diapers. Invite-only login, admin / parent / caregiver roles, and session revocation. |
| [Epic: Diaper tracking & shared timeline](https://github.com/hash167/baby_tracker/issues/3) | Accounts and diapers. Diaper types and timestamps, corrections, and a shared daily timeline. |
| [Epic: Breastfeeding tracking](https://github.com/hash167/baby_tracker/issues/4) | Feeding. Left/right sessions stored on the server, a plain JavaScript timer, and manual entries. |
| [Epic: Nara Baby CSV import](https://github.com/hash167/baby_tracker/issues/5) | Import and launch. Previewable, deduplicated import. Fixtures in the repo stay synthetic. |
| [Epic: Deployment, observability & reliability](https://github.com/hash167/baby_tracker/issues/6) | Starts in accounts and diapers (k3s, tunnel, baseline telemetry). Phone validation is in feeding. Dashboards, alarms, backups, and launch checks are in import and launch. |

## Stack

The agreed stack is Python and Django. Pages are rendered on the server. The browser stack is HTMX and plain JavaScript. HTMX is the vendored file [`static/js/htmx.min.js`](static/js/htmx.min.js). [`templates/base.html`](templates/base.html) loads that file and [`static/js/app.js`](static/js/app.js), and sets `hx-boost="true"` on the page body. `app.js` is empty, and the diaper, feeding, and import screens are still to be built. The app has no Node.js build, no Next.js app, and no TypeScript. The Playwright harness in [`e2e/`](e2e/) is plain TypeScript and is only for browser tests.

PostgreSQL 16 is the database for local development, pytest, and production settings. SQLite is not supported; settings call `require_postgresql()` and raise if the engine is anything else. Local settings may use compose-compatible `POSTGRES_*` defaults. Production settings require `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_HOST` — there are no silent production defaults. See [issue #8](https://github.com/hash167/baby_tracker/issues/8).

Versions locked in [`uv.lock`](uv.lock) today:

- Python 3.15 ([`.python-version`](.python-version); `requires-python` is `>=3.15` in [`pyproject.toml`](pyproject.toml))
- Django 6.1.3
- django-htmx 1.29.0
- gunicorn 26.2.0
- WhiteNoise 6.12.0
- psycopg 3.3.x
- django-debug-toolbar 8.0.0, pytest, pytest-django, factory-boy, and ruff in the `dev` group

## Run it locally

Install [uv](https://docs.astral.sh/uv/), Python 3.15, and Docker. From the repository root, start PostgreSQL 16, then the app:

```bash
docker compose up -d --wait db
uv sync
uv run python manage.py check
uv run python manage.py migrate
uv run python manage.py runserver
```

`uv sync` installs the locked dependencies, including the `dev` group. Local settings import `debug_toolbar`, so leave that group installed. [`manage.py`](manage.py) sets `DJANGO_SETTINGS_MODULE` to `config.settings.local`. Local defaults match compose (`POSTGRES_HOST=127.0.0.1`, database/user/password `baby_tracker`). Copy [`.env.example`](.env.example) to `.env` only when you need overrides. When `DJANGO_SECRET_KEY` is unset, local settings use a development-only key. Allowed hosts are `localhost` and `127.0.0.1`. The local email backend writes to the console. Compose publishes Postgres on `127.0.0.1:5432` only (not on all interfaces).

`migrate` applies Django's built-in tables and the tracker tables for households, baby profiles, shared events, and audit history.

`psycopg` loads the system `libpq` shared library (there is no usable `psycopg-binary` wheel for Python 3.15 yet):

- **Debian/Ubuntu:** `sudo apt-get install -y libpq5`
- **macOS:** `brew install libpq`, then put the keg-only library on the linker fallback path before `uv run`:

```bash
export DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix libpq)/lib${DYLD_FALLBACK_LIBRARY_PATH:+:$DYLD_FALLBACK_LIBRARY_PATH}"
```

[`scripts/test.sh`](scripts/test.sh) sets that macOS path automatically when Homebrew `libpq` is installed.

`runserver` listens on `http://127.0.0.1:8000`.

- `/` renders the skeleton home page, [`templates/tracker/home.html`](templates/tracker/home.html). The heading is "Home" and the document title is "Home · Baby Tracker".
- `/healthz` returns plain text `ok` and does not query the database. Middleware answers this path before host and HTTPS checks.
- `/admin/` is Django's built-in admin. No baby-care models are registered. The planned admin screens for invitations, roles, and deactivation are separate work and are not built.

`uv run python manage.py check` is Django's system check. It does not test product behavior.

## Container image

The optional image uses production settings and requires a reachable PostgreSQL. It is not a k3s deploy.

```bash
docker compose up -d --wait db
docker build -t baby-tracker .
docker run --rm -p 8000:8000 \
  --add-host=host.docker.internal:host-gateway \
  -e DJANGO_SECRET_KEY=change-me \
  -e DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1 \
  -e POSTGRES_HOST=host.docker.internal \
  -e POSTGRES_PORT=5432 \
  -e POSTGRES_DB=baby_tracker \
  -e POSTGRES_USER=baby_tracker \
  -e POSTGRES_PASSWORD=baby_tracker \
  baby-tracker
```

The [Dockerfile](Dockerfile) sets `DJANGO_SETTINGS_MODULE=config.settings.production` and installs `libpq5`. [`config/wsgi.py`](config/wsgi.py) and [`config/asgi.py`](config/asgi.py) default to that module as well. Production settings require `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, and explicit `POSTGRES_DB` / `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_HOST`. [`docker/entrypoint.sh`](docker/entrypoint.sh) drops to the `app` user, then [`docker/migrate.py`](docker/migrate.py) waits for Postgres, takes a PostgreSQL advisory lock, runs migrations, and releases the lock before gunicorn starts (`config.wsgi:application`, port 8000, one worker).

`GET /healthz` on the published port returns `ok`.

Set `DJANGO_BEHIND_PROXY=true` only when a TLS-terminating proxy overwrites `X-Forwarded-Proto` and clients cannot reach gunicorn's port directly. The flag turns on the secure proxy header, the HTTPS redirect, secure cookies, and HSTS together. `/healthz` is exempt from that redirect. The public hostname, `CSRF_TRUSTED_ORIGINS`, and a content security policy are still [issue #36](https://github.com/hash167/baby_tracker/issues/36).

`./scripts/deploy_check.sh` runs `manage.py check --deploy --fail-level WARNING` with `DJANGO_SETTINGS_MODULE=config.settings.production` and `DJANGO_BEHIND_PROXY=true`. That command must pass. The plain-HTTP container command above does not set the proxy flag, and `./scripts/deploy_check.sh --without-proxy` is expected to fail those HTTPS checks.

Names and placeholders for these variables are in [`.env.example`](.env.example). Do not commit `.env`.

## Planned hosting and operations

None of this is configured in the repository.

The intended layout is a single app container and a private PostgreSQL database on k3s. Public HTTPS is planned through an existing Cloudflare Tunnel. The planned address is `https://baby.thewalawalas.com`. That hostname is a target in the deployment epic. It was not checked as a live site while writing this file.

OpenTelemetry is planned to export to Honeycomb, with alarms for sustained failures and a separate external availability check. Traces must leave out names, notes, feeding contents, and import contents. Dashboards, that external check, encrypted backups with a restore drill, and the launch checklist are tracked in [Epic: Deployment, observability & reliability](https://github.com/hash167/baby_tracker/issues/6).

## Tests

pytest and pytest-django run against PostgreSQL only (test DB created and destroyed each run; do not pass `--reuse-db` unless you intend to keep it).

```bash
./scripts/test.sh
# or, with Postgres already up:
uv run pytest
```

[`tests/test_database_engine.py`](tests/test_database_engine.py) asserts the engine is PostgreSQL and that SQLite is rejected. [`tests/test_db_concurrency.py`](tests/test_db_concurrency.py) commits a synthetic user and reads it on a second DB connection (`transaction=True`). [`tests/factories.py`](tests/factories.py) builds caregivers, households, babies, and events with Faker names and `@example.com` emails. Household, baby, and event factories persist the tracker models. Caregivers are Django users. Tests and factories use synthetic data only.

Playwright needs Node.js 22. It loads Django's admin login at `/admin/login/` (the product login page is still planned). Start PostgreSQL, migrate, and `runserver`, then:

```bash
./scripts/e2e.sh
```

## Continuous integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every pull request and on pushes to `main`. A temporary PostgreSQL 16 service backs pytest, migration checks, and Playwright. No GitHub Actions secrets are used. The database user, name, and password are the same public local values as [`docker-compose.yml`](docker-compose.yml).

| Status check | Fails when |
| --- | --- |
| `CI / lint` | `ruff check` or `ruff format --check` fails |
| `CI / pytest` | pytest fails against that PostgreSQL service |
| `CI / migrations` | `makemigrations --check` finds a missing migration, or `migrate` from an empty database fails |
| `CI / playwright` | the Playwright login-page smoke test fails |
| `CI / dependency-audit` | `pip-audit` reports a known vulnerability in `uv.lock` (runtime and dev groups) |
| `CI / bandit` | Bandit reports an issue in first-party Python |
| `CI / deploy-check` | `manage.py check --deploy --fail-level WARNING` fails with production settings and `DJANGO_BEHIND_PROXY=true` |
| `CI / secrets` | gitleaks finds a secret in the working tree or any commit in the clone, a CSV export sits outside `tests/fixtures/`, a database dump is tracked, or the pre-commit hooks fail |

A pull request with a failing unit test, a missing migration, a failing Playwright test, a vulnerable locked dependency, a Bandit finding, a failing deploy check, or a secret / export / dump finding fails CI. Playwright uploads `e2e/test-results/` (traces and failure screenshots) only after a browser failure. Those artifacts are the login page, not household data. The secrets job checks out full history (`fetch-depth: 0`). Gitleaks then uses its default `git log --full-history --all`, so a secret introduced in an earlier commit of the branch still fails the job after it is deleted from the tip. A separate `--no-git` pass scans the working tree.

The lint, pytest, migrations, and Playwright names are the required status checks called out when CI was added. The four security jobs fail the same workflow. Enabling any of them in branch protection is a repository setting and is not part of this change.

## Security scanning

[`.github/dependabot.yml`](.github/dependabot.yml) asks Dependabot to update the uv lockfile, the Dockerfile, `docker-compose.yml`, and GitHub Actions weekly. [`.pre-commit-config.yaml`](.pre-commit-config.yaml) runs gitleaks on staged changes and blocks CSV exports outside `tests/fixtures/` plus database dumps (`.sql`, `.dump`, `.sqlite`, `.db`, and the compressed forms listed in [`scripts/forbid_data_files.py`](scripts/forbid_data_files.py)).

```bash
./scripts/audit_dependencies.sh
./scripts/bandit.sh
./scripts/deploy_check.sh
./scripts/scan_secrets.sh
python3 scripts/forbid_data_files.py --all
uvx pre-commit==4.7.0 install
uvx pre-commit==4.7.0 run --all-files
```

`scan_secrets.sh` installs gitleaks 8.30.1 after checking the published checksum, then scans the working tree and every commit in the clone. Its summary prints the rule, path, and commit. It does not print secret values. Pinned versions: pip-audit 2.10.1, Bandit 1.9.4, gitleaks 8.30.1, pre-commit 4.7.0.
