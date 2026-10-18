from pathlib import Path
import sys

import pandas as pd
import pytest


RESEARCH_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RESEARCH_DIR))

from research_core import (  # noqa: E402
    SPLITS,
    _exit_trade,
    _passes_filter,
    _resample_15m,
    minimum_gross_win_rate,
)


def test_break_even_win_rate_includes_average_cost():
    assert minimum_gross_win_rate(100.0, 80.0, 10.0) == pytest.approx(0.5)


def test_exit_assumes_stop_before_target_when_both_hit_in_same_bar():
    frame = pd.DataFrame(
        [
            {
                "timestamp": pd.Timestamp("2025-01-02 09:25", tz="Asia/Kolkata"),
                "bar_end_timestamp": pd.Timestamp("2025-01-02 09:30", tz="Asia/Kolkata"),
                "open": 100.0,
                "high": 102.0,
                "low": 98.5,
                "close": 101.0,
            }
        ]
    )
    event = {
        "symbol": "TEST",
        "signal_timestamp": pd.Timestamp("2025-01-02 09:20", tz="Asia/Kolkata"),
        "entry_timestamp": pd.Timestamp("2025-01-02 09:25", tz="Asia/Kolkata"),
        "entry_index": 0,
        "down_count": 0,
        "atr_pct": 0.8,
        "turnover_ma_20": 3_000_000,
    }

    trade = _exit_trade(
        frame,
        event,
        {"variant": "target_1_5R_h1", "horizon": 1, "target_r": 1.5},
    )

    assert trade["exit_reason"] == "stop_exit"
    assert trade["exit_price"] == pytest.approx(99.0)


def test_filter_rules_use_signal_features_and_clock_time():
    event = {
        "atr_pct": 0.75,
        "turnover_ma_20": 3_000_000,
        "time_of_day": pd.Timestamp("2025-01-02 10:00").time(),
    }
    assert _passes_filter(event, "atr_0_5")
    assert not _passes_filter(event, "atr_1_0")
    assert _passes_filter(event, "first_90")
    assert _passes_filter(event, "turnover_25l")
    assert _passes_filter(event, "combined")


def test_15_minute_resampling_keeps_only_complete_bars():
    start = pd.Timestamp("2025-01-02 09:15", tz="Asia/Kolkata")
    rows = []
    for index in range(7):
        timestamp = start + pd.Timedelta(minutes=5 * index)
        rows.append(
            {
                "stock": "TEST",
                "timestamp": timestamp,
                "open": 100 + index,
                "high": 101 + index,
                "low": 99 + index,
                "close": 100.5 + index,
                "volume": 1000,
            }
        )
    bars = _resample_15m(pd.DataFrame(rows))

    assert len(bars) == 2
    assert bars["bar_count"].tolist() == [3, 3]
    assert bars.iloc[0]["open"] == 100
    assert bars.iloc[0]["high"] == 103
    assert bars.iloc[0]["close"] == 102.5


def test_variant_splits_exclude_previously_used_oos_period():
    assert SPLITS[0]["role"] == "development"
    assert all(split["role"] == "walk_forward" for split in SPLITS[1:])
    assert max(split["end"] for split in SPLITS) == pd.Timestamp(
        "2025-12-01 23:59:59+05:30"
    )