"""DeepSeek adapter. Explicit keys; no raw headers or unrestricted response text retained."""
from dataclasses import dataclass
import math
import time

MAX_PROVIDER_TIMEOUT_SECONDS = 20.0
DEFAULT_MAX_COMPLETION_TOKENS = 512
DEFAULT_MAX_RESPONSE_BYTES = 16 * 1024
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
MAX_ERROR_BODY_BYTES = 8192
MAX_ERROR_MESSAGE_CHARS = 256


@dataclass(frozen=True)
class ProviderResult:
    content: str
    provider_id: str
    model_id: str
    requested_model_id: str
    latency_ms: float
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    finish_reason: str | None = None
    response_bytes: int | None = None


class ProviderResponseError(ValueError):
    """Safe provider-response failure with a bounded diagnostic code."""

    def __init__(self, diagnostic_code: str):
        self.diagnostic_code = diagnostic_code
        super().__init__(diagnostic_code)


_SAFE_PROVIDER_CODES = frozenset({
    "invalid_request", "invalid_request_error", "invalid_request_format", "invalid_parameter",
    "authentication_error", "authentication_failure", "invalid_api_key", "insufficient_balance",
    "rate_limit", "rate_limit_error", "server_error", "internal_server_error", "model_not_found",
})
_SAFE_PROVIDER_MESSAGES = frozenset({
    "Invalid request body", "Invalid request body.", "Invalid request format", "Invalid request format.",
    "Invalid request", "Invalid request.", "Authentication Fails", "Invalid Authentication",
    "Insufficient Balance", "Rate Limit Reached", "Server Error", "Server Overloaded",
    "Unknown parameter: max_completion_tokens", "Unrecognized request argument supplied: max_completion_tokens",
    "Invalid response_format", "Invalid model",
    "Response format requires one of the messages to contain the word 'json' in some form.",
})
_ERROR_CATEGORIES = frozenset({"invalid_request", "authentication_failure", "insufficient_balance",
    "rate_limit", "server_error", "timeout", "transport_failure", "http_error"})


def _allowed_error_text(value, allowed, api_key=None):
    # Return a known constant, never a substring of an arbitrary provider message.
    if (type(value) is not str or len(value) > MAX_ERROR_MESSAGE_CHARS
            or (api_key and api_key.casefold() in value.casefold())):
        return None
    return next((item for item in allowed if value.strip().casefold() == item.casefold()), None)


def provider_error_diagnostic(error, *, api_key=None):
    """Conservative error projection; raw bodies/headers/exception text never escape."""
    status = getattr(error, "status_code", None)
    if status is None:
        status = getattr(getattr(error, "response", None), "status_code", None)
    if isinstance(status, bool) or not isinstance(status, int) or not 100 <= status <= 599:
        status = None
    category = {400: "invalid_request", 401: "authentication_failure", 403: "authentication_failure",
                402: "insufficient_balance", 408: "timeout", 429: "rate_limit"}.get(status)
    if category is None:
        category = ("server_error" if status is not None and status >= 500 else
                    "http_error" if status is not None else
                    "timeout" if isinstance(error, TimeoutError) or type(error).__name__ == "APITimeoutError"
                    else "transport_failure")
    body = getattr(error, "body", None)
    response = getattr(error, "response", None)
    raw = getattr(response, "content", b"")
    oversized = type(raw) is bytes and len(raw) > MAX_ERROR_BODY_BYTES
    missing = body is None or (type(body) in (str, bytes) and not body)
    body_status = "oversized" if oversized else "missing" if missing else "json" if type(body) is dict else "non_json"
    fields = body.get("error", body) if type(body) is dict and not oversized else {}
    if type(fields) is not dict:
        fields = {}
    message = fields.get("message")
    safe_message = _allowed_error_text(message, _SAFE_PROVIDER_MESSAGES, api_key)
    return {"http_status": status, "category": category,
        "provider_error_type": None if oversized else _allowed_error_text(
            fields.get("type", getattr(error, "type", None)), _SAFE_PROVIDER_CODES, api_key),
        "provider_error_code": None if oversized else _allowed_error_text(
            fields.get("code", getattr(error, "code", None)), _SAFE_PROVIDER_CODES, api_key),
        "provider_error_message": safe_message, "body_status": body_status,
        "message_status": "retained" if safe_message is not None else "missing" if message is None else
                          "oversized" if type(message) is str and len(message) > MAX_ERROR_MESSAGE_CHARS else "omitted"}


def retained_provider_diagnostic(error):
    """Revalidate the exception attachment before copying it into durable receipts."""
    data = vars(error).get("provider_error_diagnostic")
    if (type(data) is not dict or set(data) != {"http_status", "category", "provider_error_type",
            "provider_error_code", "provider_error_message", "body_status", "message_status"}
            or any(type(data[k]) is not str for k in ("category", "body_status", "message_status"))
            or any(data[k] is not None and type(data[k]) is not str for k in (
                "provider_error_type", "provider_error_code", "provider_error_message"))
            or (data["http_status"] is not None and (type(data["http_status"]) is not int or not 100 <= data["http_status"] <= 599))
            or data["category"] not in _ERROR_CATEGORIES
            or data["body_status"] not in {"json", "non_json", "missing", "oversized"}
            or data["message_status"] not in {"retained", "missing", "omitted", "oversized"}
            or any(data[k] is not None and data[k] not in _SAFE_PROVIDER_CODES for k in ("provider_error_code", "provider_error_type"))
            or (data["provider_error_message"] is not None and data["provider_error_message"] not in _SAFE_PROVIDER_MESSAGES)):
        return None
    return dict(data)


class DeepSeekProvider:
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
            from openai import OpenAI, DefaultHttpxClient
            self._client = OpenAI(
                base_url=DEEPSEEK_BASE_URL, api_key=self._api_key,
                timeout=self.timeout_seconds, max_retries=0,
                http_client=DefaultHttpxClient(follow_redirects=False, timeout=self.timeout_seconds),
            )
        return self._client

    def complete(self, request_payload: dict) -> ProviderResult:
        started = time.perf_counter()
        stage = "client_initialization"
        try:
            client = self._get_client()
            stage = "request_submission_or_transport"
            response = client.chat.completions.create(
                model=self.model_id,
                messages=[
                    {"role": "system", "content": request_payload["system_prompt"]},
                    {"role": "user", "content": request_payload["user_payload"]},
                ],
                response_format={"type": "json_object"},
                max_tokens=self.max_completion_tokens,
            )
            stage = "response_extraction"
            latency = (time.perf_counter() - started) * 1000
            try:
                choices = response.choices
                if not isinstance(choices, (list, tuple)):
                    raise ProviderResponseError("unexpected_sdk_structure")
                if not choices:
                    raise ProviderResponseError("empty_response")
                choice = choices[0]
                message = choice.message
                content = message.content
            except ProviderResponseError:
                raise
            except (AttributeError, IndexError, KeyError, TypeError):
                raise ProviderResponseError("unexpected_sdk_structure") from None
            finish_reason = getattr(choice, "finish_reason", None)
            if finish_reason == "length":
                raise ProviderResponseError("truncated_output")
            if content is None or not isinstance(content, str):
                raise ProviderResponseError("missing_content")
            response_bytes = len(content.encode("utf-8"))
            if not content:
                raise ProviderResponseError("empty_response")
            if response_bytes > self.max_response_bytes:
                raise ProviderResponseError("response_exceeds_byte_limit")
        except Exception as error:
            # Attach only allowlisted diagnostics. Original exception bodies,
            # messages and request headers remain private to the caller boundary.
            try:
                diagnostic = provider_error_diagnostic(error, api_key=self._api_key)
                error.provider_error_diagnostic = diagnostic
                error.provider_stage = stage
                error.provider_diagnostic_code = getattr(
                    error, "diagnostic_code", "provider_response_error" if stage == "response_extraction"
                    else "provider_error")
                error.provider_http_status = diagnostic["http_status"]
                error.provider_error_code = diagnostic["provider_error_code"]
                error.provider_error_type = diagnostic["provider_error_type"]
            except Exception:
                pass
            raise
        usage = getattr(response, "usage", None)
        return ProviderResult(
            content=content,
            provider_id="deepseek",
            model_id=str(getattr(response, "model", "unknown")),
            requested_model_id=self.model_id,
            latency_ms=latency,
            prompt_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
            completion_tokens=getattr(usage, "completion_tokens", None) if usage else None,
            finish_reason=finish_reason,
            response_bytes=response_bytes,
        )
