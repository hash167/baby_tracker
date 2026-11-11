# Observability

The app exports OTLP/HTTP to `OTEL_EXPORTER_OTLP_ENDPOINT` (in cluster, `http://otel-collector:4318`). It does not send a Honeycomb API key.

The collector manifest is `deploy/k8s/otel-collector.yaml`. The key is `baby-tracker-otel` / `HONEYCOMB_API_KEY`. The collector keeps only the attribute allowlist in `deploy/otel/collector.yaml`, batches spans, and retries exports. If the collector is down, the app's exporter fails the span and the HTTP request still completes.

`deploy/observability/alerts.yaml` lists the sustained triggers: 5xx ratio, database errors, save failures, import failures, and a missing backup. A single sample over the line does not fire. Alert text should stay limited to route templates, error classes, and counts.

`python manage.py emit_heartbeat` records a heartbeat span. Schedule it inside the cluster. An external check should request `https://baby.thewalawalas.com/healthz` and `https://baby.thewalawalas.com/login/` from outside the cluster. Scaling the deployment to zero, or stopping the collector, is the drill for those two alerts. That drill has to be run on the live cluster.

`scripts/smoke.py` checks `/healthz` and `/login/`. With `SMOKE_USERNAME` and `SMOKE_PASSWORD` it also creates and deletes one diaper. Put those in a Secret and use an account that belongs to a household that is not the family's real data.
