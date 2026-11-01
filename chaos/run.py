"""Run a chaos scenario against the OpenTelemetry Demo with the platform attached.

    python chaos/run.py --demo-dir ../otel-demo --scenario payment_failure

Steps: start the demo + Alertmanager + this platform, wait for baseline traffic,
turn a failure flag on, wait for the platform to open an incident and summarize
it, turn the flag off, wait for the incident to resolve, then report timings and
the captured Alertmanager payloads.

Standard library only, apart from PyYAML for the scenario catalog (the workflow
runs it with `uv run --with pyyaml`). Results are printed as
GitHub annotations and written to the job summary when running in Actions.
"""

import argparse
import base64
import gzip
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
LOCAL_TOKEN = "chaos-local-token"  # noqa: S105 - the local chaos stack's own test token


SCENARIO_DIR = REPO / "chaos" / "scenarios"


@dataclass(frozen=True)
class Scenario:
    """One failure to inject, and its known cause (ground truth for the eval).

    Scenarios live in ``chaos/scenarios/*.yaml``. ``inject`` is one of:
    - ``{type: flag, flag: paymentFailure, variant: "100%"}``: a demo feature flag
    - ``{type: docker_stop, container: currency}``: stop a service's container
    - ``{type: deploy_then_flag, service: payment, version: v2, flag: ..., variant: ...}``:
      report a deploy, then turn the flag on (a "bad deploy")
    """

    name: str
    description: str
    inject: dict[str, Any]
    root_cause_service: str
    category: str
    # The service whose incident the run waits for. Usually the broken one, but a
    # service that is down entirely sends no spans, so only its callers alert.
    watch_service: str = ""
    also_correct: tuple[dict[str, str], ...] = ()

    @property
    def watched(self) -> str:
        return self.watch_service or self.root_cause_service

    def truth(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "root_cause_service": self.root_cause_service,
            "category": self.category,
            "also_correct": [
                {"service": c["service"], "category": c["category"]} for c in self.also_correct
            ],
        }


def load_scenarios() -> dict[str, Scenario]:
    import yaml  # the workflow runs this with `uv run --with pyyaml`

    scenarios: dict[str, Scenario] = {}
    for path in sorted(SCENARIO_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text())
        scenario = Scenario(
            name=path.stem,
            description=data["description"],
            inject=data["inject"],
            root_cause_service=data["root_cause_service"],
            category=data["category"],
            watch_service=data.get("watch_service", ""),
            also_correct=tuple(data.get("also_correct", [])),
        )
        scenarios[scenario.name] = scenario
    return scenarios


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
        existing = existing.rstrip("\n") + f"\nDEMO_VERSION={demo_version}\n"
    # The collector's extras file also sends logs to Loki (deploy/demo-env).
    if "OTEL_COLLECTOR_CONFIG_EXTRAS=" not in existing:
        extras = DEMO_ENV / "otelcol-extras.yml"
        existing = existing.rstrip("\n") + f"\nOTEL_COLLECTOR_CONFIG_EXTRAS={extras}\n"
    override.write_text(existing)

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


def inject(demo_dir: Path, scenario: Scenario) -> None:
    spec = scenario.inject
    kind = spec["type"]
    if kind == "flag":
        set_flag(demo_dir, spec["flag"], spec["variant"])
    elif kind == "docker_stop":
        subprocess.run(["docker", "stop", spec["container"]], check=True, capture_output=True)  # noqa: S603, S607
        log(f"stopped container {spec['container']}")
    elif kind == "deploy_then_flag":
        post_json(
            f"{IIP}/v1/deploys",
            {"service": spec["service"], "version": spec["version"], "description": "chaos deploy"},
            token=LOCAL_TOKEN,
        )
        log(f"reported deploy {spec['service']} {spec['version']}")
        set_flag(demo_dir, spec["flag"], spec["variant"])
    else:
        raise ValueError(f"unknown injection type {kind!r}")


def restore(demo_dir: Path, scenario: Scenario) -> None:
    spec = scenario.inject
    if spec["type"] in {"flag", "deploy_then_flag"}:
        set_flag(demo_dir, spec["flag"], spec.get("off_variant", "off"))
    elif spec["type"] == "docker_stop":
        subprocess.run(["docker", "start", spec["container"]], check=True, capture_output=True)  # noqa: S603, S607
        log(f"started container {spec['container']}")


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
        f"baseline traffic for {scenario.watched}",
        lambda: prom_query(
            "sum(rate(traces_span_metrics_calls_total"
            f'{{service_name="{scenario.watched}"}}[3m])) > 0'
        ),
        900,
        15,
    )
    log(f"baseline traffic present: {traffic}")
    rules = get_json(f"{PROMETHEUS}/api/v1/rules")["data"]["groups"]
    if not any(g["name"] == "iip-demo" for g in rules):
        raise RuntimeError("Prometheus did not load the iip-demo rule group")

    t_on = time.monotonic()
    inject(demo_dir, scenario)

    target = scenario.watched
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

    evidence = check_evidence(target_id, target, scenario, run_dir)
    analysis = check_analysis(target_id, scenario)

    restore(demo_dir, scenario)
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
        "evidence": evidence,
        "analysis": analysis,
    }


def check_evidence(
    incident_id: str, service: str, scenario: Scenario, run_dir: Path
) -> dict[str, Any]:
    """Evidence is collected, then collected again with Loki stopped (M3 acceptance).

    1. Wait for the first collection and count evidence by kind.
    2. Stop Loki and report a deploy to the failing service. A deploy during an
       incident schedules another collection, which must still succeed for the
       other sources and record "logs unavailable" on the timeline.
    """
    t0 = time.monotonic()

    def enriched() -> dict[str, Any] | None:
        inc = get_json(f"{IIP}/v1/incidents/{incident_id}")
        return inc if inc.get("enriched_at") else None

    first = wait_for("evidence collection", enriched, 300, 5)
    export_snapshot(first, scenario, run_dir)
    seconds_to_evidence = round(time.monotonic() - t0)
    kinds: dict[str, int] = {}
    for item in first["evidence"]:
        kinds[item["kind"]] = kinds.get(item["kind"], 0) + 1
    unavailable = [t["reason"] for t in first["timeline"] if t["kind"] == "evidence_unavailable"]
    log(f"evidence after {seconds_to_evidence}s: {kinds}, unavailable: {unavailable}")
    for item in first["evidence"]:
        log(f"  {item['id']}: {item['summary']}")

    subprocess.run(["docker", "stop", "loki"], check=True, capture_output=True)  # noqa: S607
    post_json(
        f"{IIP}/v1/deploys",
        {
            "service": service,
            "version": "chaos-hotfix",
            "description": "Simulated hotfix deploy during the incident",
        },
        token=LOCAL_TOKEN,
    )

    def logs_missing() -> list[str] | None:
        inc = get_json(f"{IIP}/v1/incidents/{incident_id}")
        reasons = [
            t["reason"]
            for t in inc["timeline"]
            if t["kind"] == "evidence_unavailable" and t.get("source") == "logs"
        ]
        return reasons or None

    loki_down = wait_for("a 'logs unavailable' note with Loki stopped", logs_missing, 300, 5)
    subprocess.run(["docker", "start", "loki"], check=False, capture_output=True)  # noqa: S607
    after = get_json(f"{IIP}/v1/incidents/{incident_id}")
    log(f"with Loki stopped: {loki_down[0]}")
    return {
        "seconds_to_evidence": seconds_to_evidence,
        "evidence_by_kind": kinds,
        "unavailable_first_run": unavailable,
        "loki_down_note": loki_down[0],
        "evidence_after_loki_down": len(after["evidence"]),
        "deploy_evidence": [e["summary"] for e in after["evidence"] if e["kind"] == "deploy"],
        "source_ms": {
            t["source"]: t.get("duration_ms")
            for t in first["timeline"]
            if t["kind"] == "evidence_collected"
        },
        "examples": {
            kind: next(e["summary"] for e in first["evidence"] if e["kind"] == kind)
            for kind in kinds
        },
    }


def export_snapshot(incident: dict[str, Any], scenario: Scenario, run_dir: Path) -> None:
    """Record what the analysis sees, plus the known cause, for the offline eval (M4).

    Written to the run directory and printed to the log (gzip, base64) between
    markers, so it can be collected without downloading artifacts.
    """
    unavailable = {
        t["source"]: t["reason"]
        for t in incident["timeline"]
        if t["kind"] == "evidence_unavailable" and t.get("source")
    }
    evidence = []
    for e in incident["evidence"]:
        payload = {k: v for k, v in e["payload"].items() if k != "points"}  # not in the prompt
        evidence.append({**e, "payload": payload})
    run_id = os.environ.get("GITHUB_RUN_ID", "local")
    attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "1")
    snapshot = {
        "id": f"{scenario.name}-{run_id}-{attempt}",
        "scenario": scenario.truth(),
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": {
            "commit": os.environ.get("GITHUB_SHA", "local"),
            "run_id": run_id,
            "demo_version": os.environ.get("OTEL_DEMO_REF", ""),
        },
        "incident": {k: incident[k] for k in ("title", "status", "services", "started_at")},
        "alerts": [
            {
                k: a[k]
                for k in (
                    "alertname",
                    "status",
                    "service",
                    "severity",
                    "labels",
                    "annotations",
                    "starts_at",
                    "ends_at",
                )
            }
            for a in incident["alerts"]
        ],
        "evidence": evidence,
        "unavailable": unavailable,
    }
    text = json.dumps(snapshot, indent=2, sort_keys=True)
    (run_dir / "snapshot.json").write_text(text)
    packed = base64.b64encode(gzip.compress(text.encode())).decode()
    print(f"SNAPSHOT-BEGIN {snapshot['id']}")
    for i in range(0, len(packed), 4000):
        print(packed[i : i + 4000])
    print("SNAPSHOT-END", flush=True)
    log(f"snapshot {snapshot['id']}: {len(evidence)} evidence items, {len(text)} bytes")


def check_analysis(incident_id: str, scenario: Scenario) -> dict[str, Any]:
    """Wait for the root-cause analysis and compare it with the known cause."""

    def analyzed() -> dict[str, Any] | None:
        inc = get_json(f"{IIP}/v1/incidents/{incident_id}")
        analysis = inc.get("analysis") or {}
        if analysis.get("status") == "done" and not analysis.get("in_progress"):
            return analysis
        if analysis.get("status") == "failed":
            return analysis
        return None

    analysis = wait_for("the root-cause analysis", analyzed, 300, 5)
    if analysis.get("status") != "done":
        return {"status": "failed"}
    result = analysis["result"]
    hypotheses = result.get("hypotheses", [])
    services = [h["root_cause_service"] for h in hypotheses]
    pairs = [(h["root_cause_service"], h["category"]) for h in hypotheses]
    truth = (scenario.root_cause_service, scenario.category)
    for h in hypotheses:
        log(
            f"  #{h['rank']} {h['root_cause_service']} / {h['category']} ({h['confidence']}): "
            f"{h['explanation']} {h['evidence_ids']}"
        )
    return {
        "status": "done",
        "model": analysis.get("model"),
        "latency_ms": analysis.get("latency_ms"),
        "top": pairs[0] if pairs else None,
        "truth": truth,
        "service_top1": bool(services) and services[0] == scenario.root_cause_service,
        "top1": bool(pairs) and pairs[0] == truth,
        "top3": truth in pairs[:3],
        "citations": result.get("citations"),
        "summary": result.get("summary"),
    }


def post_json(url: str, body: dict[str, Any], token: str) -> Any:
    request = urllib.request.Request(  # noqa: S310 - fixed local URL
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as resp:  # noqa: S310
        return json.loads(resp.read())


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
        json.dumps(
            {k: v for k, v in result.items() if k not in {"summary_text", "evidence", "analysis"}}
        ),
    )
    if result.get("summary_text"):
        annotate("notice", "summary text", result["summary_text"])
    annotate("notice", "analysis", json.dumps(result.get("analysis") or {}))
    evidence = result.get("evidence") or {}
    annotate("notice", "evidence", json.dumps(evidence))
    missing = {"metric", "log", "trace"} - set(evidence.get("evidence_by_kind", {}))
    if missing:
        annotate("warning", "evidence missing", f"no {', '.join(sorted(missing))} evidence")
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
    parser.add_argument("--demo-dir", type=Path, default=Path("../otel-demo"))
    scenarios = load_scenarios()
    parser.add_argument("--scenario", choices=sorted(scenarios), default="payment_failure")
    parser.add_argument("--list", action="store_true", help="print scenario names as JSON")
    parser.add_argument("--run-dir", type=Path, default=REPO / "chaos" / "runs" / "latest")
    parser.add_argument("--demo-version", default="3.1.0")
    args = parser.parse_args()
    if args.list:
        print(json.dumps(sorted(scenarios)))
        return 0

    try:
        result = run(
            args.demo_dir.resolve(),
            scenarios[args.scenario],
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
