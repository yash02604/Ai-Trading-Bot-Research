"""Offline wire-contract and signal-failure regressions; no external services."""
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_ai_led_reviewed import setup, used
from test_signal_request_remediation import options
from app.research import ai_signal_only as signal, ai_led_guarded as guard, ai_led_reviewed as reviewed
from app.research import ai_led_offline as core, signal_evaluation as evaluation
from app.research.offline_session_calendar import OfflineSessionCalendar
from app.research.prospective_preflight import CALENDAR_PATH
from app.v2.provider import DeepSeekProvider

_REAL_GET_CLIENT = DeepSeekProvider._get_client


def snapshot(output):
    return {p.relative_to(output).as_posix(): p.read_bytes() for p in output.rglob("*") if p.is_file()}


@pytest.fixture
def wire(setup, monkeypatch):
    """Exercise the real SDK serializer and app-owned client, replacing only I/O."""
    import openai
    import httpx2
    default = openai.DefaultHttpxClient
    requests, providers = [], []
    response = {"status": 200}
    settings = []

    def handler(request):
        body = json.loads(request.content)
        saved = guard._read(next((setup.output / "requests").glob("*.json")))["payload"]
        # Exact equality forbids every unspecified OpenAI field and null parameter.
        assert body == {
            "model": "deepseek-flash",
            "messages": [{"role": "system", "content": saved["system_prompt"]},
                         {"role": "user", "content": saved["user_payload"]}],
            "max_tokens": 512,
            "response_format": {"type": "json_object"},
        }
        assert body["messages"][0]["content"] == signal.PROMPT
        assert "json" in body["messages"][0]["content"].lower()
        assert request.method == "POST" and str(request.url) == "https://api.deepseek.com/chat/completions"
        assert request.headers["content-type"] == "application/json"
        assert request.headers["authorization"] == "Bearer " + setup.settings.deepseek_api_key
        requests.append(body)
        if response["status"] != 200:
            return httpx2.Response(response["status"], request=request, json={"error": {
                "message": "Invalid request body; Authorization: Bearer " + setup.settings.deepseek_api_key,
                "type": setup.settings.deepseek_api_key, "code": setup.settings.upstox_access_token}})
        return httpx2.Response(200, request=request, json={
            "id": "synthetic", "object": "chat.completion", "created": 1, "model": "deepseek-flash",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps(setup.body[0])}}]})

    def transport(**kwargs):
        settings.append(kwargs)
        return default(transport=httpx2.MockTransport(handler), **kwargs)

    def client(provider):
        providers.append(provider)
        return _REAL_GET_CLIENT(provider)

    monkeypatch.setattr(openai, "DefaultHttpxClient", transport)
    monkeypatch.setattr(DeepSeekProvider, "_get_client", client)
    try:
        yield SimpleNamespace(requests=requests, response=response, providers=providers, settings=settings)
    finally:
        for provider in providers:
            if provider._client is not None:
                provider._client.close()


def assert_private(s):
    data = b"\n".join(snapshot(s.output).values())
    for forbidden in (s.settings.deepseek_api_key, s.settings.upstox_access_token,
                      "Authorization", "Bearer "):
        assert forbidden.encode() not in data
    assert not (s.output / "paper_account").exists()


def test_exact_deepseek_wire_body_and_valid_signal_reuse(setup, wire):
    opts = options(setup)
    result = signal.run_signal_only(**opts)
    assert result["status"] == "VALID_PREDICTION" and result["schema_version"] == signal.SCHEMA
    assert result["taken"] is result["execution_enabled"] is result["trade_placement_enabled"] is False
    assert len(wire.requests) == used(setup, "request_budget") == 1
    assert wire.providers[0]._client.max_retries == 0 and wire.settings[0]["follow_redirects"] is False
    assert wire.settings[0]["timeout"] == 10
    assert wire.providers[0].max_response_bytes == 16384
    before = snapshot(setup.output)
    assert signal.run_signal_only(**opts) == result
    assert snapshot(setup.output) == before and len(wire.requests) == len(setup.http) == 1
    assert_private(setup)


def test_http_400_consumes_once_without_prediction_fallback_or_retry(setup, wire):
    opts = options(setup)
    wire.response["status"] = 400
    result = signal.run_signal_only(**opts)
    saved = guard._read(next((setup.output / "responses").glob("*.json")))
    evidence = saved["evidence"]
    assert result["status"] == "PROVIDER_FAILURE"
    assert result["prediction"] is result["provider_response_received_at"] is result["decision_ready_at"] is None
    assert result["taken"] is result["execution_enabled"] is result["trade_placement_enabled"] is False
    assert evidence["category"] == "provider_failure" and evidence["reservation_consumed"] is True
    assert evidence["core_response"] is evidence["response_sha256"] is evidence["rationale"] is None
    assert saved["response_received_at"] is saved["decision_ready_at"] is None
    assert b"NO_TRADE" not in next((setup.output / "responses").glob("*.json")).read_bytes()
    receipts = [json.loads(line) for line in (setup.output / "transport_receipts.jsonl").read_text().splitlines()]
    receipt = next(r for r in receipts if r["kind"] == "deepseek")
    assert receipt["http_status"] == 400 and receipt["category"] == "invalid_request"
    assert receipt["status"] == "FAILED" and receipt["response_sha256"] is None
    assert receipt["message"] == "Request failed; details redacted"
    assert wire.providers[0]._client.max_retries == 0
    calendar = OfflineSessionCalendar(CALENDAR_PATH)
    evaluated = evaluation.evaluate_signal(result, {}, as_of=setup.clock[0], calendar=calendar)
    assert evaluated["status"] == "NOT_A_VALID_PREDICTION" and evaluated["rows"] == []
    summary = evaluation.summarize_predictions([result], [evaluated])
    assert summary["total_completed_evaluations"] == 0 and summary["no_trade_frequency"] is None
    before = snapshot(setup.output)
    for _ in range(2):
        assert signal.run_signal_only(**opts) == result
    assert signal.run_signal_only(**{**opts, "recover_only": True,
        "allow_market_data_requests": False, "allow_provider_requests": False}) == result
    assert snapshot(setup.output) == before
    assert len(wire.requests) == used(setup, "request_budget") == len(setup.http) == 1
    assert_private(setup)


@pytest.mark.parametrize("kind", ["invalid_schema", "invalid_proposal", "budget_exhausted", "transport_failure"])
def test_signal_failure_evidence_has_no_local_no_trade_response(setup, kind):
    opts = options(setup)
    if kind == "invalid_schema":
        setup.body[0]["schema_version"] = guard.REVIEWED_SCHEMA
    elif kind == "invalid_proposal":
        setup.body[0]["proposals"][0]["entry_price"] = 1
    elif kind == "budget_exhausted":
        opts["provider_cap"] = 0
    else:
        setup.behavior["sdk_error"] = RuntimeError("sensitive transport detail")
    result = signal.run_signal_only(**opts)
    evidence = guard._read(next((setup.output / "responses").glob("*.json")))["evidence"]
    assert evidence["core_response"] is None and result["prediction"] is None
    assert result["status"] != "VALID_PREDICTION" and result["schema_version"] == signal.SCHEMA
    assert evidence["reservation_consumed"] is (kind != "budget_exhausted")
    assert used(setup, "request_budget") == (0 if kind == "budget_exhausted" else 1)
    before = snapshot(setup.output)
    assert signal.run_signal_only(**opts) == result and snapshot(setup.output) == before
    assert_private(setup)


def test_genuine_provider_no_trade_is_still_a_valid_prediction(setup, wire):
    opts = options(setup)
    setup.body[0].update(action="NO_TRADE", proposals=[])
    result = signal.run_signal_only(**opts)
    assert result["status"] == "VALID_PREDICTION" and result["prediction"]["action"] == "NO_TRADE"
    assert result["response_sha256"] is not None
    assert len(wire.requests) == 1


def test_signal_validator_rejects_legacy_failure_placeholder_without_mutation(tmp_path):
    payload = {"system_prompt": "JSON", "user_payload": "{}"}
    evidence = {"request_id": "r", "batch_id": "b", "payload_sha256": core._digest(payload),
        "core_response": guard._no_trade(), "category": "provider_failure",
        "response_sha256": None, "rationale": None, "reservation_consumed": True}
    (tmp_path / "request_budget.jsonl").write_text('{"request_id":"r"}\n')
    before = snapshot(tmp_path)
    with pytest.raises(guard.GuardedBatchError, match="invalid_request_evidence"):
        guard._validate_response_evidence(evidence, "r", "b", payload, [], {}, tmp_path,
                                          reviewed=True, signal_only=True)
    assert snapshot(tmp_path) == before
    # Existing paper validation keeps its separate historical placeholder contract.
    guard._validate_response_evidence(evidence, "r", "b", payload, [], {}, tmp_path, reviewed=True)


def test_old_02_experiment_is_read_only_and_copied_resume_fails_closed(setup, tmp_path):
    original = Path(__file__).resolve().parents[1] / "backtests/ai_signal_chain_trial_20261104_02"
    before = snapshot(original)
    copied = tmp_path / "old-02-copy"
    shutil.copytree(original, copied)
    copy_before = snapshot(copied)
    opts = options(setup)
    opts.update(output_dir=copied, recover_only=True, allow_market_data_requests=False,
                allow_provider_requests=False)
    with pytest.raises(guard.GuardedBatchError, match="signal_configuration_changed"):
        signal.run_signal_only(**opts)
    assert snapshot(original) == before and snapshot(copied) == copy_before
    assert setup.http == setup.sdk == []


@pytest.mark.parametrize("status,stage,category", [
    (400, "request_submission_or_transport", "invalid_request"),
    (400, "response_extraction", "transport_failure"),
    (401, "request_submission_or_transport", "authentication"),
    (429, "request_submission_or_transport", "rate_limit"),
])
def test_error_metadata_uses_fixed_classification_without_error_text(status, stage, category):
    error = RuntimeError("Authorization: Bearer synthetic-secret")
    error.provider_http_status, error.provider_stage = status, stage
    error.provider_error_code = error.provider_error_type = "synthetic-secret"
    assert reviewed._error_fields(error) == {
        "category": category, "http_status": status, "message": "Request failed; details redacted"}
