from datetime import date, datetime, time, timedelta
from time import sleep
from zoneinfo import ZoneInfo

import pandas as pd

from app.market_analysis import (
    REQUIRED_CANDLES,
    calculate_indicators,
    candles_to_dataframe,
)
from app.reversal_detector import detect_reversal_candidates
from app.upstox_client import get_historical_candles, get_intraday_candles
from strategy_backtest import STOCKS


TIMEZONE = ZoneInfo("Asia/Kolkata")
INTERVAL_MINUTES = 5
POLL_INTERVAL_SECONDS = 60
EVENT_GAP_MINUTES = 15
SESSION_START = time(9, 15)
SESSION_END = time(15, 30)
HISTORICAL_LOOKBACK_DAYS = 14


def _as_ist_timestamp(value):
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        return timestamp.tz_localize(TIMEZONE)
    return timestamp.tz_convert(TIMEZONE)


def _response_candles(response):
    if not isinstance(response, dict):
        return []
    return response.get("data", {}).get("candles", [])


def _deduplicate_candles(candles):
    unique = {}
    for candle in candles:
        if not candle or len(candle) < 7:
            continue
        try:
            timestamp = _as_ist_timestamp(candle[0])
        except (TypeError, ValueError):
            continue
        unique[timestamp.value] = [timestamp.isoformat(), *candle[1:]]
    return [unique[key] for key in sorted(unique)]


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


def _is_regular_session_candle(candle, session_date):
    timestamp = _as_ist_timestamp(candle[0])
    local_time = timestamp.timetz().replace(tzinfo=None)
    return (
        timestamp.date() == session_date
        and SESSION_START <= local_time < SESSION_END
    )


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


def is_regular_session(now):
    now = _as_ist_timestamp(now)
    local_time = now.timetz().replace(tzinfo=None)
    return (
        now.weekday() < 5
        and SESSION_START <= local_time <= SESSION_END
    )


class SignalMonitor:
    def __init__(
        self,
        stocks=None,
        intraday_fetcher=None,
        historical_fetcher=None,
        output=None,
        poll_interval_seconds=POLL_INTERVAL_SECONDS,
    ):
        self.stocks = stocks if stocks is not None else STOCKS
        self.intraday_fetcher = intraday_fetcher or get_intraday_candles
        self.historical_fetcher = historical_fetcher or get_historical_candles
        self.output = output if output is not None else print
        self.poll_interval_seconds = poll_interval_seconds

        self.last_processed = {}
        self.last_signal_at = {}
        self.historical_cache = {}
        self.historical_cache_date = {}

    def _log_error(self, stock, now, error):
        self.output(
            f"[{now.strftime('%H:%M')}] {stock}: "
            f"scan failed ({type(error).__name__})"
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
            if cursor.weekday() < 5:
                response = self.historical_fetcher(
                    instrument_key,
                    cursor.isoformat(),
                    interval=INTERVAL_MINUTES,
                )
                candles.extend(_response_candles(response))
                attempts += 1
            cursor -= timedelta(days=1)

        context = _deduplicate_candles(candles)
        self.historical_cache[stock] = context
        self.historical_cache_date[stock] = session_date
        return context

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
        intraday = _response_candles(response)
        today_candles = [
            candle
            for candle in intraday
            if _is_regular_session_candle(candle, now.date())
        ]
        completed_intraday = _completed_candles(today_candles, now)
        if not completed_intraday:
            return [], False

        completed_intraday = _deduplicate_candles(completed_intraday)
        latest_timestamp = _as_ist_timestamp(completed_intraday[-1][0])
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

        try:
            context = self._load_historical_context(
                stock,
                instrument_key,
                now.date(),
            )
        except Exception as error:
            self.historical_cache[stock] = []
            self.historical_cache_date[stock] = now.date()
            self._log_error(stock, now, error)
            context = []

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

            if is_new_event:
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
        return alerts, True

    def scan_once(self, now=None):
        now = _as_ist_timestamp(now or datetime.now(TIMEZONE))
        if not is_regular_session(now):
            return []

        alerts = []
        processed_any = False
        for stock, instrument_key in self.stocks.items():
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

    def run(self):
        while True:
            now = datetime.now(TIMEZONE)
            if is_regular_session(now):
                self.scan_once(now)
            sleep(self.poll_interval_seconds)


def main():
    SignalMonitor().run()


if __name__ == "__main__":
    main()
