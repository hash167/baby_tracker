# Private check of a real Nara export

Do this on a machine that is not committing to git, and not in CI.

1. Export from Nara Baby on a private device.
2. Upload it to a non-production copy of the app, or to a local server.
3. Read the preview. Resolve warnings. Commit only after the preview looks right.
4. Spot-check the timeline. Try rollback on the non-production copy first.
5. Record the outcome in generic terms (for example, "all record types parsed, two warning categories resolved"). Do not paste names, dates, ids, notes, or sample rows into GitHub, CI logs, or screenshots.

The fixtures in `tests/fixtures/nara/` are synthetic and are the only CSV files that belong in this repository.
