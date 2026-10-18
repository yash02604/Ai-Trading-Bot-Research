from datetime import datetime

import pandas as pd
import pytest

from app.cost_model import estimate_round_trip_costs
from app.config import Settings
from app.paper_session import (
    INDEX_ENV, PaperSession, _index_keys, candle_exit, completed_frame,
    context_decision, daily_limit_reason, paper_mode_enabled, run,
    _stock_feed_is_fresh,
)
from app.paper_store import PaperStore
import app.paper_session as paper_session


TZ = "Asia/Kolkata"


def candle(ts, op=100, high=101, low=99, close=100):
    return [ts, op, high, low, close, 1000, 0]


def execution_quote(at, price=102.0, quantity=10000):
    stamp = pd.Timestamp(at)
    return {"price": price, "quantity": quantity,
            "observed_at": stamp, "snapshot_at": stamp}


def test_incomplete_five_minute_candle_is_excluded():
    now = pd.Timestamp("2026-10-10 09:24:59", tz=TZ)
    frame = completed_frame([candle("2026-10-10T09:15:00+05:30"), candle("2026-10-10T09:20:00+05:30")], now)
    assert list(frame.timestamp.dt.minute) == [15]


def test_completed_candle_is_included():
    frame = completed_frame([candle("2026-10-10T09:20:00+05:30")], pd.Timestamp("2026-10-10 09:25", tz=TZ))
    assert len(frame) == 1


def test_next_candle_open_is_the_recorded_entry_and_not_signal_close(tmp_path):
    store = PaperStore(tmp_path / "state.db")
    runner = PaperSession(store)
    pos = {"signal_id": "s", "symbol": "X", "quantity": 1, "entry_price": 101.0,
           "stop_loss": 99.99, "signal_time": "2026-10-10T09:20:00+05:30",
           "entry_candle_time": "2026-10-10T09:25:00+05:30",
           "entry_time": "2026-10-10T09:25:01+05:30", "entry_observed_at": "2026-10-10T09:25:01+05:30",
           "bars_held": 0, "strategy_version": "V1"}
    store.set_position(pos)
    store.commit()
    assert store.get_position()["entry_price"] == 101
    assert store.get_position()["entry_observed_at"] == store.get_position()["entry_time"]
    assert store.get_position()["entry_candle_time"] < store.get_position()["entry_time"]
    store.close()


def test_signal_and_trade_ids_prevent_duplicate_entries(tmp_path):
    store = PaperStore(tmp_path / "state.db")
    args = ("sig", "2026-10-10T09:20:00+05:30", "X", "paper_filled", "reason", {})
    assert store.record_decision(*args)
    assert not store.record_decision(*args)
    store.close()


def test_intrabar_stop_and_gap_through_stop_are_conservative():
    position = {"stop_loss": 99, "bars_held": 1, "entry_time": "2026-10-10T09:20:00+05:30"}
    regular = pd.Series({"open": 100, "low": 98, "close": 98.5, "timestamp": pd.Timestamp("2026-10-10 09:25", tz=TZ)})
    gap = pd.Series({"open": 97, "low": 96, "close": 96.5, "timestamp": pd.Timestamp("2026-10-10 09:30", tz=TZ)})
    assert candle_exit(position, regular) == ("stop", 99)
    assert candle_exit(position, gap) == ("gap_stop", 97)


def test_six_candle_horizon_and_session_exit():
    bar = pd.Series({"open": 100, "low": 100, "close": 102, "timestamp": pd.Timestamp("2026-10-10 10:00", tz=TZ)})
    assert candle_exit({"stop_loss": 99, "bars_held": 6, "entry_time": "2026-10-10T09:30:00+05:30"}, bar) == ("six_candle_horizon", 102)
    close_bar = bar.copy()
    close_bar.timestamp = pd.Timestamp("2026-10-10 15:25", tz=TZ)
    assert candle_exit({"stop_loss": 99, "bars_held": 2, "entry_time": "2026-10-10T09:30:00+05:30"}, close_bar)[0] == "session_exit"


def test_daily_state_rolls_over_by_trading_date(tmp_path):
    store = PaperStore(tmp_path / "state.db")
    first = store.daily_state(datetime(2026, 10, 9).date(), 100000)
    store.update_daily("2026-10-09", 700, 2, 99300)
    store.commit()
    second = store.daily_state(datetime(2026, 10, 10).date(), 99300)
    assert first["trades"] == 0 and second["loss"] == 0 and second["trades"] == 0
    store.close()


def test_frozen_daily_loss_and_trade_limits():
    assert daily_limit_reason({"trades": 3, "loss": 0, "capital": 100000}) == "maximum_trades_per_day"
    assert daily_limit_reason({"trades": 2, "loss": 2000, "capital": 100000}) == "daily_loss_limit"
    assert daily_limit_reason({"trades": 2, "loss": 1999, "capital": 100000}) is None


def test_context_veto_uses_four_indices_and_rejects_missing_or_stale():
    ts = pd.Timestamp("2026-10-10 09:20", tz=TZ)
    now = pd.Timestamp("2026-10-10 09:25", tz=TZ)
    frames = {name: pd.DataFrame([{"timestamp": ts, "open": 101 if i < 3 else 100,
                                   "close": 100 if i < 3 else 101}])
              for i, name in enumerate(INDEX_ENV)}
    passed, reason, _ = context_decision(ts, frames, now)
    assert not passed and reason == "index_down_count:3"
    frames.pop("SENSEX")
    assert context_decision(ts, frames, now)[1].startswith("missing_index_data")
    stale = {name: pd.DataFrame([{"timestamp": ts-pd.Timedelta(minutes=15), "open": 100, "close": 100}]) for name in INDEX_ENV}
    assert context_decision(ts, stale, now)[1].startswith("stale_index_data")


@pytest.mark.parametrize("missing_index", INDEX_ENV)
@pytest.mark.parametrize("failure", ["missing", "stale", "malformed"])
def test_each_required_index_fails_closed_when_unavailable_or_malformed(missing_index, failure):
    signal_time = pd.Timestamp("2026-10-10 09:20", tz=TZ)
    now = pd.Timestamp("2026-10-10 09:25", tz=TZ)
    frames = {
        name: pd.DataFrame([{"timestamp": signal_time, "open": 100.0, "close": 101.0}])
        for name in INDEX_ENV
    }
    if failure == "missing":
        del frames[missing_index]
        expected = "missing_index_data"
    elif failure == "stale":
        frames[missing_index] = frames[missing_index].assign(
            timestamp=signal_time - pd.Timedelta(minutes=15)
        )
        expected = "stale_index_data"
    else:
        frames[missing_index] = frames[missing_index].assign(timestamp="not-a-timestamp")
        expected = "malformed_index_data"

    allowed, reason, _ = context_decision(signal_time, frames, now)
    assert not allowed
    assert reason == f"{expected}:{missing_index}"


def test_completed_frame_drops_malformed_ohlc_and_unfinished_candles():
    now = pd.Timestamp("2026-10-10 09:29:59", tz=TZ)
    valid = candle("2026-10-10T09:15:00+05:30")
    malformed = candle("2026-10-10T09:20:00+05:30", op=100, high=99, low=98, close=98.5)
    unfinished = candle("2026-10-10T09:25:00+05:30")

    frame = completed_frame([valid, malformed, unfinished], now)

    assert frame.timestamp.tolist() == [pd.Timestamp("2026-10-10 09:15", tz=TZ)]


def _candidate_inputs():
    signal_time = pd.Timestamp("2026-10-10 09:20", tz=TZ)
    now = pd.Timestamp("2026-10-10 09:25:01", tz=TZ)
    timestamps = [pd.Timestamp("2026-10-10 06:30", tz=TZ) + pd.Timedelta(minutes=5 * i)
                  for i in range(35)]
    frame = pd.DataFrame({
        "timestamp": timestamps, "open": 100.0, "high": 101.0,
        "low": 99.0, "close": 100.0, "volume": 1000,
    })
    raw = pd.concat([frame, pd.DataFrame([{
        "timestamp": signal_time + pd.Timedelta(minutes=5), "open": 101.0,
        "high": 102.0, "low": 100.0, "close": 101.0, "volume": 1000,
    }])], ignore_index=True)
    indices = {
        name: pd.DataFrame([{"timestamp": signal_time, "open": 100.0, "close": 101.0}])
        for name in INDEX_ENV
    }
    return signal_time, now, frame, raw, indices


def _mark_latest_candidate(frame, **_kwargs):
    result = frame.copy()
    result["decline_pct"] = 0.0
    result["rsi_14"] = 50.0
    result["reversal_condition"] = False
    result.loc[result.index[-1], ["decline_pct", "rsi_14", "reversal_condition"]] = [-1.5, 22.0, True]
    return result


def test_duplicate_signal_is_not_filled_again_after_restart(tmp_path, monkeypatch):
    from app.paper_session import _signal_id

    signal_time, now, frame, raw, indices = _candidate_inputs()
    monkeypatch.setattr(paper_session, "calculate_indicators", lambda data: data)
    monkeypatch.setattr(paper_session, "detect_reversal_candidates", _mark_latest_candidate)
    store = PaperStore(tmp_path / "duplicate-session.sqlite3")
    sid = _signal_id("SIM", signal_time)
    assert store.record_decision(sid, signal_time.isoformat(), "SIM", "paper_filled", "prior", {})

    results = PaperSession(store).evaluate(
        {"SIM": frame, "__raw__SIM": raw}, indices, now, {"SIM": now},
        lambda _symbol: execution_quote(now),
    )

    assert not any(result["decision"] == "PAPER_FILL" for result in results)
    assert store.get_position() is None
    assert store.db.execute("SELECT COUNT(*) FROM decisions WHERE signal_id=?", (sid,)).fetchone()[0] == 1
    assert store.db.execute("SELECT COUNT(*) FROM audit WHERE event_type='duplicate_signal'").fetchone()[0] == 1
    store.close()


def test_insufficient_capital_rejects_without_paper_entry(tmp_path, monkeypatch):
    signal_time, now, frame, raw, indices = _candidate_inputs()
    monkeypatch.setattr(paper_session, "calculate_indicators", lambda data: data)
    monkeypatch.setattr(paper_session, "detect_reversal_candidates", _mark_latest_candidate)
    store = PaperStore(tmp_path / "insufficient-capital.sqlite3")

    results = PaperSession(store, initial_capital=50).evaluate(
        {"SIM": frame, "__raw__SIM": raw}, indices, now, {"SIM": now},
        lambda _symbol: execution_quote(now),
    )

    assert results == [{"decision": "REJECTED", "symbol": "SIM", "reason": "no_whole_share_capacity"}]
    assert store.get_position() is None
    assert store.daily_state(now.date(), 50)["trades"] == 0
    store.close()


def test_open_position_survives_restart(tmp_path):
    path = tmp_path / "state.db"
    store = PaperStore(path)
    store.set_position({"signal_id": "sig", "symbol": "X", "quantity": 2})
    store.commit()
    store.close()
    recovered = PaperStore(path)
    assert recovered.get_position()["signal_id"] == "sig"
    recovered.close()


def test_cost_and_net_pnl_reconcile(tmp_path):
    store = PaperStore(tmp_path / "state.db")
    session = PaperSession(store)
    day = store.daily_state(datetime(2026, 10, 10).date(), 100000)
    store.commit()
    pos = {"signal_id": "s", "symbol": "X", "quantity": 10, "entry_price": 100,
           "stop_loss": 99, "entry_time": "2026-10-10T09:20:00+05:30", "bars_held": 1}
    result = session._close(pos, 101, "test", pd.Timestamp("2026-10-10 09:25", tz=TZ), day)
    costs = estimate_round_trip_costs(100, 101, 10, datetime(2026, 10, 10).date())
    assert result["gross_pnl"] == 10
    assert result["estimated_costs"] == pytest.approx(costs["total_cost"])
    assert result["net_pnl"] == pytest.approx(10-costs["total_cost"])
    assert result["paper_fill"] is True
    store.close()


def test_api_failure_cannot_produce_entry(tmp_path):
    # Empty stock/index data produces no candidate, including after a data outage.
    store = PaperStore(tmp_path / "state.db")
    assert PaperSession(store).evaluate({}, {}, pd.Timestamp("2026-10-10 10:00", tz=TZ)) == []
    store.close()


def test_raw_candle_frames_are_not_scanned_as_independent_symbols(tmp_path, monkeypatch):
    now = pd.Timestamp("2026-10-10 12:10", tz=TZ)
    timestamps = [now - pd.Timedelta(minutes=5 * offset) for offset in range(35, 0, -1)]
    frame = pd.DataFrame({
        "timestamp": timestamps,
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.0,
        "volume": 1000,
    })
    analyzed = []
    monkeypatch.setattr(paper_session, "calculate_indicators", lambda data: data)

    def identify(data, **_kwargs):
        analyzed.append(len(data))
        return data.assign(decline_pct=0.0, rsi_14=50.0, reversal_condition=False)

    monkeypatch.setattr(paper_session, "detect_reversal_candidates", identify)
    store = PaperStore(tmp_path / "raw-frame.sqlite3")
    results = PaperSession(store).evaluate(
        {"SIM": frame, "__raw__SIM": frame.copy()}, {}, now,
        {"SIM": now},
    )
    assert results == []
    assert analyzed == [35]
    assert store.db.execute(
        "SELECT COUNT(*) FROM audit WHERE reason LIKE '%__raw__%'"
    ).fetchone()[0] == 0
    store.close()


def test_signal_window_gap_is_audited_and_rejected(tmp_path, monkeypatch):
    now = pd.Timestamp("2026-10-10 12:16", tz=TZ)
    timestamps = [pd.Timestamp("2026-10-10 09:15", tz=TZ) + pd.Timedelta(minutes=5 * i)
                  for i in range(35)]
    timestamps[-1] += pd.Timedelta(minutes=5)
    frame = pd.DataFrame({
        "timestamp": timestamps,
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.0,
        "volume": 1000,
    })
    raw = pd.concat([frame, pd.DataFrame([{
        "timestamp": timestamps[-1] + pd.Timedelta(minutes=5),
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000,
    }])], ignore_index=True)
    monkeypatch.setattr(paper_session, "calculate_indicators", lambda data: data)

    def identify(data, **_kwargs):
        result = data.assign(decline_pct=0.0, rsi_14=50.0, reversal_condition=False)
        result.loc[result.index[-1], ["decline_pct", "rsi_14", "reversal_condition"]] = [-1.5, 22.0, True]
        return result

    monkeypatch.setattr(paper_session, "detect_reversal_candidates", identify)
    store = PaperStore(tmp_path / "gap.sqlite3")
    quote_calls = []
    result = PaperSession(store).evaluate(
        {"SIM": frame, "__raw__SIM": raw}, {}, now,
        {"SIM": now}, lambda symbol: quote_calls.append(symbol),
    )
    audit = store.db.execute(
        "SELECT reason FROM audit WHERE event_type='signal_gap_rejection'"
    ).fetchone()
    assert result == []
    assert audit and audit[0].startswith("SIM:")
    assert quote_calls == []
    assert store.get_position() is None
    store.close()


def test_stock_feed_staleness_rejects_entry_candidates(tmp_path):
    # Staleness is checked before signal generation in the session runner.
    store = PaperStore(tmp_path / "state.db")
    runner = PaperSession(store)
    stale = pd.DataFrame([{"timestamp": pd.Timestamp("2026-10-10 09:00", tz=TZ)}] * 35)
    assert runner.evaluate({"X": stale}, {}, pd.Timestamp("2026-10-10 10:00", tz=TZ)) == []
    store.close()


def test_malformed_and_timezone_incompatible_stock_timestamps_fail_closed():
    now = pd.Timestamp("2026-10-10 10:00", tz=TZ)
    malformed = pd.DataFrame({"timestamp": ["not-a-timestamp"]})
    incompatible_timezone = pd.DataFrame({"timestamp": [object()]})
    empty = pd.DataFrame(columns=["timestamp"])
    assert not _stock_feed_is_fresh(malformed, now)
    assert not _stock_feed_is_fresh(incompatible_timezone, now)
    assert not _stock_feed_is_fresh(empty, now)
    assert not _stock_feed_is_fresh(None, now)


def test_paper_mode_fails_closed_and_index_configuration_is_mandatory(monkeypatch):
    for config in ({}, {"PAPER_TRADING": ""}, {"PAPER_TRADING": "false"},
                   {"PAPER_TRADING": "invalid"}, {"PAPER_TRADING": None}):
        assert not paper_mode_enabled(config)
    assert paper_mode_enabled({"PAPER_TRADING": "true"})
    monkeypatch.delenv("PAPER_TRADING", raising=False)
    assert not Settings.from_env().paper_trading
    with pytest.raises(RuntimeError, match="four-index"):
        _index_keys({})


def test_real_runner_fails_before_session_start_without_mode_or_index_keys(monkeypatch):
    monkeypatch.delenv("PAPER_TRADING", raising=False)
    with pytest.raises(RuntimeError, match="PAPER_TRADING"):
        run()

    monkeypatch.setenv("PAPER_TRADING", "true")
    monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "test-only-token")
    for key in INDEX_ENV.values():
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(RuntimeError, match="four-index"):
        run()


def test_paper_workflow_contains_no_broker_order_endpoint():
    from pathlib import Path
    app_dir = Path(__file__).parents[1] / "app"
    source = (app_dir / "paper_session.py").read_text(encoding="utf-8").lower()
    client_source = (app_dir / "upstox_client.py").read_text(encoding="utf-8").lower()
    assert "get_intraday_candles" in source
    assert "place_order" not in source and "modify_order" not in source and "cancel_order" not in source
    assert ".post(" not in client_source and ".put(" not in client_source and ".delete(" not in client_source


def test_runner_emits_scan_progress_and_request_counts(monkeypatch, tmp_path, caplog):
    import logging

    monkeypatch.setenv("PAPER_TRADING", "true")
    monkeypatch.setattr(paper_session, "get_settings", lambda: Settings(
        "test-only-token", None, "test-model", 1.0, True, 100_000.0,
    ))
    monkeypatch.setattr(paper_session, "STOCKS", {"TEST_STOCK": "test-stock-key"})
    monkeypatch.setattr(paper_session, "PaperStore", lambda: PaperStore(tmp_path / "state.db"))
    for index, env_key in INDEX_ENV.items():
        monkeypatch.setenv(env_key, f"test-{index}-key")
    requests = []
    monkeypatch.setattr(paper_session, "get_intraday_candles", lambda key, interval: (
        requests.append((key, interval)) or {"data": {"candles": []}}
    ))

    def stop_after_one_scan(_seconds):
        raise KeyboardInterrupt

    monkeypatch.setattr(paper_session.time_module, "sleep", stop_after_one_scan)
    with caplog.at_level(logging.INFO, logger="app.paper_session"):
        run()

    assert len(requests) == 1 + len(INDEX_ENV)
    assert "paper_scan_start scan=1" in caplog.text
    assert "stock_requests=1 stock_successes=1 stock_failures=0" in caplog.text
    assert "index_requests=4 index_successes=4 index_failures=0" in caplog.text
    assert "'TEST_STOCK': None" in caplog.text
    assert "test-only-token" not in caplog.text


def test_runner_market_data_exceptions_fail_closed_for_the_scan(monkeypatch, tmp_path, caplog):
    import logging

    monkeypatch.setenv("PAPER_TRADING", "true")
    monkeypatch.setattr(paper_session, "get_settings", lambda: Settings(
        "test-only-token", None, "test-model", 1.0, True, 100_000.0,
    ))
    monkeypatch.setattr(paper_session, "STOCKS", {"TEST_STOCK": "test-stock-key"})
    monkeypatch.setattr(paper_session, "PaperStore", lambda: PaperStore(tmp_path / "api-errors.db"))
    for env_key in INDEX_ENV.values():
        monkeypatch.setenv(env_key, f"test-{env_key}")
    requested_keys = []

    def fail_read_only_request(key, interval):
        requested_keys.append((key, interval))
        raise TimeoutError("offline test outage")

    monkeypatch.setattr(paper_session, "get_intraday_candles", fail_read_only_request)
    monkeypatch.setattr(paper_session.time_module, "sleep", lambda _seconds: (_ for _ in ()).throw(KeyboardInterrupt))

    with caplog.at_level(logging.INFO, logger="app.paper_session"):
        run()

    assert len(requested_keys) == 1 + len(INDEX_ENV)
    assert all(interval == 5 for _, interval in requested_keys)
    assert "category=stock symbol=TEST_STOCK exception=TimeoutError" in caplog.text
    assert "category=index index=NIFTY50 exception=TimeoutError" in caplog.text
    assert "stock_requests=1 stock_successes=0 stock_failures=1" in caplog.text
    assert "index_requests=4 index_successes=0 index_failures=4" in caplog.text
    assert "test-only-token" not in caplog.text


def test_buy_execution_quote_requires_fresh_observable_ask():
    from app.paper_session import _buy_execution_quote
    observed = pd.Timestamp("2026-10-10 09:25:01", tz=TZ)
    valid = {"data": {"NSE_EQ:TEST": {
        "timestamp": observed.isoformat(),
        "depth": {"sell": [{"price": 102.0, "quantity": 50}]},
    }}}
    quote = _buy_execution_quote(valid, observed)
    assert quote["price"] == 102.0 and quote["quantity"] == 50
    assert _buy_execution_quote({"data": {}}, observed) is None
    stale = {"data": {"NSE_EQ:TEST": {
        "timestamp": (observed - pd.Timedelta(seconds=6)).isoformat(),
        "depth": {"sell": [{"price": 102.0, "quantity": 50}]},
    }}}
    assert _buy_execution_quote(stale, observed) is None
    no_ask = {"data": {"NSE_EQ:TEST": {
        "timestamp": observed.isoformat(), "depth": {"sell": [{"price": 0, "quantity": 0}]},
    }}}
    assert _buy_execution_quote(no_ask, observed) is None


@pytest.mark.parametrize("quote", [
    {"data": {"NSE_EQ:TEST": {"timestamp": "invalid", "depth": {"sell": [{"price": 102, "quantity": 1}]}}}},
    {"data": {"NSE_EQ:TEST": {"timestamp": "2026-10-10T09:25:01+05:30", "depth": {"sell": [{"price": float("nan"), "quantity": 1}]}}}},
    {"data": {"NSE_EQ:TEST": {"timestamp": "2026-10-10T09:25:01+05:30", "depth": {"sell": [{"price": 102, "quantity": "bad"}]}}}},
    {"data": {"NSE_EQ:TEST": {"timestamp": "2026-10-10T09:25:01+05:30", "depth": {}}}},
])
def test_malformed_execution_quotes_are_rejected(quote):
    from app.paper_session import _buy_execution_quote

    observed = pd.Timestamp("2026-10-10 09:25:01", tz=TZ)
    assert _buy_execution_quote(quote, observed) is None


def test_deterministic_paper_session_simulation(tmp_path, monkeypatch, caplog):
    """Exercise the runner logic end-to-end without the polling loop or any network calls."""
    signal_time = pd.Timestamp("2026-10-10 09:20", tz=TZ)
    before_next_open = pd.Timestamp("2026-10-10 09:24:59", tz=TZ)
    observed_open = pd.Timestamp("2026-10-10 09:25:01", tz=TZ)
    calls = []

    def identify_signal(frame, **kwargs):
        calls.append(frame.timestamp.max())
        result = frame.copy()
        result["decline_pct"] = 0.0
        result["rsi_14"] = 30.0
        result["reversal_condition"] = False
        mask = result.timestamp == signal_time
        result.loc[mask, ["decline_pct", "rsi_14", "reversal_condition"]] = [-1.5, 22.0, True]
        return result

    monkeypatch.setattr(paper_session, "calculate_indicators", lambda frame: frame)
    monkeypatch.setattr(paper_session, "detect_reversal_candidates", identify_signal)
    market_api = __import__("unittest.mock", fromlist=["Mock"]).Mock(
        side_effect=AssertionError("market API must not be called in deterministic simulation")
    )
    monkeypatch.setattr(paper_session, "get_intraday_candles", market_api)

    candles = []
    start = pd.Timestamp("2026-10-10 06:30", tz=TZ)
    for i in range(36):
        ts = start + pd.Timedelta(minutes=5 * i)
        if ts == signal_time:
            candles.append(candle(ts.isoformat(), 100, 101, 89, 90))
        elif ts == signal_time + pd.Timedelta(minutes=5):
            candles.append(candle(ts.isoformat(), 101, 102, 100, 101))
        else:
            candles.append(candle(ts.isoformat()))
    raw = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume", "oi"])
    raw["timestamp"] = raw.timestamp.map(paper_session._stamp)
    indices = {
        name: pd.DataFrame([{"timestamp": signal_time, "open": 100.0, "close": 101.0}])
        for name in INDEX_ENV
    }

    db_path = tmp_path / "simulation.sqlite3"
    store = PaperStore(db_path)
    session = PaperSession(store)
    incomplete = completed_frame(candles, before_next_open)
    first = session.evaluate({"SIM": incomplete, "__raw__SIM": raw}, indices, before_next_open,
                             {"SIM": before_next_open})
    assert not any(result["decision"] == "PAPER_FILL" for result in first)
    assert signal_time not in calls

    completed = completed_frame(candles, observed_open)
    results = session.evaluate({"SIM": completed, "__raw__SIM": raw}, indices, observed_open,
                               {"SIM": observed_open}, lambda _symbol: execution_quote(observed_open))
    fill = next(result for result in results if result["decision"] == "PAPER_FILL")
    assert calls[-1] == signal_time  # no unfinished/future stock candle reached the detector
    assert fill["entry_price"] == 102.0  # observed best ask, not historical candle open 101 or signal close 90
    assert fill["entry_reference_price"] == 101.0
    assert fill["entry_reference_kind"] == "next_candle_open"
    assert fill["entry_candle_time"] == "2026-10-10T09:25:00+05:30"
    assert fill["entry_time"] == fill["entry_observed_at"] == observed_open.isoformat()
    fill_observation = store.db.execute(
        "SELECT * FROM execution_observations WHERE signal_id=? AND paper_entry_eligible=1",
        (fill["signal_id"],),
    ).fetchone()
    assert fill_observation["reference_price"] == 101.0
    assert fill_observation["simulated_entry_price"] == 102.0
    assert fill_observation["simulated_quantity"] == fill["quantity"]
    assert fill_observation["required_quantity"] == fill["quantity"]
    assert fill_observation["ask_quantity"] >= fill_observation["required_quantity"]

    failed_store = PaperStore(tmp_path / "observation-write-failure.sqlite3")
    original_record_observation = failed_store.record_execution_observation

    def fail_fill_observation(record):
        if record["paper_entry_eligible"]:
            raise OSError("synthetic observation storage failure")
        return original_record_observation(record)

    monkeypatch.setattr(failed_store, "record_execution_observation", fail_fill_observation)
    failed_scan = PaperSession(failed_store).evaluate(
        {"SIM": completed, "__raw__SIM": raw}, indices, observed_open,
        {"SIM": observed_open}, lambda _symbol: execution_quote(observed_open),
    )
    assert failed_scan == [{
        "decision": "REJECTED", "symbol": "SIM",
        "reason": "paper_fill_persistence_failed",
    }]
    failed_store.close()
    failed_store = PaperStore(tmp_path / "observation-write-failure.sqlite3")
    assert failed_store.get_position() is None
    assert failed_store.db.execute(
        "SELECT outcome,reason FROM decisions WHERE outcome='rejected'"
    ).fetchone()[:] == ("rejected", "paper_fill_persistence_failed")
    assert failed_store.db.execute(
        "SELECT COUNT(*) FROM decisions WHERE outcome='paper_filled'"
    ).fetchone()[0] == 0
    assert failed_store.db.execute(
        "SELECT trades FROM daily WHERE day=?", (observed_open.date().isoformat(),)
    ).fetchone()[0] == 0
    assert failed_store.db.execute(
        "SELECT COUNT(*) FROM execution_observations"
    ).fetchone()[0] == 0
    assert failed_store.db.execute(
        "SELECT reason FROM audit WHERE event_type='paper_fill_persistence_failure'"
    ).fetchone()[0] == "OSError"
    assert "paper_entry_rejected symbol=SIM reason=paper_fill_persistence_failed exception=OSError" in caplog.text
    failed_store.close()
    early_store = PaperStore(tmp_path / "early-open.sqlite3")
    too_early = PaperSession(early_store).evaluate(
        {"SIM": completed, "__raw__SIM": raw}, indices, observed_open,
        {"SIM": before_next_open}, lambda _symbol: execution_quote(observed_open),
    )
    assert not any(result["decision"] == "PAPER_FILL" for result in too_early)
    assert early_store.get_position() is None
    early_store.close()
    unavailable_store = PaperStore(tmp_path / "unavailable-quote.sqlite3")
    def failed_quote(_symbol):
        raise ConnectionError("simulated quote outage")

    unavailable = PaperSession(unavailable_store).evaluate(
        {"SIM": completed, "__raw__SIM": raw}, indices, observed_open,
        {"SIM": observed_open}, failed_quote,
    )
    assert not any(result["decision"] == "PAPER_FILL" for result in unavailable)
    assert unavailable_store.get_position() is None
    assert unavailable_store.db.execute(
        "SELECT COUNT(*) FROM audit WHERE event_type='unavailable_execution_price'"
    ).fetchone()[0] == 1
    assert unavailable_store.db.execute(
        "SELECT reason FROM audit WHERE event_type='execution_quote_error'"
    ).fetchone()[0] == "SIM:ConnectionError"
    unavailable_observation = unavailable_store.db.execute(
        "SELECT quote_valid,quote_reason,eligibility_reason FROM execution_observations"
    ).fetchone()
    assert tuple(unavailable_observation) == (
        0, "quote_request_error:ConnectionError", "quote_request_error:ConnectionError",
    )
    unavailable_store.close()
    low_depth_store = PaperStore(tmp_path / "low-depth.sqlite3")
    low_depth = PaperSession(low_depth_store).evaluate(
        {"SIM": completed, "__raw__SIM": raw}, indices, observed_open,
        {"SIM": observed_open}, lambda _symbol: execution_quote(observed_open, quantity=1),
    )
    assert low_depth == [{"decision": "REJECTED", "symbol": "SIM", "reason": "insufficient_ask_quantity"}]
    assert low_depth_store.get_position() is None
    low_depth_observation = low_depth_store.db.execute(
        "SELECT ask_quantity,paper_entry_eligible,eligibility_reason FROM execution_observations"
    ).fetchone()
    assert tuple(low_depth_observation) == (1, 0, "insufficient_ask_quantity")
    low_depth_store.close()
    late_store = PaperStore(tmp_path / "late-quote.sqlite3")
    late_now = pd.Timestamp("2026-10-10 09:30:01", tz=TZ)
    late = PaperSession(late_store).evaluate(
        {"SIM": completed, "__raw__SIM": raw}, indices, observed_open,
        {"SIM": observed_open}, lambda _symbol: execution_quote(late_now),
    )
    assert not any(result["decision"] == "PAPER_FILL" for result in late)
    assert late_store.get_position() is None
    late_observation = late_store.db.execute(
        "SELECT quote_valid,eligibility_reason FROM execution_observations"
    ).fetchone()
    assert tuple(late_observation) == (1, "late_execution_quote")
    late_store.close()
    # Existing open position blocks another signal while state is recovered after restart.
    store.close()
    store = PaperStore(db_path)
    session = PaperSession(store)
    assert store.get_position()["signal_id"] == fill["signal_id"]
    assert session.evaluate({"SIM": completed}, indices, pd.Timestamp("2026-10-10 09:26", tz=TZ)) == []
    assert store.get_position()["signal_id"] == fill["signal_id"]

    # First completed entry bar is processed once; a later gap through stop fills at the bar open.
    exit_bars = pd.DataFrame([
        {"timestamp": pd.Timestamp("2026-10-10 09:25", tz=TZ), "open": 101, "high": 102, "low": 101, "close": 101},
        {"timestamp": pd.Timestamp("2026-10-10 09:30", tz=TZ), "open": 99.5, "high": 100, "low": 99, "close": 99.5},
    ])
    exited = session.evaluate({"SIM": exit_bars}, indices, pd.Timestamp("2026-10-10 09:35", tz=TZ))
    assert exited[0]["decision"] == "PAPER_EXIT"
    assert exited[0]["exit_reason"] == "gap_stop"
    assert exited[0]["exit_price"] == 99.5
    assert store.get_position() is None
    assert store.db.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1
    assert store.write_daily_summary(pd.Timestamp("2026-10-10").date()).parent == tmp_path / "paper_reports"
    assert market_api.call_count == 0
    store.close()

    # Context failures, daily limits, horizon, and session exit use the same deterministic code paths.
    down_indices = {
        name: pd.DataFrame([{"timestamp": signal_time, "open": 101 if i < 3 else 100,
                             "close": 100 if i < 3 else 101}])
        for i, name in enumerate(INDEX_ENV)
    }
    veto_store = PaperStore(tmp_path / "veto.sqlite3")
    veto = PaperSession(veto_store).evaluate(
        {"SIM": completed, "__raw__SIM": raw}, down_indices, observed_open,
        execution_quote_provider=lambda _symbol: execution_quote(observed_open))
    assert veto[0]["decision"] == "REJECTED"
    veto_observation = veto_store.db.execute(
        "SELECT paper_entry_eligible,eligibility_reason FROM execution_observations"
    ).fetchone()
    assert tuple(veto_observation) == (0, "index_down_count:3")
    for missing_or_stale in (
        {name: frame for name, frame in indices.items() if name != "SENSEX"},
        {name: frame.assign(timestamp=frame.timestamp - pd.Timedelta(minutes=15)) for name, frame in indices.items()},
    ):
        ok, reason, _ = context_decision(signal_time, missing_or_stale, observed_open)
        assert not ok and ("missing_index_data" in reason or "stale_index_data" in reason)
        context_store = PaperStore(tmp_path / f"context-{len(missing_or_stale)}-{reason.split(':')[0]}.sqlite3")
        rejected = PaperSession(context_store).evaluate(
            {"SIM": completed, "__raw__SIM": raw}, missing_or_stale, observed_open,
            execution_quote_provider=lambda _symbol: execution_quote(observed_open)
        )
        assert rejected[0]["decision"] == "REJECTED"
        assert context_store.get_position() is None
        context_store.close()
    veto_store.close()

    limit_cases = (("max_trades", 0, 3, "maximum_trades_per_day"),
                   ("daily_loss", 2000, 0, "daily_loss_limit"))
    for suffix, loss, count, reason in limit_cases:
        limited_store = PaperStore(tmp_path / f"{suffix}.sqlite3")
        state = limited_store.daily_state(observed_open.date(), 100000)
        limited_store.update_daily(observed_open.date().isoformat(), loss, count, state["capital"])
        limited_store.commit()
        rejected = PaperSession(limited_store).evaluate(
            {"SIM": completed, "__raw__SIM": raw}, indices, observed_open,
            execution_quote_provider=lambda _symbol: execution_quote(observed_open)
        )
        assert rejected[0]["decision"] == "REJECTED"
        assert rejected[0]["reason"] == reason
        assert limited_store.get_position() is None
        limited_store.close()

    assert daily_limit_reason({"trades": 3, "loss": 0, "capital": 100000}) == "maximum_trades_per_day"
    assert daily_limit_reason({"trades": 2, "loss": 2000, "capital": 100000, "day_start_capital": 100000}) == "daily_loss_limit"
    assert candle_exit({"stop_loss": 99, "bars_held": 6, "entry_time": "2026-10-10T09:20:00+05:30"},
                       pd.Series({"timestamp": pd.Timestamp("2026-10-10 10:00", tz=TZ), "open": 100, "low": 100, "close": 102})) == ("six_candle_horizon", 102.0)
    assert candle_exit({"stop_loss": 99, "bars_held": 1, "entry_time": "2026-10-10T09:20:00+05:30"},
                       pd.Series({"timestamp": pd.Timestamp("2026-10-10 15:25", tz=TZ), "open": 100, "low": 100, "close": 102}))[0] == "session_exit"

    for suffix, held, entry_time, bar_time, expected_reason in (
        ("horizon", 5, "2026-10-10T09:55:00+05:30", "2026-10-10 10:00", "six_candle_horizon"),
        ("session", 1, "2026-10-10T09:20:00+05:30", "2026-10-10 15:25", "session_exit"),
    ):
        exit_store = PaperStore(tmp_path / f"{suffix}.sqlite3")
        exit_store.daily_state(observed_open.date(), 100000)
        exit_store.set_position({
            "signal_id": suffix, "symbol": "SIM", "quantity": 1, "entry_price": 100,
            "stop_loss": 99, "entry_time": entry_time, "entry_candle_time": entry_time,
            "bars_held": held,
        })
        exit_store.commit()
        bar_ts = pd.Timestamp(bar_time, tz=TZ)
        exit_results = PaperSession(exit_store).evaluate(
            {"SIM": pd.DataFrame([{"timestamp": bar_ts, "open": 100, "high": 102,
                                    "low": 100, "close": 102}]),
             }, indices, bar_ts + pd.Timedelta(minutes=5)
        )
        assert exit_results[0]["decision"] == "PAPER_EXIT"
        assert exit_results[0]["exit_reason"] == expected_reason
        exit_store.close()

