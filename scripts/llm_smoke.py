"""Live check against the real Gemini API (run by .github/workflows/llm-smoke.yml).

    python scripts/llm_smoke.py list-models   # which models this key can call
    python scripts/llm_smoke.py summarize     # summarize a fixture incident

Results are printed as GitHub annotations so they show on the PR. Only synthetic
fixture data is sent; the API key comes from GEMINI_API_KEY and is never printed.
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from iip.db.models import Alert, Incident
from iip.llm.base import LLMError
from iip.llm.gemini import GeminiProvider
from iip.llm.prompting import load_prompt
from iip.llm.retry import with_retries
from iip.llm.summarize import SUMMARY_PROMPT, build_prompt

FIXTURE = Path(__file__).resolve().parents[1] / "tests/fixtures/alertmanager/two_alerts_firing.json"


def _provider() -> GeminiProvider:
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        print("::error::GEMINI_API_KEY is not set (add it as a repository secret)")
        raise SystemExit(1)
    model = os.environ.get("IIP_LLM_MODEL", "gemini-3.5-flash-lite")
    return GeminiProvider(api_key=key, model=model, timeout_seconds=60)


def _fixture_incident() -> Incident:
    data = json.loads(FIXTURE.read_text())
    alerts = [
        Alert(
            alertname=a["labels"].get("alertname", "unnamed"),
            status=a["status"],
            labels=a["labels"],
            annotations=a["annotations"],
            starts_at=datetime.fromisoformat(a["startsAt"].replace("Z", "+00:00")),
        )
        for a in data["alerts"]
    ]
    return Incident(
        title="cart latency and recommendation down",
        status="open",
        services=["cart", "recommendation"],
        started_at=min(a.starts_at for a in alerts),
        alerts=alerts,
    )


async def list_models() -> None:
    provider = _provider()
    try:
        models = await provider.list_models()
    finally:
        await provider.aclose()
    gemini = sorted(m for m in models if "gemini" in m)
    print(f"::notice title=models ({len(models)} total)::{', '.join(gemini)[:3000]}")


async def summarize() -> None:
    provider = _provider()
    prompt = load_prompt(*SUMMARY_PROMPT)
    user_prompt, redactions = build_prompt(prompt, _fixture_incident())
    try:
        response = await with_retries(
            lambda: provider.generate(user_prompt, system=prompt.system),
            max_retries=2,
        )
    except LLMError as exc:
        print(f"::error title=summary failed ({provider.model})::{exc}")
        raise SystemExit(1) from exc
    finally:
        await provider.aclose()
    print(
        f"::notice title=summary ok::model={response.model} latency_ms={response.latency_ms} "
        f"input_tokens={response.input_tokens} output_tokens={response.output_tokens} "
        f"redactions={redactions}"
    )
    print(f"::notice title=summary text::{response.text.replace(chr(10), ' ')[:1500]}")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "list-models":
        asyncio.run(list_models())
    elif command == "summarize":
        asyncio.run(summarize())
    else:
        raise SystemExit(f"usage: {sys.argv[0]} list-models|summarize")
