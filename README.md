# Baby Tracker

Baby Tracker is a phone-friendly web app for shared baby care. Parents and a caregiver sign in to one household, log diaper changes and breastfeeding, and read the same daily timeline. An admin invites people, changes roles, and deactivates accounts. A parent can also stage a Nara Baby CSV export, preview it, and commit it without creating duplicate rows.

Pages are server-rendered HTML with HTMX and plain JavaScript. The timer on screen is computed from timestamps saved on the server, so a reload keeps the same feed.

## Accounts

There is no public signup. An admin opens **Admin**, creates a one-time invite link, and shares that link. The link expires and works once. The person sets a password and joins the household as an admin, a parent, or a caregiver.

Parents and admins can add and correct any diaper or feed. Caregivers can read the shared timeline, add entries, and edit or delete only their own. Every check is done on the server. Deactivating someone, changing their role, or choosing **Sign out everywhere** ends their existing sessions. A person can also sign out their other sessions from **Account**.

Repeated failed logins are throttled. Invitation links are the way accounts are created and the way an admin helps someone who is locked out: issue a new invite, or change the password with them present. No email provider is configured. See [docs/decisions/0001-invitation-delivery.md](docs/decisions/0001-invitation-delivery.md).

## Diapers and timeline

The timeline has large buttons for wet, dirty, both, and dry. Leave the time blank to record now, or pick a time to backdate. A double tap or a retry of the same submission stores one event. If the save does not reach the server, the page says **Not saved** and offers retry. It does not show an unsaved entry as recorded.

The day is grouped in the household's timezone. Totals skip soft-deleted rows. A parent or admin can edit, delete, and restore an entry. A stale edit shows the current values instead of overwriting them. **History** lists who changed the entry and when.

While the timeline tab is visible it polls for changes from other devices and refreshes when you come back to it. Hidden tabs do not poll.

## Breastfeeding

**Feed** starts a left or right timer, and can pause, switch side, or finish. Elapsed time is computed in the browser from the segment timestamps, corrected by the server clock. The server is contacted on those actions and on a slow refresh, not every second. Reloading the page, or opening the feed on another device, shows the same session. Only one unfinished feed can exist for a baby.

**Enter a finished feed** records minutes on each side. Use 0 for a side that was not used. A blank duration is not the same thing as zero. Feeds that pass local midnight count on the day they started. Today's card shows per-side totals, the last side, and how long it has been since the last feed started and ended.

## Nara Baby import

**Import** accepts a CSV shaped like a Nara Baby export. The original file is stored in PostgreSQL and removed after the retention period, so any app replica can download it. Staging does not change the timeline. The preview groups rows by type and lists warnings: timestamp mismatches, unknown values, missing units, and duplicate ids in the file. Caregiver names in the file can be mapped to someone who already has an account, or left as attribution text. The import does not create accounts.

Committing writes diapers and finished feeds, including per-side totals and the start and end side. It does not invent a left/right switch sequence. Sleep, growth, and medical rows are kept and listed under **Account → Archived import records**. A second import of the same file adds nothing. A changed row is applied only when you choose **Apply update**. **Roll back** removes that batch. If someone edited an imported row afterward, the rollback asks whether to keep or remove that edit. Other entries are left alone.

The column layout the parser expects is described in [docs/nara_csv_format.md](docs/nara_csv_format.md). Files in this repository are synthetic. A real family export stays off this repo, off issues, and off CI. Checking a real export is a private step described in [docs/runbooks/private-import.md](docs/runbooks/private-import.md).

## Run it locally

Install [uv](https://docs.astral.sh/uv/), Python 3.15, and Docker. From the repository root:

```bash
docker compose up --build
```

That starts PostgreSQL 16, runs migrations as their own step, then serves the app at `http://127.0.0.1:8000`. The compose database name, user, and password are the local development values `baby_tracker`. They are not production credentials.

Create the first household when you are ready. The command exits if any value is missing:

```bash
export BOOTSTRAP_ADMIN_USERNAME=...
export BOOTSTRAP_ADMIN_PASSWORD=...
export BOOTSTRAP_ADMIN_EMAIL=...
export BOOTSTRAP_HOUSEHOLD_NAME=...
export BOOTSTRAP_BABY_NAME=...
export BOOTSTRAP_TIMEZONE=America/Los_Angeles
docker compose exec app python manage.py bootstrap_household
```

For tests and day-to-day development without rebuilding the image:

```bash
docker compose up -d --wait db
uv sync
uv run python manage.py migrate
uv run python manage.py runserver
```

`psycopg` loads the system `libpq` library (there is no usable `psycopg-binary` wheel for Python 3.15 yet):

- **Debian/Ubuntu:** `sudo apt-get install -y libpq5`
- **macOS:** `brew install libpq`, then:

```bash
export DYLD_FALLBACK_LIBRARY_PATH="$(brew --prefix libpq)/lib${DYLD_FALLBACK_LIBRARY_PATH:+:$DYLD_FALLBACK_LIBRARY_PATH}"
```

[`scripts/test.sh`](scripts/test.sh) sets that macOS path when Homebrew `libpq` is installed.

`GET /healthz` returns `ok` and does not touch the database. `GET /readyz` returns `ok` when PostgreSQL answers `SELECT 1`, and `unavailable` with HTTP 503 otherwise. Neither response includes connection details.

## Configuration

Settings come from the environment. Names are listed in [`.env.example`](.env.example). Do not commit a `.env` file.

Production requires `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_HOST`. Behind the Cloudflare Tunnel, also set `DJANGO_BEHIND_PROXY=true` and `DJANGO_CSRF_TRUSTED_ORIGINS=https://baby.thewalawalas.com`. That turns on the HTTPS redirect, secure cookies, HSTS, and the proxy header together. `/healthz` and `/readyz` stay exempt from the redirect so probes can use HTTP inside the cluster.

OpenTelemetry traces are exported only when `OTEL_EXPORTER_OTLP_ENDPOINT` or `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` is set. The app does not read a vendor API key. Span names use route templates. The export filter keeps method, status, route, database operation, error class, and the low-cardinality fields `app.event_type`, `app.outcome`, and `app.role`. An unreachable collector does not fail the request. The in-cluster collector and the Honeycomb key are described in [docs/runbooks/observability.md](docs/runbooks/observability.md).

## Tests

pytest and pytest-django run against PostgreSQL only. The test database is created and destroyed each run.

```bash
./scripts/test.sh
```

Playwright is plain JavaScript and needs Node.js 22. Seed the synthetic household, start the app, then run the browser tests:

```bash
uv run python manage.py seed_e2e
TIMELINE_POLL_MS=1000 uv run python manage.py runserver 127.0.0.1:8000
./scripts/e2e.sh
```

The seeded usernames are `synth_admin`, `synth_parent`, and `synth_caregiver`. The shared test password is the one in [`tests/factories.py`](tests/factories.py). These accounts exist only for tests.

## Container image and deployment

Build the image:

```bash
docker build -t baby-tracker .
```

The image runs gunicorn and WhiteNoise. It does not migrate on startup and it does not contain a secret. Apply migrations with:

```bash
python /app/docker/migrate.py
```

Kubernetes manifests are in [`deploy/k8s/`](deploy/k8s/). They create the `baby-tracker` namespace, a private PostgreSQL 16 StatefulSet, the app Deployment and NodePort Service, a migration Job, a bootstrap Job, and a backup CronJob. The collector manifest is optional. Secrets are referenced by name (`baby-tracker-app`, `baby-tracker-db`, `baby-tracker-backup`, `baby-tracker-bootstrap`, and `baby-tracker-otel` only if you run the collector). The manifests do not contain secret values. Probes use `/readyz` for readiness and `/healthz` for liveness and do not set a Host header. A database outage removes the pod from the Service without a restart loop. Building the image and importing it into k3s with no registry is described in [docs/runbooks/deploy-k3s.md](docs/runbooks/deploy-k3s.md).

GitHub Actions builds the image on every pull request. A push to `main` publishes `ghcr.io/hash167/baby_tracker:<commit sha>`. The cluster pulls that image. GitHub does not receive a kubeconfig. See [docs/decisions/0002-image-delivery.md](docs/decisions/0002-image-delivery.md) and [docs/runbooks/rollback.md](docs/runbooks/rollback.md).

The intended public address is `https://baby.thewalawalas.com`, through the existing Cloudflare Tunnel, pointing at `http://baby-tracker.baby-tracker.svc.cluster.local:8000`. The tunnel change itself is made in the tunnel's own configuration, not in this repo. See [docs/runbooks/cloudflare-tunnel.md](docs/runbooks/cloudflare-tunnel.md).

Encrypted backups, the restore drill, and the launch checklist are in [docs/runbooks/restore.md](docs/runbooks/restore.md) and [docs/runbooks/release-checklist.md](docs/runbooks/release-checklist.md).

## Continuous integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on pull requests and on pushes to `main`. A temporary PostgreSQL 16 service backs pytest, migration checks, and Playwright. The database user, name, and password are the same public local values as compose.

| Status check | Fails when |
| --- | --- |
| `CI / lint` | `ruff check` or `ruff format --check` fails |
| `CI / pytest` | pytest fails against PostgreSQL |
| `CI / migrations` | a migration is missing, or `migrate` from an empty database fails |
| `CI / playwright` | a browser test fails |
| `CI / dependency-audit` | `pip-audit` reports a known vulnerability in `uv.lock` |
| `CI / bandit` | Bandit reports an issue in first-party Python |
| `CI / deploy-check` | `manage.py check --deploy` fails |
| `CI / secrets` | gitleaks, the data-file guard, or pre-commit fails |
| `CI / manifests` | kubeconform rejects `deploy/k8s/` |
| `CI / image` | `docker build` fails |

## Stack

Python 3.15, Django 6.1, PostgreSQL 16, HTMX 2 (vendored at [`static/js/htmx.min.js`](static/js/htmx.min.js)), WhiteNoise, and gunicorn. Tests use pytest, pytest-django, and Playwright. There is no Node.js application build and no TypeScript.
