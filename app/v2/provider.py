"""OpenRouter adapter. Explicit API key injection; never logs headers or response text."""
from dataclasses import dataclass
import time


@dataclass(frozen=True)
class ProviderResult:
    content: str
    provider_id: str
    model_id: str
    requested_model_id: str
    latency_ms: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class OpenRouterProvider:
    def __init__(self, *, api_key: str, model_id: str, timeout_seconds: float = 10.0, client=None):
        if not api_key or not model_id or timeout_seconds <= 0:
            raise ValueError("explicit API key, model ID, and positive timeout are required")
        self._api_key = api_key
        self.model_id = model_id
        self.timeout_seconds = timeout_seconds
        self._client = client

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(
                base_url="https://openrouter.ai/api/v1", api_key=self._api_key,
                timeout=self.timeout_seconds, max_retries=0,
            )
        return self._client

    def complete(self, request_payload: dict) -> ProviderResult:
        started = time.perf_counter()
        response = self._get_client().chat.completions.create(
            model=self.model_id,
            messages=[
                {"role": "system", "content": request_payload["system_prompt"]},
                {"role": "user", "content": request_payload["user_payload"]},
            ],
            response_format={"type": "json_object"},
        )
        latency = (time.perf_counter() - started) * 1000
        choice = response.choices[0]
        content = choice.message.content
        if not isinstance(content, str):
            raise ValueError("provider returned no text content")
        usage = getattr(response, "usage", None)
        return ProviderResult(
            content=content,
            provider_id="openrouter",
            model_id=str(getattr(response, "model", "unknown")),
            requested_model_id=self.model_id,
            latency_ms=latency,
            prompt_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
            completion_tokens=getattr(usage, "completion_tokens", None) if usage else None,
        )
