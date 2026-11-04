import json
from pathlib import Path

import pandas as pd
import pytest

from app.research import ai_led_offline as offline
from strategy_backtest import STOCKS

SYMBOLS = ["RELIANCE", "HDFCBANK"]


@pytest.fixture(autouse=True)
def deny_network(monkeypatch):
    import socket

    def denied(*args, **kwargs):
        raise AssertionError("network forbidden in offline tests")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "create_connection", denied)


def batch(tmp_path, count=35, mutate=None, prices=None):
    paths = []
    times = pd.date_range("2026-10-26T09:15:00+05:30", periods=count, freq="5min")
    now = times[-1] + pd.Timedelta(minutes=5)
    for symbol in SYMBOLS:
        price = (prices or {}).get(symbol, 600.0)
        candles = [{"timestamp": t.isoformat(), "open": price, "high": price + 0.5,
                    "low": price - 0.5, "close": price, "volume": 1000} for t in times]
        body = {"schema_version": "deepseek-prospective-observation-v1", "symbol": symbol,
                "instrument_key": STOCKS[symbol], "source": "offline-fixture",
                "observed_at": now.isoformat(), "candles": candles}
        if mutate:
            mutate(body)
        path = tmp_path / f"{symbol}-{count}.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        paths.append(path)
    return paths, now


def proposal(symbol="RELIANCE", score=0.8, price=600.0, **changes):
    return {"symbol": symbol, "score": score, "entry_price": price,
            "stop_loss": price * 0.99, "target_price": price * 1.02,
            "holding_bars": 6, **changes}


def provider(proposals=None):
    proposals = [proposal()] if proposals is None else proposals
    return offline.SavedResponseProvider(json.dumps({"schema_version": offline.SCHEMA,
                                                    "action": "BUY" if proposals else "NO_TRADE",
                                                    "proposals": proposals}))


def run(tmp_path, paths, now, ai=None, output="account", **kwargs):
    return offline.run_offline_batch(paths, now=now, output_dir=tmp_path / output,
                                     instrument_priority=kwargs.pop("instrument_priority", SYMBOLS),
                                     capital=kwargs.pop("capital", 1000),
                                     provider=ai or provider(), **kwargs)


def state(tmp_path, output="account"):
    return json.loads((tmp_path / output / "ai_led_offline_state.json").read_text())["state"]


def test_independent_ai_generation_does_not_require_reversal(tmp_path, monkeypatch):
    import app.research.deepseek_prospective as quantitative
    monkeypatch.setattr(quantitative, "_prepare_quant_candidates",
                        lambda *args: pytest.fail("frozen predicate must not be called"))
    paths, now = batch(tmp_path)
    result = run(tmp_path, paths, now)
    assert result["decision"]["selected_symbol"] == "RELIANCE"
    assert result["position"]["quantity"] == 1
    assert result["trade_placement_enabled"] is False


@pytest.mark.parametrize("priority", [SYMBOLS, SYMBOLS[::-1]])
def test_complete_batch_and_proposal_order_do_not_change_tie_winner(tmp_path, priority):
    paths, now = batch(tmp_path)
    candidates = [proposal(s) for s in SYMBOLS]
    a = run(tmp_path, paths, now, provider(candidates), output="a", instrument_priority=priority)
    b = run(tmp_path, paths[::-1], now, provider(candidates[::-1]), output="b", instrument_priority=priority)
    # Response hashes/order document what the AI actually supplied; selection is invariant.
    for key in ("selected_symbol", "proposals", "inputs", "status", "decision_id"):
        assert a["decision"][key] == b["decision"][key]
    assert a["decision"]["selected_symbol"] == priority[0]
    assert state(tmp_path, "a")["config"]["instrument_priority"] == priority


def test_higher_score_wins_and_unaffordable_first_proposal_falls_back(tmp_path):
    paths, now = batch(tmp_path)
    result = run(tmp_path, paths, now, provider([proposal(), proposal("HDFCBANK", score=0.9)]))
    assert result["decision"]["selected_symbol"] == "HDFCBANK"
    paths, now = batch(tmp_path, prices={"RELIANCE": 1000})
    result = run(tmp_path, paths, now, provider([proposal(price=1000, score=0.9), proposal("HDFCBANK")]),
                 output="fallback")
    assert result["decision"]["selected_symbol"] == "HDFCBANK"
    assert result["decision"]["proposals"][0]["eligibility"]["eligible"] is False


@pytest.mark.parametrize("kind", ["missing", "stale", "future", "identity", "liquidity", "gap", "unaligned", "incomplete", "turnover_overflow"])
def test_bad_batches_rejected_before_ai(tmp_path, kind):
    def mutate(body):
        if kind == "identity":
            body["instrument_key"] = "forged"
        elif kind == "liquidity":
            body["candles"][-1]["volume"] = 0
        elif kind == "gap":
            body["candles"].pop(15)
        elif kind == "unaligned":
            body["candles"][-1]["timestamp"] = "2026-10-26T12:06:00+05:30"
        elif kind == "turnover_overflow":
            for bar in body["candles"]:
                bar.update(open=1e200, high=1e200, low=1e200, close=1e200, volume=1e200)
    paths, now = batch(tmp_path, mutate=mutate)
    if kind == "missing":
        paths = paths[:1]
    if kind == "stale":
        now += pd.Timedelta(minutes=16)
    if kind == "future":
        now -= pd.Timedelta(minutes=10)
    if kind == "incomplete":
        now -= pd.Timedelta(minutes=1)
        for path in paths:
            body = json.loads(path.read_text()); body["observed_at"] = now.isoformat()
            path.write_text(json.dumps(body))
    ai = provider()
    with pytest.raises(ValueError):
        run(tmp_path, paths, now, ai)
    assert ai.calls == 0
    assert not (tmp_path / "account" / "ai_led_offline_state.json").exists()


@pytest.mark.parametrize("patch", [
    {"quantity": 999}, {"score": True}, {"score": float("nan")},
    {"symbol": "UNKNOWN"}, {"stop_loss": 599.9}, {"stop_loss": 590},
    {"holding_bars": 13}, {"entry_price": 601}, {"target_price": 700},
])
def test_strict_schema_fails_closed(tmp_path, patch):
    paths, now = batch(tmp_path)
    result = run(tmp_path, paths, now, provider([proposal(**patch)]))
    assert result["position"] is None
    assert result["decision"]["reason"] == "invalid AI response; details redacted"


def test_invalid_response_text_is_never_persisted(tmp_path):
    paths, now = batch(tmp_path)
    secret = "unlabeled-secret-sk-offline-test-only"
    result = run(tmp_path, paths, now, offline.SavedResponseProvider(secret))
    assert secret not in json.dumps(result)
    assert secret not in (tmp_path / "account" / "ai_led_offline_state.json").read_text()


def test_no_trade_and_duplicate_decision_are_idempotent_across_restart(tmp_path):
    paths, now = batch(tmp_path)
    ai = provider([])
    first = run(tmp_path, paths, now, ai)
    second = run(tmp_path, paths[::-1], now, ai)
    assert first["position"] is None
    assert second["idempotent_replay"] is True
    assert ai.calls == 1
    assert len(state(tmp_path)["decisions"]) == 1


@pytest.mark.parametrize("capital", [1000, 5000, 10000])
def test_configurable_capital_and_immutable_resume(tmp_path, capital):
    paths, now = batch(tmp_path)
    run(tmp_path, paths, now, capital=capital)
    before = (tmp_path / "account" / "ai_led_offline_state.json").read_bytes()
    with pytest.raises(ValueError, match="immutable"):
        run(tmp_path, paths, now, capital=capital + 1)
    with pytest.raises(ValueError, match="immutable"):
        run(tmp_path, paths, now, capital=capital, instrument_priority=SYMBOLS[::-1])
    assert (tmp_path / "account" / "ai_led_offline_state.json").read_bytes() == before


def test_actual_entry_rejection_clears_pending_without_quota(tmp_path):
    paths, now = batch(tmp_path)
    run(tmp_path, paths, now)
    def gap(body):
        body["candles"][-1].update(open=1001, high=1002, low=599, close=600)
    later, clock = batch(tmp_path, count=37, mutate=gap)
    result = run(tmp_path, later, clock, provider([]))
    saved = state(tmp_path)
    assert result["position"] is None
    assert saved["events"][0]["type"] == "ENTRY_REJECTED"
    assert offline._risk_state(saved, clock.date()).trades_today == 0
    assert saved["cash"] == 1000


def test_valid_entry_stays_open_blocks_later_ai_then_settles_with_costs(tmp_path):
    paths, now = batch(tmp_path)
    run(tmp_path, paths, now)
    later, clock = batch(tmp_path, count=37)
    ai = provider()
    result = run(tmp_path, later, clock, ai)
    assert result["position"]["status"] == "OPEN"
    assert 0 < result["available_cash"] < 400
    assert ai.calls == 0
    assert offline._risk_state(state(tmp_path), clock.date()).trades_today == 1
    later, clock = batch(tmp_path, count=42)
    result = run(tmp_path, later, clock, provider([]))
    saved = state(tmp_path)
    assert result["position"] is None
    settlement = [e for e in saved["events"] if e["type"] == "SETTLED"][0]
    assert settlement["net_pnl"] == -settlement["costs"]["total_cost"]
    assert saved["cash"] == 1000 + settlement["net_pnl"] < 1000
    assert offline._risk_state(saved, clock.date()).trades_today == 1
    replay = run(tmp_path, later, clock, provider([]))
    assert replay["idempotent_replay"]
    assert len([e for e in state(tmp_path)["events"] if e["type"] == "SETTLED"]) == 1


@pytest.mark.parametrize("mode,expected", [("stop", "stop"), ("gap", "stop_gap"),
                                          ("target", "target"), ("both", "stop")])
def test_exit_monitoring_and_conservative_same_bar_order(tmp_path, mode, expected):
    paths, now = batch(tmp_path)
    run(tmp_path, paths, now)
    def move(body):
        bar = body["candles"][-1]
        bar.update(low=593 if mode in ("stop", "both") else 599,
                   high=613 if mode in ("target", "both") else 601)
        if mode == "gap":
            bar.update(open=590, low=589, high=601)
    later, clock = batch(tmp_path, count=38, mutate=move)
    result = run(tmp_path, later, clock, provider([]))
    event = next(e for e in state(tmp_path)["events"] if e["type"] == "SETTLED")
    assert result["position"] is None
    assert event["exit_reason"] == expected
    assert event["stop_gap_can_exceed_estimate"] is True


def test_realized_loss_changes_next_affordability_and_risk_quantity(tmp_path):
    paths, now = batch(tmp_path)
    run(tmp_path, paths, now)
    def loss(body):
        body["candles"][37].update(open=590, low=589, high=601)
    later, clock = batch(tmp_path, count=42, mutate=loss)
    result = run(tmp_path, later, clock)
    saved = state(tmp_path)
    assert saved["cash"] < 990
    assert result["decision"]["proposals"][0]["eligibility"]["available_cash"] < 990
    assert result["position"]["quantity"] == 1


def test_risk_engine_daily_limit_rejects_next_opportunity(tmp_path, monkeypatch):
    paths, now = batch(tmp_path)
    monkeypatch.setattr(offline, "check_trade_allowed", lambda *args: (False, "Daily loss limit reached"))
    ai = provider()
    result = run(tmp_path, paths, now, ai)
    assert result["position"] is None
    assert ai.calls == 0


def test_three_accepted_trades_exhaust_daily_quota(tmp_path):
    for count in (35, 37, 39):
        paths, now = batch(tmp_path, count=count)
        run(tmp_path, paths, now, provider([proposal(holding_bars=1)]))
    paths, now = batch(tmp_path, count=41)
    ai = provider()
    result = run(tmp_path, paths, now, ai)
    assert result["position"] is None
    assert result["decision"]["reason"] == "Maximum trades for today reached"
    assert ai.calls == 0
    assert offline._risk_state(state(tmp_path), now.date()).trades_today == 3


def test_actual_loss_limit_blocks_even_with_unused_quota(tmp_path):
    # A stop gap can exceed both planned stop risk and the daily loss percentage.
    paths, now = batch(tmp_path)
    run(tmp_path, paths, now)
    later, clock = batch(tmp_path, count=37)
    run(tmp_path, later, clock)
    def gap(body):
        body["candles"][37].update(open=570, low=569, high=601)
    later, clock = batch(tmp_path, count=38, mutate=gap)
    ai = provider()
    result = run(tmp_path, later, clock, ai)
    assert result["decision"]["reason"] == "Daily loss limit reached"
    assert ai.calls == 0
    assert state(tmp_path)["cash"] < 980


def test_account_checksum_and_cash_reconciliation_fail_closed(tmp_path):
    paths, now = batch(tmp_path)
    run(tmp_path, paths, now, provider([]))
    path = tmp_path / "account" / "ai_led_offline_state.json"
    envelope = json.loads(path.read_text())
    envelope["state"]["cash"] = 2000
    envelope["sha256"] = offline._digest(envelope["state"])
    path.write_text(json.dumps(envelope))
    with pytest.raises(ValueError, match="reconcile"):
        run(tmp_path, paths, now)


def test_non_offline_provider_and_existing_session_are_refused(tmp_path):
    paths, now = batch(tmp_path)
    with pytest.raises(ValueError, match="saved offline"):
        run(tmp_path, paths, now, object())
    output = tmp_path / "account"
    output.mkdir(); (output / "request_budget.jsonl").write_text("preserved")
    with pytest.raises(ValueError, match="new empty"):
        run(tmp_path, paths, now)
    assert (output / "request_budget.jsonl").read_text() == "preserved"
    assert not (output / ".prospective.lock").exists()


def test_cost_inclusive_risk_and_zero_shares_are_rejected(tmp_path):
    paths, now = batch(tmp_path, prices={"RELIANCE": 450, "HDFCBANK": 2000})
    result = run(tmp_path, paths, now, provider([
        proposal(price=450), proposal("HDFCBANK", price=2000),
    ]))
    assert result["position"] is None
    checks = result["decision"]["proposals"]
    assert checks[0]["quantity"] == 2
    assert checks[0]["eligibility"]["cost_inclusive_stop_risk"] > 10
    assert checks[1]["quantity"] == 0


def test_gains_are_reconciled_without_raising_funding_cap(tmp_path):
    paths, now = batch(tmp_path)
    run(tmp_path, paths, now)
    def target(body):
        body["candles"][37].update(high=613)
    later, clock = batch(tmp_path, count=38, mutate=target)
    run(tmp_path, later, clock, provider([]))
    saved = state(tmp_path)
    assert saved["balance"] > 1000
    assert saved["cash"] == 1000
    net = next(e["net_pnl"] for e in saved["events"] if e["type"] == "SETTLED")
    assert saved["balance"] == 1000 + net
    run(tmp_path, later, clock, provider([]))  # Resume verifies the reconciliation.


def test_missing_entry_rejection_does_not_fabricate_fill(tmp_path):
    paths, now = batch(tmp_path)
    run(tmp_path, paths, now)
    later, clock = batch(tmp_path, count=36)
    # No actual-entry bar yet, so pending state still reserves the one position.
    result = run(tmp_path, later, clock, provider([]))
    assert result["position"]["status"] == "PENDING"
    # Later rolling window excludes the expected entry: reject, do not fabricate a fill.
    later, clock = batch(tmp_path, count=73)
    for path in later:
        body = json.loads(path.read_text()); body["candles"] = body["candles"][-35:]
        path.write_text(json.dumps(body))
    result = run(tmp_path, later, clock, provider([]))
    assert result["position"] is None
    assert state(tmp_path)["events"][0]["type"] == "ENTRY_REJECTED"


def test_open_entry_price_revision_is_refused_without_state_change(tmp_path):
    paths, now = batch(tmp_path)
    run(tmp_path, paths, now)
    later, clock = batch(tmp_path, count=37)
    run(tmp_path, later, clock)
    before = (tmp_path / "account" / "ai_led_offline_state.json").read_bytes()
    def changed(body):
        body["candles"][36]["open"] = 600.1
    revised, clock = batch(tmp_path, count=38, mutate=changed)
    with pytest.raises(ValueError, match="accepted entry"):
        run(tmp_path, revised, clock)
    assert (tmp_path / "account" / "ai_led_offline_state.json").read_bytes() == before


def test_previously_monitored_bar_revision_is_rejected(tmp_path):
    paths, now = batch(tmp_path)
    run(tmp_path, paths, now)
    later, clock = batch(tmp_path, count=38)
    run(tmp_path, later, clock)
    before = (tmp_path / "account" / "ai_led_offline_state.json").read_bytes()
    def revised(body):
        body["candles"][37]["low"] = 593
    later, clock = batch(tmp_path, count=39, mutate=revised)
    with pytest.raises(ValueError, match="previously monitored"):
        run(tmp_path, later, clock)
    assert (tmp_path / "account" / "ai_led_offline_state.json").read_bytes() == before


def test_same_timestamp_input_revision_and_backward_clock_are_rejected(tmp_path):
    paths, now = batch(tmp_path)
    run(tmp_path, paths, now, provider([]))
    later, clock = batch(tmp_path, count=36)
    run(tmp_path, later, clock, provider([]))
    def revise(body):
        body["candles"][-1]["volume"] = 1001
    paths, now = batch(tmp_path, mutate=revise)
    with pytest.raises(ValueError, match="changed market"):
        run(tmp_path, paths, now)
    older, clock = batch(tmp_path, count=35)
    # Different candidate universe order is immutable even when data are replayed.
    with pytest.raises(ValueError, match="immutable"):
        run(tmp_path, older, clock, instrument_priority=SYMBOLS[::-1])


def test_duplicate_json_keys_and_duplicate_symbols_fail_closed(tmp_path):
    paths, now = batch(tmp_path)
    result = run(tmp_path, paths, now, provider([proposal(), proposal()]))
    assert result["position"] is None
    duplicate = '{"schema_version":"ai-led-offline-paper-v1","action":"BUY","action":"NO_TRADE","proposals":[]}'
    result = run(tmp_path, paths, now, offline.SavedResponseProvider(duplicate), output="duplicate-json")
    assert result["position"] is None
    assert result["decision"]["reason"] == "invalid AI response; details redacted"


def test_atomic_write_failure_preserves_account(tmp_path, monkeypatch):
    paths, now = batch(tmp_path)
    run(tmp_path, paths, now, provider([]))
    before = (tmp_path / "account" / "ai_led_offline_state.json").read_bytes()
    later, clock = batch(tmp_path, count=36)
    def fail(*args):
        raise OSError("simulated disk failure")
    monkeypatch.setattr(offline.os, "replace", fail)
    with pytest.raises(OSError):
        run(tmp_path, later, clock, provider([]))
    assert (tmp_path / "account" / "ai_led_offline_state.json").read_bytes() == before


def test_cli_only_accepts_saved_responses(tmp_path, monkeypatch, capsys):
    from scripts.run_ai_led_offline import main
    paths, now = batch(tmp_path)
    response = tmp_path / "response.json"
    response.write_text(provider([]).content)
    monkeypatch.setattr("sys.argv", ["offline", "--snapshot", str(paths[0]), "--snapshot", str(paths[1]),
                        "--instrument-priority", *SYMBOLS, "--paper-capital", "1000",
                        "--response", str(response), "--as-of", now.isoformat(),
                        "--output", str(tmp_path / "account")])
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["trade_placement_enabled"] is False
