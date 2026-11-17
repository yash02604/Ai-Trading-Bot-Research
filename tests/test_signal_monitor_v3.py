"""V3 defect regressions. Synthetic OHLCV; production calendar/indicators/detector."""
import copy
import json

import pandas as pd
import pytest

import signal_monitor as sm
from test_signal_monitor_v2 import NOW, candle, fixture, forbid_indicators, transport
from app import upstox_client
from app.research.deepseek_prospective import AppendOnlyJsonl


def older(calendar, before, count):
    rows = []
    day = pd.Timestamp(before).normalize()
    while len(rows) < count:
        day -= pd.Timedelta(days=1)
        rows = [candle(t, 110 + (i % 9) / 10) for i, t in
                enumerate(sm._session_labels(calendar, day))] + rows
    return rows[-count:]


def indicator_row(s):
    selected = s.m._select_history(s.history, s.clock[0].date())
    df = sm.calculate_indicators(sm.candles_to_dataframe(selected + sm._canonical_candles(s.current)))
    return sm.detect_reversal_candidates(df).iloc[-1], selected


@pytest.mark.parametrize("extra", [0, 100, 500])
@pytest.mark.parametrize("reverse", [False, True])
def test_canonical_prefix_indicators_context_and_signal_are_identical(tmp_path, extra, reverse):
    s = fixture(tmp_path / "base")
    base, selected = indicator_row(s)
    s.history[:0] = older(s.m.calendar, selected[0][0], extra)
    if reverse:
        s.history.reverse()
        s.current.reverse()
    row, canonical = indicator_row(s)
    assert canonical == selected
    for key in ("rsi_14", "macd", "macd_signal", "decline_pct"):
        assert row[key] == base[key]
    assert sm.is_frozen_signal(row) == sm.is_frozen_signal(base) is True
    assert len(s.m.scan_once()) == 1
    entry = sm._read_state(s.m.state_path)["symbols"]["TEST"]
    assert entry["history"] == selected
    assert entry["context_sha256"] == s.m._context_hash("TEST", NOW.date(), selected,
                                                     sm._canonical_candles(s.current))


def test_restart_larger_history_is_frozen_and_reacquisition_is_identical(tmp_path):
    s = fixture(tmp_path)
    assert len(s.m.scan_once()) == 1
    before = sm._read_state(s.m.state_path)["symbols"]["TEST"]
    extra = older(s.m.calendar, s.history[0][0], 500) + copy.deepcopy(s.history)
    restarted = fixture(tmp_path, history_rows=extra)
    # Independently reacquired raw response also selects the exact frozen context.
    assert restarted.m._select_history(extra, NOW.date()) == before["history"]
    assert restarted.m.scan_once() == []
    assert restarted.calls == []  # Process-once avoids unnecessary reacquisition.
    restarted.current.append(candle(NOW, 98.7))
    restarted.clock[0] += pd.Timedelta(minutes=5)
    restarted.m.scan_once()
    assert restarted.m.statuses["TEST"]["status"] == "EVALUATED"
    assert restarted.calls == []
    assert sm._read_state(s.m.state_path)["symbols"]["TEST"]["history"] == before["history"]


def test_real_recursive_indicator_window_comparison_and_canonical_invariance(tmp_path):
    s = fixture(tmp_path)
    # A prior loss well before the 36-row tail reproduces recursive seed dependence.
    for index, row in enumerate(s.history):
        price = 140. if index < len(s.history)-60 else 100.
        row[1:5] = [price, price+1, price-1, price]
    s.history[-1][1:5] = [99., 100., 98., 99.]
    canonical = copy.deepcopy(s.history)
    extra = older(s.m.calendar, canonical[0][0], 500)
    one_session = [r for r in canonical if pd.Timestamp(r[0]).date() == pd.Timestamp(canonical[-1][0]).date()]
    records = []
    def measure(name, rows):
        frame = sm.calculate_indicators(sm.candles_to_dataframe(sm._canonical_candles(rows)+sm._canonical_candles(s.current)))
        last = sm.detect_reversal_candidates(frame).iloc[-1]
        return {"context": name, "rows": len(rows), "rsi_14": float(last.rsi_14),
                "macd": float(last.macd), "macd_signal": float(last.macd_signal),
                "signal": sm.is_frozen_signal(last)}
    for name, rows in [("36", canonical[-36:]), ("50", canonical[-50:]),
                       ("one_session", one_session), ("two_sessions_canonical", canonical),
                       ("larger_reference", extra+canonical)]:
        records.append(measure(name, rows))
    assert records[0]["rsi_14"] != records[3]["rsi_14"]
    fixed = records[3]
    for count in (100, 500):
        selected = s.m._select_history(extra[-count:]+canonical, NOW.date())
        row = measure(f"canonical_plus_{count}", selected)
        assert {k: v for k, v in row.items() if k != "context"} == {k: v for k, v in fixed.items() if k != "context"}
        records.append(row)
    s.m.scan_once()
    restart = fixture(tmp_path, history_rows=extra+canonical)
    restart.m.scan_once()
    frozen = sm._read_state(s.m.state_path)["symbols"]["TEST"]["history"]
    records.append(measure("restart_frozen", frozen))
    assert frozen == sm._canonical_candles(canonical)
    (tmp_path / "indicator-stability.json").write_text(json.dumps(records, indent=2))


@pytest.mark.parametrize("kind", ["36", "tail", "whole"])
def test_canonical_missing_history_rejects(tmp_path, monkeypatch, kind):
    s = fixture(tmp_path)
    if kind == "36":
        s.history[:] = s.history[-36:]
    elif kind == "tail":
        s.history.pop()
    else:
        day = pd.Timestamp(s.history[-1][0]).date()
        s.history[:] = [r for r in s.history if pd.Timestamp(r[0]).date() != day]
    forbid_indicators(monkeypatch)
    assert s.m.scan_once() == []
    assert s.m.statuses["TEST"]["status"] == "INCOMPLETE_HISTORY"


@pytest.mark.parametrize("field,value", [(1, 98.4), (2, 101.), (3, 96.), (4, 98.4), (5, 2000.)])
@pytest.mark.parametrize("prior_signal", [True, False])
def test_processed_ohlcv_revision_after_restart_fails_before_indicators(tmp_path, monkeypatch, field, value, prior_signal):
    s = fixture(tmp_path, gain=.5 if prior_signal else .6)
    assert len(s.m.scan_once()) == int(prior_signal)
    original = s.m.state_path.read_bytes()
    restarted = fixture(tmp_path, gain=.5 if prior_signal else .6)
    restarted.current[-1][field] = value
    restarted.current.append(candle(NOW, 98.7))
    restarted.clock[0] += pd.Timedelta(minutes=5)
    forbid_indicators(monkeypatch)
    assert restarted.m.scan_once() == []
    status = restarted.m.statuses["TEST"]
    assert status["status"] == "PROCESSED_CANDLE_REVISION"
    assert status["symbol"] == "TEST" and status["timestamp"] == pd.Timestamp(s.current[-1][0]).isoformat()
    assert ("open", "high", "low", "close", "volume")[field-1] in status["differing_fields"]
    assert s.m.state_path.read_bytes() == original


@pytest.mark.parametrize("kind", ["same", "duplicate", "missing", "multiple_one_revised"])
def test_processed_prefix_match_duplicate_omission_and_revision(tmp_path, kind):
    s = fixture(tmp_path)
    s.m.scan_once()
    original = s.m.state_path.read_bytes()
    s.current.append(candle(NOW, 98.7))
    s.clock[0] += pd.Timedelta(minutes=5)
    if kind == "duplicate":
        s.current.append(s.current[0].copy())
    elif kind == "missing":
        s.current.pop(0)
    elif kind == "multiple_one_revised":
        s.current[0][5] += 1
    s.m.scan_once()
    assert s.m.statuses["TEST"]["status"] == {
        "same": "EVALUATED", "duplicate": "EVALUATED", "missing": "MISSING_PROCESSED_CANDLE",
        "multiple_one_revised": "PROCESSED_CANDLE_REVISION"}[kind]
    if kind in {"missing", "multiple_one_revised"}:
        assert s.m.state_path.read_bytes() == original


@pytest.mark.parametrize("create", [False, True])
def test_processed_revision_that_would_create_or_remove_real_signal_is_rejected(tmp_path, monkeypatch, create):
    s = fixture(tmp_path, gain=.6)
    original_price, revised_price = (98.6, 97.) if create else (97., 98.6)
    s.current[-1] = candle(pd.Timestamp(s.current[-1][0]), original_price)
    assert s.m.scan_once() == []
    original = s.m.state_path.read_bytes()
    future = candle(NOW, 97.7)
    original_rows = copy.deepcopy(s.current) + [future]
    revised_rows = copy.deepcopy(original_rows)
    revised_rows[-2] = candle(pd.Timestamp(revised_rows[-2][0]), revised_price)
    def signal(rows):
        frame = sm.calculate_indicators(sm.candles_to_dataframe(sm._canonical_candles(s.history)+sm._canonical_candles(rows)))
        return sm.is_frozen_signal(sm.detect_reversal_candidates(frame).iloc[-1])
    assert signal(original_rows) is (not create)
    assert signal(revised_rows) is create
    s.current[:] = revised_rows
    s.clock[0] += pd.Timedelta(minutes=5)
    forbid_indicators(monkeypatch)
    assert s.m.scan_once() == []
    assert s.m.statuses["TEST"]["status"] == "PROCESSED_CANDLE_REVISION"
    assert s.m.state_path.read_bytes() == original


def test_next_session_archives_previous_candles_without_revision_comparison(tmp_path):
    s = fixture(tmp_path)
    s.m.scan_once()
    next_day = pd.Timestamp("2026-10-05T09:25:00+05:30")
    nxt = fixture(tmp_path, now=next_day)
    assert len(nxt.m.scan_once()) == 1
    state = sm._read_state(nxt.m.state_path)
    assert len(state["archive"]) == 1
    again = fixture(tmp_path, now=next_day)
    assert again.m.scan_once() == []
    assert again.m.statuses["TEST"]["status"] == "ALREADY_PROCESSED"


@pytest.mark.parametrize("day,close", [("2026-12-08", "15:30"), ("2026-10-01", "15:15")])
@pytest.mark.parametrize("drain,poll", [(120, 60), (30, 10)])
@pytest.mark.parametrize("available", [1, 50, 60, 61, 70, 90, 119, "boundary", "after", "never"])
def test_final_preexpiry_poll_matrix(tmp_path, day, close, drain, poll, available):
    end = pd.Timestamp(f"{day}T{close}:00+05:30")
    s = fixture(tmp_path, now=end)
    s.m.post_close_drain = pd.Timedelta(seconds=drain)
    s.m.poll_interval_seconds = poll
    missing = s.current.pop()
    calls = []
    arrives = {"boundary": drain, "after": drain+.01, "never": float("inf")}.get(available, available)
    def fetch(*a, **k):
        elapsed = (s.clock[0]-end).total_seconds()
        calls.append(elapsed)
        rows = s.current + ([missing] if elapsed >= arrives else [])
        return {"data": {"candles": rows + [candle(end, 999)]}}
    s.m.intraday_fetcher = fetch
    s.m.run(now_fn=lambda: s.clock[0], sleep_fn=lambda seconds: s.clock.__setitem__(0, s.clock[0]+pd.Timedelta(seconds=seconds)))
    expected = arrives <= drain-1
    assert ("TEST" in s.m.last_processed) is expected
    assert all(t < drain for t in calls) and len(calls) == len(set(calls))
    if expected:
        assert s.m.last_processed["TEST"] == end-pd.Timedelta(minutes=5)
    else:
        assert calls[-1] == drain-1
    if drain == 120 and available in (70, 90, 119):
        assert calls == [0, 60, 119]


@pytest.mark.parametrize("returned", [120, 120.01])
def test_final_fetch_returning_at_or_after_deadline_is_rejected(tmp_path, monkeypatch, returned):
    end = pd.Timestamp("2026-10-01T15:15:00+05:30")
    s = fixture(tmp_path, now=end)
    s.clock[0] = end + pd.Timedelta(seconds=119)
    def fetch(*a, **k):
        s.clock[0] = end + pd.Timedelta(seconds=returned)
        return {"data": {"candles": s.current}}
    s.m.intraday_fetcher = fetch
    forbid_indicators(monkeypatch)
    s.m.scan_once()
    assert s.m.statuses["TEST"]["status"] == "ACQUISITION_DEADLINE_EXCEEDED"
    assert not s.m.last_processed


@pytest.mark.parametrize("transport", [4], indirect=True)
def test_final_opportunity_uses_reviewed_transport_and_spends_each_attempt(tmp_path, monkeypatch, transport):
    t = transport
    end = pd.Timestamp("2026-10-01T15:15:00+05:30")
    s = fixture(tmp_path / "data", now=end)
    t.clock[0] = end
    times = []
    def get(url, *args):
        logical = AppendOnlyJsonl(t.adapter.output / "request_budget.jsonl", "request_id").records()
        physical = AppendOnlyJsonl(t.adapter.output / "physical_attempts.jsonl", "request_id").records()
        assert len(logical) == len(physical) == len(t.calls)+1
        t.calls.append(url)
        if "intraday" in url:
            elapsed = (t.clock[0]-end).total_seconds()
            times.append(elapsed)
            rows = s.current if elapsed >= 119 else s.current[:-1]
        else:
            rows = s.history
        return {"status": 200, "data": {"data": {"candles": rows}}}
    monkeypatch.setattr(upstox_client, "_bounded_get", get)
    m = sm.SignalMonitor(stocks=t.adapter.stocks, calendar=t.adapter.calendar, transport=t.adapter,
                         state_path=tmp_path / "integrated/checkpoint.json", output=lambda _: None)
    m.run(sleep_fn=lambda seconds: t.clock.__setitem__(0, t.clock[0]+pd.Timedelta(seconds=seconds)))
    assert times == [0, 60, 119] and len(t.calls) == 4
    assert len(AppendOnlyJsonl(t.adapter.output / "request_budget.jsonl", "request_id").records()) == 4
    assert len(AppendOnlyJsonl(t.adapter.output / "physical_attempts.jsonl", "request_id").records()) == 4
    assert m.last_processed["TEST"] == end-pd.Timedelta(minutes=5)


def test_pending_restart_visibility_advancement_new_event_and_explicit_ack(tmp_path):
    s = fixture(tmp_path)
    def crash(text):
        if "FROZEN SIGNAL DETECTED" in text:
            raise RuntimeError("simulated output interruption")
    s.m.output = crash
    assert len(s.m.scan_once()) == 1
    first = sm._read_state(s.m.state_path)["events"][0]
    restarted = fixture(tmp_path)
    assert restarted.m.scan_once() == []
    assert any("PENDING_ALERT_REQUIRES_RECONCILIATION" in text and first["id"] in text for text in restarted.messages)
    restarted.current.append(candle(NOW, 98.7))
    restarted.clock[0] += pd.Timedelta(minutes=5)
    restarted.m.scan_once()
    status = restarted.m.statuses["TEST"]
    assert status["status"] == "EVALUATED" and status["pending_count"] == 1
    assert status["oldest_pending_timestamp"] == first["alert"]["timestamp"]
    with pytest.raises(ValueError):
        restarted.m.acknowledge_pending(first["id"])
    restarted.m.acknowledge_pending(first["id"], confirmed_delivered=True)
    restarted.m.scan_once()
    assert restarted.m.statuses["TEST"]["pending_count"] == 0
    state = sm._read_state(s.m.state_path)
    assert len(state["events"]) == 1
    assert state["events"][0]["acknowledgement"] == "OPERATOR_CONFIRMED"
    again = fixture(tmp_path, now=restarted.clock[0], current=restarted.current)
    again.m.scan_once()
    assert not any("PENDING_ALERT" in text for text in again.messages)


def test_new_signal_does_not_hide_old_pending_event(tmp_path, monkeypatch):
    s = fixture(tmp_path)
    def crash(text):
        raise RuntimeError("console unavailable")
    s.m.output = crash
    s.m.scan_once()
    first_id = sm._read_state(s.m.state_path)["events"][0]["id"]
    restart = fixture(tmp_path)
    restart.clock[0] += pd.Timedelta(minutes=20)
    restart.current.extend(candle(t, 98.7) for t in pd.date_range(NOW, NOW+pd.Timedelta(minutes=15), freq="5min"))
    target = NOW+pd.Timedelta(minutes=15)
    def detect(frame, **kw):
        frame = frame.copy()
        frame["decline_pct"], frame["rsi_14"], frame["reversal_condition"] = -.5, 35., False
        frame.loc[frame.timestamp == target, ["decline_pct", "rsi_14", "reversal_condition"]] = [-1.5, 22., True]
        return frame
    monkeypatch.setattr(sm, "detect_reversal_candidates", detect)
    assert len(restart.m.scan_once()) == 1
    state = sm._read_state(s.m.state_path)
    assert len(state["events"]) == 2 and len({e["id"] for e in state["events"]}) == 2
    assert state["events"][0]["id"] == first_id and state["events"][0]["delivery"] == "PENDING"
    assert restart.m.statuses["TEST"]["pending_event_ids"] == [first_id]


@pytest.mark.parametrize("kind", ["count", "missing_event", "duplicate", "overlap", "symbol", "date", "future",
                                  "no_candle", "orphan_candle", "orphan_event", "transition", "history", "context"])
def test_valid_checksum_semantically_impossible_state_fails_closed(tmp_path, monkeypatch, kind):
    s = fixture(tmp_path)
    s.m.scan_once()
    state = sm._read_state(s.m.state_path)
    e = state["symbols"]["TEST"]
    if kind == "count":
        e["evaluation"]["signal_count"] = e["evaluations"][-1]["evaluation"]["signal_count"] = 200
    elif kind == "missing_event":
        state["events"] = []
    elif kind == "duplicate":
        state["events"].append(copy.deepcopy(state["events"][0]))
    elif kind == "overlap":
        state["events"][0]["delivery"] = "PENDING"
    elif kind in {"symbol", "date", "future"}:
        event = state["events"][0]
        event["alert"]["stock" if kind == "symbol" else "timestamp"] = {
            "symbol": "OTHER", "date": "2026-09-30T09:20:00+05:30", "future": "2026-10-01T10:20:00+05:30"}[kind]
        old = event["id"]
        event["id"] = sm._digest({"alert": event["alert"], "context_sha256": event["context_sha256"]})
        e["evaluations"][-1]["event_ids"] = [event["id"] if t == old else t for t in e["evaluations"][-1]["event_ids"]]
    elif kind == "no_candle":
        e["last_processed"] = "2026-10-01T09:25:00+05:30"
    elif kind == "orphan_candle":
        e["intraday"].append(candle(NOW, 98.7))
        e["intraday_sha256"] = sm._digest(e["intraday"])
    elif kind == "orphan_event":
        e["evaluations"][-1]["event_ids"] = []
        e["evaluation"]["signal_count"] = e["evaluations"][-1]["evaluation"]["signal_count"] = 0
    elif kind == "transition":
        e["evaluations"][-1]["previous"] = e["last_processed"]
    elif kind == "history":
        e["history_date"] = "2026-09-30"
    else:
        e["context_sha256"] = "0"*64
    sm._save_state(s.m.state_path, state)
    before = s.m.state_path.read_bytes()
    forbid_indicators(monkeypatch)
    s.m.intraday_fetcher = lambda *a, **k: pytest.fail("invalid state acquired data")
    assert s.m.scan_once() == []
    assert s.m.statuses["TEST"]["status"] == "CHECKPOINT_STATE_INVALID"
    assert s.m.state_path.read_bytes() == before
