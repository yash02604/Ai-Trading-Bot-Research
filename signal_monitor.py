from datetime import datetime, timedelta
from pathlib import Path
from time import sleep
from zoneinfo import ZoneInfo

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


TIMEZONE = ZoneInfo("Asia/Kolkata")
INTERVAL_MINUTES = 5
POLL_INTERVAL_SECONDS = 60
EVENT_GAP_MINUTES = 15
HISTORICAL_LOOKBACK_DAYS = 14
POST_CLOSE_DRAIN_SECONDS = 120
CALENDAR_PATH = Path(__file__).resolve().parent / "data/audits/nse_cm_session_manifest_v3.json"


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
    ):
        self.stocks = stocks if stocks is not None else STOCKS
        self.intraday_fetcher = intraday_fetcher or _network_disabled
        self.historical_fetcher = historical_fetcher or _network_disabled
        self.output = output if output is not None else print
        self.poll_interval_seconds = poll_interval_seconds
        if not 0 < poll_interval_seconds <= 60 or not 0 < post_close_drain_seconds <= 300:
            raise ValueError("bounded positive poll/drain intervals required")
        self.calendar = calendar if calendar is not None else OfflineSessionCalendar(CALENDAR_PATH)
        self.post_close_drain = pd.Timedelta(seconds=post_close_drain_seconds)
        self.statuses = {}

        self.last_processed = {}
        self.last_signal_at = {}
        self.historical_cache = {}
        self.historical_cache_date = {}

    def _log_error(self, stock, now, error):
        status = "CONFLICTING_CANDLES" if isinstance(error, CandleDataError) and error.details else "SCAN_FAILED"
        self.statuses[stock] = {"status": status, "error_type": type(error).__name__}
        # Only validated conflict timestamps are copied; arbitrary exception text may contain credentials.
        timestamps = [str(item["timestamp"]) for item in getattr(error, "details", [])]
        self.output(
            f"[{now.strftime('%H:%M')}] {stock}: "
            f"{status} ({type(error).__name__}) conflicts={timestamps}"
        )

    def _load_historical_context(self, stock, instrument_key, session_date):
        if self.historical_cache_date.get(stock) == session_date:
            return self.historical_cache.get(stock, [])

        candles = []
        cursor = session_date - timedelta(days=1)
        attempts = 0

        while (
            len(_deduplicate_candles(candles)) < REQUIRED_CANDLES
            and attempts < HISTORICAL_LOOKBACK_DAYS
        ):
            day = pd.Timestamp(cursor, tz=TIMEZONE)
            if self.calendar.intervals(day):
                response = self.historical_fetcher(
                    instrument_key,
                    cursor.isoformat(),
                    interval=INTERVAL_MINUTES,
                )
                batch = _deduplicate_candles(_response_candles(response))
                if not batch:
                    raise ValueError("INCOMPLETE_HISTORY")
                for candle in batch:
                    stamp = _as_ist_timestamp(candle[0])
                    if stamp.date() >= session_date or not _is_regular_session_candle(candle, stamp.date(), self.calendar):
                        raise ValueError("INVALID_HISTORY_SESSION")
                candles = _deduplicate_candles(candles + batch)
                attempts += 1
            cursor -= timedelta(days=1)

        context = _deduplicate_candles(candles)
        if len(context) < REQUIRED_CANDLES:
            raise ValueError("INSUFFICIENT_HISTORY")
        self._require_contiguous(context)
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

    def _scan_stock(self, stock, instrument_key, now):
        response = self.intraday_fetcher(
            instrument_key,
            interval=INTERVAL_MINUTES,
        )
        intraday = _deduplicate_candles(_response_candles(response))
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
            return [], False
        if [_as_ist_timestamp(c[0]) for c in completed_intraday] != eligible:
            raise ValueError("INCOMPLETE_SESSION_CANDLES")
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

        context = self._load_historical_context(stock, instrument_key, now.date())

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
            earlier_signals = self._signal_rows(
                detected[detected["timestamp"] < latest_timestamp]
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
        processed_timestamp = previous_timestamp
        for _, row in new_rows.iterrows():
            timestamp = row["timestamp"]
            processed_timestamp = timestamp
            if not is_frozen_signal(row):
                continue

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
        self.statuses[stock] = {"status": "EVALUATED", "timestamp": self.last_processed[stock],
                                "signal_count": len(alerts)}
        return alerts, True

    def scan_once(self, now=None):
        now = _as_ist_timestamp(now or datetime.now(TIMEZONE))
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
            if now >= intervals[-1][1] and self.last_processed.get(stock) == labels[-1]:
                continue
            try:
                stock_alerts, processed = self._scan_stock(
                    stock,
                    instrument_key,
                    now,
                )
                alerts.extend(stock_alerts)
                processed_any = processed_any or processed
            except Exception as error:
                self._log_error(stock, now, error)

        if processed_any:
            if alerts:
                for alert in alerts:
                    self.output(self._format_alert(alert))
            else:
                self.output(
                    f"[{now.strftime('%H:%M')}] "
                    "Scan complete - 0 frozen signals."
                )

        return alerts

    def run(self, now_fn=None, sleep_fn=None):
        now_fn = now_fn or (lambda: datetime.now(TIMEZONE))
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
            self.scan_once(now)
            labels = _session_labels(self.calendar, now)
            if now >= intervals[-1][1] and all(self.last_processed.get(stock) == labels[-1] for stock in self.stocks):
                return
            sleep_fn(self.poll_interval_seconds)


def main():
    SignalMonitor().run()


if __name__ == "__main__":
    main()
