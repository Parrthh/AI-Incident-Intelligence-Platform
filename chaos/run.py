"""Run a chaos scenario against the OpenTelemetry Demo with the platform attached.

    python chaos/run.py --demo-dir ../otel-demo --scenario payment_failure

Steps: start the demo + Alertmanager + this platform, wait for baseline traffic,
turn a failure flag on, wait for the platform to open an incident and summarize
it, turn the flag off, wait for the incident to resolve, then report timings and
the captured Alertmanager payloads.

Standard library only, so it runs on a bare CI runner. Results are printed as
GitHub annotations and written to the job summary when running in Actions.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
DEMO_ENV = REPO / "deploy" / "demo-env"
IIP = "http://127.0.0.1:8000"
PROMETHEUS = "http://127.0.0.1:9090"
ALERTMANAGER = "http://127.0.0.1:9093"
DEMO_WEBHOOK_URL = "https://iip-demo.onrender.com/v1/webhooks/alertmanager"


@dataclass(frozen=True)
class Scenario:
    name: str
    flag: str
    on_variant: str
    # Ground truth for evaluation (M4): the service that was actually broken.
    root_cause_service: str
    category: str


SCENARIOS = {
    "payment_failure": Scenario(
        name="payment_failure",
        flag="paymentFailure",
        on_variant="100%",
        root_cause_service="payment",
        category="code_bug",
    ),
}


# ---------------------------------------------------------------- helpers


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def annotate(level: str, title: str, message: str) -> None:
    clean = message.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
    print(f"::{level} title={title}::{clean}", flush=True)


def get_json(url: str, timeout: float = 10) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - fixed local URLs
        return json.loads(resp.read())


def wait_for(description: str, check: Any, timeout_s: float, interval_s: float = 10) -> Any:
    deadline = time.monotonic() + timeout_s
    last_error = ""
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            last_error = f" (last error: {type(exc).__name__}: {exc})"
        time.sleep(interval_s)
    raise TimeoutError(f"timed out after {timeout_s:.0f}s waiting for {description}{last_error}")


def compose(demo_dir: Path, *args: str) -> None:
    files = [
        "-f",
        "compose.yaml",
        "-f",
        "compose.observability.yaml",
        "-f",
        str(DEMO_ENV / "compose.iip.yaml"),
    ]
    env_files = ["--env-file", ".env", "--env-file", ".env.override"]
    cmd = ["docker", "compose", *env_files, *files, *args]
    log("$ " + " ".join(cmd))
    subprocess.run(cmd, cwd=demo_dir, check=True)  # noqa: S603


# ---------------------------------------------------------------- setup


def prepare(demo_dir: Path, run_dir: Path, demo_version: str) -> None:
    (run_dir / "captures").mkdir(parents=True, exist_ok=True)
    os.chmod(run_dir / "captures", 0o777)  # noqa: S103 - written by a container user

    # Pin the demo's image versions to the checked-out release (append: the file
    # ships with the demo and may hold other overrides).
    override = demo_dir / ".env.override"
    existing = override.read_text() if override.exists() else ""
    if "DEMO_VERSION=" not in existing:
        override.write_text(existing.rstrip("\n") + f"\nDEMO_VERSION={demo_version}\n")

    # Add our rules and Alertmanager to the demo's Prometheus config.
    prom_cfg = demo_dir / "src" / "prometheus" / "prometheus-config.yaml"
    text = prom_cfg.read_text()
    if "iip-rules.yml" not in text:
        prom_cfg.write_text(
            text.rstrip()
            + "\n\nrule_files:\n  - /etc/prometheus/iip-rules.yml\n"
            + "\nalerting:\n  alertmanagers:\n    - static_configs:\n"
            + "        - targets: ['alertmanager:9093']\n"
        )

    # Render the Alertmanager config; forward to the hosted demo only if a token exists.
    template = (DEMO_ENV / "alertmanager.yml.tmpl").read_text()
    token = os.environ.get("IIP_DEMO_WEBHOOK_TOKEN", "").strip()
    if token:
        route = "    - receiver: iip-demo\n      continue: true"
        receiver = (
            "  - name: iip-demo\n    webhook_configs:\n"
            f"      - url: {DEMO_WEBHOOK_URL}\n        send_resolved: true\n"
            "        http_config:\n          authorization:\n"
            f"            type: Bearer\n            credentials: {token}"
        )
        log("forwarding alerts to the hosted demo as well")
    else:
        route, receiver = "", ""
        log("IIP_DEMO_WEBHOOK_TOKEN not set: alerts stay in this run")
    config = template.replace("__DEMO_ROUTE__", route).replace("__DEMO_RECEIVER__", receiver)
    am = run_dir / "alertmanager.yml"
    am.write_text(config)
    os.chmod(am, 0o644)


def set_flag(demo_dir: Path, flag: str, variant: str) -> None:
    path = demo_dir / "src" / "flagd" / "demo.flagd.json"
    data = json.loads(path.read_text())
    data["flags"][flag]["defaultVariant"] = variant
    path.write_text(json.dumps(data, indent=2))
    log(f"flag {flag} -> {variant}")


# ---------------------------------------------------------------- checks


def prom_query(expr: str) -> list[dict[str, Any]]:
    url = f"{PROMETHEUS}/api/v1/query?" + urllib.parse.urlencode({"query": expr})
    return get_json(url)["data"]["result"]


def incidents(status: str | None = None) -> list[dict[str, Any]]:
    url = f"{IIP}/v1/incidents?limit=100" + (f"&status={status}" if status else "")
    return get_json(url)["items"]


def diagnostics() -> None:
    for name, url in [
        ("prometheus rules", f"{PROMETHEUS}/api/v1/rules"),
        ("prometheus alerts", f"{PROMETHEUS}/api/v1/alerts"),
        ("alertmanager alerts", f"{ALERTMANAGER}/api/v2/alerts"),
        ("iip incidents", f"{IIP}/v1/incidents"),
    ]:
        try:
            annotate("warning", f"diag: {name}", json.dumps(get_json(url))[:6000])
        except (urllib.error.URLError, OSError, ValueError) as exc:
            annotate("warning", f"diag: {name}", f"unavailable: {exc}")
    for container in ("iip", "alertmanager", "prometheus"):
        out = subprocess.run(  # noqa: S603
            ["docker", "logs", "--tail", "40", container],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
        annotate("warning", f"diag: {container} logs", (out.stdout + out.stderr)[-6000:])


# ---------------------------------------------------------------- run


def run(demo_dir: Path, scenario: Scenario, run_dir: Path, demo_version: str) -> dict[str, Any]:
    prepare(demo_dir, run_dir, demo_version)
    os.environ["IIP_RUN_DIR"] = str(run_dir)
    os.environ["IIP_REPO"] = str(REPO)
    compose(demo_dir, "up", "--detach", "--quiet-pull")

    wait_for("platform readiness", lambda: get_json(f"{IIP}/readyz")["status"] == "ready", 600)
    log("platform is ready")
    traffic = wait_for(
        f"baseline traffic for {scenario.root_cause_service}",
        lambda: prom_query(
            "sum(rate(traces_span_metrics_calls_total"
            f'{{service_name="{scenario.root_cause_service}"}}[3m])) > 0'
        ),
        900,
        15,
    )
    log(f"baseline traffic present: {traffic}")
    rules = get_json(f"{PROMETHEUS}/api/v1/rules")["data"]["groups"]
    if not any(g["name"] == "iip-demo" for g in rules):
        raise RuntimeError("Prometheus did not load the iip-demo rule group")

    t_on = time.monotonic()
    set_flag(demo_dir, scenario.flag, scenario.on_variant)

    target = scenario.root_cause_service
    pre_existing = {i["id"] for i in incidents()}

    def target_incidents(status: str | None = None) -> list[dict[str, Any]]:
        return [
            i for i in incidents(status) if target in i["services"] and i["id"] not in pre_existing
        ]

    opened = wait_for(f"an open incident for {target}", lambda: target_incidents("open"), 900, 15)
    t_opened = time.monotonic()
    log(f"{target} incident opened after {t_opened - t_on:.0f}s: {opened[0]['title']}")
    target_id = opened[0]["id"]

    def summary_finished() -> dict[str, Any] | None:
        inc = get_json(f"{IIP}/v1/incidents/{target_id}")
        s = inc.get("summary")
        return inc if s and s["status"] in {"done", "failed"} else None

    summarized = wait_for("the incident's summary", summary_finished, 300, 5)
    t_summarized = time.monotonic()

    set_flag(demo_dir, scenario.flag, "off")
    t_off = time.monotonic()
    wait_for(f"all {target} incidents to resolve", lambda: not target_incidents("open"), 1200, 20)
    t_resolved = time.monotonic()

    final = [i for i in incidents() if i["id"] not in pre_existing]
    alert_counts = {
        i["id"]: len(get_json(f"{IIP}/v1/incidents/{i['id']}")["alerts"]) for i in final
    }
    total_alerts = sum(alert_counts.values())
    return {
        "scenario": scenario.name,
        "root_cause_service": scenario.root_cause_service,
        "seconds_flag_on_to_incident": round(t_opened - t_on),
        "seconds_incident_to_summary": round(t_summarized - t_opened),
        "seconds_flag_off_to_resolved": round(t_resolved - t_off),
        # Noise reduction: how many alerts the on-call engineer sees as one incident.
        "alerts": total_alerts,
        "incident_count": len(final),
        "incidents": [
            {
                "title": i["title"],
                "services": i["services"],
                "alerts": alert_counts[i["id"]],
                "status": i["status"],
                "summary_status": (i.get("summary") or {}).get("status"),
                "summary_model": (i.get("summary") or {}).get("model"),
            }
            for i in final
        ],
        "summary_text": (summarized.get("summary") or {}).get("text"),
    }


def print_captures(run_dir: Path) -> None:
    captures = sorted((run_dir / "captures").glob("*.json"))
    annotate("notice", "captures", f"{len(captures)} Alertmanager payloads captured")
    firing = next((p for p in captures if p.name.endswith("_firing.json")), None)
    resolved = next((p for p in captures if p.name.endswith("_resolved.json")), None)
    for label, path in (
        ("captured firing payload", firing),
        ("captured resolved payload", resolved),
    ):
        if path:
            annotate("notice", label, path.read_text())


def report(result: dict[str, Any], run_dir: Path) -> None:
    annotate(
        "notice",
        "chaos result",
        json.dumps({k: v for k, v in result.items() if k != "summary_text"}),
    )
    if result.get("summary_text"):
        annotate("notice", "summary text", result["summary_text"])
    print_captures(run_dir)

    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a") as fh:
            fh.write(f"## Chaos run: {result['scenario']}\n\n")
            fh.write("| Measure | Seconds |\n|---|---|\n")
            for key in (
                "seconds_flag_on_to_incident",
                "seconds_incident_to_summary",
                "seconds_flag_off_to_resolved",
            ):
                fh.write(f"| {key} | {result[key]} |\n")
            fh.write("\n### Incidents\n\n")
            for inc in result["incidents"]:
                services = ", ".join(inc["services"])
                fh.write(
                    f"- {inc['title']} ({services}): {inc['status']}, "
                    f"summary {inc['summary_status']}\n"
                )
            if result.get("summary_text"):
                fh.write(f"\n### Example summary\n\n> {result['summary_text']}\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--demo-dir", type=Path, required=True)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="payment_failure")
    parser.add_argument("--run-dir", type=Path, default=REPO / "chaos" / "runs" / "latest")
    parser.add_argument("--demo-version", default="3.1.0")
    args = parser.parse_args()

    try:
        result = run(
            args.demo_dir.resolve(),
            SCENARIOS[args.scenario],
            args.run_dir.resolve(),
            args.demo_version,
        )
    except (TimeoutError, RuntimeError, subprocess.CalledProcessError) as exc:
        annotate("error", "chaos run failed", str(exc))
        diagnostics()
        print_captures(args.run_dir.resolve())
        return 1
    report(result, args.run_dir.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
