from datetime import timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import requests

from app.research import ai_led_reviewed as reviewed
from app.research import ai_led_guarded as guarded
from app.research import ai_led_offline as core
from app.v2.provider import DeepSeekProvider
from app.research.deepseek_prospective import PersistentRequestBudget

PRIORITY = ["RELIANCE", "HDFCBANK"]
NOW = pd.Timestamp("2026-10-28T12:10:01+05:30")
CANDIDATE = NOW.floor("5min") - pd.Timedelta(minutes=5)


def proposal():
    return {"schema_version": guarded.REVIEWED_SCHEMA, "candidate_timestamp": CANDIDATE.isoformat(),
            "action": "BUY", "rationale_codes": ["trend"], "proposals": [{
                "symbol": "RELIANCE", "score": 0.8, "entry_price": 600, "stop_loss": 594,
                "target_price": 612, "holding_bars": 6, "rationale_codes": ["momentum"]}]}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    import socket
    def denied(*a, **k): raise AssertionError("actual external requests forbidden")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    settings = SimpleNamespace(paper_trading=True, deepseek_model="deepseek-flash", request_timeout_seconds=10.0,
                               upstox_access_token="FAKE-ACCESS-TOKEN-PRIVATE", deepseek_api_key="sk-FAKE-KEY-PRIVATE")
    monkeypatch.setattr(reviewed, "get_settings", lambda: settings)
    monkeypatch.setattr(guarded, "get_settings", lambda: settings)
    monkeypatch.setattr(reviewed.upstox_client, "get_settings", lambda: settings)
    clock = [NOW]
    monkeypatch.setattr(reviewed, "_utc_now", lambda: clock[0].to_pydatetime())
    monkeypatch.setattr(reviewed.upstox_client, "_utc_now", lambda: clock[0].to_pydatetime())
    times = pd.date_range("2026-10-28T09:15:00+05:30", periods=35, freq="5min")
    data = {s: {"status": "success", "data": {"candles": [[t.isoformat(),600,601,599,600,10000,0]
                                                           for t in times]}} for s in PRIORITY}
    quotes = {s: {"status": "success", "data": {core.STOCKS[s]: {
        "instrument_token": core.STOCKS[s], "timestamp": NOW.isoformat(),
        "depth": {"buy": [{"price":599.9,"quantity":100}], "sell": [{"price":600.1,"quantity":100}]}}}}
              for s in PRIORITY}
    http, sdk = [], []
    body = [proposal()]
    behavior = {"http_error": None, "sdk_error": None, "after_sdk": None,
                "returned_model": "deepseek-flash", "finish_reason": "stop"}
    def get(session, url, **kwargs):
        kind = "quote" if "market-quote" in url else "candles"
        symbol = next(s for s in PRIORITY if core.STOCKS[s] in (str(kwargs.get("params")) if kind=="quote" else url))
        http.append((kind, symbol))
        assert kwargs["headers"]["Authorization"] == "Bearer FAKE-ACCESS-TOKEN-PRIVATE"
        assert kwargs["timeout"] == 10
        if behavior["http_error"]:
            raise behavior["http_error"]
        payload = quotes[symbol] if kind == "quote" else data[symbol]
        if type(payload) is int:
            response = requests.Response();response.status_code=payload
            response._content=b'FAKE-ACCESS-TOKEN-PRIVATE sk-FAKE-KEY-PRIVATE sensitive-body'
            return response
        response = requests.Response();response.status_code=200
        response._content=(b'not-JSON-sensitive-body' if payload=="BAD_RAW_JSON" else json.dumps(payload).encode())
        response.encoding="utf-8"
        return response
    monkeypatch.setattr(requests.Session, "get", get)
    def create(**kwargs):
        sdk.append(kwargs)
        if behavior["sdk_error"]:
            raise behavior["sdk_error"]
        if behavior["after_sdk"]: behavior["after_sdk"]()
        content = body[0] if type(body[0]) is str else json.dumps(body[0])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content),finish_reason=behavior["finish_reason"])],
                               model=behavior["returned_model"], usage=None)
    monkeypatch.setattr(DeepSeekProvider, "_get_client", lambda self: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    return SimpleNamespace(output=tmp_path/"new-paper", clock=clock, settings=settings, data=data, quotes=quotes,
                           http=http, sdk=sdk, body=body, behavior=behavior)


def options(s, **kwargs):
    return dict(output_dir=s.output, instrument_priority=PRIORITY, capital=1000, provider_cap=1, market_data_cap=4,
                enable_prospective_paper=True, allow_market_data_requests=True, allow_provider_requests=True, **kwargs)


def used(s, stem):
    path=s.output/(stem+".jsonl")
    return len(path.read_text().splitlines()) if path.exists() else 0


@pytest.mark.parametrize("flag", ["enable_prospective_paper", "allow_market_data_requests", "allow_provider_requests"])
def test_each_missing_opt_in_blocks_all_network_and_reservations(setup, flag):
    opts=options(setup);opts[flag]=False
    with pytest.raises(guarded.GuardedBatchError, match="network_disabled"):
        reviewed.run_reviewed_paper(**opts)
    assert setup.http == setup.sdk == [] and not setup.output.exists()


def test_default_entry_and_cli_disable_network(setup, monkeypatch, capsys):
    import sys
    from scripts.run_ai_led_prospective_paper import main
    with pytest.raises(guarded.GuardedBatchError, match="network_disabled"):
        reviewed.run_reviewed_paper(output_dir=setup.output, instrument_priority=PRIORITY,
                                    capital=1000, provider_cap=1, market_data_cap=4)
    monkeypatch.setattr(sys,"argv",["runner","--output",str(setup.output),"--instrument-priority",*PRIORITY,
        "--paper-capital","1000","--max-provider-requests","1","--max-market-data-requests","4"])
    assert main()==2 and json.loads(capsys.readouterr().out)["status"]=="BLOCKED"
    assert setup.http == setup.sdk == []


@pytest.mark.parametrize("quotes", [False, True])
def test_real_client_interfaces_with_mocked_http_and_sdk_complete_shared_batch(setup, quotes):
    result=reviewed.run_reviewed_paper(**options(setup,include_quotes=quotes))
    assert len(setup.http)==(4 if quotes else 2) and len(setup.sdk)==1
    assert result["provider_category"]=="validated_provider_proposal"
    assert result["position"]["quantity"]==1 and result["trade_placement_enabled"] is False
    assert result["mock_provider_calls"]==0
    assert used(setup,"market_data_request_budget")==len(setup.http)
    assert used(setup,"request_budget")==1
    request=json.loads(setup.sdk[0]["messages"][1]["content"])
    assert set(request["market"])==set(PRIORITY)
    assert ("quotes" in request)==quotes
    assert setup.sdk[0]["max_tokens"]==512
    config=guarded._read(setup.output/guarded.MANIFEST)
    assert config["mode"]=="REVIEWED_PROSPECTIVE_PAPER"
    assert config["network_policy"]["provider_retries"]==0
    for path in setup.output.rglob("*"):
        if path.is_file():
            assert b"FAKE-ACCESS-TOKEN-PRIVATE" not in path.read_bytes()
            assert b"sk-FAKE-KEY-PRIVATE" not in path.read_bytes()


@pytest.mark.parametrize("kind", ["missing", "stale", "bad_ohlcv", "future", "json", "identity", "rate", "auth", "timeout"])
def test_invalid_upstox_response_blocks_deepseek_and_keeps_reservations(setup, kind):
    row=setup.data["RELIANCE"]
    if kind=="missing": row["data"]["candles"]=[]
    elif kind=="stale": row["data"]["candles"]=row["data"]["candles"][:-4]
    elif kind=="bad_ohlcv": row["data"]["candles"][-1][2]=-1
    elif kind=="future": row["data"]["candles"][-1][0]=(NOW+pd.Timedelta(minutes=5)).isoformat()
    elif kind=="json": setup.data["RELIANCE"]="BAD_RAW_JSON"
    elif kind=="identity": row["data"]["instrument_key"]="wrong"
    elif kind=="rate": setup.data["RELIANCE"]=429
    elif kind=="auth": setup.data["RELIANCE"]=401
    else: setup.behavior["http_error"]=requests.Timeout("Bearer SECRET-TIMEOUT")
    with pytest.raises(guarded.GuardedBatchError):
        reviewed.run_reviewed_paper(**options(setup))
    assert setup.sdk==[] and used(setup,"market_data_request_budget")==2
    assert used(setup,"request_budget")==0
    for path in setup.output.rglob("*.json*"):
        assert b"sensitive-body" not in path.read_bytes() and b"SECRET-TIMEOUT" not in path.read_bytes()
    if kind in {"auth","rate","timeout","json"}:
        rows=[json.loads(line) for line in (setup.output/"transport_receipts.jsonl").read_text().splitlines()]
        expected={"auth":"authentication","rate":"rate_limit","timeout":"network_error","json":"json_decode"}[kind]
        assert any(row.get("category")==expected for row in rows)


@pytest.mark.parametrize("kind", ["stale", "identity", "no_depth"])
def test_quote_identity_depth_and_freshness_are_mandatory_when_requested(setup, kind):
    quote=next(iter(setup.quotes["RELIANCE"]["data"].values()))
    if kind=="stale": quote["timestamp"]=(NOW-pd.Timedelta(seconds=6)).isoformat()
    elif kind=="identity": quote["instrument_token"]="wrong"
    else: quote["depth"]={}
    with pytest.raises(guarded.GuardedBatchError):
        reviewed.run_reviewed_paper(**options(setup, include_quotes=True))
    assert setup.sdk==[] and used(setup,"market_data_request_budget")==4


@pytest.mark.parametrize("kind",["extra_field","invalid_price"])
def test_malformed_cached_quote_cannot_leak_text_into_provider_payload(setup,monkeypatch,kind):
    original=guarded._read
    def corrupted(path):
        row=original(path)
        if Path(path).parent.name=="quotes":
            row["arbitrary" if kind=="extra_field" else "ask_price"]="sk-CACHED-SECRET-NEVER-SEND"
        return row
    monkeypatch.setattr(guarded,"_read",corrupted)
    with pytest.raises(guarded.GuardedBatchError,match="cached_quote_schema"):
        reviewed.run_reviewed_paper(**options(setup,include_quotes=True))
    assert setup.sdk==[] and used(setup,"request_budget")==0
    for path in setup.output.rglob("*.json*"):
        assert b"sk-CACHED-SECRET-NEVER-SEND" not in path.read_bytes()


@pytest.mark.parametrize("kind", ["malformed", "quantity", "timestamp", "secret", "no_trade", "failure"])
def test_provider_response_strictness_and_failed_attempt_accounting(setup, kind):
    if kind=="malformed": setup.body[0]="{broken"
    elif kind=="quantity": setup.body[0]["proposals"][0]["quantity"]=1000
    elif kind=="timestamp": setup.body[0]["candidate_timestamp"]=NOW.isoformat()
    elif kind=="secret": setup.body[0]["rationale_codes"]=["sk-PRIVATE-ARBITRARY"]
    elif kind=="no_trade": setup.body[0].update(action="NO_TRADE", proposals=[], rationale_codes=["no_edge"])
    else: setup.behavior["sdk_error"]=RuntimeError("sk-PRIVATE-ARBITRARY Bearer token body")
    result=reviewed.run_reviewed_paper(**options(setup))
    assert result["position"] is None and len(setup.sdk)==1 and used(setup,"request_budget")==1
    again=reviewed.run_reviewed_paper(**options(setup))
    assert again["idempotent_replay"] is True and len(setup.sdk)==1 and len(setup.http)==2
    for path in setup.output.rglob("*.json*"):
        assert b"sk-PRIVATE-ARBITRARY" not in path.read_bytes()


def test_complete_batch_duplicate_resume_makes_no_second_request(setup):
    first=reviewed.run_reviewed_paper(**options(setup))
    again=reviewed.run_reviewed_paper(**options(setup))
    assert first["batch_id"]==again["batch_id"] and again["idempotent_replay"] is True
    assert again["provider_request_attempted"] is False
    assert len(setup.http)==2 and len(setup.sdk)==1


@pytest.mark.parametrize("provider_cap,market_cap", [(0,4),(1,1)])
def test_budget_exhaustion_never_calls_unreserved_client(setup,provider_cap,market_cap):
    opts=options(setup);opts.update(provider_cap=provider_cap,market_data_cap=market_cap)
    if market_cap==1:
        with pytest.raises(guarded.GuardedBatchError): reviewed.run_reviewed_paper(**opts)
        assert len(setup.http)==1
    else:
        assert reviewed.run_reviewed_paper(**opts)["provider_category"]=="provider_budget_exhausted"
    assert setup.sdk==[]


@pytest.mark.parametrize("kind", ["calendar", "attestation", "paper", "timeout", "model", "capital"])
def test_preflight_or_config_failure_has_zero_http_and_sdk_calls(setup,monkeypatch,kind):
    opts=options(setup)
    if kind=="calendar": setup.clock[0]=pd.Timestamp("2026-11-08T14:30:00+05:30")
    elif kind=="attestation":
        import app.research.prospective_preflight as preflight
        monkeypatch.setattr(preflight,"verify_client_attestation",lambda: (_ for _ in ()).throw(ValueError("private")))
    elif kind=="paper": setup.settings.paper_trading=False
    elif kind=="timeout": setup.settings.request_timeout_seconds=21
    elif kind=="model": setup.settings.deepseek_model="other-model"
    else: opts["capital"]=float("nan")
    with pytest.raises(guarded.GuardedBatchError): reviewed.run_reviewed_paper(**opts)
    assert setup.http==setup.sdk==[]
    assert used(setup,"market_data_request_budget")==used(setup,"request_budget")==0


def test_direct_transport_methods_cannot_skip_budget_or_validated_request(setup):
    binding=reviewed._ReviewedRun(**options(setup))
    with core._experiment_lock(setup.output): binding._preflight()
    with pytest.raises(guarded.GuardedBatchError,match="reservation"):
        binding.fetch("RELIANCE")
    with pytest.raises((OSError,guarded.GuardedBatchError)):
        binding.complete({"system_prompt":"arbitrary", "user_payload":"{}"})
    assert setup.http==setup.sdk==[]


def test_one_use_send_marker_blocks_duplicate_direct_call(setup):
    reviewed.run_reviewed_paper(**options(setup))
    binding=reviewed._ReviewedRun(**options(setup))
    with pytest.raises(guarded.GuardedBatchError,match="already_sent"):
        binding.fetch("RELIANCE")
    assert len(setup.http)==2 and len(setup.sdk)==1


def test_provider_return_after_expected_entry_is_never_accepted(setup):
    setup.behavior["after_sdk"]=lambda: setup.clock.__setitem__(0,NOW+pd.Timedelta(minutes=5))
    result=reviewed.run_reviewed_paper(**options(setup))
    assert result["position"] is None and result["provider_category"]=="provider_failure"
    assert used(setup,"request_budget")==1


def test_warmup_rejected_before_any_reservation_or_http(setup):
    setup.clock[0]=pd.Timestamp("2026-10-28T10:00:00+05:30")
    with pytest.raises(guarded.GuardedBatchError, match="warmup"):
        reviewed.run_reviewed_paper(**options(setup))
    assert setup.http==setup.sdk==[]
    assert used(setup,"market_data_request_budget")==0


@pytest.mark.parametrize("kind",["model", "truncated"])
def test_wrong_model_and_truncated_sdk_output_do_not_create_position(setup,kind):
    if kind=="model": setup.behavior["returned_model"]="unexpected-model"
    else: setup.behavior["finish_reason"]="length"
    result=reviewed.run_reviewed_paper(**options(setup))
    assert result["position"] is None and result["provider_category"]=="provider_failure"
    assert used(setup,"request_budget")==1


def test_interrupted_provider_is_not_retried_on_restart(setup):
    setup.behavior["sdk_error"]=KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt): reviewed.run_reviewed_paper(**options(setup))
    assert used(setup,"request_budget")==1 and len(setup.sdk)==1
    setup.behavior["sdk_error"]=None
    result=reviewed.run_reviewed_paper(**options(setup))
    assert result["provider_category"]=="reserved_without_result"
    assert result["position"] is None and len(setup.sdk)==1


def test_preflight_only_command_with_all_flags_never_sends(setup,monkeypatch,capsys):
    import sys
    from scripts.run_ai_led_prospective_paper import main
    monkeypatch.setattr(sys,"argv",["runner","--output",str(setup.output),"--instrument-priority",*PRIORITY,
        "--paper-capital","1000","--max-provider-requests","1","--max-market-data-requests","4",
        "--enable-prospective-paper","--allow-market-data-requests","--allow-provider-requests","--preflight-only"])
    assert main()==0 and json.loads(capsys.readouterr().out)["status"]=="PREFLIGHT_ONLY"
    assert setup.http==setup.sdk==[] and used(setup,"market_data_request_budget")==0


@pytest.mark.parametrize("capital",[1000,10000])
def test_paper_capital_is_immutable_and_not_limited_to_1000(setup,capital):
    opts=options(setup);opts["capital"]=capital
    reviewed.run_reviewed_paper(**opts)
    before=(setup.output/guarded.MANIFEST).read_bytes()
    opts["capital"]=capital+1
    with pytest.raises(guarded.GuardedBatchError): reviewed.run_reviewed_paper(**opts)
    assert (setup.output/guarded.MANIFEST).read_bytes()==before
    assert len(setup.sdk)==1
