"""Prometheus metrics (M2 step 9).

Counters and histograms are updated where the work happens. Queue and outbox
gauges (backlog sizes) are read from Redis and Postgres only when ``/metrics`` is
scraped, so an unscraped deployment, like the free demo, spends no Redis
commands or database time on them (ADR-0004).

Names follow Prometheus conventions: ``_total`` for counters, base units
(seconds) for durations.
"""

from prometheus_client import Counter, Gauge, Histogram

HTTP_REQUEST_DURATION = Histogram(
    "iip_http_request_duration_seconds",
    "HTTP request duration, by route template and status code.",
    ["method", "route", "status"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)

ALERTS_RECEIVED = Counter("iip_alerts_received_total", "Alerts received on the webhook.")
INCIDENTS_OPENED = Counter("iip_incidents_opened_total", "Incidents opened.")

JOBS = Counter(
    "iip_jobs_total",
    "Background jobs handled, by type and outcome (done, retry, dead_lettered).",
    ["type", "outcome"],
)
JOB_DURATION = Histogram(
    "iip_job_duration_seconds",
    "Background job run time, by type.",
    ["type"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60, 120),
)

LLM_REQUEST_DURATION = Histogram(
    "iip_llm_request_duration_seconds",
    "Successful LLM call duration as reported by the provider client.",
    ["provider", "model"],
    buckets=(0.1, 0.25, 0.5, 1, 2, 5, 10, 30),
)
LLM_TOKENS = Counter("iip_llm_tokens_total", "LLM tokens used.", ["provider", "model", "direction"])

OUTBOX_PUBLISHED = Counter("iip_outbox_published_total", "Outbox rows published to Redis.")
OUTBOX_PUBLISH_ERRORS = Counter("iip_outbox_publish_errors_total", "Failed outbox publishes.")

QUEUE_LENGTH = Gauge("iip_queue_length", "Entries in the jobs stream (includes handled ones).")
QUEUE_PENDING = Gauge("iip_queue_pending", "Jobs delivered to a worker and not yet acknowledged.")
QUEUE_DEAD_LETTERS = Gauge("iip_queue_dead_letters", "Jobs in the dead-letter stream.")
OUTBOX_UNPUBLISHED = Gauge("iip_outbox_unpublished", "Outbox rows not yet published.")
OUTBOX_LAG = Gauge(
    "iip_outbox_lag_seconds", "Age of the oldest unpublished outbox row (0 when none)."
)
SCRAPE_ERRORS = Counter(
    "iip_metrics_collection_errors_total",
    "Failures reading queue or outbox gauges during a scrape.",
    ["source"],
)
