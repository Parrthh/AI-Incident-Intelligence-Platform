# PromQL for the OpenTelemetry Demo

The demo's OTel Collector turns spans into metrics (spanmetrics connector) and pushes them into Prometheus over OTLP every 60 seconds. Labels include `service_name`, `span_name`, `span_kind` and `status_code`.

Request rate per service:

```promql
sum by (service_name) (rate(traces_span_metrics_calls_total[3m]))
```

Error ratio per service (what `HighErrorRate` alerts on):

```promql
sum by (service_name) (rate(traces_span_metrics_calls_total{status_code="STATUS_CODE_ERROR"}[3m]))
/
sum by (service_name) (rate(traces_span_metrics_calls_total[3m]))
```

p95 latency per service, in milliseconds (what `HighLatencyP95` alerts on):

```promql
histogram_quantile(0.95, sum by (le, service_name) (rate(traces_span_metrics_duration_milliseconds_bucket[3m])))
```

Windows are 3 minutes because samples arrive once a minute: `rate` needs at least two samples, and a wider window smooths out single bad scrapes.
