"""Synthetic monitor audit reproductions; real calendar, merge, indicators and detector."""
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import signal_monitor as sm
from app import market_analysis, reversal_detector, upstox_client, trade_logger
from app.monitor_transport import BoundedMonitorTransport, MonitorAcquisitionError
from app.research.deepseek_prospective import AppendOnlyJsonl

NOW = pd.Timestamp("2026-10-01T09:25:00+05:30")


def candle(stamp, price=100.0):
    return [str(stamp), price, price + 1, price - 1, price, 1000, 0]


def history(calendar, today):
    result = []
    sessions = 0
    for offset in range(1, 15):
        day = today.normalize() - pd.Timedelta(days=offset)
        previous = sm._session_labels(calendar, day)
        sessions += bool(previous)
        result = previous + result
        if sessions >= 2 and len(result) >= 36:
            return [candle(t) for t in result]
    raise AssertionError("fixture calendar insufficient")


def fixture(tmp_path, *, now=NOW, gain=.5, path=None, history_rows=None, current=None, clock=None):
    calendar = sm.OfflineSessionCalendar(sm.CALENDAR_PATH)
    saved = history(calendar, now) if history_rows is None else history_rows
    saved[-1][1:5] = [99., 100., 98., 99.]
    if current is None:
        labels = [t for t in sm._session_labels(calendar, now) if t + pd.Timedelta(minutes=5) <= now]
        current = [candle(t) for t in labels]
        current[-2:] = [candle(labels[-2], 98.), candle(labels[-1], 98. + gain)]
    clock = [now] if clock is None else clock
    calls, messages = [], []
    def historical(key, date, interval):
        calls.append(date)
        return {"data": {"candles": saved}}
    monitor = sm.SignalMonitor(stocks={"TEST": "NSE_EQ|TEST"}, calendar=calendar,
        intraday_fetcher=lambda *a, **k: {"data": {"candles": current}},
        historical_fetcher=historical, output=messages.append, offline=True,
        now_fn=lambda: clock[0], state_path=path or tmp_path / "state/checkpoint.json")
    return SimpleNamespace(m=monitor, history=saved, current=current, clock=clock, calls=calls, messages=messages)


def forbid_indicators(monkeypatch):
    monkeypatch.setattr(sm, "calculate_indicators", lambda *a: pytest.fail("rejected poll reached indicators"))


@pytest.mark.parametrize("kind", ["last", "tail", "whole_session", "internal", "truncated", "wrong_coverage", "empty"])
def test_required_history_rejected_before_cache_indicators_and_progress(tmp_path, monkeypatch, kind):
    s = fixture(tmp_path)
    original = [r.copy() for r in s.history]
    if kind in {"last", "tail"}:
        del s.history[-(1 if kind == "last" else 4):]
    elif kind == "internal":
        s.history.pop(15)
    elif kind == "truncated":
        s.history[:] = s.history[:35]
    elif kind in {"whole_session", "wrong_coverage"}:
        s.history[:] = history(s.m.calendar, NOW - pd.Timedelta(days=1))
    else:
        s.history.clear()
    forbid_indicators(monkeypatch)
    assert s.m.scan_once() == []
    assert s.m.statuses["TEST"]["status"] == "INCOMPLETE_HISTORY"
    assert s.m.statuses["TEST"]["missing_count"] >= 1
    assert not s.m.historical_cache and not s.m.historical_cache_date and not s.m.last_processed
    state = sm._read_state(s.m.state_path)
    assert state["symbols"] == {}
    # Restore complete history: next poll may acquire it; no failed cache entry exists.
    s.history[:] = original


@pytest.mark.parametrize("now", [pd.Timestamp("2026-10-05T09:25:00+05:30"), NOW])
def test_calendar_tail_coverage_and_successful_history_retry(tmp_path, now):
    s = fixture(tmp_path, now=now)
    final = s.history.pop()
    assert s.m.scan_once() == []
    assert not s.m.historical_cache
    s.history.append(final)
    result = s.m.scan_once()
    assert s.m.statuses["TEST"]["status"] == "EVALUATED"
    # The incomplete poll checks both required session dates; recovery needs one broad batch.
    assert len(result) == 1 and len(s.calls) == 3
    assert s.m.last_processed["TEST"] == now - pd.Timedelta(minutes=5)
    if now.date().isoformat() == "2026-10-05":
        assert pd.Timestamp(final[0]) == pd.Timestamp("2026-10-01T15:10:00+05:30")


def oracle(s, gain, expected_signal):
    raw = sm._deduplicate_candles(s.history + s.current)
    enriched = market_analysis.calculate_indicators(market_analysis.candles_to_dataframe(raw))
    detected = reversal_detector.detect_reversal_candidates(enriched, decline_candles=3,
                                                           min_decline_pct=.5, rsi_threshold=30)
    row = detected.iloc[-1]
    q = 13 / 14
    expected_rsi = 100 * gain / (gain + q*q + q)
    assert float(row.rsi_14) == pytest.approx(expected_rsi, abs=1e-9)
    assert float(row.decline_pct) == pytest.approx(gain - 2, abs=1e-9)
    assert bool(row.reversal_condition) is True
    assert sm.is_frozen_signal(row) is expected_signal
    alerts = s.m.scan_once()
    assert s.m.statuses["TEST"]["status"] == "EVALUATED"
    assert len(alerts) == int(expected_signal)
    if alerts:
        assert alerts[0]["rsi_14"] == pytest.approx(expected_rsi, abs=1e-9)
        assert alerts[0]["decline_pct"] == pytest.approx(gain - 2, abs=1e-9)
    return row


@pytest.mark.parametrize("gain,positive", [(.5, True), (.5969, True), (.6, False)])
@pytest.mark.parametrize("reverse", [False, True])
def test_real_indicator_numeric_signal_oracle(tmp_path, gain, positive, reverse):
    s = fixture(tmp_path, gain=gain)
    if reverse:
        s.history.reverse()
        s.current.reverse()
    oracle(s, gain, positive)


def test_truncated_history_would_create_a_false_real_signal_but_monitor_rejects(tmp_path, monkeypatch):
    s = fixture(tmp_path, gain=.6)
    full = market_analysis.calculate_indicators(market_analysis.candles_to_dataframe(s.history + s.current))
    truncated = market_analysis.calculate_indicators(market_analysis.candles_to_dataframe(s.history[:-1] + s.current))
    assert not sm.is_frozen_signal(reversal_detector.detect_reversal_candidates(full).iloc[-1])
    assert sm.is_frozen_signal(reversal_detector.detect_reversal_candidates(truncated).iloc[-1])
    s.history.pop()
    forbid_indicators(monkeypatch)
    assert s.m.scan_once() == [] and s.m.statuses["TEST"]["status"] == "INCOMPLETE_HISTORY"


@pytest.mark.parametrize("mutation", ["rsi100", "rsi0", "unchanged", "detector_bypassed"])
def test_real_oracle_rejects_material_indicator_mutations(tmp_path, monkeypatch, mutation):
    # Deliberate negative controls only; the six normal integration cases above use real functions.
    s = fixture(tmp_path)
    if mutation == "detector_bypassed":
        monkeypatch.setattr(sm, "detect_reversal_candidates", lambda frame, **kw: frame)
    else:
        real = sm.calculate_indicators
        def broken(frame):
            if mutation == "unchanged":
                return frame
            value = real(frame)
            value["rsi_14"] = 100 if mutation == "rsi100" else 0
            return value
        monkeypatch.setattr(sm, "calculate_indicators", broken)
    with pytest.raises(AssertionError):
        oracle(s, .5, True)


@pytest.mark.parametrize("fresh", [False, True])
def test_authoritative_clock_refresh_after_candle_boundary(tmp_path, fresh):
    start = NOW + pd.Timedelta(minutes=4, seconds=59)
    s = fixture(tmp_path, now=NOW)
    s.clock[0] = start
    def fetch(*a, **k):
        s.clock[0] = NOW + pd.Timedelta(minutes=5, seconds=1)
        if fresh:
            s.current.append(candle(NOW, 98.6))
        return {"data": {"candles": s.current}}
    s.m.intraday_fetcher = fetch
    s.m.scan_once()
    assert s.m.statuses["TEST"]["status"] == ("EVALUATED" if fresh else "STALE_MARKET_DATA")
    assert bool(s.m.last_processed) is fresh
    if not fresh:
        s.current.append(candle(NOW, 98.6))
        s.m.intraday_fetcher = lambda *a, **k: {"data": {"candles": s.current}}
        s.m.scan_once()
        assert s.m.last_processed["TEST"] == NOW


def test_history_fetch_also_refreshes_decision_clock(tmp_path, monkeypatch):
    s = fixture(tmp_path)
    s.clock[0] = NOW + pd.Timedelta(minutes=4, seconds=59)
    original = s.m.historical_fetcher
    def slow_history(*a, **k):
        s.clock[0] += pd.Timedelta(seconds=2)
        return original(*a, **k)
    s.m.historical_fetcher = slow_history
    forbid_indicators(monkeypatch)
    s.m.scan_once()
    assert s.m.statuses["TEST"]["status"] == "STALE_MARKET_DATA" and not s.m.last_processed


@pytest.mark.parametrize("gain", [.5, .6])
def test_restart_preserves_signal_or_no_signal_and_frozen_history(tmp_path, gain):
    s = fixture(tmp_path, gain=gain)
    first = s.m.scan_once()
    before = s.m.state_path.read_bytes()
    restarted = fixture(tmp_path, gain=gain, path=s.m.state_path)
    restarted.m.intraday_fetcher = lambda *a, **k: pytest.fail("already processed candle acquired again")
    assert restarted.m.scan_once() == []
    assert restarted.m.state_path.read_bytes() == before
    assert restarted.m.statuses["TEST"]["status"] == "ALREADY_PROCESSED"
    restarted.clock[0] += pd.Timedelta(minutes=5)
    restarted.current.append(candle(NOW, 98.6))
    restarted.m.intraday_fetcher = lambda *a, **k: {"data": {"candles": restarted.current}}
    restarted.m.historical_fetcher = lambda *a, **k: pytest.fail("frozen same-day history reacquired")
    restarted.m.scan_once()
    assert restarted.m.last_processed["TEST"] == NOW
    assert not any("FROZEN SIGNAL DETECTED" in m for m in restarted.messages)


@pytest.mark.parametrize("kind", ["malformed", "truncated", "hash", "identity", "missing_state", "missing_marker"])
def test_bad_checkpoint_never_resets_and_never_acquires(tmp_path, kind):
    s = fixture(tmp_path)
    s.m.scan_once()
    if kind in {"malformed", "truncated"}:
        s.m.state_path.write_bytes(b'{"bad":' if kind == "truncated" else b"not JSON")
    elif kind == "missing_state":
        s.m.state_path.unlink()
    elif kind == "missing_marker":
        s.m.state_path.with_suffix(".identity.json").unlink()
    else:
        data = json.loads(s.m.state_path.read_bytes())
        if kind == "hash":
            data["sha256"] = "0" * 64
        else:
            data["record"]["identity"]["stocks"] = {"OTHER": "wrong"}
            data["sha256"] = sm._digest(data["record"])
        s.m.state_path.write_text(json.dumps(data))
    other = fixture(tmp_path, path=s.m.state_path)
    other.m.intraday_fetcher = lambda *a, **k: pytest.fail("invalid checkpoint acquired data")
    assert other.m.scan_once() == []
    assert other.m.statuses["TEST"]["status"] == "CHECKPOINT_STATE_INVALID"


def test_output_crash_keeps_explicit_durable_pending_event_without_duplicate(tmp_path):
    s = fixture(tmp_path)
    s.m.output = lambda *a: (_ for _ in ()).throw(RuntimeError("output interrupted"))
    assert len(s.m.scan_once()) == 1
    assert sm._read_state(s.m.state_path)["events"][0]["delivery"] == "PENDING"
    other = fixture(tmp_path, path=s.m.state_path)
    assert other.m.scan_once() == []
    assert len(other.m.statuses["TEST"]["pending_event_ids"]) == 1


def test_downtime_catches_up_state_without_retroactive_alert(tmp_path):
    s = fixture(tmp_path)
    s.m.scan_once()
    other = fixture(tmp_path, path=s.m.state_path)
    other.clock[0] += pd.Timedelta(minutes=15)
    other.current.extend(candle(NOW + pd.Timedelta(minutes=n), 98.6) for n in (0, 5, 10))
    other.m.scan_once()
    assert other.m.last_processed["TEST"] == NOW + pd.Timedelta(minutes=10)
    assert other.m.statuses["TEST"]["evaluated_rows"] == 3
    assert other.m.statuses["TEST"]["initial_latest_only"] is False


@pytest.mark.parametrize("day,close", [("2026-10-01", "15:15"), ("2026-12-08", "15:30")])
def test_restart_before_and_after_final_candle_and_late_deadline(tmp_path, day, close):
    end = pd.Timestamp(f"{day}T{close}:00+05:30")
    s = fixture(tmp_path, now=end)
    final = s.current.pop()
    assert s.m.scan_once() == [] and not s.m.last_processed
    other = fixture(tmp_path, now=end + pd.Timedelta(seconds=30), path=s.m.state_path)
    other.m.scan_once()
    assert other.m.last_processed["TEST"] == end - pd.Timedelta(minutes=5)
    again = fixture(tmp_path, now=end + pd.Timedelta(seconds=60), path=s.m.state_path)
    assert again.m.scan_once() == [] and again.m.statuses["TEST"]["status"] == "ALREADY_PROCESSED"


@pytest.mark.parametrize("delay", [10, 29, None])
def test_short_drain_polling_retries_or_expires_without_post_session_eligibility(tmp_path, delay):
    end = pd.Timestamp("2026-10-01T15:15:00+05:30")
    s = fixture(tmp_path, now=end)
    final = s.current.pop()
    s.m.poll_interval_seconds = 1
    s.m.post_close_drain = pd.Timedelta(seconds=30)
    def advance(seconds):
        s.clock[0] += pd.Timedelta(seconds=seconds)
        if delay is not None and s.clock[0] >= end + pd.Timedelta(seconds=delay) and final not in s.current:
            s.current.extend([final, candle(end, 999)])
    s.m.run(now_fn=lambda: s.clock[0], sleep_fn=advance)
    assert bool(s.m.last_processed) is (delay is not None)
    assert s.clock[0] <= end + pd.Timedelta(seconds=30)


def test_unsafe_drain_poll_configuration_is_rejected():
    with pytest.raises(ValueError):
        sm.SignalMonitor(poll_interval_seconds=60, post_close_drain_seconds=30)


@pytest.mark.parametrize("expiry", ["poll", "drain"])
def test_late_fetch_cannot_reach_indicators_or_checkpoint(tmp_path, monkeypatch, expiry):
    now = NOW if expiry == "poll" else pd.Timestamp("2026-10-01T15:16:50+05:30")
    s = fixture(tmp_path, now=now)
    def delayed(*a, **k):
        s.clock[0] += pd.Timedelta(seconds=21)
        return {"data": {"candles": s.current}}
    s.m.intraday_fetcher = delayed
    forbid_indicators(monkeypatch)
    s.m.scan_once()
    assert s.m.statuses["TEST"]["status"] == "ACQUISITION_DEADLINE_EXCEEDED"
    assert not s.m.last_processed


@pytest.fixture
def transport(tmp_path, monkeypatch, request):
    calendar = sm.OfflineSessionCalendar(sm.CALENDAR_PATH)
    clock = [NOW]
    settings = SimpleNamespace(upstox_access_token="SYNTHETIC", request_timeout_seconds=10,
                               upstox_total_deadline_seconds=20, upstox_max_retry_delay_seconds=1)
    monkeypatch.setattr(upstox_client, "get_settings", lambda: settings)
    monkeypatch.setattr(upstox_client, "_utc_now", lambda: clock[0].to_pydatetime())
    adapter = BoundedMonitorTransport(tmp_path / "acquisition", maximum_requests=getattr(request, "param", 2), calendar=calendar,
                                     stocks={"TEST": "NSE_EQ|TEST"}, now_fn=lambda: clock[0], enabled=True)
    calls = []
    state = {"status": 200, "delay": 0}
    def get(*args):
        assert len(AppendOnlyJsonl(adapter.output / "physical_attempts.jsonl", "request_id").records()) == len(calls) + 1
        assert len(AppendOnlyJsonl(adapter.output / "request_budget.jsonl", "request_id").records()) == len(calls) + 1
        calls.append(args)
        clock[0] += pd.Timedelta(seconds=state["delay"])
        return {"status": state["status"], "data": {"data": {"candles": []}}}
    monkeypatch.setattr(upstox_client, "_bounded_get", get)
    return SimpleNamespace(adapter=adapter, calls=calls, clock=clock, state=state)


@pytest.mark.parametrize("status", [200, 429, 500, 503])
def test_reviewed_transport_persistent_logical_and_physical_caps_no_retries(transport, status):
    t = transport
    t.state["status"] = status
    for _ in range(2):
        try:
            t.adapter.fetch("intraday", "NSE_EQ|TEST", deadline=NOW + pd.Timedelta(seconds=20))
        except upstox_client.UpstoxRequestError:
            assert status != 200
    assert len(t.calls) == 2
    restarted = BoundedMonitorTransport(t.adapter.output, maximum_requests=2, calendar=t.adapter.calendar,
        stocks=t.adapter.stocks, now_fn=t.adapter.now_fn, enabled=True)
    with pytest.raises(MonitorAcquisitionError, match="REQUEST_BUDGET_EXHAUSTED"):
        restarted.fetch("intraday", "NSE_EQ|TEST", deadline=NOW + pd.Timedelta(seconds=20))
    assert len(t.calls) == 2


def test_transport_disabled_and_expired_session_do_not_reserve(transport):
    t = transport
    t.adapter.enabled = False
    with pytest.raises(MonitorAcquisitionError, match="ACQUISITION_DISABLED"):
        t.adapter.fetch("intraday", "NSE_EQ|TEST", deadline=NOW + pd.Timedelta(seconds=20))
    assert t.calls == [] and not t.adapter.output.exists()
    t.adapter.enabled = True
    t.clock[0] = pd.Timestamp("2026-10-01T15:17:01+05:30")
    with pytest.raises(MonitorAcquisitionError):
        t.adapter.fetch("intraday", "NSE_EQ|TEST", deadline=t.clock[0] + pd.Timedelta(seconds=20))
    assert t.calls == []


def test_transport_late_completion_spends_attempt_without_replacement(transport):
    t = transport
    t.state["delay"] = 21
    with pytest.raises(MonitorAcquisitionError, match="ACQUISITION_DEADLINE_EXCEEDED"):
        t.adapter.fetch("intraday", "NSE_EQ|TEST", deadline=NOW + pd.Timedelta(seconds=20))
    assert len(t.calls) == 1
    assert len(AppendOnlyJsonl(t.adapter.output / "request_budget.jsonl", "request_id").records()) == 1


@pytest.mark.parametrize("reverse", [False, True])
def test_expanded_legacy_collector_rejects_conflicts_and_collapses_identical(monkeypatch, reverse):
    import expanded_event_oos_validation as legacy
    first = candle("2026-10-01T09:15:00+05:30")
    second = candle("2026-10-01T09:15:00+05:30", 100.5)
    batches = [[first], [first.copy()]]
    monkeypatch.setattr(legacy, "ANCHOR_DATES", ["a", "b"])
    monkeypatch.setattr(legacy, "get_historical_candles", lambda *a: {"data": {"candles": batches.pop(0)}})
    assert len(legacy.collect_historical_candles("TEST")) == 1
    batches[:] = [[first], [second]]
    if reverse:
        batches.reverse()
    with pytest.raises(sm.CandleDataError):
        legacy.collect_historical_candles("TEST")


def test_decision_logging_isolated_from_real_persistent_log():
    real = Path(sm.__file__).parent / "logs/decisions.jsonl"
    before = real.read_bytes() if real.exists() else None
    trade_logger.log_decision({"symbol": "TEST"}, {"decision": "HOLD"})
    assert Path(trade_logger.DECISION_LOG_FILE).read_text().count('"TEST"') == 1
    assert (real.read_bytes() if real.exists() else None) == before


def test_next_eligible_day_does_not_inherit_suppression(tmp_path):
    first = fixture(tmp_path)
    assert len(first.m.scan_once()) == 1
    later = fixture(tmp_path, now=pd.Timestamp("2026-10-05T09:25:00+05:30"), path=first.m.state_path)
    assert len(later.m.scan_once()) == 1
    assert later.m.last_processed["TEST"].date().isoformat() == "2026-10-05"
    assert len(sm._read_state(later.m.state_path)["events"]) == 2


@pytest.mark.parametrize("failure", ["stale", "history"])
def test_restart_after_rejected_poll_can_recover_without_reset(tmp_path, failure):
    s = fixture(tmp_path)
    removed = s.current.pop() if failure == "stale" else s.history.pop()
    s.m.scan_once()
    assert not s.m.last_processed
    other = fixture(tmp_path, path=s.m.state_path)
    assert len(other.m.scan_once()) == 1


@pytest.mark.parametrize("day,close", [("2026-10-01", "15:15"), ("2026-12-08", "15:30")])
@pytest.mark.parametrize("fresh", [False, True])
def test_boundary_at_close_uses_post_fetch_calendar_time(tmp_path, day, close, fresh):
    end = pd.Timestamp(f"{day}T{close}:00+05:30")
    s = fixture(tmp_path, now=end)
    final = s.current.pop()
    s.clock[0] = end - pd.Timedelta(seconds=1)
    def fetch(*a, **k):
        s.clock[0] = end + pd.Timedelta(seconds=1)
        if fresh:
            s.current.append(final)
        return {"data": {"candles": s.current}}
    s.m.intraday_fetcher = fetch
    s.m.scan_once()
    assert s.m.statuses["TEST"]["status"] == ("EVALUATED" if fresh else "STALE_MARKET_DATA")
    assert bool(s.m.last_processed) is fresh


def test_atomic_commit_failure_preserves_previous_checkpoint_and_suppression(tmp_path, monkeypatch):
    s = fixture(tmp_path)
    s.m.scan_once()
    previous = s.m.last_processed["TEST"]
    before = s.m.state_path.read_bytes()
    s.clock[0] += pd.Timedelta(minutes=5)
    s.current.append(candle(NOW, 98.6))
    replace = sm.os.replace
    def fail(source, destination):
        if Path(destination) == s.m.state_path:
            raise OSError("synthetic commit interruption")
        return replace(source, destination)
    with monkeypatch.context() as patch:
        patch.setattr(sm.os, "replace", fail)
        assert s.m.scan_once() == []
    assert s.m.last_processed["TEST"] == previous
    assert s.m.state_path.read_bytes() == before
    s.m.scan_once()
    assert s.m.last_processed["TEST"] == NOW


@pytest.mark.parametrize("transport", [4], indirect=True)
def test_monitor_integrates_real_request_window_budget_and_restart(tmp_path, monkeypatch, transport):
    t = transport
    s = fixture(tmp_path)
    def get(url, *args):
        physical = AppendOnlyJsonl(t.adapter.output / "physical_attempts.jsonl", "request_id").records()
        assert len(physical) == len(t.calls) + 1
        t.calls.append(url)
        rows = s.current if "intraday" in url else s.history
        return {"status": 200, "data": {"data": {"candles": rows}}}
    monkeypatch.setattr(upstox_client, "_bounded_get", get)
    m = sm.SignalMonitor(stocks=t.adapter.stocks, calendar=t.adapter.calendar, transport=t.adapter,
                         state_path=tmp_path / "integrated/checkpoint.json", output=lambda _: None)
    assert len(m.scan_once()) == 1
    assert len(t.calls) == 2
    other = sm.SignalMonitor(stocks=t.adapter.stocks, calendar=t.adapter.calendar, transport=t.adapter,
                             state_path=m.state_path, output=lambda _: None)
    assert other.scan_once() == [] and len(t.calls) == 2
    assert len(AppendOnlyJsonl(t.adapter.output / "request_budget.jsonl", "request_id").records()) == 2


def test_monitor_integrated_deadline_rejection_has_no_progress(tmp_path, transport, monkeypatch):
    t = transport
    t.state["delay"] = 21
    m = sm.SignalMonitor(stocks=t.adapter.stocks, calendar=t.adapter.calendar, transport=t.adapter,
                         state_path=tmp_path / "integrated/checkpoint.json", output=lambda _: None)
    forbid_indicators(monkeypatch)
    assert m.scan_once() == []
    assert m.statuses["TEST"]["status"] == "ACQUISITION_DEADLINE_EXCEEDED"
    assert not m.last_processed and len(t.calls) == 1


def test_transport_revalidates_calendar_before_reservation(transport):
    t = transport
    t.adapter.calendar.sessions["2026-10-01"]["execution_supported"] = False
    with pytest.raises(MonitorAcquisitionError, match="SESSION_INVALID"):
        t.adapter.fetch("intraday", "NSE_EQ|TEST", deadline=NOW + pd.Timedelta(seconds=20))
    assert t.calls == []


def test_monitor_transport_cannot_adopt_existing_experiment_evidence(transport):
    t = transport
    t.adapter.output.mkdir()
    evidence = t.adapter.output / "request_budget.jsonl"
    evidence.write_bytes(b'{"request_id":"prior-experiment"}\n')
    before = {p.name: p.read_bytes() for p in t.adapter.output.iterdir()}
    with pytest.raises(MonitorAcquisitionError, match="ACQUISITION_IDENTITY_INVALID"):
        t.adapter.fetch("intraday", "NSE_EQ|TEST", deadline=NOW + pd.Timedelta(seconds=20))
    assert {p.name: p.read_bytes() for p in t.adapter.output.iterdir()} == before
    assert t.calls == []
