"""Provider-only diagnostics. No market, calendar, broker or account imports."""
import hashlib
import json
import math
import multiprocessing
import os
from pathlib import Path
import re
import time

from app.v2.provider import DEEPSEEK_BASE_URL, DeepSeekProvider, retained_provider_diagnostic

DIAGNOSTIC_ROOT = Path(__file__).resolve().parents[2] / "diagnostics" / "deepseek_provider"
MODEL = "deepseek-flash"
DEADLINE_SECONDS = 10.0
PAYLOAD = {"system_prompt": 'Return exactly this JSON object and no other text: {"status":"OK"}.',
           "user_payload": 'Return JSON: {"status":"OK"}'}
DISABLED = {"execution_enabled": False, "trade_placement_enabled": False, "paper_execution_enabled": False}


def _write_once(path, value):
    data = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    with path.open("xb") as stream:
        stream.write(data + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _worker(send, api_key, timeout):
    provider = DeepSeekProvider(api_key=api_key, model_id=MODEL, timeout_seconds=timeout,
                                max_completion_tokens=64, max_response_bytes=1024)
    result = {"status": "FAILED", "category": "transport_failure", **DISABLED}
    try:
        response = provider.complete(PAYLOAD)
        result["category"] = "invalid_smoke_response"
        # Never save arbitrary successful model text, even if it echoes a secret.
        if (response.model_id == MODEL and response.requested_model_id == MODEL
                and json.loads(response.content) == {"status": "OK"}):
            result = {"status": "OK", "response": {"status": "OK"}, **DISABLED}
        else:
            result["category"] = "invalid_smoke_response"
    except Exception as error:
        diagnostic = retained_provider_diagnostic(error)
        if diagnostic is not None:
            result.update(category=diagnostic["category"], provider_diagnostic=diagnostic)
    finally:
        if provider._client is not None:
            try:
                provider._client.close()
            except Exception:
                pass
    try:
        send.send(result)
    finally:
        send.close()


def _bounded_request(api_key, timeout):
    """One child, one provider invocation; deadline cancellation never grants a retry."""
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    worker = context.Process(target=_worker, args=(send, api_key, timeout), daemon=True)
    started = time.monotonic()
    try:
        worker.start()
        send.close()
        remaining = DEADLINE_SECONDS - (time.monotonic() - started)
        if remaining <= 0 or not receive.poll(remaining):
            return {"status": "FAILED", "category": "timeout", **DISABLED}
        result = receive.recv()
        if time.monotonic() - started >= DEADLINE_SECONDS:
            return {"status": "FAILED", "category": "timeout", **DISABLED}
        return result
    except Exception:
        return {"status": "FAILED", "category": "transport_failure", **DISABLED}
    finally:
        if worker.pid is not None:
            if worker.is_alive():
                worker.terminate()
            worker.join(timeout=0.5)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=0.5)
        receive.close()
        send.close()


def run_provider_smoke(*, run_id, enabled=False):
    if enabled is not True:
        raise ValueError("provider_smoke_requires_explicit_authorization")
    if type(run_id) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", run_id):
        raise ValueError("invalid_diagnostic_run_id")
    # Credentials are loaded only after explicit operator opt-in.
    from app.config import get_settings
    settings = get_settings()
    if (settings.deepseek_model != MODEL or type(settings.deepseek_api_key) is not str
            or not settings.deepseek_api_key.strip()
            or type(settings.request_timeout_seconds) not in (int, float)
            or not math.isfinite(settings.request_timeout_seconds) or settings.request_timeout_seconds <= 0):
        raise ValueError("invalid_provider_smoke_settings")
    timeout = min(float(settings.request_timeout_seconds), 5.0)
    root = DIAGNOSTIC_ROOT
    if any(p.is_symlink() or getattr(p, "is_junction", lambda: False)() for p in (root, root.parent)):
        raise ValueError("diagnostic_output_must_not_be_linked")
    root.mkdir(parents=True, exist_ok=True)
    output = root / run_id
    output.mkdir()  # Exclusive claim: concurrent/restarted/partial runs cannot send again.
    request = {"schema_version": "deepseek-provider-diagnostic-v1", "base_url": DEEPSEEK_BASE_URL,
        "model": MODEL, "messages": [{"role": "system", "content": PAYLOAD["system_prompt"]},
                                   {"role": "user", "content": PAYLOAD["user_payload"]}],
        "max_tokens": 64, "response_format": {"type": "json_object"},
        "timeout_seconds": timeout, "deadline_seconds": DEADLINE_SECONDS,
        "provider_retries": 0, "follow_redirects": False, **DISABLED}
    identity = hashlib.sha256(json.dumps(request, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    _write_once(output / "request.json", request)
    _write_once(output / "request_budget_config.json", {"max_total_requests": 1})
    _write_once(output / "request_budget.json", {"request_id": identity, "requests_consumed": 1})
    # Conservative authorization, not a claim that bytes reached DeepSeek.
    _write_once(output / "send_authorization.json", {
        "request_id": identity, "status": "SINGLE_SEND_AUTHORIZED_NOT_PROOF_OF_TRANSMISSION"})
    result = _bounded_request(settings.deepseek_api_key, timeout)
    result.update(request_id=identity, requests_consumed=1, **DISABLED)
    _write_once(output / "result.json", result)
    return {"status": result["status"], "output": str(output), "requests_consumed": 1, **DISABLED}
