"""Build the configured LLM provider from settings."""

from iip.config import Settings
from iip.llm.base import LLMProvider
from iip.llm.fake import FakeProvider
from iip.llm.gemini import GeminiProvider


def build_provider(settings: Settings) -> LLMProvider | None:
    if settings.llm_provider == "none":
        return None
    if settings.llm_provider == "fake":
        return FakeProvider(model="fake-model")
    # "gemini": the settings validator guarantees the key is present.
    if settings.gemini_api_key is None:  # pragma: no cover - guarded by Settings
        raise ValueError("GEMINI_API_KEY is required for the gemini provider")
    return GeminiProvider(
        api_key=settings.gemini_api_key.get_secret_value(),
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
    )
