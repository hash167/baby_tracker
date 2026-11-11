# Release checklist

Each line is either covered by an automated test in this repository or is a live-cluster step the owner still has to run.

## Accounts and diapers

- [x] Permission matrix, including cross-household isolation (`tests/test_access.py`)
- [x] Invite expiry, reuse, and closed signup
- [x] Session revocation on deactivation and role change
- [x] Diaper idempotency, failed-save response, edit conflict, restore
- [x] Timeline day boundaries and both DST shapes
- [x] `/healthz` independent of the database, `/readyz` fails closed
- [x] Manifests contain no secret values; kubeconform runs in CI
- [ ] Cloudflare hostname pointed at the Service (see `cloudflare-tunnel.md`)
- [ ] Image tag deployed on the cluster and a migration Job completed

## Feeding

- [x] One open feed, contiguous switch, idempotent timer retry, two-connection start
- [x] Manual 0 versus unknown duration
- [x] Midnight attribution and last-side summary
- [x] Playwright timer reload and clock jump
- [ ] Home-screen install and a real phone lock (see below)

## Import and launch

- [x] Synthetic parse, dedup, changed-row confirmation, rollback, failed commit
- [x] Upload retention
- [x] Backup encryption round trip; dev restore drill noted in `restore.md`
- [ ] Real export validated privately (`private-import.md`)
- [ ] Honeycomb triggers created and a staged error shown to fire and clear
- [ ] External uptime check and heartbeat alert proven by a staged outage
- [ ] Backup CronJob observed on the cluster, including one failed-run alert
- [ ] `scripts/smoke.py` run against `https://baby.thewalawalas.com` with a smoke account in its own household

## Phone install

The app serves `/manifest.webmanifest` and `/sw.js`. The service worker caches the stylesheet and scripts only. Authenticated HTML is `Cache-Control: no-store` and is not put in the cache.

Still to do on a device, because a desktop browser cannot prove it:

- Add to the home screen on iOS and open standalone.
- Add to the home screen on Android and open standalone.
- Start a timer, lock the phone for several minutes, reopen, and check the elapsed time.
- Force-close the app, reopen, and check the elapsed time.
- Switch sides from a second device.
- Log out and confirm the timeline is not shown from a cached page.
