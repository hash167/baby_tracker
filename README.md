# Baby Tracker

Baby Tracker is a mobile-friendly web app for shared baby care. Parents and caregivers will use one site to record diapers and breastfeeding and to read a shared timeline. Accounts are planned to be invite-only, with an admin who invites people, changes roles, and deactivates accounts.

The running code is the Django skeleton from [Django project skeleton, settings and container image](https://github.com/hash167/baby_tracker/issues/7), merged in [pull request #43](https://github.com/hash167/baby_tracker/pull/43). That skeleton serves a home page and `/healthz`. Accounts, diaper logging, feeding timers, the shared timeline, CSV import, PostgreSQL, the automated test suite, and deployment are still planned. They are specified in the epics below and are not part of the app you can run today.

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

The agreed stack is Python and Django. Pages are rendered on the server. The browser stack is HTMX and plain JavaScript. HTMX is the vendored file [`static/js/htmx.min.js`](static/js/htmx.min.js). [`templates/base.html`](templates/base.html) loads that file and [`static/js/app.js`](static/js/app.js), and sets `hx-boost="true"` on the page body. `app.js` is empty, and the diaper, feeding, and import screens are still to be built. The app has no Node.js build, no Next.js app, and no TypeScript.

PostgreSQL is the planned database for production and for integration tests. Nothing in the repository connects to PostgreSQL yet.

SQLite is allowed only for the skeleton in [issue #7](https://github.com/hash167/baby_tracker/issues/7), so local development and the container can start without a database server. [`config/settings/local.py`](config/settings/local.py) uses the SQLite file `db.sqlite3` from [`config/settings/base.py`](config/settings/base.py). [`config/settings/production.py`](config/settings/production.py) also still uses SQLite, at `DJANGO_DB_PATH` or `data/db.sqlite3`. Later tickets replace the deployed database and the integration-test database with PostgreSQL. The skeleton SQLite file does not stand in for those tests.

Versions locked in [`uv.lock`](uv.lock) today:

- Python 3.15 ([`.python-version`](.python-version); `requires-python` is `>=3.15` in [`pyproject.toml`](pyproject.toml))
- Django 6.1.3
- django-htmx 1.29.0
- gunicorn 26.2.0
- WhiteNoise 6.12.0
- django-debug-toolbar 8.0.0, in the `dev` group, imported only by local settings

## Run it locally

Install [uv](https://docs.astral.sh/uv/) and Python 3.15. uv can install the interpreter named in `.python-version`. From the repository root:

```bash
uv sync
uv run python manage.py check
uv run python manage.py migrate
uv run python manage.py runserver
```

`uv sync` installs the locked dependencies, including the dev group. Local settings import `debug_toolbar`, so leave that group installed. [`manage.py`](manage.py) sets `DJANGO_SETTINGS_MODULE` to `config.settings.local`. This path does not need PostgreSQL or a `.env` file. When `DJANGO_SECRET_KEY` is unset, local settings use a development-only key. Allowed hosts are `localhost` and `127.0.0.1`. The local email backend writes to the console. Nothing in the skeleton sends mail.

`migrate` creates `db.sqlite3` in the repository root and applies Django's built-in tables. The `tracker` app has no models yet. `db.sqlite3` is gitignored.

`runserver` listens on `http://127.0.0.1:8000`.

- `/` renders the skeleton home page, [`templates/tracker/home.html`](templates/tracker/home.html). The heading is "Home" and the document title is "Home · Baby Tracker".
- `/healthz` returns plain text `ok` and does not query the database. Middleware answers this path before host and HTTPS checks.
- `/admin/` is Django's built-in admin. No baby-care models are registered. The planned admin screens for invitations, roles, and deactivation are separate work and are not built.

`uv run python manage.py check` is Django's system check. It does not test product behavior.

## Container image

The optional image is the same skeleton, with production settings and SQLite. It is not a k3s deploy.

```bash
docker build -t baby-tracker .
docker run --rm -p 8000:8000 \
  -e DJANGO_SECRET_KEY=change-me \
  -e DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1 \
  baby-tracker
```

The [Dockerfile](Dockerfile) sets `DJANGO_SETTINGS_MODULE=config.settings.production`. [`config/wsgi.py`](config/wsgi.py) and [`config/asgi.py`](config/asgi.py) default to that module as well. Production settings require `DJANGO_SECRET_KEY` and `DJANGO_ALLOWED_HOSTS`. [`docker/entrypoint.sh`](docker/entrypoint.sh) runs `python manage.py migrate --noinput` under a file lock, then the image starts gunicorn (`config.wsgi:application`, port 8000, one worker). The database file defaults to `/app/data/db.sqlite3` inside the image (`DJANGO_DB_PATH` overrides it). Use one replica for that file. The entrypoint starts as root only long enough to prepare the data directory, then drops to the `app` user.

`GET /healthz` on the published port returns `ok`.

Set `DJANGO_BEHIND_PROXY=true` only when a TLS-terminating proxy overwrites `X-Forwarded-Proto` and clients cannot reach gunicorn's port directly. The flag turns on the secure proxy header, the HTTPS redirect, and secure cookies together. `/healthz` is exempt from that redirect.

Names and placeholders for these variables are in [`.env.example`](.env.example). Copy that file to `.env` only on a machine that needs the variables. Do not commit `.env`.

## Planned hosting and operations

None of this is configured in the repository.

The intended layout is a single app container and a private PostgreSQL database on k3s. Public HTTPS is planned through an existing Cloudflare Tunnel. The planned address is `https://baby.thewalawalas.com`. That hostname is a target in the deployment epic. It was not checked as a live site while writing this file.

OpenTelemetry is planned to export to Honeycomb, with alarms for sustained failures and a separate external availability check. Traces must leave out names, notes, feeding contents, and import contents. Dashboards, that external check, encrypted backups with a restore drill, and the launch checklist are tracked in [Epic: Deployment, observability & reliability](https://github.com/hash167/baby_tracker/issues/6).

## Tests

The planned strategy is pytest and pytest-django against a real PostgreSQL database, plus Playwright driving the plain JavaScript UI. The important cases are timer correctness, permissions, concurrency (one active feed per baby, idempotent saves), and import correctness (validation, preview, and deduplication).

That suite is not in the tree. [`pyproject.toml`](pyproject.toml) does not depend on pytest or Playwright. There is no `.github` workflow. [`tracker/tests.py`](tracker/tests.py) is Django's empty `TestCase` placeholder. The SQLite database used by the skeleton is not the planned PostgreSQL integration database.
