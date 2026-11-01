# Alertmanager webhook fixtures

Payloads in Alertmanager's webhook format (version 4), used by tests so they
never need a running Alertmanager.

- `high_error_rate_firing.json` / `high_error_rate_resolved.json`: one alert,
  firing then resolved (same fingerprint and `startsAt`).
- `two_alerts_firing.json`: a grouped delivery with two alerts.

- `captured_payment_failure_firing.json` / `captured_payment_failure_resolved.json`:
  captured from a real Alertmanager 0.28 during the `payment_failure` chaos run
  (2026-11-01). Use these to check behavior against what Alertmanager actually sends.

The other files are hand-written from the documented format, using OpenTelemetry
Demo service names.
