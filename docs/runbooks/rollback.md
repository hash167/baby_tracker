# Roll back the app

1. Find the previous image tag. It is the commit SHA that was published before the bad deploy: `ghcr.io/hash167/baby_tracker:<sha>`.
2. From a machine that already has cluster access, set the Deployment image to that tag.
3. If the bad release included a migration that cannot run backward, restore the database from the encrypted backup (see `restore.md`) into a new database and point the app at it. Do not edit the backup file in the git repo.
4. Confirm `https://baby.thewalawalas.com/healthz` returns `ok` and `/readyz` returns `ok`.

GitHub Actions does not roll the cluster back. It only publishes images.
