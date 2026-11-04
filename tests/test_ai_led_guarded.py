import hashlib
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

import pandas as pd
import pytest

from app.research import ai_led_guarded as guarded
from app.research import ai_led_offline as core
from app.research.deepseek_prospective import PersistentRequestBudget
from app.client_attestation import ATTESTATION, ROOT, verify_client_attestation
from strategy_backtest import STOCKS

PRIORITY = ["RELIANCE", "HDFCBANK"]


@pytest.fixture(autouse=True)
def no_network_and_explicit_paper(monkeypatch):
    import socket
    def denied(*args, **kwargs):
        raise AssertionError("all external networking forbidden")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(guarded, "get_settings", lambda: SimpleNamespace(paper_trading=True))


def batch(tmp_path, count=35, *, prices=None, mutate=None):
    statuses = {}
    times = pd.date_range("2026-10-28T09:15:00+05:30", periods=count, freq="5min")
    now = times[-1] + pd.Timedelta(minutes=5)
    for symbol in PRIORITY:
        price = (prices or {}).get(symbol, 600.0)
        body = {"schema_version": "deepseek-prospective-observation-v1", "source": "offline-fixture",
                "symbol": symbol, "instrument_key": STOCKS[symbol], "observed_at": now.isoformat(),
                "candles": [{"timestamp": stamp.isoformat(), "open": price, "high": price+0.5,
                             "low": price-0.5, "close": price, "volume": 10000} for stamp in times]}
        if mutate:
            mutate(body)
        path = tmp_path / (symbol + "-" + str(count) + ".json")
        path.write_text(json.dumps(body))
        statuses[symbol] = {"status": "READY", "snapshot": path}
    return statuses, now, times[-1]


def proposal(symbol="RELIANCE", price=600, **kwargs):
    return {"symbol": symbol, "score": 0.8, "entry_price": price, "stop_loss": price * 0.99,
            "target_price": price * 1.02, "holding_bars": 1, "rationale_codes": ["momentum"], **kwargs}


def provider(stamp, proposals=None, **kwargs):
    proposals = [proposal()] if proposals is None else proposals
    return guarded.MockDecisionProvider(json.dumps({
        "schema_version": guarded.SCHEMA, "candidate_timestamp": stamp.isoformat(),
        "action": "BUY" if proposals else "NO_TRADE", "proposals": proposals,
        "rationale_codes": ["trend"] if proposals else ["no_edge"]}), **kwargs)


def run(tmp_path, statuses, now, ai, output="account", **kwargs):
    return guarded.run_mock_batch(statuses, output_dir=tmp_path/output,
        instrument_priority=kwargs.pop("instrument_priority", PRIORITY),
        capital=kwargs.pop("capital", 1000), now=now, provider=ai,
        provider_cap=kwargs.pop("provider_cap", 5), **kwargs)


def read_account(tmp_path, output="account"):
    return json.loads((tmp_path/output/"paper_account/ai_led_offline_state.json").read_bytes())["state"]


def budget_used(output, stem="request_budget"):
    path = output / (stem + ".jsonl")
    return len(path.read_text().splitlines()) if path.exists() else 0


def test_complete_batch_discovery_without_frozen_predicate_and_structured_rationale(tmp_path, monkeypatch):
    import app.research.deepseek_prospective as quant
    monkeypatch.setattr(quant, "_prepare_quant_candidates", lambda *a: pytest.fail("frozen predicate used"))
    statuses, now, stamp = batch(tmp_path)
    ai = provider(stamp)
    result = run(tmp_path, statuses, now, ai)
    assert ai.calls == 1 and result["position"]["quantity"] == 1
    assert result["decision"]["selected_symbol"] == "RELIANCE"
    assert result["trade_placement_enabled"] is False and result["external_api_requests"] == 0
    event = guarded._read(next((tmp_path/"account/batches").glob("*.json")))
    assert event["provider"]["rationale"]["proposals"]["RELIANCE"] == ["momentum"]
    request = json.loads(ai.payloads[0]["user_payload"])
    assert set(request["market"]) == set(PRIORITY)
    assert request["candidate_timestamp"] == stamp.isoformat()
    assert all(pd.Timestamp(r["timestamp"]) + pd.Timedelta(minutes=5) <= now
               for s in request["market"].values() for r in s["candles"])


def test_submission_and_proposal_order_are_deterministic(tmp_path):
    statuses, now, stamp = batch(tmp_path)
    proposals = [proposal(s) for s in PRIORITY]
    a = run(tmp_path, statuses, now, provider(stamp, proposals), output="a")
    b = run(tmp_path, dict(reversed(list(statuses.items()))), now, provider(stamp, proposals[::-1]), output="b")
    assert a["batch_id"] == b["batch_id"] and a["provider_request_id"] == b["provider_request_id"]
    for key in ("status", "selected_symbol", "proposals", "inputs"):
        assert a["decision"][key] == b["decision"][key]
    assert a["decision"]["selected_symbol"] == PRIORITY[0]


@pytest.mark.parametrize("kind", ["missing", "failed", "extra", "identity", "stale", "future", "gap",
                                 "duplicate", "unaligned", "timestamp_mismatch", "ohlcv", "liquidity"])
def test_invalid_or_incomplete_batch_never_reserves_provider(tmp_path, kind):
    statuses, now, stamp = batch(tmp_path)
    if kind == "missing":
        statuses.pop(PRIORITY[1])
    elif kind == "failed":
        statuses[PRIORITY[1]] = {"status": "FAILED"}
    elif kind == "extra":
        statuses["UNKNOWN"] = {"status": "FAILED"}
    else:
        path = statuses[PRIORITY[0]]["snapshot"]
        body = json.loads(path.read_bytes())
        if kind == "identity": body["instrument_key"] = "forged"
        if kind == "stale": body["observed_at"] = (now-pd.Timedelta(minutes=16)).isoformat()
        if kind == "future": body["candles"][-1]["timestamp"] = (now+pd.Timedelta(minutes=5)).isoformat()
        if kind == "gap": body["candles"].pop(12)
        if kind == "duplicate": body["candles"][15]["timestamp"] = body["candles"][14]["timestamp"]
        if kind == "unaligned": body["candles"][-1]["timestamp"] = (stamp+pd.Timedelta(minutes=1)).isoformat()
        if kind == "timestamp_mismatch": body["candles"].pop()
        if kind == "ohlcv": body["candles"][-1]["high"] = -1
        if kind == "liquidity": body["candles"][-1]["volume"] = 0
        path.write_text(json.dumps(body))
    ai = provider(stamp)
    with pytest.raises(guarded.GuardedBatchError, match="incomplete_or_invalid_batch"):
        run(tmp_path, statuses, now, ai)
    assert ai.calls == 0 and budget_used(tmp_path/"account") == 0
    assert not (tmp_path/"account/paper_account/ai_led_offline_state.json").exists()
    failure = json.loads((tmp_path/"account/batch_failures.jsonl").read_text())
    assert failure["reason_code"] == "incomplete_or_invalid_batch"
    assert set(failure["instrument_statuses"]) == set(PRIORITY)
    assert failure["provider_request_attempted"] is False


@pytest.mark.parametrize("kind", ["json", "symbol", "quantity", "entry", "stop", "score", "future_timestamp",
                                 "naive_timestamp", "rationale", "extra", "nonfinite"])
def test_invalid_proposals_consume_one_mock_attempt_but_never_trade(tmp_path, kind):
    statuses, now, stamp = batch(tmp_path)
    ai = provider(stamp)
    raw = json.loads(ai.content)
    p = raw["proposals"][0]
    if kind == "symbol": p["symbol"] = "FORGED"
    if kind == "quantity": p["quantity"] = 100
    if kind == "entry": p["entry_price"] = 601
    if kind == "stop": p["stop_loss"] = 600
    if kind == "score": p["score"] = -1
    if kind == "future_timestamp": raw["candidate_timestamp"] = now.isoformat()
    if kind == "naive_timestamp": raw["candidate_timestamp"] = stamp.tz_localize(None).isoformat()
    if kind == "rationale": p["rationale_codes"] = ["sk-SECRET-DO-NOT-LOG"]
    if kind == "extra": raw["secret"] = "Bearer SECRET-DO-NOT-LOG"
    if kind == "nonfinite": p["score"] = float("nan")
    ai.content = "not JSON SECRET-DO-NOT-LOG" if kind == "json" else json.dumps(raw)
    result = run(tmp_path, statuses, now, ai)
    assert ai.calls == 1 and result["position"] is None
    assert result["provider_category"] == "invalid_proposal"
    assert budget_used(tmp_path/"account") == 1
    assert all(b"SECRET-DO-NOT-LOG" not in p.read_bytes() for p in (tmp_path/"account").rglob("*.json"))


def test_no_trade_and_mock_failure_are_safe_and_idempotent(tmp_path):
    statuses, now, stamp = batch(tmp_path)
    for name, ai in (("no-trade", provider(stamp, [])),
                     ("failure", provider(stamp, error=RuntimeError("unlabeled-secret-xyz")))):
        result = run(tmp_path, statuses, now, ai, output=name)
        assert result["position"] is None and ai.calls == 1
        assert run(tmp_path, statuses, now, ai, output=name)["idempotent_replay"] is True
        assert ai.calls == 1 and budget_used(tmp_path/name) == 1
        assert all(b"unlabeled-secret-xyz" not in p.read_bytes() for p in (tmp_path/name).rglob("*.json"))


def test_provider_cap_is_persistent_and_exhausted_before_mock_call(tmp_path):
    statuses, now, stamp = batch(tmp_path)
    ai = provider(stamp)
    result = run(tmp_path, statuses, now, ai, provider_cap=0)
    assert result["provider_category"] == "provider_budget_exhausted"
    assert ai.calls == 0 and result["position"] is None
    with pytest.raises(guarded.GuardedBatchError, match="preflight_failed"):
        run(tmp_path, statuses, now, ai, provider_cap=1)


@pytest.mark.parametrize("now", ["2026-11-10T12:10:00+05:30", "2026-11-08T14:30:00+05:30",
                                 "2027-01-01T12:10:00+05:30", "2026-10-28T15:20:00+05:30"])
def test_unsupported_calendar_blocks_provider_and_capture(tmp_path, now):
    statuses, _, stamp = batch(tmp_path)
    ai = provider(stamp)
    client = guarded.MockMarketDataClient({s: v["snapshot"] for s,v in statuses.items()})
    with pytest.raises(guarded.GuardedBatchError, match="preflight_failed"):
        guarded.capture_mock_batch(client, candidate_timestamp=stamp, output_dir=tmp_path/"account",
            instrument_priority=PRIORITY, capital=1000, now=now, provider=ai, provider_cap=2, market_data_cap=2)
    assert client.calls == [] and ai.calls == 0
    assert budget_used(tmp_path/"account", "market_data_request_budget") == 0


@pytest.mark.parametrize("kind", ["client", "attestation", "calendar"])
def test_real_evidence_corruption_blocks_all_mock_requests(tmp_path, monkeypatch, kind):
    import app.research.prospective_preflight as preflight
    statuses, now, stamp = batch(tmp_path)
    if kind == "calendar":
        path = tmp_path/"corrupt-calendar.json"
        path.write_bytes(preflight.CALENDAR_PATH.read_bytes() + b"changed")
        monkeypatch.setattr(preflight, "CALENDAR_PATH", path)
    else:
        root = tmp_path/"copied-root"
        shutil.copytree(ROOT/"data/attestations", root/"data/attestations")
        audit = "backtests/provenance_integrity_v1/input_hashes.json"
        (root/audit).parent.mkdir(parents=True)
        shutil.copyfile(ROOT/audit, root/audit)
        (root/"app").mkdir()
        shutil.copyfile(ROOT/"app/upstox_client.py", root/"app/upstox_client.py")
        path = root/("app/upstox_client.py" if kind == "client" else ATTESTATION)
        path.write_bytes(path.read_bytes() + b"changed")
        monkeypatch.setattr(preflight, "verify_client_attestation", lambda: verify_client_attestation(root))
    ai = provider(stamp)
    client = guarded.MockMarketDataClient({s:v["snapshot"] for s,v in statuses.items()})
    with pytest.raises(guarded.GuardedBatchError, match="preflight_failed"):
        guarded.capture_mock_batch(client, candidate_timestamp=stamp, output_dir=tmp_path/"account",
            instrument_priority=PRIORITY, capital=1000, now=now, provider=ai, provider_cap=2, market_data_cap=2)
    assert client.calls == [] and ai.calls == 0
    assert budget_used(tmp_path/"account", "market_data_request_budget") == 0


def test_capture_reservations_complete_batch_and_resume(tmp_path):
    statuses, now, stamp = batch(tmp_path)
    client = guarded.MockMarketDataClient({s:v["snapshot"] for s,v in statuses.items()})
    ai = provider(stamp)
    options = dict(candidate_timestamp=stamp, output_dir=tmp_path/"account", instrument_priority=PRIORITY,
                   capital=1000, now=now, provider=ai, provider_cap=2, market_data_cap=2)
    first = guarded.capture_mock_batch(client, **options)
    second = guarded.capture_mock_batch(client, **options)
    assert first["batch_id"] == second["batch_id"] and second["idempotent_replay"] is True
    assert client.calls == PRIORITY and ai.calls == 1
    assert budget_used(tmp_path/"account", "market_data_request_budget") == 2
    assert budget_used(tmp_path/"account") == 1


@pytest.mark.parametrize("failure", ["transport", "cap", "validation"])
def test_failed_capture_keeps_consumed_reservations_and_blocks_ai(tmp_path, failure):
    statuses, now, stamp = batch(tmp_path)
    if failure == "validation":
        path = statuses[PRIORITY[1]]["snapshot"]
        body = json.loads(path.read_bytes());body["instrument_key"] = "invalid";path.write_text(json.dumps(body))
    client = guarded.MockMarketDataClient({s:v["snapshot"] for s,v in statuses.items()},
                                          failures=[PRIORITY[1]] if failure == "transport" else [])
    ai = provider(stamp)
    cap = 1 if failure == "cap" else 2
    for _ in range(2):
        with pytest.raises(guarded.GuardedBatchError, match="incomplete_or_invalid_batch"):
            guarded.capture_mock_batch(client, candidate_timestamp=stamp, output_dir=tmp_path/"account",
                instrument_priority=PRIORITY, capital=1000, now=now, provider=ai, provider_cap=2, market_data_cap=cap)
    assert ai.calls == 0 and len(client.calls) == cap
    assert budget_used(tmp_path/"account", "market_data_request_budget") == cap


@pytest.mark.parametrize("capital", [1000, 2500, 10000, 100000])
def test_capital_is_configurable_and_immutable(tmp_path, capital):
    statuses, now, stamp = batch(tmp_path, prices={s:capital*0.6 for s in PRIORITY})
    ai = provider(stamp, [proposal(price=capital*0.6)])
    result = run(tmp_path, statuses, now, ai, capital=capital)
    assert result["position"]["quantity"] == 1
    before = (tmp_path/"account/guarded_experiment.json").read_bytes()
    with pytest.raises(guarded.GuardedBatchError, match="preflight_failed"):
        run(tmp_path, statuses, now, ai, capital=capital+1)
    assert (tmp_path/"account/guarded_experiment.json").read_bytes() == before


@pytest.mark.parametrize("kind", ["affordability", "cost_risk", "zero_shares"])
def test_risk_and_cost_rejections_are_not_resized_by_ai(tmp_path, kind):
    price = {"affordability":999, "cost_risk":900, "zero_shares":1100}[kind]
    statuses, now, stamp = batch(tmp_path, prices={s:price for s in PRIORITY})
    result = run(tmp_path, statuses, now, provider(stamp, [proposal(price=price)]))
    assert result["position"] is None
    p = result["decision"]["proposals"][0]
    assert p["eligibility"]["eligible"] is False
    if kind == "zero_shares": assert p["quantity"] == 0
    if kind == "cost_risk": assert p["eligibility"]["cost_inclusive_stop_risk"] > 10


@pytest.mark.parametrize("outcome", ["gain", "loss"])
def test_settlement_cash_summary_no_double_count_and_blocked_open_account(tmp_path, outcome):
    statuses, now, stamp = batch(tmp_path)
    ai = provider(stamp, [proposal(holding_bars=6)])
    run(tmp_path, statuses, now, ai)
    # Next complete bar arrives before expected entry: the pending position blocks AI.
    statuses2, now2, stamp2 = batch(tmp_path, count=36)
    blocked_ai = provider(stamp2)
    blocked = run(tmp_path, statuses2, now2, blocked_ai)
    assert blocked_ai.calls == 0 and blocked["provider_category"] == "account_blocked"
    def exit_bar(body):
        row = body["candles"][36]
        if outcome == "gain": row.update(high=613, close=610)
        else: row.update(low=593, close=595)
    statuses3, now3, stamp3 = batch(tmp_path, count=37, mutate=exit_bar)
    no_trade = provider(stamp3, [])
    result = run(tmp_path, statuses3, now3, no_trade)
    summary = guarded.summarize_guarded(tmp_path/"account")
    assert summary["accepted_trades"] == summary["completed_trades"] == 1
    assert summary["pending_entries"] == summary["open_positions"] == 0
    assert summary["net_pnl"] == pytest.approx(summary["gross_pnl"]-summary["costs"])
    assert summary["usable_funding"] == min(1000, summary["account_balance"])
    assert (summary["account_balance"] > 1000) == (outcome == "gain")
    assert guarded.summarize_guarded(tmp_path/"account") == summary
    assert run(tmp_path, statuses3, now3, no_trade)["idempotent_replay"] is True
    assert guarded.summarize_guarded(tmp_path/"account") == summary
    assert len([e for e in read_account(tmp_path)["events"] if e["type"] == "SETTLED"]) == 1


def test_mock_failure_response_and_account_commit_crash_recovery(tmp_path, monkeypatch):
    statuses, now, stamp = batch(tmp_path)
    ai = provider(stamp)
    original = core._commit
    def crash(*args):
        raise OSError("simulated storage failure with secret")
    monkeypatch.setattr(core, "_commit", crash)
    with pytest.raises(OSError):
        run(tmp_path, statuses, now, ai)
    assert ai.calls == 1 and budget_used(tmp_path/"account") == 1
    monkeypatch.setattr(core, "_commit", original)
    result = run(tmp_path, statuses, now, ai)
    assert ai.calls == 1 and result["position"]["status"] == "PENDING"
    assert budget_used(tmp_path/"account") == 1


def test_crash_after_account_commit_recovers_missing_batch_event_without_duplicate(tmp_path, monkeypatch):
    statuses, now, stamp = batch(tmp_path)
    ai = provider(stamp)
    original = guarded._write
    def crash(path, value):
        if path.parent.name == "batches":
            raise OSError("simulated evidence publication failure")
        return original(path, value)
    monkeypatch.setattr(guarded, "_write", crash)
    with pytest.raises(OSError):
        run(tmp_path, statuses, now, ai)
    assert len(read_account(tmp_path)["decisions"]) == 1
    monkeypatch.setattr(guarded, "_write", original)
    result = run(tmp_path, statuses, now, ai)
    assert result["idempotent_replay"] is True and ai.calls == 1
    assert len(read_account(tmp_path)["decisions"]) == 1
    assert budget_used(tmp_path/"account") == 1


def test_original_snapshot_change_during_mock_call_does_not_change_decision_inputs(tmp_path, monkeypatch):
    statuses, now, stamp = batch(tmp_path)
    ai = provider(stamp)
    complete = ai.complete
    def mutate_original(payload):
        path = statuses[PRIORITY[0]]["snapshot"]
        body = json.loads(path.read_bytes());body["candles"][-1]["close"] = 10000
        path.write_text(json.dumps(body))
        return complete(payload)
    monkeypatch.setattr(ai, "complete", mutate_original)
    result = run(tmp_path, statuses, now, ai)
    assert result["decision"]["proposals"][0]["entry_price"] == 600
    assert result["position"]["quantity"] == 1


def test_frozen_input_change_during_mock_call_fails_closed_without_refund(tmp_path, monkeypatch):
    statuses, now, stamp = batch(tmp_path)
    ai = provider(stamp)
    complete = ai.complete
    def mutate_frozen(payload):
        next((tmp_path/"account/inputs").glob("*.json")).write_text("{}")
        return complete(payload)
    monkeypatch.setattr(ai, "complete", mutate_frozen)
    with pytest.raises(guarded.GuardedBatchError, match="frozen_input_changed"):
        run(tmp_path, statuses, now, ai)
    assert budget_used(tmp_path/"account") == 1
    assert not (tmp_path/"account/paper_account/ai_led_offline_state.json").exists()


def test_invalid_cached_metadata_fails_closed_without_logging_text(tmp_path, monkeypatch):
    statuses, now, stamp = batch(tmp_path)
    ai = provider(stamp)
    original = core._commit
    monkeypatch.setattr(core, "_commit", lambda *args: (_ for _ in ()).throw(OSError()))
    with pytest.raises(OSError):
        run(tmp_path, statuses, now, ai)
    monkeypatch.setattr(core, "_commit", original)
    path = next((tmp_path/"account/responses").glob("*.json"))
    record = guarded._read(path)
    record["rationale"]["batch"] = ["Bearer-arbitrary-secret"]
    # Test semantic validation even if an untrusted cache has a consistent checksum.
    path.write_bytes(core._encoded({"sha256": core._digest(record), "record": record}))
    with pytest.raises(guarded.GuardedBatchError, match="invalid_request_evidence"):
        run(tmp_path, statuses, now, ai)
    assert ai.calls == 1 and budget_used(tmp_path/"account") == 1


def test_spent_reservation_without_result_is_not_retried(tmp_path):
    statuses, now, stamp = batch(tmp_path)
    ai = provider(stamp, error=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        run(tmp_path, statuses, now, ai)
    assert budget_used(tmp_path/"account") == 1
    retry = provider(stamp)
    result = run(tmp_path, statuses, now, retry)
    assert retry.calls == 0 and result["position"] is None
    assert result["provider_category"] == "reserved_without_result"


def test_changed_same_timestamp_and_account_tamper_block_before_provider(tmp_path):
    statuses, now, stamp = batch(tmp_path)
    ai = provider(stamp)
    run(tmp_path, statuses, now, ai)
    path = statuses[PRIORITY[0]]["snapshot"]
    original = path.read_bytes()
    body = json.loads(original);body["candles"][-1]["volume"] += 1;path.write_text(json.dumps(body))
    with pytest.raises(guarded.GuardedBatchError, match="same_timestamp_market_changed"):
        run(tmp_path, statuses, now, ai)
    path.write_bytes(original)
    state = tmp_path/"account/paper_account/ai_led_offline_state.json"
    state.write_text("{}")
    with pytest.raises(guarded.GuardedBatchError, match="preflight_failed"):
        run(tmp_path, statuses, now, ai)
    assert ai.calls == 1


def test_rejected_actual_entry_is_cleared_and_later_candidate_is_not_blocked(tmp_path):
    statuses, now, stamp = batch(tmp_path)
    run(tmp_path, statuses, now, provider(stamp))
    def invalid_entry(body):
        body["candles"][36].update(open=1100, high=1100, low=1100, close=1100)
    second, now2, stamp2 = batch(tmp_path, count=37, mutate=invalid_entry)
    result = run(tmp_path, second, now2, provider(stamp2, []))
    assert result["position"] is None
    summary = guarded.summarize_guarded(tmp_path/"account")
    assert summary["rejected_entries"] == 1 and summary["accepted_trades"] == 0
    third, now3, stamp3 = batch(tmp_path, count=38, mutate=invalid_entry)
    ai = provider(stamp3)
    result = run(tmp_path, third, now3, ai)
    assert ai.calls == 1 and result["position"]["status"] == "PENDING"
    assert core._risk_state(read_account(tmp_path), now3.date()).trades_today == 0


def test_deleted_committed_account_cannot_reset_capital_or_quota(tmp_path):
    statuses, now, stamp = batch(tmp_path)
    ai = provider(stamp)
    run(tmp_path, statuses, now, ai)
    (tmp_path/"account/paper_account/ai_led_offline_state.json").unlink()
    with pytest.raises(guarded.GuardedBatchError, match="preflight_failed"):
        run(tmp_path, statuses, now, ai)
    assert ai.calls == 1 and budget_used(tmp_path/"account") == 1


def test_late_request_preflight_failure_does_not_reserve(tmp_path, monkeypatch):
    statuses, now, stamp = batch(tmp_path)
    evidence = guarded._evidence
    checks = []
    def changed_evidence(clock):
        checks.append(clock)
        if len(checks) > 1:
            raise ValueError("arbitrary secret never recorded")
        return evidence(clock)
    monkeypatch.setattr(guarded, "_evidence", changed_evidence)
    ai = provider(stamp)
    with pytest.raises(guarded.GuardedBatchError, match="preflight_failed"):
        run(tmp_path, statuses, now, ai)
    assert ai.calls == 0 and budget_used(tmp_path/"account") == 0


@pytest.mark.parametrize("changes", [{"capital": True}, {"provider_cap": True},
                                     {"instrument_priority": ["RELIANCE", "RELIANCE"]},
                                     {"instrument_priority": ["UNKNOWN"]}, {"market_data_cap": -1}])
def test_invalid_experiment_configuration_blocks_transport(tmp_path, changes):
    statuses, now, stamp = batch(tmp_path)
    ai = provider(stamp)
    with pytest.raises(guarded.GuardedBatchError, match="preflight_failed"):
        run(tmp_path, statuses, now, ai, **changes)
    assert ai.calls == 0 and budget_used(tmp_path/"account") == 0


def test_only_exact_mock_transports_and_paper_mode_allowed(tmp_path, monkeypatch):
    statuses, now, stamp = batch(tmp_path)
    class Callback:
        model_id = "deepseek-flash"
        def complete(self, payload): pytest.fail("unguarded callback")
    with pytest.raises(guarded.GuardedBatchError, match="exact_mock_provider"):
        run(tmp_path, statuses, now, Callback())
    monkeypatch.setattr(guarded, "get_settings", lambda: SimpleNamespace(paper_trading=False))
    ai = provider(stamp)
    with pytest.raises(guarded.GuardedBatchError, match="preflight_failed"):
        run(tmp_path, statuses, now, ai)
    assert ai.calls == 0


def test_offline_cli_uses_mock_only_and_reports_paper_result(tmp_path, monkeypatch, capsys):
    import sys
    from scripts.run_ai_led_guarded_mock import main
    statuses, now, stamp = batch(tmp_path)
    status_file = tmp_path/"statuses.json"
    status_file.write_text(json.dumps({s:{"status":"READY", "snapshot":v["snapshot"].name}
                                      for s,v in statuses.items()}))
    response = tmp_path/"mock-response.json";response.write_text(provider(stamp, []).content)
    monkeypatch.setattr(sys, "argv", ["adapter", "--batch-statuses", str(status_file),
        "--mock-response", str(response), "--instrument-priority", *PRIORITY,
        "--paper-capital", "1000", "--as-of", now.isoformat(), "--output", str(tmp_path/"cli"),
        "--max-provider-requests", "1"])
    assert main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["external_api_requests"] == 0 and result["trade_placement_enabled"] is False
    assert result["decision"]["status"] == "NO_TRADE"


def test_historical_directory_not_adopted_and_original_sessions_preserved(tmp_path):
    protected = [p for s in ("02","03","04","05")
                 for p in (ROOT/"backtests"/("deepseek_prospective_session_"+s)).rglob("*") if p.is_file()]
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in protected}
    statuses, now, stamp = batch(tmp_path)
    output = tmp_path/"account";output.mkdir()
    (output/"old-evidence.json").write_text("{}")
    with pytest.raises(guarded.GuardedBatchError, match="preflight_failed"):
        run(tmp_path, statuses, now, provider(stamp))
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest() == sha for p,sha in before.items())
    assert not (ROOT/"backtests/deepseek_prospective_session_06").exists()
