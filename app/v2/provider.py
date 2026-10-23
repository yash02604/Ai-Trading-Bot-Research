"""OpenRouter adapter. Explicit API key injection; never logs headers or response text."""
from dataclasses import dataclass
import math
import time

MAX_PROVIDER_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_COMPLETION_TOKENS = 512
DEFAULT_MAX_RESPONSE_BYTES = 16 * 1024


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
    def __init__(self, *, api_key: str, model_id: str, timeout_seconds: float = 10.0, client=None,
                 max_completion_tokens: int = DEFAULT_MAX_COMPLETION_TOKENS,
                 max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES):
        if (not isinstance(api_key, str) or not api_key.strip()
                or not isinstance(model_id, str) or not model_id.strip()
                or isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
                or not math.isfinite(timeout_seconds) or timeout_seconds <= 0
                or timeout_seconds > MAX_PROVIDER_TIMEOUT_SECONDS):
            raise ValueError("explicit API key, model ID, and timeout within the provider bound are required")
        if (isinstance(max_completion_tokens, bool) or not isinstance(max_completion_tokens, int)
                or not 1 <= max_completion_tokens <= 2048):
            raise ValueError("max_completion_tokens must be an integer from 1 to 2048")
        if (isinstance(max_response_bytes, bool) or not isinstance(max_response_bytes, int)
                or not 1 <= max_response_bytes <= 65536):
            raise ValueError("max_response_bytes must be an integer from 1 to 65536")
        self._api_key = api_key
        self.model_id = model_id
        self.timeout_seconds = timeout_seconds
        self.max_completion_tokens = max_completion_tokens
        self.max_response_bytes = max_response_bytes
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
            max_tokens=self.max_completion_tokens,
        )
        latency = (time.perf_counter() - started) * 1000
        choice = response.choices[0]
        content = choice.message.content
        if not isinstance(content, str):
            raise ValueError("provider returned no text content")
        if len(content.encode("utf-8")) > self.max_response_bytes:
            raise ValueError("provider response exceeded the configured byte limit")
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
