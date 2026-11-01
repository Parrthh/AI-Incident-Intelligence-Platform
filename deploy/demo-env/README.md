# Demo environment (chaos runs)

Files added on top of the [OpenTelemetry Demo](https://github.com/open-telemetry/opentelemetry-demo) (pinned release `3.1.0`) so it sends alerts to this platform.

| File | Purpose |
|---|---|
| `compose.iip.yaml` | Compose override: Alertmanager, this platform (+ Postgres, Redis), a payload capture receiver, and our alert rules mounted into the demo's Prometheus |
| `prometheus-rules.yml` | Alert rules: `HighErrorRate` (more than 5% error spans per service) and `HighLatencyP95` (p95 above 1.5 s) |
| `alertmanager.yml.tmpl` | Alertmanager routing: every alert goes to the capture receiver and the platform, and optionally to the hosted demo |
| `capture_server.py` | Saves each webhook body to a file, so real payloads can become test fixtures |
| `queries.md` | The PromQL behind the rules, for exploring the demo's metrics |

The run itself is `chaos/run.py`, used by `.github/workflows/chaos.yml`.

## Running it locally

Needs Docker with about 10 GB of free memory and disk.

```bash
git clone --depth 1 --branch 3.1.0 https://github.com/open-telemetry/opentelemetry-demo.git ../otel-demo
docker build -t iip:chaos .
IIP_IMAGE=iip:chaos python3 chaos/run.py --demo-dir ../otel-demo --scenario payment_failure
```

The platform UI is then at http://127.0.0.1:8000/incidents, Prometheus at :9090, Alertmanager at :9093. Stop with `docker compose ... down` from the demo directory (see the workflow's last step).
