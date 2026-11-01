# 0005: LLM provider interface, Gemini first

- Status: accepted
- Date: 2026-11-01

## Context

The platform calls an LLM to summarize incidents (M1) and later to produce root-cause hypotheses (M4). Self-hosters have different constraints: some can use a hosted API, others won't send logs outside their network. The hosted demo must cost nothing. Tests must never call a real model.

## Options considered

1. **One vendor SDK called directly** (for example `google-genai` everywhere). Fastest to write, but every call site depends on one vendor, and swapping means touching all of them.
2. **A framework like LangChain or LiteLLM.** Many providers at once, but a large dependency surface for the few calls we make, and less control over timeouts, retries and error types.
3. **A small `LLMProvider` protocol** with one implementation per provider, written against each provider's HTTP API with httpx.

## Decision

Option 3. `LLMProvider.generate(prompt, system=...) -> LLMResponse` with typed `LLMError(retryable=...)`. Implementations: Gemini (now), Anthropic and Ollama (later milestones), and a `FakeProvider` for tests. The hosted demo uses Gemini's free tier.

## Consequences

- Swapping models is a config change (`IIP_LLM_PROVIDER`, `IIP_LLM_MODEL`), and M4's model comparison can run the same eval across providers.
- We own retry and error classification: 429, 5xx, timeouts and connection errors retry with exponential backoff and full jitter; 4xx and safety blocks don't.
- Every analysis row stores provider, model (as reported by the API), prompt version, tokens, latency, attempts and redaction count, so any result can be traced and costed.
- Prompts are versioned files inside the package (`src/iip/llm/prompts/`), so they ship in the Docker image. Changing a prompt means adding a new version.
- Text is redacted before it reaches any provider. M1 covers common secret and email patterns; M6 extends it.
- Gemini's free tier may use prompts to improve Google's products. The demo only sends synthetic chaos-test data; the self-hosting docs will say to use a paid key or Ollama for real data.
- More code to maintain per provider than with a framework. Acceptable at three providers.
