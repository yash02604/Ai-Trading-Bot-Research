"""Offline contracts for observation-only V2.2 transports."""
import copy
import json
from types import SimpleNamespace

import pandas as pd
import pytest
import requests


@pytest.fixture
def live(tmp_path, monkeypatch):
    from app.research import v2_live_transport as module
    from strategy_backtest import STOCKS
    now = [pd.Timestamp("2026-10-28T12:10:01+05:30")]
    cfg = {"schema_version": "scheduled-forward-paper-v2", "mode": "FORWARD_PAPER",
           "initial_capital": 100000, "instrument_priority": ["RELIANCE"],
           "provider_cap": 2, "market_data_cap": 4,
           "transport": {"schema_version": "v22-live-transports-v1", "market_data_enabled": True,
             "provider_enabled": True, "production_start_enabled": True, "review_approval": None,
             "model": "deepseek-flash", "min_turnover": 100000, "poll_seconds": 1, "max_cycles": 10,
             "request_timeout_seconds": 10, "total_deadline_seconds": 20, "max_retry_delay_seconds": 1}}
    monkeypatch.setattr(module, "readiness", lambda *a, **k: {"status": "READY", "reason_codes": []})
    settings = SimpleNamespace(upstox_access_token="FAKE-TOKEN-PRIVATE", deepseek_api_key="FAKE-KEY-PRIVATE",
       request_timeout_seconds=10, upstox_total_deadline_seconds=20, upstox_max_retry_delay_seconds=1)
    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(module.upstox_client, "get_settings", lambda: settings)
    monkeypatch.setattr(module.upstox_client, "_utc_now", lambda: now[0].to_pydatetime())
    rows = [["2026-10-28T12:05:00+05:30", 600, 601, 599, 600, 10000, 0],
            ["2026-10-28T12:10:00+05:30", 600, 601, 599, 600, 10000, 0]]
    quote = {"instrument_token": STOCKS["RELIANCE"], "timestamp": now[0].isoformat(),
             "last_price": 600, "depth": {"buy": [{"price": 599.9, "quantity": 100}],
                                          "sell": [{"price": 600.1, "quantity": 100}]}}
    responses = {"candles": {"status": "success", "data": {"candles": rows}},
                 "quote": {"status": "success", "data": {STOCKS["RELIANCE"]: quote}}}
    calls = []
    def get(session, url, **kwargs):
        kind = "quote" if "market-quote" in url else "candles"
        calls.append(kind)
        response = requests.Response(); response.status_code = 200
        response._content = json.dumps(responses[kind]).encode()
        return response
    monkeypatch.setattr(requests.Session, "get", get)
    obj = module.LiveTransport(tmp_path / "transport", cfg, lambda: now[0])
    return SimpleNamespace(module=module, obj=obj, cfg=cfg, now=now, calls=calls,
          responses=responses, root=tmp_path / "transport", settings=settings)


def test_completed_candles_cached_without_redating_or_resending(live):
    result = live.obj.candles("RELIANCE", "2026-10-28T12:05:00+05:30")
    assert len(result["normalized"]["candles"]) == 1
    assert result["normalized"]["excluded_incomplete_count"] == 1
    assert result["normalized"]["identity_basis"] == "REQUEST_BOUND_NOT_ECHOED"
    live.now[0] += pd.Timedelta(seconds=1)
    assert live.obj.candles("RELIANCE", "2026-10-28T12:05:00+05:30") == result
    assert live.calls == ["candles"]
    assert live.obj.health()["market_data_requests"] == 1


@pytest.mark.parametrize("bad", ["future", "duplicate", "ohlc", "naive", "identity", "interval", "symbol"])
def test_candle_invalid_response_consumes_once_and_never_retries(live, bad):
    data = live.responses["candles"]["data"]
    if bad == "future": data["candles"][0][0] = "2026-10-28T12:15:00+05:30"
    if bad == "duplicate": data["candles"].append(copy.deepcopy(data["candles"][0]))
    if bad == "ohlc": data["candles"][0][2] = 1
    if bad == "naive": data["candles"][0][0] = "2026-10-28T12:05:00"
    if bad == "identity": data["instrument_key"] = "WRONG"
    if bad == "interval": data["interval"] = 1
    if bad == "symbol": data["symbol"] = "WRONG"
    with pytest.raises(live.module.TransportError): live.obj.candles("RELIANCE", "2026-10-28T12:05:00+05:30")
    with pytest.raises(live.module.TransportError): live.obj.candles("RELIANCE", "2026-10-28T12:05:00+05:30")
    assert live.calls == ["candles"]


@pytest.mark.parametrize("bad", ["stale", "naive", "identity", "bid", "crossed", "last", "bool", "quantity"])
def test_quote_validation_rejects_bad_observations(live, bad):
    q = next(iter(live.responses["quote"]["data"].values()))
    if bad == "stale": q["timestamp"] = "2026-10-28T12:09:00+05:30"
    if bad == "naive": q["timestamp"] = "2026-10-28T12:10:01"
    if bad == "identity": q["instrument_token"] = "WRONG"
    if bad == "bid": q["depth"]["buy"] = []
    if bad == "crossed": q["depth"]["buy"][0]["price"] = 700
    if bad == "last": q["last_price"] = -1
    if bad == "bool": q["depth"]["buy"][0]["price"] = True
    if bad == "quantity": q["depth"]["buy"][0]["quantity"] = 1.5
    with pytest.raises(live.module.TransportError): live.obj.quote("RELIANCE", "2026-10-28T12:05:00+05:30")


def test_quote_valid_and_no_secret_persisted(live):
    result = live.obj.quote("RELIANCE", "2026-10-28T12:05:00+05:30")
    assert result["normalized"]["last_price"] == 600
    assert result["normalized"]["source_quote_timestamp"] is None
    assert result["normalized"]["bid_price"] <= result["normalized"]["ask_price"]
    assert not any("PRIVATE" in p.read_text() for p in live.root.rglob("*") if p.is_file())


def test_disabled_readiness_has_no_directory_or_send(live, monkeypatch):
    monkeypatch.setattr(live.module, "readiness", lambda *a, **k: {"status": "NO_GO", "reason_codes": ["NETWORK_DISABLED"]})
    with pytest.raises(live.module.TransportError): live.obj.candles("RELIANCE", "2026-10-28T12:05:00+05:30")
    assert not live.root.exists()
    assert not live.calls


def test_exhausted_final_budget_slot_and_restart(live):
    live.obj.config["market_data_cap"] = 1
    live.obj.quote("RELIANCE", "2026-10-28T12:05:00+05:30")
    with pytest.raises(live.module.TransportError, match="BUDGET_EXHAUSTED"):
        live.obj.candles("RELIANCE", "2026-10-28T12:05:00+05:30")
    restarted = live.module.LiveTransport(live.root, live.obj.config, lambda: live.now[0])
    assert restarted.quote("RELIANCE", "2026-10-28T12:05:00+05:30")["kind"] == "quote"
    assert live.calls == ["quote"]


def test_sent_without_result_never_reacquires(live):
    live.obj.candles("RELIANCE", "2026-10-28T12:05:00+05:30")
    next((live.root / "results").glob("*.json")).unlink()
    restarted = live.module.LiveTransport(live.root, live.cfg, lambda: live.now[0])
    with pytest.raises(live.module.TransportError, match="REQUEST_INCOMPLETE"):
        restarted.candles("RELIANCE", "2026-10-28T12:05:00+05:30")
    assert live.calls == ["candles"]

@pytest.mark.parametrize('lost',['market_data_budget.jsonl','sent.jsonl','physical_attempts.jsonl'])
def test_surviving_result_cannot_hide_missing_request_evidence(live,lost):
    live.obj.candles('RELIANCE','2026-10-28T12:05:00+05:30')
    (live.root/lost).write_text('')
    with pytest.raises(live.module.TransportError):
        live.obj.quote('RELIANCE','2026-10-28T12:05:00+05:30')
    assert live.calls==['candles']

def test_durable_attempt_delay_must_not_send_after_deadline(live,monkeypatch):
    original=live.module._sync
    def sync(path):
        original(path)
        if path.name=='physical_attempts.jsonl':live.now[0]+=pd.Timedelta(seconds=30)
    monkeypatch.setattr(live.module,'_sync',sync)
    with pytest.raises(live.module.TransportError):
        live.obj.candles('RELIANCE','2026-10-28T12:05:00+05:30')
    assert not live.calls


def test_retry_uses_one_logical_slot_and_three_physical_attempts(live, monkeypatch):
    calls = []
    def get(*a):
        calls.append(1)
        return {"status": 503, "retry_after": "0"} if len(calls) < 3 else {"status": 200, "data": live.responses["candles"]}
    monkeypatch.setattr(live.module.upstox_client, "_bounded_get", get)
    live.obj.candles("RELIANCE", "2026-10-28T12:05:00+05:30")
    assert len(calls) == 3
    assert live.obj.health()["market_data_requests"] == 1
    assert len((live.root / "physical_attempts.jsonl").read_text().splitlines()) == 3


def test_retry_boundary_revalidated_and_late_response_rejected(live, monkeypatch):
    calls = []
    def get(*a):
        calls.append(1)
        live.now[0] = pd.Timestamp("2026-10-28T12:15:00+05:30")
        return {"status": 200, "data": live.responses["candles"]}
    monkeypatch.setattr(live.module.upstox_client, "_bounded_get", get)
    with pytest.raises(live.module.TransportError):
        live.obj.candles("RELIANCE", "2026-10-28T12:05:00+05:30")
    assert len(calls) == 1
    assert not (live.root / "results").exists()


def test_invalid_credentials_before_root_mutation(live):
    live.settings.upstox_access_token = ""
    with pytest.raises(live.module.TransportError, match="CREDENTIALS_INVALID"):
        live.obj.candles("RELIANCE", "2026-10-28T12:05:00+05:30")
    assert not live.root.exists()


def test_clock_cannot_move_backwards_across_acquisition(live, monkeypatch):
    def get(*a):
        live.now[0] -= pd.Timedelta(seconds=1)
        return {"status": 200, "data": live.responses["candles"]}
    monkeypatch.setattr(live.module.upstox_client, "_bounded_get", get)
    with pytest.raises(live.module.TransportError):
        live.obj.candles("RELIANCE", "2026-10-28T12:05:00+05:30")


def test_final_session_completed_candle_preserves_forming_row_exclusion(live):
    live.now[0] = pd.Timestamp("2026-10-28T15:15:01+05:30")
    rows = live.responses["candles"]["data"]["candles"]
    rows[0][0], rows[1][0] = "2026-10-28T15:10:00+05:30", "2026-10-28T15:15:00+05:30"
    result = live.obj.candles("RELIANCE", rows[0][0], purpose="monitor")
    assert len(result["normalized"]["candles"]) == 1
    assert result["normalized"]["excluded_incomplete_count"] == 1


def test_health_does_not_publish_arbitrary_persisted_error(live):
    live.root.mkdir()
    (live.root / "failures.jsonl").write_text(json.dumps({"request_id": "r", "code": "FAKE-KEY-PRIVATE"}) + "\n")
    assert "PRIVATE" not in json.dumps(live.obj.health())


def test_quote_freshness_revalidated_after_normalization(live, monkeypatch):
    original = live.module.parse_upstox_full_quote
    def parse(*args, **kwargs):
        parsed = original(*args, **kwargs)
        live.now[0] += pd.Timedelta(seconds=6)
        return parsed
    monkeypatch.setattr(live.module, "parse_upstox_full_quote", parse)
    with pytest.raises(live.module.TransportError):
        live.obj.quote("RELIANCE", "2026-10-28T12:05:00+05:30")


@pytest.mark.parametrize("bad", [None, "model", "wrong_symbol", "json", "missing_stop", "missing_target", "score", "duplicate", "truncated", "http", "timeout"])
def test_provider_wire_and_strict_proposals(live, monkeypatch, bad):
    import openai
    import httpx2
    from app.research import ai_led_guarded as guarded, ai_led_offline as core
    slot = "2026-10-28T12:05:00+05:30"
    body = {"schema_version": guarded.REVIEWED_SCHEMA, "candidate_timestamp": slot,
       "action": "BUY", "rationale_codes": ["trend"], "proposals": [{"symbol": "RELIANCE", "score": .8,
       "entry_price": 600, "stop_loss": 594, "target_price": 612, "holding_bars": 6, "rationale_codes": ["momentum"]}]}
    calls = []
    if bad == "wrong_symbol": body["proposals"][0]["symbol"] = "WRONG"
    if bad == "missing_stop": del body["proposals"][0]["stop_loss"]
    if bad == "missing_target": del body["proposals"][0]["target_price"]
    if bad == "score": body["proposals"][0]["score"] = 2
    if bad == "duplicate": body["proposals"].append(copy.deepcopy(body["proposals"][0]))
    def handler(request):
        sent = json.loads(request.content); calls.append(sent)
        assert set(sent) == {"model", "messages", "response_format", "max_tokens"}
        assert sent["max_tokens"] == 512
        if bad == "timeout": raise httpx2.ReadTimeout("FAKE-KEY-PRIVATE", request=request)
        if bad == "http": return httpx2.Response(500, request=request, json={"error": {"message": "FAKE-KEY-PRIVATE"}})
        return httpx2.Response(200, request=request, json={"id": "fake", "object": "chat.completion", "created": 1,
           "model": "WRONG" if bad == "model" else "deepseek-flash", "choices": [{"index": 0,
           "finish_reason": "length" if bad == "truncated" else "stop",
           "message": {"role": "assistant", "content": "FAKE-KEY-PRIVATE" if bad == "json" else json.dumps(body)}}]})
    default = openai.DefaultHttpxClient
    monkeypatch.setattr(openai, "DefaultHttpxClient", lambda **kw: default(transport=httpx2.MockTransport(handler), **kw))
    payload = {"system_prompt": live.module.SYSTEM_PROMPT, "user_payload": core._encoded({
       "schema_version": core.SCHEMA, "candidate_timestamp": slot, "decision_time": live.now[0].isoformat(),
       "instrument_priority": ["RELIANCE"], "market": {"RELIANCE": {}}, "quotes": {}}).decode()}
    arguments = {"frames": {"RELIANCE": pd.DataFrame({"close": [600]})}, "candidate_time": slot}
    if bad:
        for _ in range(2):
            with pytest.raises(live.module.TransportError): live.obj.proposals(payload, **arguments)
        assert len(calls) == 1
        assert not any("PRIVATE" in p.read_text() for p in live.root.rglob("*") if p.is_file())
        return
    result = live.obj.proposals(payload, **arguments)
    assert result["normalized"]["action"] == "BUY"
    assert result["normalized"]["proposals"][0]["symbol"] == "RELIANCE"
    assert live.obj.proposals(payload, frames={"RELIANCE": pd.DataFrame({"close": [600]})}, candidate_time=slot) == result
    assert len(calls) == 1
