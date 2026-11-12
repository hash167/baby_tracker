# Deploy on k3s without a registry

This cluster does not pull from a registry. Build the image on one node, save it, and import it on every node. `imagePullPolicy: IfNotPresent` then uses the imported image.

## Build and import

On a node with Docker, from a checkout of the commit you are deploying:

```bash
docker build -t ghcr.io/hash167/baby_tracker:REPLACE_SHA .
docker save ghcr.io/hash167/baby_tracker:REPLACE_SHA -o baby-tracker.tar
```

Copy `baby-tracker.tar` to each node and import it into k3s:

```bash
sudo k3s ctr images import baby-tracker.tar
sudo k3s ctr images ls | grep baby-tracker
```

The image name and tag must match the Deployment, Job, and CronJob. There is no `nodeSelector` and no node affinity that excludes a node. Two app replicas use a preferred pod anti-affinity (weight 100) and `topologySpreadConstraints` with `maxSkew: 1` and `whenUnsatisfiable: ScheduleAnyway`, so they spread across nodes when they can and still start when they cannot.

Uploaded CSVs are stored in PostgreSQL (`ImportFile`), not on a volume. Any replica can download the file until retention deletes the row. A restart does not 404 the download.

PostgreSQL stays on `postgres:16`. Its PVC uses the cluster default StorageClass. On this cluster that is `local-path`, so the database volume and the Postgres pod stay on the node where the volume was first created. Do not add a nodeSelector for that; the volume's node affinity is the pin.

The backup CronJob writes to PVC `baby-tracker-backups`, also ReadWriteOnce on that default StorageClass. The job therefore stays on the node that already holds the volume (on this cluster, the node where that PVC was created). That pin is the volume's affinity. The manifests do not add a node selector or a node affinity that excludes other nodes. Deleting and recreating the PVC is what moves backups to another node.

## Apply

Create the Secrets first. The manifests only name them.

- `baby-tracker-app`: `DJANGO_SECRET_KEY`
- `baby-tracker-db`: `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
- `baby-tracker-backup`: `BACKUP_ENCRYPTION_KEY`
- `baby-tracker-bootstrap`: `BOOTSTRAP_ADMIN_USERNAME`, `BOOTSTRAP_ADMIN_PASSWORD`, `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_HOUSEHOLD_NAME`, `BOOTSTRAP_BABY_NAME`, `BOOTSTRAP_TIMEZONE`

```bash
kubectl apply -k deploy/k8s
kubectl -n baby-tracker delete job baby-tracker-migrate --ignore-not-found
kubectl apply -f deploy/k8s/migrate-job.yaml
kubectl -n baby-tracker wait --for=condition=complete job/baby-tracker-migrate
kubectl apply -f deploy/k8s/bootstrap-job.yaml
```

The app Service is a NodePort on 30820. The base ConfigMap allows the public hostname, localhost, and the in-cluster Service DNS names. For browser access through a node address, copy `deploy/k8s/overlays/nodeport/configmap-hosts.yaml`, replace `NODE1`, `NODE2`, and `NODE3`, and apply that overlay instead:

```bash
kubectl apply -k deploy/k8s/overlays/nodeport
```

Do not commit those addresses.

`/healthz` and `/readyz` answer before host checks, including when kubelet sends the pod IP as `Host`. Probes do not set a Host header.

The backup CronJob runs at 03:30 America/Los_Angeles.

## Tracing

Leave `OTEL_EXPORTER_OTLP_ENDPOINT` unset when there is no collector. The app starts and serves traffic with export disabled. Do not apply `deploy/k8s/otel-collector.yaml` unless Secret `baby-tracker-otel` exists (`HONEYCOMB_API_KEY`). The app does not read that key. If the endpoint is set and the collector is down, export fails and the request still completes.

## Migrations

The migrate Job runs `python /app/docker/migrate.py`. That script puts `/app` on `sys.path` itself. The image also sets `PYTHONPATH=/app`.
