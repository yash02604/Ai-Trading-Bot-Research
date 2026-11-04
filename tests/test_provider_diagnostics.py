"""Offline diagnostic retention and provider-only smoke boundaries."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_ai_led_reviewed import setup, used
from test_signal_request_remediation import options
from app.research import ai_signal_only as signal, ai_led_guarded as guard
from app.v2 import provider_smoke as smoke
from app.v2.provider import DeepSeekProvider, provider_error_diagnostic, retained_provider_diagnostic

_REAL_GET_CLIENT = DeepSeekProvider._get_client


def files(output):
    return {p.relative_to(output).as_posix(): p.read_bytes() for p in output.rglob("*") if p.is_file()}


@pytest.fixture
def http(setup, monkeypatch):
    import openai
    import httpx2
    default = openai.DefaultHttpxClient
    state = {"status": 400, "body": {"error": {"type": "invalid_request_error",
             "code": "invalid_parameter", "message": "Invalid request body"}}}
    calls, providers, transports = [], [], []

    def handler(request):
        calls.append(json.loads(request.content))
        if state.get("raise"):
            raise state["raise"]("synthetic secret transport text", request=request)
        if state["status"] == 200:
            return httpx2.Response(200, request=request, json={"id": "synthetic",
                "object": "chat.completion", "created": 1, "model": "deepseek-flash",
                "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant",
                    "content": state.get("success", '{"status":"OK"}')}}]})
        if type(state["body"]) is bytes:
            return httpx2.Response(state["status"], content=state["body"], request=request)
        return httpx2.Response(state["status"], json=state["body"], request=request)

    def transport(**kwargs):
        transports.append(kwargs)
        return default(transport=httpx2.MockTransport(handler), **kwargs)

    def client(provider):
        providers.append(provider)
        return _REAL_GET_CLIENT(provider)

    monkeypatch.setattr(openai, "DefaultHttpxClient", transport)
    monkeypatch.setattr(DeepSeekProvider, "_get_client", client)
    yield SimpleNamespace(state=state, calls=calls, providers=providers, transports=transports)
    for provider in providers:
        if provider._client is not None:
            provider._client.close()


CASES = [
    (400, {"error": {"type": "invalid_request_error", "code": "invalid_parameter",
                     "message": "Invalid request body"}}, "invalid_request", "json", "retained"),
    (400, {"error": {"type": "sk-FAKE-KEY-PRIVATE", "code": "Authorization",
                     "message": "Bearer sk-FAKE-KEY-PRIVATE DEEPSEEK_API_KEY=sk-secret"}}, "invalid_request", "json", "omitted"),
    (401, {"error": {"type": "authentication_error", "message": "Authentication Fails"}},
     "authentication_failure", "json", "retained"),
    (402, {"error": {"code": "insufficient_balance", "message": "Insufficient Balance"}},
     "insufficient_balance", "json", "retained"),
    (429, {"error": {"type": "rate_limit_error", "message": "Rate Limit Reached"}}, "rate_limit", "json", "retained"),
    (500, {"error": {"type": "server_error", "message": "Server Error"}}, "server_error", "json", "retained"),
    (400, b"non-JSON private response sk-FAKE-KEY-PRIVATE", "invalid_request", "non_json", "missing"),
    (400, {"error": {"message": "x" * 300}}, "invalid_request", "json", "oversized"),
    (400, b"", "invalid_request", "missing", "missing"),
    (400, {"error": {"message": "Invalid request body"}, "echo": "x" * 9000},
     "invalid_request", "oversized", "missing"),
    (400, {"error": {"message": "unknown provider reason with arbitrary echoed content"}},
     "invalid_request", "json", "omitted"),
]


@pytest.mark.parametrize("status,body,category,body_status,message_status", CASES)
def test_http_error_retention_and_signal_failure_are_single_use(
        setup, http, status, body, category, body_status, message_status):
    http.state.update(status=status, body=body)
    opts = options(setup)
    record = signal.run_signal_only(**opts)
    assert record["status"] == "PROVIDER_FAILURE" and record["prediction"] is None
    assert record["taken"] is record["execution_enabled"] is record["trade_placement_enabled"] is False
    evidence = guard._read(next((setup.output / "responses").glob("*.json")))["evidence"]
    assert evidence["reservation_consumed"] is True and evidence["core_response"] is None
    receipt = next(json.loads(line) for line in (setup.output / "transport_receipts.jsonl").read_text().splitlines()
                   if json.loads(line)["kind"] == "deepseek")
    diagnostic = receipt["provider_diagnostic"]
    assert diagnostic["http_status"] == status and diagnostic["category"] == category
    assert diagnostic["body_status"] == body_status and diagnostic["message_status"] == message_status
    assert set(diagnostic) == {"http_status", "category", "provider_error_type", "provider_error_code",
                               "provider_error_message", "body_status", "message_status"}
    assert diagnostic["provider_error_message"] == (
        body["error"]["message"] if message_status == "retained" else None)
    if status == 400 and message_status == "retained":
        assert diagnostic["provider_error_type"] == "invalid_request_error"
        assert diagnostic["provider_error_code"] == "invalid_parameter"
    before = files(setup.output)
    for _ in range(2):
        assert signal.run_signal_only(**opts) == record
    assert files(setup.output) == before
    assert len(http.calls) == len(setup.http) == used(setup, "request_budget") == 1
    assert http.providers[0]._client.max_retries == 0 and http.transports[0]["follow_redirects"] is False
    saved = b"\n".join(before.values())
    for forbidden in (setup.settings.deepseek_api_key, setup.settings.upstox_access_token,
                      "Authorization", "Bearer ", "DEEPSEEK_API_KEY", "arbitrary echoed", "private response"):
        assert forbidden.encode() not in saved
    assert not (setup.output / "paper_account").exists()


@pytest.mark.parametrize("kind,category", [("ReadTimeout", "timeout"), ("ConnectError", "transport_failure")])
def test_timeout_and_transport_are_not_detailed_provider_reasons(setup, http, kind, category):
    import httpx2
    http.state["raise"] = getattr(httpx2, kind)
    record = signal.run_signal_only(**options(setup))
    receipt = [json.loads(line) for line in (setup.output / "transport_receipts.jsonl").read_text().splitlines()][-1]
    diagnostic = receipt["provider_diagnostic"]
    assert diagnostic["category"] == category and diagnostic["http_status"] is None
    assert diagnostic["provider_error_message"] is None and record["prediction"] is None
    assert used(setup, "request_budget") == len(http.calls) == 1


@pytest.mark.parametrize("value", [
    {"category": []}, {"provider_error_code": {}}, {"provider_error_message": "Bearer sk-secret"},
])
def test_untrusted_diagnostic_attachment_is_revalidated(value):
    error = RuntimeError("private")
    error.provider_error_diagnostic = {**provider_error_diagnostic(error), **value}
    assert retained_provider_diagnostic(error) is None


def test_even_allowlisted_text_cannot_equal_configured_secret():
    error = RuntimeError()
    error.body = {"message": "Invalid request body", "code": "invalid_request_error"}
    diagnostic = provider_error_diagnostic(error, api_key="Invalid request body")
    assert diagnostic["provider_error_message"] is None
    diagnostic = provider_error_diagnostic(error, api_key="invalid_request_error")
    assert diagnostic["provider_error_code"] is None


@pytest.fixture
def worker(setup, http, tmp_path, monkeypatch):
    """No child process is launched: exercise its target against mocked HTTP."""
    import app.config
    monkeypatch.setattr(app.config, "get_settings", lambda: setup.settings)
    monkeypatch.setattr(smoke, "DIAGNOSTIC_ROOT", tmp_path / "diagnostics" / "deepseek_provider")
    process = SimpleNamespace(terminated=0, killed=0, joins=[], poll=[], starts=0, timed_out=False)
    queue = []
    class Connection:
        def send(self, value): queue.append(value)
        def close(self): pass
        def poll(self, timeout):
            process.poll.append(timeout)
            return bool(queue)
        def recv(self): return queue.pop(0)
    class Process:
        pid = None
        def __init__(self, *, target, args, daemon):
            self.target, self.args = target, args
            assert daemon is True
        def start(self):
            self.pid = 1
            process.starts += 1
            if not process.timed_out:
                self.target(*self.args)
        def is_alive(self): return process.timed_out
        def terminate(self): process.terminated += 1
        def kill(self): process.killed += 1
        def join(self, timeout): process.joins.append(timeout)
    context = SimpleNamespace(Pipe=lambda duplex: (Connection(), Connection()), Process=Process)
    monkeypatch.setattr(smoke.multiprocessing, "get_context", lambda mode: context if mode == "spawn" else None)
    return process


@pytest.mark.parametrize("status", [200, 400, 401, 402, 429, 500])
def test_smoke_has_separate_exact_one_budget_and_no_market_execution(setup, http, worker, status):
    http.state["status"] = status
    if status != 200:
        http.state["body"] = {"error": {"message": "Invalid request body", "type": "invalid_request_error"}}
    result = smoke.run_provider_smoke(run_id="synthetic-provider-only", enabled=True)
    output = Path(result["output"])
    assert result["status"] == ("OK" if status == 200 else "FAILED")
    assert len(http.calls) == worker.starts == 1 and setup.http == setup.sdk == []
    assert http.calls[0] == {"model": "deepseek-flash", "messages": [
        {"role": "system", "content": smoke.PAYLOAD["system_prompt"]},
        {"role": "user", "content": smoke.PAYLOAD["user_payload"]}],
        "max_tokens": 64, "response_format": {"type": "json_object"}}
    assert json.loads((output / "request_budget_config.json").read_bytes()) == {"max_total_requests": 1}
    assert json.loads((output / "request_budget.json").read_bytes())["requests_consumed"] == 1
    assert set(files(output)) == {"request.json", "request_budget_config.json", "request_budget.json",
                                  "send_authorization.json", "result.json"}
    assert http.providers[0]._client.max_retries == 0 and http.transports[0]["follow_redirects"] is False
    assert http.transports[0]["timeout"] == 5 and all(0 < n <= 10 for n in worker.poll)
    assert result["execution_enabled"] is result["paper_execution_enabled"] is result["trade_placement_enabled"] is False
    before = files(output)
    for _ in range(2):
        with pytest.raises(FileExistsError):
            smoke.run_provider_smoke(run_id="synthetic-provider-only", enabled=True)
    assert files(output) == before and len(http.calls) == 1
    data = b"\n".join(before.values())
    assert setup.settings.deepseek_api_key.encode() not in data and b"Authorization" not in data
    assert not (output / "signal_experiment.json").exists()


def test_smoke_deadline_stops_worker_without_replacement(setup, http, worker):
    worker.timed_out = True
    result = smoke.run_provider_smoke(run_id="synthetic-timeout", enabled=True)
    output = Path(result["output"])
    assert result["status"] == "FAILED"
    assert json.loads((output / "result.json").read_bytes())["category"] == "timeout"
    assert worker.terminated == worker.killed == 1 and worker.joins == [0.5, 0.5]
    assert worker.starts == 1 and http.calls == []
    with pytest.raises(FileExistsError):
        smoke.run_provider_smoke(run_id="synthetic-timeout", enabled=True)


@pytest.mark.parametrize("boundary", ["request.json", "request_budget_config.json", "request_budget.json",
                                     "send_authorization.json", "result.json"])
def test_smoke_publication_crashes_never_authorize_replacement(setup, http, worker, monkeypatch, boundary):
    http.state["status"] = 200
    original = smoke._write_once
    def interrupted(path, record):
        original(path, record)
        if path.name == boundary:
            raise KeyboardInterrupt()
    monkeypatch.setattr(smoke, "_write_once", interrupted)
    with pytest.raises(KeyboardInterrupt):
        smoke.run_provider_smoke(run_id="synthetic-crash", enabled=True)
    output = smoke.DIAGNOSTIC_ROOT / "synthetic-crash"
    before = files(output)
    with pytest.raises(FileExistsError):
        smoke.run_provider_smoke(run_id="synthetic-crash", enabled=True)
    assert files(output) == before and len(http.calls) == (1 if boundary == "result.json" else 0)


def test_smoke_requires_opt_in_and_fixed_run_id_without_loading_credentials(monkeypatch):
    import app.config
    monkeypatch.setattr(app.config, "get_settings", lambda: pytest.fail("credentials loaded without authorization"))
    with pytest.raises(ValueError):
        smoke.run_provider_smoke(run_id="disabled")
    with pytest.raises(ValueError):
        smoke.run_provider_smoke(run_id="../backtests/ai_signal_chain_trial_20261104_02", enabled=True)


@pytest.mark.parametrize("content", ['{"status":"OTHER"}', 'not JSON', '{"api_key":"sk-FAKE-KEY-PRIVATE"}'])
def test_smoke_never_retains_arbitrary_successful_model_text(setup, http, worker, content):
    http.state.update(status=200, success=content)
    result = smoke.run_provider_smoke(run_id="synthetic-unexpected-output", enabled=True)
    saved = json.loads((Path(result["output"]) / "result.json").read_bytes())
    assert saved["status"] == "FAILED" and saved["category"] == "invalid_smoke_response"
    assert "response" not in saved and setup.settings.deepseek_api_key not in json.dumps(saved)


def test_concurrent_smoke_claims_cannot_spend_the_same_budget_twice(setup, worker, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    calls = []
    monkeypatch.setattr(smoke, "_bounded_request", lambda *args: calls.append(1) or {"status": "OK"})
    def attempt():
        try:
            return smoke.run_provider_smoke(run_id="synthetic-concurrent", enabled=True)["status"]
        except FileExistsError:
            return "BLOCKED"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: attempt(), range(2))) == ["BLOCKED", "OK"]
    assert calls == [1]
