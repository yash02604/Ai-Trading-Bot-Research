"""Synthetic and mocked transports only; never genuine captures or predictions."""
import json

import pandas as pd
import pytest

from test_ai_led_reviewed import setup, used, NOW
from test_signal_observation_chain import collected, capture, replay
from app.research import ai_signal_only as signal, ai_led_reviewed as reviewed, ai_led_guarded as guard
from app.research import signal_request_budget as slots
from app.research.deepseek_prospective import AppendOnlyJsonl
from app.v2.provider import DeepSeekProvider


def options(s):
    s.body[0]["schema_version"] = signal.SCHEMA
    return dict(output_dir=s.output, instrument_priority=["RELIANCE"], capital=1000,
                provider_cap=1, market_data_cap=2, enable_signal_only=True,
                allow_market_data_requests=True, allow_provider_requests=True)


def physical(s):
    return AppendOnlyJsonl(s.output / "physical_request_attempts.jsonl", "request_id").records()


@pytest.mark.parametrize("status", [429, 500, 503, 307, 401])
def test_failed_initial_capture_has_one_durable_physical_attempt_and_no_retry(setup, monkeypatch, status):
    opts = options(setup)
    setup.data["RELIANCE"] = status
    def no_sleep(*args):
        pytest.fail("signal transport scheduled an automatic retry")
    monkeypatch.setattr(reviewed.upstox_client.time, "sleep", no_sleep)
    original = reviewed.upstox_client._bounded_get
    def checkpoint(*args):
        rows = physical(setup)
        assert len(rows) == 1 and rows[0]["purpose"] == "INITIAL_SNAPSHOT"
        assert used(setup, "market_data_request_budget") == 1
        assert used(setup, "transport_sent") == 1
        return original(*args)
    monkeypatch.setattr(reviewed.upstox_client, "_bounded_get", checkpoint)
    with pytest.raises(guard.GuardedBatchError):
        signal.run_signal_only(**opts)
    assert len(setup.http) == len(physical(setup)) == 1 and setup.sdk == []
    before = {p: p.read_bytes() for p in setup.output.rglob("*") if p.is_file()}
    with pytest.raises(guard.GuardedBatchError):
        signal.run_signal_only(**opts)
    setup.clock[0] += pd.Timedelta(minutes=5)
    with pytest.raises(guard.GuardedBatchError, match="immutable_evidence_changed"):
        signal.run_signal_only(**opts)
    assert len(setup.http) == 1 and used(setup, "market_data_request_budget") == 1
    assert all(p.read_bytes() == value for p, value in before.items())


def test_purpose_slots_held_before_any_request_and_capacity_required(setup):
    opts = options(setup)
    with pytest.raises(guard.GuardedBatchError, match="hold_initial_and_future"):
        signal.run_signal_only(**{**opts, "market_data_cap": 1})
    assert not setup.http and not setup.sdk
    signal.run_signal_only(**opts, preflight_only=True)
    plan = guard._read(setup.output / "signal_request_slots.json")
    assert plan["slots"] == [{"purpose": "INITIAL_SNAPSHOT", "symbol": "RELIANCE"},
                              {"purpose": "FUTURE_OBSERVATION", "symbol": "RELIANCE"}]
    assert not setup.http and not physical(setup) and used(setup, "market_data_request_budget") == 0


def test_completed_initial_restart_in_later_bucket_reuses_prediction(setup):
    opts = options(setup)
    original = signal.run_signal_only(**opts)
    before = {p: p.read_bytes() for p in setup.output.rglob("*") if p.is_file()}
    setup.clock[0] += pd.Timedelta(hours=5)
    assert signal.run_signal_only(**opts) == original
    assert len(setup.http) == len(setup.sdk) == len(physical(setup)) == 1
    assert all(p.read_bytes() == value for p, value in before.items())


def test_changed_saved_candidate_fails_closed_without_new_capture(setup):
    opts = options(setup)
    signal.run_signal_only(**opts)
    path = setup.output / "initial_capture.json"
    record = guard._read(path)
    record["candidate_timestamp"] = (NOW - pd.Timedelta(minutes=10)).isoformat()
    path.write_bytes(signal.core._encoded({"sha256": signal.core._digest(record), "record": record}))
    with pytest.raises(guard.GuardedBatchError, match="candidate_changed"):
        signal.run_signal_only(**opts)
    assert len(setup.http) == len(setup.sdk) == 1


def test_slot_cannot_be_rebound_or_used_for_wrong_purpose(collected):
    s, opts, _, _ = collected
    binding = reviewed._ReviewedRun(**opts, purpose="signal_future", enable_signal_future=True,
                                    allow_market_data_requests=True)
    with signal.core._experiment_lock(s.output):
        with pytest.raises(guard.GuardedBatchError, match="immutable_evidence_changed"):
            slots.reserve(binding, "replacement", "RELIANCE", "INITIAL_SNAPSHOT")
        with pytest.raises(guard.GuardedBatchError, match="purpose_not_reserved"):
            slots.reserve(binding, "wrong", "RELIANCE", "INITIAL_QUOTE")
    assert len(s.http) == len(s.sdk) == 1


@pytest.mark.parametrize("status", [200, 429, 503, 307])
def test_future_has_its_own_single_physical_attempt_and_never_refunds(collected, status):
    s, _, _, _ = collected
    if status != 200:
        s.data["RELIANCE"] = status
    result = capture(collected)
    assert [r["purpose"] for r in physical(s)] == ["INITIAL_SNAPSHOT", "FUTURE_OBSERVATION"]
    assert len(s.http) == len(physical(s)) == used(s, "market_data_request_budget") == 2
    assert len(s.sdk) == used(s, "request_budget") == 1
    assert capture(collected) == replay(collected) == result
    assert len(s.http) == 2
    assert result["total_completed_evaluations"] == (1 if status == 200 else 0)
    assert not (s.output / "paper_account").exists()


@pytest.mark.parametrize("boundary", ["raw", "observation", "seal"])
def test_partial_capture_is_deterministic_missing_and_never_repaired_or_resent(collected, monkeypatch, boundary):
    s, _, _, _ = collected
    original = guard._write
    def interrupted(path, record):
        parent = path.parent.name
        if (boundary == "raw" and parent == "future_received") or (
                boundary in {"observation", "seal"} and parent == "future_observations"):
            if boundary != "observation":
                original(path, record)
            raise KeyboardInterrupt()
        return original(path, record)
    monkeypatch.setattr(guard, "_write", interrupted)
    with pytest.raises(KeyboardInterrupt):
        capture(collected)
    before = {p: p.read_bytes() for p in s.output.rglob("*") if p.is_file()}
    first = replay(collected)
    assert first == replay(collected) == capture(collected)
    assert first["total_expected_prediction_evaluations"] == first["total_missing_insufficient"] == 1
    assert first["total_completed_evaluations"] == 0 and first["ai_up_frequency"] is None
    assert first["observation_bindings"][0]["validation_status"].startswith("UNRECOVERABLE_PARTIAL_")
    assert len(s.http) == len(physical(s)) == used(s, "market_data_request_budget") == 2
    assert len(s.sdk) == 1 and not (s.output / "future_observation_seals.jsonl").exists()
    assert all(p.read_bytes() == value for p, value in before.items())


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_real_sdk_redirect_is_rejected_after_one_mock_http_request(monkeypatch, status):
    import openai
    import httpx2
    default = openai.DefaultHttpxClient
    requests = []
    settings = []
    def handler(request):
        requests.append(request)
        return httpx2.Response(status, headers={"location": "https://api.deepseek.com/redirect"}, request=request)
    def transport(**kwargs):
        settings.append(kwargs)
        return default(transport=httpx2.MockTransport(handler), **kwargs)
    monkeypatch.setattr(openai, "DefaultHttpxClient", transport)
    provider = DeepSeekProvider(api_key="synthetic-only", model_id="deepseek-flash", timeout_seconds=2)
    try:
        with pytest.raises(openai.APIStatusError):
            provider.complete({"system_prompt": "synthetic", "user_payload": "{}"})
        assert len(requests) == 1 and requests[0].method == "POST"
        assert settings[0]["follow_redirects"] is False and provider._client.max_retries == 0
    finally:
        if provider._client is not None:
            provider._client.close()
