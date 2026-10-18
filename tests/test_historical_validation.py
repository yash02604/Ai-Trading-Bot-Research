import pandas as pd
import pytest

import backtester as backtester_module
import app.historical_validation as validation_module
from app.cost_model import estimate_round_trip_costs, nse_transaction_rate
from app.historical_validation import calculate_metrics
from app.market_analysis import calculate_indicators, candles_to_dataframe
from app.historical_validation import build_market_regimes


def _candles(count=80):
    start = pd.Timestamp("2025-02-03 09:15:00+05:30")
    return [
        [
            (start + pd.Timedelta(minutes=index * 5)).isoformat(),
            100 + index * 0.1,
            101 + index * 0.1,
            99 + index * 0.1,
            100 + index * 0.1,
            1000 + index,
            0,
        ]
        for index in range(count)
    ]


def test_indicators_at_a_timestamp_do_not_change_when_future_bars_are_appended():
    candles = _candles()
    full = calculate_indicators(candles_to_dataframe(candles))
    cutoff_timestamp = pd.Timestamp(candles[55][0])
    prefix = calculate_indicators(candles_to_dataframe(candles[:56]))
    full_row = full.loc[full.timestamp == cutoff_timestamp].iloc[0]
    prefix_row = prefix.loc[prefix.timestamp == cutoff_timestamp].iloc[0]
    for column in ("ema_9", "ema_20", "rsi_14", "macd", "macd_signal", "macd_histogram", "volume_ma_20"):
        assert full_row[column] == pytest.approx(prefix_row[column])


def test_cost_model_is_itemized_and_uses_conservative_published_nse_rate():
    costs = estimate_round_trip_costs(100, 100, 1, pd.Timestamp("2025-02-03"))
    assert costs["brokerage"] == pytest.approx(0.2)
    assert costs["stt"] == pytest.approx(0.025)
    assert costs["stamp_duty"] == pytest.approx(0.003)
    assert costs["slippage"] == pytest.approx(0.1)
    assert costs["total_cost"] == pytest.approx(sum(v for k, v in costs.items() if k != "total_cost"))
    assert nse_transaction_rate(pd.Timestamp("2023-12-01")) == pytest.approx(0.0000322)
    assert nse_transaction_rate(pd.Timestamp("2025-12-01")) == pytest.approx(0.0000322)


def test_metrics_distinguish_gross_and_after_cost_performance():
    trades = [
        {"gross_pnl": 20.0, "net_pnl": 19.0, "total_cost": 1.0, "pnl": 20.0,
         "holding_minutes": 5, "planned_risk": 10, "capital_exposure": 1000},
        {"gross_pnl": -10.0, "net_pnl": -11.0, "total_cost": 1.0, "pnl": -10.0,
         "holding_minutes": 15, "planned_risk": 10, "capital_exposure": 1000},
    ]
    result = calculate_metrics(trades, 10_000, 10)
    assert result["gross_pnl"] == 10
    assert result["net_pnl"] == 8
    assert result["total_estimated_costs"] == 2
    assert result["winning_trades"] == 1
    assert result["average_holding_minutes"] == 10


def test_backtester_rejects_signal_entry_across_intraday_data_gap(monkeypatch):
    from backtester import Backtester

    candles = _candles(45)
    for index in range(4, len(candles)):
        candles[index][0] = (pd.Timestamp(candles[index][0]) + pd.Timedelta(minutes=5)).isoformat()
    tester = Backtester(candles)
    monkeypatch.setattr(backtester_module, "calculate_indicators", lambda df: df)

    def mark_one(df, **kwargs):
        result = df.copy()
        result["_signal"] = False
        result.loc[3, "_signal"] = True
        return result

    monkeypatch.setattr(backtester_module, "detect_reversal_candidates", mark_one)
    monkeypatch.setattr(backtester_module, "is_frozen_reversal", lambda row: bool(row["_signal"]))
    result = tester.run_reversal_backtest()
    assert result["total_trades"] == 0


def test_signal_window_preserves_overnight_observation_bars_but_rejects_intraday_gaps():
    overnight = pd.DataFrame({
        "timestamp": pd.to_datetime([
            "2025-02-03 15:25+05:30", "2025-02-04 09:15+05:30",
            "2025-02-04 09:20+05:30", "2025-02-04 09:25+05:30",
            "2025-02-04 09:30+05:30",
        ]),
        "frozen_candidate": [False, False, False, True, False],
        "decline_pct": [0, 0, 0, -1.5, 0],
        "rsi_14": [50, 50, 50, 22, 50],
    })
    events, _, rejected = validation_module._make_events({"RELIANCE": overnight})
    assert len(events) == 1
    assert not rejected

    intraday_gap = overnight.copy()
    intraday_gap["timestamp"] = pd.to_datetime([
        "2025-02-04 09:15+05:30", "2025-02-04 09:20+05:30",
        "2025-02-04 10:00+05:30", "2025-02-04 10:05+05:30",
        "2025-02-04 10:10+05:30",
    ])
    events, _, rejected = validation_module._make_events({"RELIANCE": intraday_gap})
    assert not events
    assert rejected[("RELIANCE", "intrasession_signal_data_gap")] == 1


def test_market_regime_for_a_day_uses_only_prior_sessions():
    rows = []
    start = pd.Timestamp("2024-01-01")
    for day in range(100):
        for symbol, base in (("A", 100.0), ("B", 200.0)):
            close = base * (1.001 ** day)
            rows.append({
                "stock": symbol,
                "timestamp": start + pd.Timedelta(days=day, hours=15, minutes=25),
                "open": close, "high": close + 1, "low": close - 1,
                "close": close, "volume": 1000,
            })
    data = pd.DataFrame(rows)
    full_regimes = build_market_regimes(data)
    cutoff = data.timestamp.dt.date.unique()[80]
    prefix = data[data.timestamp.dt.date <= cutoff]
    prefix_regimes = build_market_regimes(prefix)
    full_row = full_regimes[full_regimes.date == cutoff].iloc[0]
    prefix_row = prefix_regimes[prefix_regimes.date == cutoff].iloc[0]
    assert full_row.trend_regime == prefix_row.trend_regime
    assert full_row.volatility_regime == prefix_row.volatility_regime
