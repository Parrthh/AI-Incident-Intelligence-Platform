# Alertmanager webhook fixtures

Payloads in Alertmanager's webhook format (version 4), used by tests so they
never need a running Alertmanager.

- `high_error_rate_firing.json` / `high_error_rate_resolved.json`: one alert,
  firing then resolved (same fingerprint and `startsAt`).
- `two_alerts_firing.json`: a grouped delivery with two alerts.

These are hand-written from the documented format, using OpenTelemetry Demo
service names. M1's chaos workflow adds payloads captured from a real
Alertmanager next to them (`captured_*.json`).
