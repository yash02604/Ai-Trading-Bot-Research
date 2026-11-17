import pandas as pd
import pytest

import signal_monitor
from signal_monitor import SignalMonitor, is_frozen_signal


NOW = pd.Timestamp("2026-10-01 09:25:00+05:30")


def _candle(timestamp, close=100.0):
    return [timestamp, close, close + 1, close - 1, close, 1000, 0]


def _historical_candles(today=NOW):
    calendar = signal_monitor.OfflineSessionCalendar(signal_monitor.CALENDAR_PATH)
    labels, sessions = [], 0
    for offset in range(1, 15):
        previous = signal_monitor._session_labels(calendar, today.normalize() - pd.Timedelta(days=offset))
        labels = previous + labels
        sessions += bool(previous)
        if sessions >= 2 and len(labels) >= 36:
            return [_candle(stamp.isoformat()) for stamp in labels]
    raise AssertionError("fixture calendar insufficient")


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
        offline=True,
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

    assert len(first_alerts) == 1, (monitor.statuses, messages)
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


def _offline_monitor(*, now=NOW, history=None, intraday=None, calendar=None):
    """Real validation, calendar, indicators and detector; only external I/O is replaced."""
    history = _historical_candles() if history is None else history
    calendar = calendar or signal_monitor.OfflineSessionCalendar(signal_monitor.CALENDAR_PATH)
    if intraday is None:
        labels = signal_monitor._session_labels(calendar, now)
        intraday = [_candle(t.isoformat(), 101 + i / 10)
                    for i, t in enumerate(labels) if t + pd.Timedelta(minutes=5) <= now]
    requests, messages = [], []

    def fetch_history(*args, **kwargs):
        requests.append((args, kwargs))
        return {"data": {"candles": history}}

    monitor = SignalMonitor(stocks={"TEST": "NSE_EQ|TEST"}, calendar=calendar,
                            intraday_fetcher=lambda *a, **k: {"data": {"candles": intraday}},
                            historical_fetcher=fetch_history, output=messages.append, offline=True)
    return monitor, history, intraday, requests, messages


def _forbid_indicators(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("rejected data reached production indicators")
    monkeypatch.setattr(signal_monitor, "calculate_indicators", forbidden)
    monkeypatch.setattr(signal_monitor, "detect_reversal_candidates", forbidden)


@pytest.mark.parametrize("failure", ["transport", "validation", "malformed", "empty", "conflict", "gap", "session", "short"])
def test_history_failure_then_recovery_with_real_indicators(failure):
    monitor, history, intraday, requests, messages = _offline_monitor()
    good = monitor.historical_fetcher
    attempts = []
    recovering = [False]

    def recover(*args, **kwargs):
        attempts.append(1)
        if recovering[0]:
            return good(*args, **kwargs)
        if failure == "transport":
            raise TimeoutError("temporary")
        if failure == "malformed":
            return {"data": None}
        bad = [c.copy() for c in history]
        if failure == "validation":
            bad[0][2] = 1  # high below open/close
        elif failure == "empty":
            bad = []
        elif failure == "conflict":
            bad.append(_candle(history[0][0], 100.5))
        elif failure == "gap":
            bad.pop(10)
            bad.append(_candle("2026-09-30T12:10:00+05:30"))
        elif failure == "session":
            bad[0][0] = "2026-09-30T08:55:00+05:30"
        elif failure == "short":
            # A repeated partial provider response cannot accumulate into valid history.
            bad = bad[:1]
            monitor.historical_fetcher = lambda *a, **k: {"data": {"candles": bad}}
        return {"data": {"candles": bad}}

    monitor.historical_fetcher = recover
    with pytest.MonkeyPatch.context() as rejected:
        _forbid_indicators(rejected)
        assert monitor.scan_once(NOW) == []
    assert "TEST" not in monitor.historical_cache_date
    assert "TEST" not in monitor.historical_cache
    assert "TEST" not in monitor.last_processed
    assert monitor.statuses["TEST"]["status"] in {"SCAN_FAILED", "CONFLICTING_CANDLES", "INCOMPLETE_HISTORY"}
    if failure == "short":
        monitor.historical_fetcher = recover
    failed_attempts = len(attempts)
    recovering[0] = True
    intraday.append(_candle("2026-10-01T09:25:00+05:30", 101.2))
    assert monitor.scan_once(NOW + pd.Timedelta(minutes=5)) == []
    assert len(attempts) == failed_attempts + 1
    assert len(requests) == 1
    assert monitor.historical_cache_date["TEST"] == NOW.date()
    assert len(monitor.historical_cache["TEST"]) == len(monitor._required_history(NOW.date()))
    assert monitor.last_processed["TEST"] == pd.Timestamp("2026-10-01T09:25:00+05:30")
    assert monitor.statuses["TEST"]["status"] == "EVALUATED"
    assert "0 frozen signals" in messages[-1]


@pytest.mark.parametrize("clock", ["09:40", "12:00", "15:14", "15:15"])
def test_stale_market_data_suppresses_indicators(monkeypatch, clock):
    monitor, _, _, requests, messages = _offline_monitor()
    _forbid_indicators(monkeypatch)
    assert monitor.scan_once(pd.Timestamp(f"2026-10-01T{clock}:00+05:30")) == []
    status = monitor.statuses["TEST"]
    assert status["status"] == "STALE_MARKET_DATA"
    assert status["latest_received"] == NOW - pd.Timedelta(minutes=5)
    assert status["delay_seconds"] > 0
    assert requests == []
    assert "TEST" in messages[-1] and "expected=" in messages[-1]


@pytest.mark.parametrize("clock,expected", [("09:15", None), ("09:19", None),
                                           ("09:20", "09:15"), ("09:25", "09:20"),
                                           ("15:14", "15:05"), ("15:15", "15:10")])
def test_real_indicator_freshness_boundaries(clock, expected):
    now = pd.Timestamp(f"2026-10-01T{clock}:00+05:30")
    monitor, _, intraday, requests, messages = _offline_monitor(now=now)
    # The provider also sends the current, incomplete bar: it must never be evaluated.
    if clock not in {"15:15"}:
        intraday.append(_candle(now.floor("5min").isoformat(), 500))
    assert monitor.scan_once(now) == []
    if expected is None:
        assert requests == []
        assert not monitor.last_processed
    else:
        assert monitor.last_processed["TEST"] == pd.Timestamp(f"2026-10-01T{expected}:00+05:30")
        assert monitor.statuses["TEST"]["status"] == "EVALUATED"
        assert "0 frozen signals" in messages[-1]


def test_in_progress_candle_cannot_replace_missing_completed_candle(monkeypatch):
    monitor, _, intraday, _, _ = _offline_monitor()
    intraday.pop()
    intraday.append(_candle("2026-10-01T09:25:00+05:30", 500))
    _forbid_indicators(monkeypatch)
    monitor.scan_once(NOW)
    assert monitor.statuses["TEST"]["status"] == "STALE_MARKET_DATA"


@pytest.mark.parametrize("field,value", [(1, 100.5), (2, 103), (3, 98), (4, 100.5), (5, 2000)])
@pytest.mark.parametrize("reverse", [False, True])
def test_conflicting_intraday_duplicates_rejected_before_indicators(monkeypatch, field, value, reverse):
    monitor, _, intraday, requests, messages = _offline_monitor()
    conflicting = intraday[0].copy()
    conflicting[field] = value
    intraday.append(conflicting)
    if reverse:
        intraday.reverse()
    _forbid_indicators(monkeypatch)
    assert monitor.scan_once(NOW) == []
    assert monitor.statuses["TEST"]["status"] == "CONFLICTING_CANDLES"
    assert "2026-10-01 09:15:00+05:30" in messages[-1]
    assert requests == []
    assert not monitor.last_processed


@pytest.mark.parametrize("reverse", [False, True])
def test_real_indicators_identical_duplicates_order_and_repeat_are_deterministic(reverse):
    monitor, history, intraday, requests, messages = _offline_monitor()
    history.extend([c.copy() for c in history])
    intraday.extend([c.copy() for c in intraday])
    if reverse:
        history.reverse()
        intraday.reverse()
    assert monitor.scan_once(NOW) == []
    assert monitor.last_processed["TEST"] == NOW - pd.Timedelta(minutes=5)
    assert len(monitor.historical_cache["TEST"]) == len(monitor._required_history(NOW.date()))
    assert monitor.statuses["TEST"]["status"] == "EVALUATED"
    assert messages == ["[09:25] Scan complete - 0 frozen signals."]
    assert monitor.scan_once(NOW) == []
    assert len(messages) == len(requests) == 1


@pytest.mark.parametrize("invalid", [[], ["bad"], ["2026-10-01T09:20:00", 100, 101, 99, 100, 1000, 0]])
def test_malformed_intraday_is_rejected(monkeypatch, invalid):
    monitor, _, intraday, _, _ = _offline_monitor()
    intraday.append(invalid)
    _forbid_indicators(monkeypatch)
    monitor.scan_once(NOW)
    assert monitor.statuses["TEST"]["status"] == "SCAN_FAILED"


@pytest.mark.parametrize("day", ["2026-10-02", "2026-10-03", "2026-11-08", "2027-01-04"])
def test_calendar_holiday_weekend_blocked_muhurat_and_unknown_fail_closed(day):
    monitor, _, _, requests, _ = _offline_monitor()
    def forbidden(*args, **kwargs):
        pytest.fail("calendar rejected date reached acquisition")
    monitor.intraday_fetcher = forbidden
    assert monitor.scan_once(pd.Timestamp(f"{day}T14:30:00+05:30")) == []
    assert requests == []
    assert not monitor.last_processed


def _closing_monitor(monkeypatch, day="2026-12-08", close="15:30", real=False):
    now = pd.Timestamp(f"{day}T{close}:00+05:30")
    history = _historical_candles(now)
    monitor, history, intraday, requests, messages = _offline_monitor(now=now, history=history)
    final = intraday.pop()
    if not real:
        monkeypatch.setattr(signal_monitor, "calculate_indicators", lambda df: df)
        def detect(frame, **kwargs):
            result = frame.copy()
            result["decline_pct"] = -0.5
            result["rsi_14"] = 35.0
            result["reversal_condition"] = False
            mask = result.timestamp == pd.Timestamp(final[0])
            result.loc[mask, ["decline_pct", "rsi_14", "reversal_condition"]] = [-1.5, 22, True]
            return result
        monkeypatch.setattr(signal_monitor, "detect_reversal_candidates", detect)
    return monitor, intraday, final, now, requests, messages


@pytest.mark.parametrize("day,close", [("2026-12-08", "15:30"), ("2026-10-01", "15:15")])
def test_final_candle_arrives_after_calendar_close_and_is_evaluated_once(monkeypatch, day, close):
    monitor, intraday, final, now, requests, messages = _closing_monitor(monkeypatch, day, close)
    assert monitor.scan_once(now) == []
    assert monitor.statuses["TEST"]["status"] == "STALE_MARKET_DATA"
    assert not requests
    intraday.append(final)
    intraday.append(_candle(now.isoformat(), 999))  # post-session candle is never eligible
    alerts = monitor.scan_once(now + pd.Timedelta(seconds=30))
    assert len(alerts) == 1
    assert alerts[0]["timestamp"] == now - pd.Timedelta(minutes=5)
    assert monitor.scan_once(now + pd.Timedelta(seconds=60)) == []
    assert len(requests) == 1
    assert sum("FROZEN SIGNAL DETECTED" in message for message in messages) == 1


def test_real_indicator_final_candle_after_close(monkeypatch):
    monitor, intraday, final, now, requests, messages = _closing_monitor(monkeypatch, real=True)
    intraday.append(final)
    assert monitor.scan_once(now + pd.Timedelta(seconds=30)) == []
    assert monitor.last_processed["TEST"] == now - pd.Timedelta(minutes=5)
    assert monitor.statuses["TEST"]["status"] == "EVALUATED"
    assert "0 frozen signals" in messages[-1]


@pytest.mark.parametrize("arrives", [False, True])
def test_run_post_close_drain_terminates_on_completion_or_deadline(monkeypatch, arrives):
    monitor, intraday, final, now, requests, messages = _closing_monitor(monkeypatch)
    clock = [now]
    polls = []
    original = monitor.intraday_fetcher
    def fetch(*args, **kwargs):
        polls.append(clock[0])
        return original(*args, **kwargs)
    monitor.intraday_fetcher = fetch
    def advance(seconds):
        clock[0] += pd.Timedelta(seconds=seconds)
        if arrives and len(polls) == 1:
            intraday.append(final)
    monitor.run(now_fn=lambda: clock[0], sleep_fn=advance)
    assert len(polls) == (2 if arrives else 3)
    assert clock[0] <= now + pd.Timedelta(minutes=3)
    assert len(requests) == (1 if arrives else 0)
    assert ("TEST" in monitor.last_processed) is arrives


def test_default_monitor_has_no_network_transport():
    monitor = SignalMonitor(output=lambda text: None)
    monitor.scan_once(NOW)
    assert all(status["error_type"] == "RuntimeError" for status in monitor.statuses.values())
    assert not monitor.last_processed


def test_missing_intraday_bar_rejects_even_when_latest_is_fresh(monkeypatch):
    monitor, _, intraday, _, _ = _offline_monitor()
    intraday.pop(0)
    _forbid_indicators(monkeypatch)
    monitor.scan_once(NOW)
    assert monitor.statuses["TEST"]["status"] == "SCAN_FAILED"


def test_post_close_deadline_prevents_further_acquisition(monkeypatch):
    monitor, _, _, now, _, _ = _closing_monitor(monkeypatch)
    def forbidden(*args, **kwargs):
        pytest.fail("expired drain reached acquisition")
    monitor.intraday_fetcher = forbidden
    assert monitor.scan_once(now + pd.Timedelta(seconds=121)) == []
    monitor.run(now_fn=lambda: now + pd.Timedelta(seconds=121))


def test_freshness_is_timezone_normalized():
    monitor, _, _, _, _ = _offline_monitor()
    assert monitor.scan_once(NOW.tz_convert("UTC")) == []
    assert monitor.last_processed["TEST"] == NOW - pd.Timedelta(minutes=5)


def test_recovered_backlog_does_not_emit_stale_signal(monkeypatch):
    monitor, messages, _, intraday = _monitor(monkeypatch, signal_timestamp="2026-10-01T09:25:00+05:30")
    assert monitor.scan_once(NOW) == []
    intraday.extend(_candle(f"2026-10-01T09:{minute}:00+05:30") for minute in (25, 30, 35))
    assert monitor.scan_once(NOW + pd.Timedelta(minutes=15)) == []
    assert monitor.last_processed["TEST"] == NOW + pd.Timedelta(minutes=10)
    assert monitor.last_signal_at["TEST"] == NOW
    assert not any("FROZEN SIGNAL DETECTED" in message for message in messages)
