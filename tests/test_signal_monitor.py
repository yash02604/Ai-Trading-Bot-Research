import pandas as pd
import pytest

import signal_monitor
from signal_monitor import SignalMonitor, is_frozen_signal


NOW = pd.Timestamp("2026-10-01 09:25:00+05:30")


def _candle(timestamp, close=100.0):
    return [timestamp, close, close + 1, close - 1, close, 1000, 0]


def _historical_candles():
    start = pd.Timestamp("2026-09-30 09:15:00+05:30")
    return [
        _candle((start + pd.Timedelta(minutes=5 * index)).isoformat())
        for index in range(35)
    ]


def _monitor(
    monkeypatch,
    *,
    signal_timestamp=None,
    signal_timestamps=None,
    ai_calls=None,
):
    history = _historical_candles()
    intraday = [
        _candle("2026-10-01T09:15:00+05:30"),
        _candle("2026-10-01T09:20:00+05:30"),
    ]
    history_calls = []
    messages = []
    selected_signals = {
        pd.Timestamp(timestamp)
        for timestamp in (signal_timestamps or [])
    }
    if signal_timestamp is not None:
        selected_signals.add(pd.Timestamp(signal_timestamp))

    def fetch_history(instrument_key, date, interval=5):
        history_calls.append((instrument_key, date, interval))
        return {"data": {"candles": history}}

    def fetch_intraday(instrument_key, interval=5):
        return {"data": {"candles": intraday}}

    monkeypatch.setattr(signal_monitor, "calculate_indicators", lambda df: df)

    def detect(df, **kwargs):
        result = df.copy()
        result["decline_pct"] = -0.5
        result["rsi_14"] = 35.0
        result["reversal_condition"] = False
        if selected_signals:
            mask = result["timestamp"].isin(selected_signals)
            result.loc[mask, "decline_pct"] = -1.5
            result.loc[mask, "rsi_14"] = 22.0
            result.loc[mask, "reversal_condition"] = True
        return result

    monkeypatch.setattr(signal_monitor, "detect_reversal_candidates", detect)
    if ai_calls is not None:
        monkeypatch.setattr(
            signal_monitor,
            "analyze_market",
            lambda market_data: ai_calls.append(market_data),
            raising=False,
        )

    monitor = SignalMonitor(
        stocks={"TEST": "NSE_EQ|TEST"},
        intraday_fetcher=fetch_intraday,
        historical_fetcher=fetch_history,
        output=messages.append,
    )
    return monitor, messages, history_calls, intraday


@pytest.mark.parametrize(
    ("decline_pct", "expected"),
    [
        (-2.0001, False),
        (-2.0000, False),
        (-1.9999, True),
        (-1.0001, True),
        (-1.0000, True),
        (-0.9999, False),
    ],
)
def test_frozen_signal_decline_boundaries(decline_pct, expected):
    row = {"decline_pct": decline_pct, "rsi_14": 22.0, "reversal_condition": True}
    assert is_frozen_signal(row) is expected


@pytest.mark.parametrize(
    "row",
    [
        {},
        {"decline_pct": "bad", "rsi_14": 22.0, "reversal_condition": True},
        {"decline_pct": float("nan"), "rsi_14": 22.0, "reversal_condition": True},
        {"decline_pct": -1.5, "rsi_14": float("inf"), "reversal_condition": True},
    ],
)
def test_frozen_signal_invalid_inputs_fail_closed(row):
    assert not is_frozen_signal(row)


def test_frozen_signal_other_thresholds_are_unchanged():
    assert not is_frozen_signal(
        {"decline_pct": -1.5, "rsi_14": 25.0, "reversal_condition": True}
    )


def test_only_regular_session_candles_are_eligible():
    session_date = pd.Timestamp("2026-10-01").date()

    assert signal_monitor._is_regular_session_candle(
        _candle("2026-10-01T09:15:00+05:30"), session_date
    )
    assert not signal_monitor._is_regular_session_candle(
        _candle("2026-10-01T09:10:00+05:30"), session_date
    )
    assert not signal_monitor._is_regular_session_candle(
        _candle("2026-10-01T15:30:00+05:30"), session_date
    )


def test_same_completed_candle_is_alerted_only_once(monkeypatch):
    monitor, messages, history_calls, _ = _monitor(
        monkeypatch,
        signal_timestamp="2026-10-01T09:20:00+05:30",
    )

    first_alerts = monitor.scan_once(NOW)
    second_alerts = monitor.scan_once(NOW + pd.Timedelta(minutes=1))

    assert len(first_alerts) == 1
    assert second_alerts == []
    assert sum("FROZEN SIGNAL DETECTED" in message for message in messages) == 1
    assert len(history_calls) == 1


def test_first_signal_per_fifteen_minute_event(monkeypatch):
    monitor, messages, _, intraday = _monitor(
        monkeypatch,
        signal_timestamps=[
            "2026-10-01T09:20:00+05:30",
            "2026-10-01T09:35:00+05:30",
            "2026-10-01T09:55:00+05:30",
        ],
    )

    first_alerts = monitor.scan_once(NOW)

    intraday.extend(
        _candle(f"2026-10-01T09:{minute:02d}:00+05:30")
        for minute in (25, 30, 35)
    )
    same_event_alerts = monitor.scan_once(
        pd.Timestamp("2026-10-01 09:40:00+05:30")
    )

    intraday.extend(
        _candle(f"2026-10-01T09:{minute:02d}:00+05:30")
        for minute in (40, 45, 50, 55)
    )
    next_event_alerts = monitor.scan_once(
        pd.Timestamp("2026-10-01 10:00:00+05:30")
    )

    assert [alert["timestamp"] for alert in first_alerts] == [
        pd.Timestamp("2026-10-01 09:20:00+05:30")
    ]
    assert same_event_alerts == []
    assert [alert["timestamp"] for alert in next_event_alerts] == [
        pd.Timestamp("2026-10-01 09:55:00+05:30")
    ]
    assert sum("FROZEN SIGNAL DETECTED" in message for message in messages) == 2


def test_no_signal_does_not_call_ai(monkeypatch):
    ai_calls = []
    monitor, messages, _, _ = _monitor(monkeypatch, ai_calls=ai_calls)

    alerts = monitor.scan_once(NOW)

    assert alerts == []
    assert ai_calls == []
    assert messages == ["[09:25] Scan complete - 0 frozen signals."]
