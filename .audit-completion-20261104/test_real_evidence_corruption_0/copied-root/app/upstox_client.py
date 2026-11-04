import requests
from requests.exceptions import JSONDecodeError
from requests.adapters import HTTPAdapter
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
import multiprocessing
import threading
import time

from app.config import get_settings

BASE_URL = "https://api.upstox.com"
_WINDOW = ContextVar("upstox_read_window", default=None)
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


@contextmanager
def request_window(*, end, before_attempt, max_attempts=3, before_send=None):
    """Bind physical GET attempts to a verified acquisition window in the caller."""
    if (end.tzinfo is None or not callable(before_attempt)
            or type(max_attempts) is not int or not 1 <= max_attempts <= 3
            or (before_send is not None and not callable(before_send))):
        raise ValueError("verified request window required")
    token = _WINDOW.set((end, before_attempt, max_attempts, before_send))
    try:
        yield
    finally:
        _WINDOW.reset(token)


def _utc_now():
    return datetime.now(timezone.utc)


def _http_worker(pipe, url, headers, params, timeout):
    """One GET, no implicit retries/redirects. Credentials travel only via local IPC."""
    try:
        with requests.Session() as session:
            session.mount("https://", HTTPAdapter(max_retries=0))
            with session.get(url, headers=headers, params=params, timeout=timeout,
                             allow_redirects=False, stream=True) as response:
                status = response.status_code
                retry_after = response.headers.get("Retry-After")
                if retry_after is not None and (not isinstance(retry_after, str) or len(retry_after) > 128):
                    retry_after = "INVALID"
                result = {"status": status, "retry_after": retry_after}
                if 200 <= status < 300:
                    body = bytearray()
                    for chunk in response.iter_content(8192):
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise ValueError()
                    try:
                        import json
                        result["data"] = json.loads(body)
                    except (UnicodeError, ValueError):
                        result["error"] = "json_decode"
                pipe.send(result)
    except (requests.ConnectTimeout, requests.ConnectionError):
        pipe.send({"error": "network_error", "retryable": True})
    except Exception:
        pipe.send({"error": "network_error", "retryable": False})
    finally:
        pipe.close()


def _bounded_get(url, headers, params, timeout, remaining):
    """Killable isolation also bounds DNS, headers and slow streaming bodies.

    A thread timeout would leave the request running. Spawned isolation permits
    termination at the deadline; at most one second is allowed for worker cleanup.
    """
    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    worker = context.Process(target=_http_worker, args=(send, url, headers, params, timeout), daemon=True)
    started = time.monotonic()
    timer = None
    try:
        worker.start()
        send.close()
        left = remaining - (time.monotonic() - started)
        if left <= 0:
            raise TimeoutError()
        def stop_worker():
            try:
                if worker.is_alive():
                    worker.terminate()
            except Exception:
                pass
        timer = threading.Timer(left, stop_worker)
        timer.daemon = True
        timer.start()
        if not receive.poll(left):
            raise TimeoutError()
        return receive.recv()
    except Exception:
        return {"error": "network_error", "retryable": False}
    finally:
        if timer is not None:
            timer.cancel()
        if worker.pid is not None:
            if worker.is_alive():
                worker.terminate()
            worker.join(timeout=0.5)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=0.5)
        receive.close()
        send.close()


def _retry_delay(value, attempt, maximum):
    if value is None:
        return min(0.3 * (2 ** attempt), maximum)
    try:
        if not isinstance(value, str) or len(value) > 128:
            return None
        if value.strip().isdigit():
            delay = float(value)
        else:
            stamp = parsedate_to_datetime(value)
            if stamp.tzinfo is None:
                return None
            delay = max(0.0, (stamp - _utc_now()).total_seconds())
        return delay if math.isfinite(delay) and 0 <= delay <= maximum else None
    except Exception:
        return None


class UpstoxRequestError(RuntimeError):
    """Sanitized, machine-readable failure from the read-only Upstox client."""

    def __init__(self, message, *, stage, category, status_code=None):
        super().__init__(message)
        self.stage = stage
        self.category = category
        self.status_code = status_code


def get_headers():
    token = get_settings().upstox_access_token
    if not token:
        raise UpstoxRequestError("Upstox access token is not configured", stage="authentication",
                                 category="authentication")

    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }


def _get(url, *, params=None):
    settings = get_settings()
    total = getattr(settings, "upstox_total_deadline_seconds", 20.0)
    maximum_delay = getattr(settings, "upstox_max_retry_delay_seconds", 1.0)
    if (type(total) not in (int, float) or not math.isfinite(total) or not 0 < total <= 60
            or type(maximum_delay) not in (int, float) or not math.isfinite(maximum_delay)
            or not 0 <= maximum_delay <= min(5, total)
            or not math.isfinite(settings.request_timeout_seconds) or settings.request_timeout_seconds <= 0):
        raise UpstoxRequestError("Upstox request timing configuration is invalid", stage="upstox_http", category="network_error")
    headers = get_headers()
    deadline = time.monotonic() + total
    window = _WINDOW.get()
    if window is not None:
        deadline = min(deadline, time.monotonic() + max(0, (window[0] - _utc_now()).total_seconds()))
    max_attempts = window[2] if window is not None else 3
    for attempt in range(max_attempts):
        if window is not None:
            try:
                window[1]()
            except Exception:
                raise UpstoxRequestError("Upstox acquisition window ended", stage="upstox_http", category="network_error") from None
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise UpstoxRequestError("Upstox total request deadline exceeded", stage="upstox_http", category="network_error")
        if window is not None and window[3] is not None:
            window[3]()  # Durable physical-attempt checkpoint, immediately before I/O.
        result = _bounded_get(url, headers, params, min(settings.request_timeout_seconds, remaining), remaining)
        if time.monotonic() >= deadline:
            raise UpstoxRequestError("Upstox total request deadline exceeded", stage="upstox_http", category="network_error")
        status = result.get("status")
        if status in (401, 403):
            raise UpstoxRequestError("Upstox authentication failed; refresh the access token", stage="upstox_http",
                                     category="authentication", status_code=status)
        category = "rate_limit" if status == 429 else "http_error" if status is not None else "network_error"
        retryable = status in (429, 500, 502, 503, 504) or result.get("retryable") is True
        if retryable and attempt < max_attempts - 1:
            delay = _retry_delay(result.get("retry_after"), attempt, maximum_delay)
            if delay is not None and delay < deadline - time.monotonic():
                time.sleep(delay)
                continue
        if type(status) is not int or not 200 <= status < 300:
            raise UpstoxRequestError("Upstox read-only request failed", stage="upstox_http", category=category,
                                     status_code=status if type(status) is int and 100 <= status <= 599 else None)
        if result.get("error") == "json_decode":
            raise UpstoxRequestError("Upstox response was not valid JSON", stage="response_json", category="json_decode", status_code=status)
        return result["data"]
    raise AssertionError("unreachable bounded retry loop")


def get_profile():
    return _get(f"{BASE_URL}/v2/user/profile")


def get_quote(instrument_key):
    return _get(
        f"{BASE_URL}/v3/market-quote/quotes",
        params={"instrument_key": instrument_key},
    )


def get_intraday_candles(instrument_key, interval=5):
    url = (
        f"{BASE_URL}/v3/historical-candle/intraday/"
        f"{instrument_key}/minutes/{interval}"
    )

    return _get(url)

def get_historical_candles(
    instrument_key,
    date,
    interval=5,
):
    url = (
        f"{BASE_URL}/v3/historical-candle/"
        f"{instrument_key}/minutes/{interval}/{date}"
    )

    return _get(url)
changed