from datetime import datetime, timedelta
from pathlib import Path
from time import sleep
from zoneinfo import ZoneInfo
import hashlib
import json
import math
import os
import tempfile
import time
import copy

import pandas as pd

from app.market_analysis import (
    REQUIRED_CANDLES,
    calculate_indicators,
    candles_to_dataframe,
)
from app.reversal_detector import detect_reversal_candidates
from app.candle_integrity import CandleDataError, merge_raw_candle_batches
from app.research.offline_session_calendar import OfflineSessionCalendar
from strategy_backtest import STOCKS
from app.monitor_transport import BoundedMonitorTransport, MonitorAcquisitionError
from app.research.deepseek_prospective import _experiment_lock


TIMEZONE = ZoneInfo("Asia/Kolkata")
INTERVAL_MINUTES = 5
POLL_INTERVAL_SECONDS = 60
EVENT_GAP_MINUTES = 15
HISTORICAL_LOOKBACK_DAYS = 14
POST_CLOSE_DRAIN_SECONDS = 120
CALENDAR_PATH = Path(__file__).resolve().parent / "data/audits/nse_cm_session_manifest_v3.json"
DEFAULT_STATE_PATH = Path(__file__).resolve().parent / "logs/signal_monitor/checkpoint.json"
# MACD signal warm-up: (26-1)+(9-1)=33 discarded rows, then detector shift(3).
REQUIRED_HISTORY_CANDLES = max(REQUIRED_CANDLES - 1, (26 - 1) + (9 - 1) + 3)
CANONICAL_HISTORY_SESSIONS = 2
CHECKPOINT_SCHEMA = "signal-monitor-checkpoint-v3"


def _canonical_candles(candles):
    # Only timestamp/OHLCV are authoritative here; unused provider OI is excluded.
    return [[_as_ist_timestamp(c[0]).isoformat(), *[float(v) for v in c[1:6]], 0.0]
            for c in _deduplicate_candles(candles)]


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate checkpoint field")
        result[key] = value
    return result


def _read_state(path):
    saved = json.loads(path.read_bytes(), object_pairs_hook=_unique_fields)
    if set(saved) != {"sha256", "record"} or saved["sha256"] != _digest(saved["record"]):
        raise ValueError("checkpoint hash mismatch")
    return saved["record"]


def _save_state(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(json.dumps({"sha256": _digest(record), "record": record}, sort_keys=True, allow_nan=False).encode())
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _session_labels(calendar, stamp):
    duration = pd.Timedelta(minutes=INTERVAL_MINUTES)
    return [label for start, end in calendar.intervals(stamp)
            for label in pd.date_range(start, end - duration, freq=duration)]


def _network_disabled(*args, **kwargs):
    raise RuntimeError("NETWORK_DISABLED: explicitly supplied offline fetchers required")


def _as_ist_timestamp(value):
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize(TIMEZONE)
    return timestamp.tz_convert(TIMEZONE)


def _response_candles(response):
    if (not isinstance(response, dict)
            or response.get("status", "success") != "success"
            or not isinstance(response.get("data"), dict)
            or not isinstance(response["data"].get("candles"), list)):
        raise ValueError("INVALID_CANDLE_RESPONSE")
    return response["data"]["candles"]


def _deduplicate_candles(candles):
    if not candles:
        return []
    merged, _ = merge_raw_candle_batches([("monitor", candles)], "MONITOR")
    return merged


def _completed_candles(candles, now):
    completed = []
    duration = pd.Timedelta(minutes=INTERVAL_MINUTES)
    for candle in candles:
        try:
            timestamp = _as_ist_timestamp(candle[0])
        except (TypeError, ValueError):
            continue
        if timestamp + duration <= now:
            completed.append(candle)
    return completed


def _is_regular_session_candle(candle, session_date, calendar=None):
    timestamp = _as_ist_timestamp(candle[0])
    calendar = calendar if calendar is not None else OfflineSessionCalendar(CALENDAR_PATH)
    return timestamp.date() == session_date and timestamp in _session_labels(calendar, timestamp)


def is_frozen_signal(row):
    try:
        decline_pct = float(row["decline_pct"])
        rsi_14 = float(row["rsi_14"])
    except (KeyError, TypeError, ValueError):
        return False

    return (
        -2.0 < decline_pct <= -1.0
        and 20.0 <= rsi_14 < 25.0
        and bool(row.get("reversal_condition", False))
    )


def is_regular_session(now, calendar=None):
    now = _as_ist_timestamp(now)
    calendar = calendar if calendar is not None else OfflineSessionCalendar(CALENDAR_PATH)
    return any(start <= now <= end for start, end in calendar.intervals(now))


class SignalMonitor:
    def __init__(
        self,
        stocks=None,
        intraday_fetcher=None,
        historical_fetcher=None,
        output=None,
        poll_interval_seconds=POLL_INTERVAL_SECONDS,
        calendar=None,
        post_close_drain_seconds=POST_CLOSE_DRAIN_SECONDS,
        now_fn=None,
        state_path=None,
        transport=None,
        offline=False,
        acquisition_timeout_seconds=20,
    ):
        self.stocks = stocks if stocks is not None else STOCKS
        self.intraday_fetcher = intraday_fetcher or _network_disabled
        self.historical_fetcher = historical_fetcher or _network_disabled
        self.output = output if output is not None else print
        if (intraday_fetcher is not None or historical_fetcher is not None) and offline is not True:
            raise ValueError("arbitrary fetchers require explicit offline fixture mode")
        if transport is not None and type(transport) is not BoundedMonitorTransport:
            raise ValueError("reviewed bounded monitor transport required")
        if transport is not None and (offline or intraday_fetcher is not None or historical_fetcher is not None):
            raise ValueError("transport modes cannot be mixed")
        self.offline, self.transport, self.now_fn = offline, transport, now_fn
        self.state_path = Path(state_path) if state_path is not None else DEFAULT_STATE_PATH
        self.acquisition_timeout_seconds = acquisition_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (
                poll_interval_seconds, post_close_drain_seconds, acquisition_timeout_seconds))
                or not 0 < poll_interval_seconds <= 60 or not 0 < post_close_drain_seconds <= 300
                or poll_interval_seconds >= post_close_drain_seconds
                or not 0 < acquisition_timeout_seconds <= 20):
            raise ValueError("bounded positive poll/drain intervals required")
        self.calendar = calendar if calendar is not None else OfflineSessionCalendar(CALENDAR_PATH)
        self.post_close_drain = pd.Timedelta(seconds=post_close_drain_seconds)
        if transport is not None:
            if (transport.calendar.identity != self.calendar.identity or transport.stocks != dict(self.stocks)
                    or transport.drain != self.post_close_drain
                    or (now_fn is not None and now_fn is not transport.now_fn)):
                raise ValueError("monitor transport/session identity mismatch")
            self.now_fn = transport.now_fn
        self.statuses = {}

        self.last_processed = {}
        self.last_signal_at = {}
        self.historical_cache = {}
        self.historical_cache_date = {}
        self.processed_intraday = {}
        self._notified_pending = set()

    def _identity(self):
        return {"stocks": dict(self.stocks), "calendar": self.calendar.identity, "interval_minutes": INTERVAL_MINUTES,
                "history_minimum": REQUIRED_HISTORY_CANDLES, "canonical_sessions": CANONICAL_HISTORY_SESSIONS,
                "acquisition_timeout_seconds": self.acquisition_timeout_seconds,
                "drain_seconds": self.post_close_drain.total_seconds(),
                "authoritative_fields": ["timestamp", "open", "high", "low", "close", "volume"],
                "sources": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (
                    Path(__file__), Path(__file__).parent / "app/market_analysis.py",
                    Path(__file__).parent / "app/reversal_detector.py")}}

    def _context_hash(self, stock, day, history, intraday):
        return _digest({"stock": stock, "instrument_key": self.stocks[stock], "session": str(day),
            "interval": INTERVAL_MINUTES, "calendar": self.calendar.identity,
            "history": history, "intraday": intraday})

    def _stored_stamp(self, value):
        if type(value) is not str or pd.Timestamp(value).tzinfo is None:
            raise ValueError("noncanonical checkpoint timestamp")
        stamp = _as_ist_timestamp(value)
        if stamp.isoformat() != value:
            raise ValueError("noncanonical checkpoint timestamp")
        return stamp

    def _validate_entry(self, stock, entry, now, events):
        fields = {"last_processed", "last_signal_at", "history_date", "history", "evaluation",
                  "intraday", "history_sha256", "intraday_sha256", "context_sha256",
                  "evaluations", "initial_signal_at"}
        if type(entry) is not dict or set(entry) != fields:
            raise ValueError("invalid checkpoint entry")
        stamp = self._stored_stamp(entry["last_processed"])
        day = stamp.date()
        labels = _session_labels(self.calendar, stamp)
        if (stamp not in labels or stamp + pd.Timedelta(minutes=INTERVAL_MINUTES) > now
                or entry["history_date"] != day.isoformat()):
            raise ValueError("invalid checkpoint session")
        historical = _canonical_candles(entry["history"])
        intraday = _canonical_candles(entry["intraday"])
        if (_digest(historical) != _digest(entry["history"]) or _digest(intraday) != _digest(entry["intraday"])
                or historical != self._select_history(historical, day)
                or [self._stored_stamp(c[0]) for c in intraday] != [t for t in labels if t <= stamp]
                or entry["history_sha256"] != _digest(historical)
                or entry["intraday_sha256"] != _digest(intraday)
                or entry["context_sha256"] != self._context_hash(stock, day, historical, intraday)):
            raise ValueError("invalid canonical candle evidence")
        journal = entry["evaluations"]
        if type(journal) is not list or not journal or entry["evaluation"] != journal[-1]["evaluation"]:
            raise ValueError("missing evaluation journal")
        prior = None
        signal = None if entry["initial_signal_at"] is None else self._stored_stamp(entry["initial_signal_at"])
        if signal is not None and signal not in [self._stored_stamp(c[0]) for c in intraday]:
            raise ValueError("invalid initial suppression")
        refs = []
        for index, item in enumerate(journal):
            if type(item) is not dict or set(item) != {"previous", "through", "candidate_times", "event_ids",
                                                     "context_sha256", "evaluation"}:
                raise ValueError("invalid evaluation journal")
            through = self._stored_stamp(item["through"])
            previous = None if item["previous"] is None else self._stored_stamp(item["previous"])
            if previous != prior or through not in labels or through > stamp or (prior is not None and through <= prior):
                raise ValueError("invalid progress transition")
            if index == 0 and signal is not None and signal >= through:
                raise ValueError("invalid initial suppression")
            evaluation = item["evaluation"]
            if (type(evaluation) is not dict or set(evaluation) != {"status", "timestamp", "signal_count",
                    "evaluated_rows", "initial_latest_only", "acquisition_start", "decision_time", "deadline"}
                    or evaluation["status"] != "EVALUATED" or evaluation["timestamp"] != through.isoformat()
                    or type(evaluation["initial_latest_only"]) is not bool
                    or evaluation["initial_latest_only"] != (index == 0)
                    or type(evaluation["signal_count"]) is not int or type(evaluation["evaluated_rows"]) is not int
                    or evaluation["evaluated_rows"] != (1 if prior is None else sum(prior < t <= through for t in labels))):
                raise ValueError("invalid evaluation metadata")
            begin = self._stored_stamp(evaluation["acquisition_start"])
            decision = self._stored_stamp(evaluation["decision_time"])
            deadline = self._stored_stamp(evaluation["deadline"])
            if not (begin <= decision < deadline and through + pd.Timedelta(minutes=INTERVAL_MINUTES) <= decision
                    and begin.date() == decision.date() == day
                    and deadline == min(begin + pd.Timedelta(seconds=self.acquisition_timeout_seconds),
                                        self.calendar.intervals(begin)[-1][1] + self.post_close_drain)):
                raise ValueError("invalid evaluation clock")
            prefix = [c for c in intraday if self._stored_stamp(c[0]) <= through]
            context = self._context_hash(stock, day, historical, prefix)
            if item["context_sha256"] != context:
                raise ValueError("evaluation context changed")
            candidates = [self._stored_stamp(t) for t in item["candidate_times"]]
            if (candidates != sorted(set(candidates)) or any(t not in labels or t > through or
                    (t != through if prior is None else t <= prior) for t in candidates)):
                raise ValueError("invalid evaluated candidates")
            expect_event = False
            for candidate in candidates:
                new = signal is None or candidate - signal > pd.Timedelta(minutes=EVENT_GAP_MINUTES)
                signal = candidate
                expect_event = expect_event or (new and candidate == through)
            ids = item["event_ids"]
            if (type(ids) is not list or len(ids) != int(expect_event)
                    or evaluation["signal_count"] != len(ids)):
                raise ValueError("event count mismatch")
            for event_id in ids:
                event = events[event_id]
                alert = event["alert"]
                if (alert["stock"] != stock or alert["timestamp"] != through.isoformat()
                        or event["context_sha256"] != context or alert["close"] != prefix[-1][4]):
                    raise ValueError("event context mismatch")
                refs.append(event_id)
            prior = through
        if prior != stamp or entry["last_signal_at"] != (signal.isoformat() if signal is not None else None):
            raise ValueError("progress or suppression mismatch")
        return stamp, signal, historical, intraday, refs

    def _load_checkpoint(self, now):
        marker = self.state_path.with_suffix(".identity.json")
        identity = self._identity()
        if not marker.exists() and not self.state_path.exists():
            _save_state(marker, identity)
            _save_state(self.state_path, {"schema": CHECKPOINT_SCHEMA, "identity": identity,
                                          "symbols": {}, "archive": [], "events": []})
        if not marker.exists() or not self.state_path.exists() or _read_state(marker) != identity:
            raise ValueError("checkpoint identity missing or changed")
        state = _read_state(self.state_path)
        if (set(state) != {"schema", "identity", "symbols", "archive", "events"}
                or state["schema"] != CHECKPOINT_SCHEMA or state["identity"] != identity
                or type(state["symbols"]) is not dict or set(state["symbols"]) - set(self.stocks)
                or type(state["events"]) is not list or type(state["archive"]) is not list):
            raise ValueError("invalid checkpoint schema")
        events = {}
        for event in state["events"]:
            alert = event["alert"]
            if (type(event) is not dict or set(event) != {"id", "alert", "context_sha256", "delivery", "acknowledgement"}
                    or type(alert) is not dict or set(alert) != {"stock", "timestamp", "close", "decline_pct", "rsi_14",
                    "reversal_condition", "event_status"} or alert["stock"] not in state["symbols"]
                    or self._stored_stamp(alert["timestamp"]) not in _session_labels(self.calendar, alert["timestamp"])
                    or type(alert["reversal_condition"]) is not bool or not is_frozen_signal(alert)
                    or any(type(alert[k]) not in (int, float) or not math.isfinite(alert[k]) for k in ("close", "decline_pct", "rsi_14"))
                    or alert["event_status"] != "FIRST SIGNAL - NEW EVENT"
                    or event["id"] != _digest({"alert": alert, "context_sha256": event["context_sha256"]})
                    or event["id"] in events or event["delivery"] not in {"PENDING", "ACKNOWLEDGED"}
                    or (event["delivery"] == "PENDING" and event["acknowledgement"] is not None)
                    or (event["delivery"] == "ACKNOWLEDGED" and event["acknowledgement"] not in
                        ("OUTPUT_RETURNED", "OPERATOR_CONFIRMED"))):
                raise ValueError("invalid checkpoint event")
            events[event["id"]] = event
        processed, signals, cache, intraday_cache, dates, refs = {}, {}, {}, {}, {}, []
        seen = set()
        entries = [(s, e, False) for s, e in state["symbols"].items()]
        for item in state["archive"]:
            if type(item) is not dict or set(item) != {"stock", "entry"} or item["stock"] not in state["symbols"]:
                raise ValueError("invalid archive")
            entries.append((item["stock"], item["entry"], True))
        for stock, entry, archived in entries:
            stamp, signal, historical, intraday, linked = self._validate_entry(stock, entry, now, events)
            key = (stock, stamp.date())
            if key in seen:
                raise ValueError("duplicate checkpoint session")
            seen.add(key)
            refs.extend(linked)
            if archived:
                if stamp.date() >= self._stored_stamp(state["symbols"][stock]["last_processed"]).date():
                    raise ValueError("archive is not historical")
            else:
                processed[stock], dates[stock] = stamp, stamp.date()
                cache[stock], intraday_cache[stock] = historical, intraday
                if signal is not None:
                    signals[stock] = signal
        if len(refs) != len(set(refs)) or set(refs) != set(events):
            raise ValueError("missing or orphan event evidence")
        self.last_processed, self.last_signal_at, self._state = processed, signals, state
        self.historical_cache.update(cache)
        self.historical_cache_date.update(dates)
        self.processed_intraday = intraday_cache

    def _commit(self, stock, alerts):
        state = copy.deepcopy(self._state)
        day = self.last_processed[stock].date()
        old = state["symbols"].get(stock)
        same_day = old is not None and old["history_date"] == day.isoformat()
        if old is not None and not same_day:
            state["archive"].append({"stock": stock, "entry": old})
        historical = _canonical_candles(self.historical_cache[stock])
        intraday = self._intraday_used
        context = self._context_hash(stock, day, historical, intraday)
        ids = []
        for alert in alerts:
            saved = {**alert, "timestamp": alert["timestamp"].isoformat()}
            event_id = _digest({"alert": saved, "context_sha256": context})
            ids.append(event_id)
            state["events"].append({"id": event_id, "alert": saved, "context_sha256": context,
                                   "delivery": "PENDING", "acknowledgement": None})
        journal = copy.deepcopy(old["evaluations"]) if same_day else []
        journal.append({"previous": old["last_processed"] if same_day else None,
            "through": self.last_processed[stock].isoformat(), "candidate_times": self._evaluated_signals,
            "event_ids": ids, "context_sha256": context, "evaluation": copy.deepcopy(self.statuses[stock])})
        state["symbols"][stock] = {"last_processed": self.last_processed[stock].isoformat(),
            "last_signal_at": self.last_signal_at[stock].isoformat() if stock in self.last_signal_at else None,
            "history_date": day.isoformat(), "history": historical, "intraday": intraday,
            "history_sha256": _digest(historical), "intraday_sha256": _digest(intraday),
            "context_sha256": context, "evaluations": journal,
            "initial_signal_at": old["initial_signal_at"] if same_day else self._initial_seed,
            "evaluation": copy.deepcopy(self.statuses[stock])}
        _save_state(self.state_path, state)
        self._state = state
        self.processed_intraday[stock] = intraday

    def _pending_summary(self, stock):
        pending = [e for e in self._state["events"] if e["delivery"] == "PENDING" and e["alert"]["stock"] == stock]
        return {"pending_count": len(pending), "pending_event_ids": [e["id"] for e in pending],
                "oldest_pending_timestamp": min((e["alert"]["timestamp"] for e in pending), default=None)}

    def _surface_pending(self):
        for stock in self.stocks:
            summary = self._pending_summary(stock)
            fresh = set(summary["pending_event_ids"]) - self._notified_pending
            if fresh:
                try:
                    self.output("PENDING_ALERT_REQUIRES_RECONCILIATION " +
                                json.dumps({"stock": stock, **summary}, sort_keys=True))
                except Exception:
                    pass
                self._notified_pending.update(fresh)

    def acknowledge_pending(self, event_id, *, confirmed_delivered=False, now=None):
        """Explicit local operator confirmation only; never retries uncertain delivery."""
        if confirmed_delivered is not True:
            raise ValueError("explicit delivered confirmation required")
        clock = self.now_fn or (lambda: datetime.now(TIMEZONE))
        with _experiment_lock(self.state_path.parent / (self.state_path.stem + ".lock")):
            self._load_checkpoint(_as_ist_timestamp(now if now is not None else clock()))
            state = copy.deepcopy(self._state)
            event = next(e for e in state["events"] if e["id"] == event_id)
            if event["delivery"] == "PENDING":
                event.update(delivery="ACKNOWLEDGED", acknowledgement="OPERATOR_CONFIRMED")
                _save_state(self.state_path, state)
                self._state = state
            return {"event_id": event_id, "delivery": event["delivery"]}

    def _fetch(self, kind, instrument_key, date=None):
        if self.transport is None and not self.offline:
            return _network_disabled()
        self._check_deadline()
        if self.transport is not None:
            response = self.transport.fetch(kind, instrument_key, date=date, deadline=self._deadline)
        elif self.offline:
            # Offline fixture callbacks cannot use the official network transport.
            from app.upstox_client import request_window
            def disabled():
                raise MonitorAcquisitionError("ACQUISITION_DISABLED")
            with request_window(end=self._deadline.to_pydatetime(), before_attempt=disabled, max_attempts=1):
                response = (self.intraday_fetcher(instrument_key, interval=INTERVAL_MINUTES) if kind == "intraday"
                            else self.historical_fetcher(instrument_key, date, interval=INTERVAL_MINUTES))
        else:
            return _network_disabled()
        self._check_deadline()
        return response

    def _check_deadline(self):
        now = _as_ist_timestamp(self._clock())
        if now >= self._deadline or time.monotonic() - self._started_monotonic >= self.acquisition_timeout_seconds:
            raise MonitorAcquisitionError("ACQUISITION_DEADLINE_EXCEEDED")
        if now.date() != self._acquisition_start.date() or not any(
                start <= now <= end + self.post_close_drain for start, end in self.calendar.intervals(now)):
            raise MonitorAcquisitionError("SESSION_INVALID")
        return now

    def _log_error(self, stock, now, error):
        status = (error.status if isinstance(error, MonitorAcquisitionError) else
                  "CONFLICTING_CANDLES" if isinstance(error, CandleDataError) and error.details else "SCAN_FAILED")
        self.statuses[stock] = {"status": status, "error_type": type(error).__name__}
        if status == "INCOMPLETE_HISTORY":
            self.statuses[stock].update(self._history_error)
        if status in {"PROCESSED_CANDLE_REVISION", "MISSING_PROCESSED_CANDLE"}:
            self.statuses[stock].update(self._revision_error)
        # Only validated conflict timestamps are copied; arbitrary exception text may contain credentials.
        timestamps = [str(item["timestamp"]) for item in getattr(error, "details", [])]
        self.output(
            f"[{now.strftime('%H:%M')}] {stock}: "
            f"{status} ({type(error).__name__}) conflicts={timestamps}"
        )

    def _required_history(self, session_date):
        labels = []
        sessions = 0
        for offset in range(1, HISTORICAL_LOOKBACK_DAYS + 1):
            day = pd.Timestamp(session_date - timedelta(days=offset), tz=TIMEZONE)
            previous = _session_labels(self.calendar, day)
            if previous:
                sessions += 1
                labels = previous + labels
            if sessions >= CANONICAL_HISTORY_SESSIONS and len(labels) >= REQUIRED_HISTORY_CANDLES:
                return labels
        raise MonitorAcquisitionError("SESSION_INVALID")

    def _validate_history(self, candles, session_date):
        required = self._required_history(session_date)
        stamps = [_as_ist_timestamp(c[0]) for c in candles]
        missing = sorted(set(required) - set(stamps))
        if missing:
            self._history_error = {"missing_count": len(missing), "required_count": len(required),
                "missing_sample": [s.isoformat() for s in missing[:5]], "expected_tail": required[-1].isoformat(),
                "received_tail": max(stamps).isoformat() if stamps else None}
            raise MonitorAcquisitionError("INCOMPLETE_HISTORY")
        for stamp in stamps:
            if stamp.date() >= session_date or stamp not in _session_labels(self.calendar, stamp):
                raise ValueError("INVALID_HISTORY_SESSION")
        self._require_contiguous(candles)

    def _select_history(self, candles, session_date):
        rows = _canonical_candles(candles)
        required = set(self._required_history(session_date))
        # Extra prefixes cannot seed indicators; malformed/conflicting rows were already rejected.
        for row in rows:
            stamp = _as_ist_timestamp(row[0])
            if stamp.date() >= session_date or stamp not in _session_labels(self.calendar, stamp):
                raise ValueError("INVALID_HISTORY_SESSION")
        selected = [c for c in rows if _as_ist_timestamp(c[0]) in required]
        self._validate_history(selected, session_date)
        return selected

    def _load_historical_context(self, stock, instrument_key, session_date):
        if self.historical_cache_date.get(stock) == session_date:
            return self.historical_cache.get(stock, [])

        candles = []
        required = self._required_history(session_date)
        for day in sorted({s.date() for s in required}, reverse=True):
            response = self._fetch("history", instrument_key, day.isoformat())
            batch = _deduplicate_candles(_response_candles(response))
            candles = _deduplicate_candles(candles + batch)
            if set(required) <= {_as_ist_timestamp(c[0]) for c in candles}:
                break

        context = self._select_history(candles, session_date)
        self.historical_cache[stock] = context
        self.historical_cache_date[stock] = session_date
        return context

    def _require_contiguous(self, candles):
        by_day = {}
        for candle in candles:
            stamp = _as_ist_timestamp(candle[0])
            by_day.setdefault(stamp.date(), []).append(stamp)
        for stamps in by_day.values():
            expected = [label for label in _session_labels(self.calendar, stamps[0])
                        if stamps[0] <= label <= stamps[-1]]
            if stamps != expected:
                raise ValueError("INCOMPLETE_SESSION_CANDLES")

    def _signal_rows(self, detected):
        return detected[
            detected.apply(is_frozen_signal, axis=1)
        ]

    def _format_alert(self, alert):
        return "\n".join(
            [
                "========================================",
                "FROZEN SIGNAL DETECTED",
                "========================================",
                f"Stock: {alert['stock']}",
                f"Timestamp: {alert['timestamp']}",
                f"Close: {alert['close']:.2f}",
                f"Decline: {alert['decline_pct']:.2f}%",
                f"RSI14: {alert['rsi_14']:.2f}",
                f"Reversal: {alert['reversal_condition']}",
                f"Event status: {alert['event_status']}",
                "========================================",
            ]
        )

    def _current_candles(self, stock, intraday, now):
        today_candles = [
            candle
            for candle in intraday
            if _is_regular_session_candle(candle, now.date(), self.calendar)
        ]
        completed_intraday = _completed_candles(today_candles, now)
        completed_intraday = _deduplicate_candles(completed_intraday)
        labels = _session_labels(self.calendar, now)
        eligible = [label for label in labels
                    if label + pd.Timedelta(minutes=INTERVAL_MINUTES) <= now]
        expected = eligible[-1]
        latest_timestamp = _as_ist_timestamp(completed_intraday[-1][0]) if completed_intraday else None
        if latest_timestamp != expected:
            self.statuses[stock] = {"status": "STALE_MARKET_DATA", "latest_received": latest_timestamp,
                                    "expected": expected,
                                    "delay_seconds": (expected - latest_timestamp).total_seconds() if latest_timestamp is not None else None}
            self.output(f"[{now.strftime('%H:%M')}] {stock}: STALE_MARKET_DATA latest={latest_timestamp} expected={expected}")
            return None
        if [_as_ist_timestamp(c[0]) for c in completed_intraday] != eligible:
            raise ValueError("INCOMPLETE_SESSION_CANDLES")
        return completed_intraday, expected

    def _scan_stock(self, stock, instrument_key, now):
        intraday = _canonical_candles(_response_candles(self._fetch("intraday", instrument_key)))
        old = self._state["symbols"].get(stock)
        if old is not None and old["history_date"] == now.date().isoformat():
            received = {c[0]: c for c in intraday}
            for stored in old["intraday"]:
                self._revision_error = {"symbol": stock, "timestamp": stored[0]}
                if stored[0] not in received:
                    raise MonitorAcquisitionError("MISSING_PROCESSED_CANDLE")
                fields = [name for i, name in enumerate(("open", "high", "low", "close", "volume"), 1)
                          if stored[i] != received[stored[0]][i]]
                if fields:
                    self._revision_error["differing_fields"] = fields
                    raise MonitorAcquisitionError("PROCESSED_CANDLE_REVISION")
        current = self._current_candles(stock, intraday, self._check_deadline())
        if current is None:
            return [], False
        context = self._load_historical_context(stock, instrument_key, now.date())
        # Authoritative decision time is sampled after *all* acquisition, including history.
        now = self._check_deadline()
        current = self._current_candles(stock, intraday, now)
        if current is None:
            return [], False
        completed_intraday, expected = current
        self._intraday_used = _canonical_candles(completed_intraday)
        latest_timestamp = expected
        previous_timestamp = self.last_processed.get(stock)
        is_first_scan_today = (
            previous_timestamp is None
            or previous_timestamp.date() != now.date()
        )

        if (
            not is_first_scan_today
            and latest_timestamp <= previous_timestamp
        ):
            return [], False

        candles = _deduplicate_candles(context + completed_intraday)
        df = candles_to_dataframe(candles)
        df = calculate_indicators(df)
        detected = detect_reversal_candidates(
            df,
            decline_candles=3,
            min_decline_pct=0.5,
            rsi_threshold=30,
        )
        detected = detected.copy()
        detected["timestamp"] = detected["timestamp"].map(
            _as_ist_timestamp
        )

        current_day_rows = detected[
            detected["timestamp"].map(lambda value: value.date() == now.date())
        ].sort_values("timestamp")

        if is_first_scan_today:
            self.last_signal_at.pop(stock, None)
            earlier_signals = self._signal_rows(
                current_day_rows[current_day_rows["timestamp"] < latest_timestamp]
            )
            if not earlier_signals.empty:
                self.last_signal_at[stock] = earlier_signals.iloc[-1]["timestamp"]
            new_rows = current_day_rows[
                current_day_rows["timestamp"] == latest_timestamp
            ]
        else:
            new_rows = current_day_rows[
                (current_day_rows["timestamp"] > previous_timestamp)
                & (current_day_rows["timestamp"] <= latest_timestamp)
            ]

        if new_rows.empty:
            return [], False

        alerts = []
        self._initial_seed = self.last_signal_at[stock].isoformat() if stock in self.last_signal_at else None
        self._evaluated_signals = []
        processed_timestamp = previous_timestamp
        for _, row in new_rows.iterrows():
            timestamp = row["timestamp"]
            processed_timestamp = timestamp
            if not is_frozen_signal(row):
                continue
            self._evaluated_signals.append(timestamp.isoformat())

            prior_signal = self.last_signal_at.get(stock)
            is_new_event = (
                prior_signal is None
                or timestamp - prior_signal
                > pd.Timedelta(minutes=EVENT_GAP_MINUTES)
            )
            self.last_signal_at[stock] = timestamp

            if is_new_event and timestamp == expected:
                alerts.append(
                    {
                        "stock": stock,
                        "timestamp": timestamp,
                        "close": float(row["close"]),
                        "decline_pct": float(row["decline_pct"]),
                        "rsi_14": float(row["rsi_14"]),
                        "reversal_condition": bool(row["reversal_condition"]),
                        "event_status": "FIRST SIGNAL - NEW EVENT",
                    }
                )

        self.last_processed[stock] = processed_timestamp or latest_timestamp
        self.statuses[stock] = {"status": "EVALUATED", "timestamp": self.last_processed[stock].isoformat(),
                                "signal_count": len(alerts), "evaluated_rows": len(new_rows),
                                "initial_latest_only": is_first_scan_today,
                                "acquisition_start": self._acquisition_start.isoformat(),
                                "decision_time": now.isoformat(), "deadline": self._deadline.isoformat()}
        return alerts, True

    def scan_once(self, now=None):
        supplied = now
        self._clock = self.now_fn or ((lambda: supplied) if supplied is not None and self.offline else
                                     (lambda: datetime.now(TIMEZONE)))
        now = _as_ist_timestamp(now if now is not None else self._clock())
        with _experiment_lock(self.state_path.parent / (self.state_path.stem + ".lock")):
            try:
                self._load_checkpoint(now)
            except Exception:
                self.statuses = {s: {"status": "CHECKPOINT_STATE_INVALID"} for s in self.stocks}
                self.output("CHECKPOINT_STATE_INVALID")
                return []
            self._surface_pending()
            alerts = self._scan_once(now)
            for stock in self.stocks:
                self.statuses.setdefault(stock, {}).update(self._pending_summary(stock))
            return alerts

    def _scan_once(self, now):
        try:
            intervals = self.calendar.intervals(now)
        except ValueError:
            self.output(f"[{now.strftime('%H:%M')}] SESSION_UNVERIFIED")
            return []
        if not any(start <= now <= end + self.post_close_drain for start, end in intervals):
            return []
        labels = _session_labels(self.calendar, now)
        if not any(label + pd.Timedelta(minutes=INTERVAL_MINUTES) <= now for label in labels):
            return []

        alerts = []
        processed_any = False
        for stock, instrument_key in self.stocks.items():
            eligible = [s for s in labels if s + pd.Timedelta(minutes=INTERVAL_MINUTES) <= now]
            if self.last_processed.get(stock) == eligible[-1]:
                self.statuses[stock] = {"status": "ALREADY_PROCESSED", "timestamp": eligible[-1].isoformat(),
                    "pending_event_ids": [e["id"] for e in self._state["events"]
                        if e["alert"]["stock"] == stock and e["delivery"] == "PENDING"]}
                continue
            old_processed, old_signals = dict(self.last_processed), dict(self.last_signal_at)
            try:
                self._acquisition_start = _as_ist_timestamp(self._clock())
                self._started_monotonic = time.monotonic()
                self._deadline = min(self._acquisition_start + pd.Timedelta(seconds=self.acquisition_timeout_seconds),
                                     intervals[-1][1] + self.post_close_drain)
                stock_alerts, processed = self._scan_stock(
                    stock,
                    instrument_key,
                    now,
                )
                if processed:
                    self._commit(stock, stock_alerts)
                alerts.extend(stock_alerts)
                processed_any = processed_any or processed
            except Exception as error:
                # A failed state commit cannot be treated as successful in-memory progress.
                self.last_processed, self.last_signal_at = old_processed, old_signals
                self._log_error(stock, now, error)

        if processed_any:
            if alerts:
                for alert in alerts:
                    event = next(e for e in self._state["events"] if e["alert"]["stock"] == alert["stock"]
                                 and e["alert"]["timestamp"] == alert["timestamp"].isoformat())
                    event_id = event["id"]
                    try:
                        self.output(self._format_alert(alert))
                        acknowledged = copy.deepcopy(self._state)
                        next(e for e in acknowledged["events"] if e["id"] == event_id).update(
                            delivery="ACKNOWLEDGED", acknowledgement="OUTPUT_RETURNED")
                        _save_state(self.state_path, acknowledged)
                        self._state = acknowledged
                    except Exception:
                        self.statuses[alert["stock"]] = {"status": "OUTPUT_DELIVERY_UNCERTAIN", "event_id": event_id}
            else:
                self.output(
                    f"[{now.strftime('%H:%M')}] "
                    "Scan complete - 0 frozen signals."
                )

        return alerts

    def run(self, now_fn=None, sleep_fn=None):
        now_fn = now_fn or self.now_fn or (lambda: datetime.now(TIMEZONE))
        self.now_fn = now_fn
        sleep_fn = sleep_fn or sleep
        while True:
            now = _as_ist_timestamp(now_fn())
            try:
                intervals = self.calendar.intervals(now)
            except ValueError:
                self.output("SESSION_UNVERIFIED")
                return
            if not intervals or now > intervals[-1][1] + self.post_close_drain:
                return
            drain_deadline = intervals[-1][1] + self.post_close_drain
            final_opportunity = drain_deadline - pd.Timedelta(seconds=min(1.0, self.post_close_drain.total_seconds()/2))
            final_poll = now >= final_opportunity
            if now >= drain_deadline:
                return
            self.scan_once(now)
            labels = _session_labels(self.calendar, now)
            if now >= intervals[-1][1] and all(self.last_processed.get(stock) == labels[-1] for stock in self.stocks):
                return
            after = _as_ist_timestamp(now_fn())
            remaining = (drain_deadline - after).total_seconds()
            if remaining <= 0 or final_poll:
                return
            # Exactly one explicit final pre-expiry opportunity, not a boundary-time request or busy loop.
            sleep_fn(min(self.poll_interval_seconds, max(0.0, (final_opportunity-after).total_seconds())))


def main():
    SignalMonitor().run()


if __name__ == "__main__":
    main()
