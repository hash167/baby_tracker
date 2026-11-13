# How the image reaches k3s

## Decision

GitHub Actions publishes the image to GHCR, tagged with the commit SHA, on a push to `main`. The workflow has `packages: write` and no Kubernetes credentials.

The k3s cluster pulls `ghcr.io/hash167/baby_tracker:<sha>`. An operator inside the cluster sets the Deployment image and applies the migration Job before the new pods serve traffic. The Kubernetes API is not exposed to GitHub and no kubeconfig is stored in this repository.

Rollback is deploying the previous SHA. See `docs/runbooks/rollback.md`.

## Why

The cluster already exists and is reached through a private network and an existing Cloudflare Tunnel. Putting a kubeconfig in GitHub Actions would put cluster admin credentials in a public repository's CI. A pull, started from inside the cluster, keeps that boundary.

GitOps (Flux or Argo CD) can watch the same GHCR tag later. It would still pull from inside the cluster. This decision does not require that controller to be installed first.

## What is not done here

This repository does not apply manifests to the live cluster. The owner applies `deploy/k8s/` from a machine that already has cluster access, after substituting the image SHA and creating the named Secrets.
