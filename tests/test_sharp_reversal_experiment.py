from datetime import date

import pandas as pd
import pytest

from app.cost_model import CostAssumptions, estimate_round_trip_costs
from app.research.execution_integrity import TimestampSemanticsEvidence
from app.research.sharp_reversal_experiment import (
    ExperimentCandidate,
    build_events,
    metrics,
    prepare_data,
    prepare_symbol_frames,
    simulate,
)


def candidate(*, stop=1.0, horizon=3):
    return ExperimentCandidate(
        strategy_id="sharp_reversal_test_v1", version="test-v1", kind="research_candidate",
        decline_bars=3, min_decline_pct=0.5, max_decline_pct_exclusive=4.0,
        rsi_min_inclusive=0.0, rsi_max_exclusive=35.0,
        require_monotonic_decline=True, stop_pct=stop, horizon_bars=horizon,
    )


def fixture_evidence():
    # Synthetic authorization for deterministic unit fixtures only; it does not
    # establish timestamp semantics or provenance for the repository datasets.
    return TimestampSemanticsEvidence(
        label_semantics="START", verification_status="VERIFIED",
        provider_documentation_url="https://provider.invalid/documentation",
        acquisition_manifest_sha256="a" * 64,
        acquisition_request_ids=("synthetic-fixture",), dataset_sha256={},
    )


def fixture_sessions():
    return {date(2025, 2, 3)}


def frame(start="2025-02-03T09:15:00+05:30", count=40):
    times = pd.date_range(start, periods=count, freq="5min")
    prices = [100.0] * count
    pattern = [103.0, 102.0, 101.0, 101.5]
    prices[17:min(21, count)] = pattern[:max(0, min(21, count) - 17)]
    opens = [value for value in prices]
    highs = [value + 0.5 for value in prices]
    lows = [value - 0.5 for value in prices]
    closes = list(prices)
    return pd.DataFrame({
        "timestamp": times, "open": opens, "high": highs, "low": lows,
        "close": closes, "rsi_14": [20.0] * count,
    })


def test_signal_uses_completed_bar_and_enters_next_bar_open():
    data = frame()
    data.loc[20, ["open", "high", "low", "close"]] = [101.0, 102.0, 100.5, 101.5]
    data.loc[21, ["open", "high", "low", "close"]] = [102.25, 103.0, 102.0, 102.75]
    events, signals, rejects = build_events({"TEST": data}, candidate())
    event = next(item for item in events if item["signal_index"] == 20)
    assert event["signal_timestamp"] == data.timestamp.iloc[20]
    assert event["entry_timestamp"] == data.timestamp.iloc[21]
    assert data.open.iloc[event["entry_index"]] == 102.25
    assert signals["TEST"] >= 1
    assert rejects["entry_crosses_session_or_gap"] == 0


def test_session_boundary_signal_is_rejected_instead_of_entering_next_day():
    prior = frame("2025-02-03T14:00:00+05:30", count=18)
    prior.loc[17, ["open", "high", "low", "close"]] = [101.0, 102.0, 100.5, 101.5]
    final = pd.DataFrame({
        "timestamp": [pd.Timestamp("2025-02-03T15:25:00+05:30"), pd.Timestamp("2025-02-04T09:15:00+05:30")],
        "open": [101.0, 101.0], "high": [102.0, 102.0], "low": [100.5, 100.5],
        "close": [101.5, 101.5], "rsi_14": [20.0, 20.0],
    })
    data = pd.concat([prior.iloc[:-1], final], ignore_index=True)
    # Force the final bar's decline pattern while maintaining its 15-candle history.
    data.loc[14:17, "close"] = [104.0, 103.0, 102.0, 102.5]
    events, _, rejects = build_events({"TEST": data}, candidate())
    assert not any(event["signal_index"] == 17 for event in events)
    assert rejects["entry_crosses_session_or_gap"] >= 1


def test_intrasession_gap_rejects_dependent_signal_window():
    data = frame().drop(index=10).reset_index(drop=True)
    _, _, rejects = build_events({"TEST": data}, candidate())
    assert rejects["feature_window_gap_or_warmup"] >= 1


def test_costs_are_itemized_and_include_adverse_slippage():
    costs = estimate_round_trip_costs(100.0, 101.0, 10, date(2025, 2, 3), CostAssumptions())
    assert costs["slippage"] == pytest.approx((1000 + 1010) * 5 / 10000)
    assert costs["total_cost"] == pytest.approx(sum(v for k, v in costs.items() if k != "total_cost"))
    assert costs["brokerage"] > 0
    assert costs["stt"] > 0


def make_event(data, entry_index, *, last_index=None):
    data = data.copy()
    return {"symbol": "TEST", "frame": data, "signal_index": entry_index - 1,
            "entry_index": entry_index, "last_index": entry_index if last_index is None else last_index,
            "signal_timestamp": data.timestamp.iloc[entry_index - 1],
            "entry_timestamp": data.timestamp.iloc[entry_index],
            "decline_pct": -1.0, "rsi_14": 20.0, "regime": {}}


def test_risk_limits_enforce_daily_trade_count_and_gap_stop():
    data = frame(count=32)
    events = [make_event(data, index) for index in (3, 7, 11, 15)]
    trades, result = simulate(events, candidate(horizon=1), 100_000, timestamp_evidence=fixture_evidence(), verified_regular_dates=fixture_sessions())
    assert len(trades) == 3
    assert all(type(trade["quantity"]) is int and trade["quantity"] > 0 for trade in trades)
    assert all(trade["planned_risk"] <= 1_000 for trade in trades)
    assert all(trade["capital_exposure"] <= 100_000 for trade in trades)
    assert result["risk_rejections"].get("Maximum trades for today reached") == 1

    loss_frame = frame(count=32)
    loss_frame.loc[22, ["open", "high", "low", "close"]] = [80.0, 81.0, 79.0, 80.0]
    losing = [make_event(loss_frame, 21, last_index=22), make_event(loss_frame, 24)]
    losing_trades, loss_result = simulate(losing, candidate(horizon=2), 100_000, timestamp_evidence=fixture_evidence(), verified_regular_dates=fixture_sessions())
    assert len(losing_trades) == 1
    assert losing_trades[0]["exit_reason"] == "gap_exit"
    assert loss_result["risk_rejections"].get("Daily loss limit reached") == 1


def test_same_time_events_cannot_open_duplicate_positions():
    data = frame(count=32)
    first = make_event(data, 10)
    duplicate = make_event(data, 10)
    duplicate["symbol"] = "OTHER"
    trades, result = simulate([first, duplicate], candidate(horizon=1), 100_000, timestamp_evidence=fixture_evidence(), verified_regular_dates=fixture_sessions())
    assert len(trades) == 1
    assert result["risk_rejections"].get("Maximum open positions reached") == 1


def test_repeat_run_is_deterministic_and_metrics_match_cost_ledger():
    data = frame(count=32)
    events = [make_event(data, index) for index in (3, 7, 11)]
    first, _ = simulate(events, candidate(horizon=1), 100_000, timestamp_evidence=fixture_evidence(), verified_regular_dates=fixture_sessions())
    second, _ = simulate(events, candidate(horizon=1), 100_000, timestamp_evidence=fixture_evidence(), verified_regular_dates=fixture_sessions())
    assert first == second
    summary = metrics(first, 100_000)
    assert summary["trade_count"] == len(first)
    assert summary["estimated_costs_inr"] == pytest.approx(sum(row["total_cost"] for row in first))
    assert summary["net_pnl_inr"] == pytest.approx(summary["gross_pnl_inr"] - summary["estimated_costs_inr"])
    assert summary["average_net_trade_inr"] == pytest.approx(summary["net_pnl_inr"] / len(first))


def test_data_loader_parses_each_source_format_and_excludes_zero_volume_session(tmp_path):
    import json

    (tmp_path / "strategy_backtest.py").write_text('STOCKS = {"TEST": "NSE_EQ|TEST"}\n', encoding="utf-8")
    (tmp_path / "data").mkdir()
    def session(day, timestamp_separator):
        times = pd.date_range(f"{day}T09:15:00+05:30", periods=75, freq="5min")
        return pd.DataFrame({
            "stock": "TEST", "instrument_key": "NSE_EQ|TEST",
            "timestamp": [stamp.isoformat().replace("T", timestamp_separator) for stamp in times],
            "open": 100, "high": 101, "low": 99, "close": 100, "volume": 10, "oi": 0,
        })
    session("2025-02-03", "T").to_csv(tmp_path / "data/main.csv", index=False)
    oos = session("2025-02-04", " ")
    oos.to_csv(tmp_path / "data/oos.csv", index=False)
    manifest = {"schema_version": 2, "segment": "CM", "sessions": [
        {"date": day, "verification_status": "VERIFIED", "session_type": "REGULAR", "special_session": False}
        for day in ("2025-02-03", "2025-02-04")
    ]}
    (tmp_path / "sessions.json").write_text(json.dumps(manifest), encoding="utf-8")
    spec = {"data_files": ["data/main.csv", "data/oos.csv"], "session_manifest": "sessions.json",
            "partitions": [{"partition_id": "fixture", "start": "2025-02-03", "end": "2025-02-04"}],
            "data_policy": {"known_date_exclusions": []}}
    data, audit = prepare_data(tmp_path, spec)
    assert len(data) == 150
    assert set(data.timestamp.dt.date) == {date(2025, 2, 3), date(2025, 2, 4)}
    assert audit["integrity_error_reason_counts"] == {}
    assert audit["incomplete_symbol_sessions_excluded"] == 0
    oos.loc[10, "volume"] = 0
    oos.to_csv(tmp_path / "data/oos.csv", index=False)
    filtered, anomaly_audit = prepare_data(tmp_path, spec)
    assert len(filtered) == 75
    assert anomaly_audit["exclusion_reason_counts"]["zero_volume_candle_unresolved"] == 1
    assert anomaly_audit["exclusion_reason_counts"]["incomplete_regular_session_coverage"] == 74
    assert anomaly_audit["incomplete_symbol_sessions_excluded"] == 1


def test_indicator_warmup_restarts_after_missing_regular_session():
    def bars(day):
        times = pd.date_range(f"{day}T09:15:00+05:30", periods=40, freq="5min")
        close = [100 + index * 0.1 for index in range(40)]
        return pd.DataFrame({"stock": "TEST", "timestamp": times, "open": close,
                             "high": [value + 0.5 for value in close],
                             "low": [value - 0.5 for value in close], "close": close,
                             "volume": 100, "open_interest": 0})
    data = pd.concat([bars("2025-02-03"), bars("2025-02-05")], ignore_index=True)
    result = prepare_symbol_frames(data, {date(2025, 2, 3), date(2025, 2, 4), date(2025, 2, 5)})["TEST"]
    later_session = result.loc[result.timestamp.dt.date == date(2025, 2, 5)]
    assert not later_session.empty
    assert later_session.timestamp.iloc[0].time() > pd.Timestamp("2025-02-05T09:15:00+05:30").time()
