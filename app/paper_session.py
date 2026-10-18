"""Manually started, read-only market-data / paper-only V1 validator."""
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
import hashlib
import logging
import math
import os
import time as time_module

import pandas as pd

from app.config import get_settings
from app.cost_model import estimate_round_trip_costs
from app.execution_observations import parse_upstox_full_quote
from app.market_analysis import calculate_indicators, candles_to_dataframe
from app.paper_store import PaperStore
from app.reversal_detector import detect_reversal_candidates, is_frozen_reversal
from app.risk_engine import RiskConfig, calculate_position_size
from app.upstox_client import get_intraday_candles, get_quote
from strategy_backtest import STOCKS

TZ = ZoneInfo("Asia/Kolkata")
BAR = pd.Timedelta(minutes=5)
STOP_PCT = 1.0
HORIZON = 6
MAX_FEED_AGE = timedelta(minutes=10)
MAX_EXECUTION_QUOTE_AGE = timedelta(seconds=5)
LOG = logging.getLogger(__name__)
INDEX_ENV = {"NIFTY50": "UPSTOX_NIFTY50_KEY", "NIFTY_BANK": "UPSTOX_NIFTY_BANK_KEY",
             "NIFTY_IT": "UPSTOX_NIFTY_IT_KEY", "SENSEX": "UPSTOX_SENSEX_KEY"}


class QuoteRequestFailure(RuntimeError):
    """Sanitized quote failure carrying only its type and local observation time."""

    def __init__(self, error_type, observed_at):
        super().__init__(error_type)
        self.error_type = error_type
        self.observed_at = observed_at


def paper_mode_enabled(environ=None):
    env = os.environ if environ is None else environ
    value = env.get("PAPER_TRADING")
    return isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}


def _stamp(value):
    stamp = pd.Timestamp(value)
    return stamp.tz_localize(TZ) if stamp.tzinfo is None else stamp.tz_convert(TZ)


def _stock_feed_is_fresh(frame, now):
    """Reject malformed, stale, future, or timezone-invalid latest stock candles."""
    try:
        if frame is None or frame.empty or "timestamp" not in frame.columns:
            return False
        latest = _stamp(frame.iloc[-1]["timestamp"])
        if pd.isna(latest):
            return False
        current = _stamp(now).to_pydatetime()
        latest_end = latest.to_pydatetime() + timedelta(minutes=5)
        age = current - latest_end
        return timedelta(0) <= age <= MAX_FEED_AGE
    except Exception:
        return False


def _buy_execution_quote(response, received_at):
    """Compatibility wrapper returning the existing fresh-ask fill interface."""
    quote = parse_upstox_full_quote(
        response, received_at, max_age_seconds=MAX_EXECUTION_QUOTE_AGE.total_seconds(),
    )
    return quote if quote["quote_valid"] else None


def _execution_quote_is_usable(quote, now):
    if not isinstance(quote, dict):
        return False
    try:
        if quote.get("quote_valid") is False:
            return False
        observed_at = _stamp(quote["observed_at"])
        snapshot_at = _stamp(quote["snapshot_at"])
        price = float(quote["price"])
        quantity = int(quote["quantity"])
        return (math.isfinite(price) and price > 0 and quantity > 0 and
                snapshot_at <= observed_at <= now and
                now - observed_at <= MAX_EXECUTION_QUOTE_AGE)
    except (KeyError, TypeError, ValueError, OverflowError):
        return False


def completed_frame(candles, now):
    """Normalize API candles and exclude every bar whose five minutes have not elapsed."""
    frame = candles_to_dataframe(candles)
    frame["timestamp"] = frame.timestamp.map(_stamp)
    frame = frame.drop_duplicates("timestamp", keep="last").sort_values("timestamp").reset_index(drop=True)
    valid = ((frame.high >= frame[["open", "close", "low"]].max(axis=1)) &
             (frame.low <= frame[["open", "close", "high"]].min(axis=1)) &
             (frame[["open", "high", "low", "close"]] > 0).all(axis=1) & (frame.volume >= 0))
    frame = frame.loc[valid].reset_index(drop=True)
    end = _stamp(now)
    return frame.loc[frame.timestamp + BAR <= end].reset_index(drop=True)


def _signal_id(symbol, timestamp):
    return hashlib.sha256(f"{symbol}|{_stamp(timestamp).isoformat()}|V1".encode()).hexdigest()


def context_decision(timestamp, index_frames, now):
    """Stage 9 frozen 3-of-4 DOWN veto; unavailable/stale context rejects closed."""
    ts = _stamp(timestamp)
    end = _stamp(now)
    directions = []
    for name in INDEX_ENV:
        frame = index_frames.get(name)
        if frame is None:
            return False, f"missing_index_data:{name}", None
        if not isinstance(frame, pd.DataFrame):
            return False, f"malformed_index_data:{name}", None
        if frame.empty:
            return False, f"missing_index_data:{name}", None
        if not {"timestamp", "open", "close"}.issubset(frame.columns):
            return False, f"malformed_index_data:{name}", None
        try:
            timestamps = frame.timestamp.map(_stamp)
            opens = pd.to_numeric(frame.open, errors="coerce")
            closes = pd.to_numeric(frame.close, errors="coerce")
            if (timestamps.isna().any() or not opens.map(math.isfinite).all() or
                    not closes.map(math.isfinite).all() or (opens <= 0).any() or (closes <= 0).any()):
                return False, f"malformed_index_data:{name}", None
        except (TypeError, ValueError, OverflowError):
            return False, f"malformed_index_data:{name}", None
        normalized = frame.assign(timestamp=timestamps, open=opens, close=closes)
        rows = normalized.loc[normalized.timestamp <= ts]
        if rows.empty:
            return False, f"missing_index_data:{name}", None
        row = rows.iloc[-1]
        if row.timestamp > ts or ts - row.timestamp > BAR or end < row.timestamp + BAR or end - (row.timestamp + BAR) > MAX_FEED_AGE:
            return False, f"stale_index_data:{name}", None
        directions.append("up" if row.close > row.open else "down" if row.close < row.open else "flat")
    downs = directions.count("down")
    return downs < 3, f"index_down_count:{downs}", {name: d for name, d in zip(INDEX_ENV, directions)}


def candle_exit(position, bar):
    """Conservative OHLC stop fill: gap open if through stop, otherwise stop price."""
    if _stamp(bar.timestamp).date() != _stamp(position["entry_time"]).date():
        if float(bar.open) <= float(position["stop_loss"]):
            return "gap_stop", float(bar.open)
        return "session_gap_exit", float(bar.open)
    if float(bar.low) <= float(position["stop_loss"]):
        return ("gap_stop" if float(bar.open) <= float(position["stop_loss"]) else "stop",
                min(float(bar.open), float(position["stop_loss"])))
    if position["bars_held"] >= HORIZON:
        return "six_candle_horizon", float(bar.close)
    if _stamp(bar.timestamp).time() >= time(15, 25):
        return "session_exit", float(bar.close)
    return None, None


def daily_limit_reason(daily):
    if int(daily["trades"]) >= 3:
        return "maximum_trades_per_day"
    if float(daily["loss"]) >= float(daily.get("day_start_capital", daily["capital"])) * 0.02:
        return "daily_loss_limit"
    return None


class PaperSession:
    def __init__(self, store=None, initial_capital=100_000.0):
        self.store = store or PaperStore()
        self.initial_capital = initial_capital

    def _record_execution_observation(self, signal_time, symbol, entry_candle_time,
                                      reference_price, quote, now, eligible,
                                      eligibility_reason, quote_error=None,
                                      simulated_entry_price=None, simulated_quantity=None,
                                      observation_time=None, required_quantity=None):
        quote = quote if isinstance(quote, dict) else {}
        try:
            observed_at = _stamp(quote.get("observed_at", observation_time or now))
        except (TypeError, ValueError, OverflowError):
            observed_at = _stamp(now)
        provider_timestamp = quote.get("provider_timestamp", quote.get("snapshot_at"))
        try:
            provider_timestamp = _stamp(provider_timestamp).isoformat() if provider_timestamp is not None else None
        except (TypeError, ValueError, OverflowError):
            provider_timestamp = None

        bid_price = quote.get("bid_price")
        bid_quantity = quote.get("bid_quantity")
        ask_price = quote.get("ask_price", quote.get("price"))
        ask_quantity = quote.get("ask_quantity", quote.get("quantity"))
        ask_vs_reference_price = None
        ask_vs_reference_bps = None
        try:
            if reference_price is not None and ask_price is not None:
                ref_value, ask_value = float(reference_price), float(ask_price)
                if math.isfinite(ref_value) and ref_value > 0 and math.isfinite(ask_value):
                    ask_vs_reference_price = ask_value - ref_value
                    ask_vs_reference_bps = ask_vs_reference_price / ref_value * 10_000
        except (TypeError, ValueError, OverflowError):
            pass
        spread_price = quote.get("spread_price")
        spread_bps = quote.get("spread_bps")
        spread_available = bool(quote.get("spread_available", False))
        spread_reason = quote.get("spread_reason", "quote_unavailable" if not quote else "unavailable")
        if spread_price is None and bid_price is not None and ask_price is not None:
            try:
                bid_value, ask_value = float(bid_price), float(ask_price)
                midpoint = (bid_value + ask_value) / 2
                if math.isfinite(bid_value) and math.isfinite(ask_value) and midpoint > 0 and ask_value >= bid_value:
                    spread_price = ask_value - bid_value
                    spread_bps = spread_price / midpoint * 10_000
                    spread_available, spread_reason = True, "available"
                else:
                    spread_reason = "crossed_or_invalid_market"
            except (TypeError, ValueError, OverflowError):
                spread_reason = "malformed_depth"

        quote_valid = bool(quote.get("quote_valid", False))
        if quote and "quote_valid" not in quote:
            quote_valid = _execution_quote_is_usable(quote, now)
        quote_reason = quote.get("quote_reason")
        if quote_error:
            quote_reason = f"quote_request_error:{quote_error}"
        elif not quote_reason:
            quote_reason = "valid" if quote_valid else "quote_unavailable"
        quote_age_ms = quote.get("quote_age_ms")
        if quote_age_ms is None and provider_timestamp is not None:
            try:
                quote_age_ms = (observed_at - _stamp(provider_timestamp)).total_seconds() * 1000
            except (TypeError, ValueError, OverflowError):
                quote_age_ms = None

        return self.store.record_execution_observation({
            "signal_id": _signal_id(symbol, signal_time),
            "observed_at": observed_at.isoformat(),
            "source_quote_timestamp": quote.get("source_quote_timestamp"),
            "provider_timestamp": provider_timestamp,
            "provider_timestamp_kind": quote.get("provider_timestamp_kind", "unclassified_provider_timestamp"),
            "source_timestamp_status": quote.get("source_timestamp_status", "not_provided_by_quote_provider"),
            "symbol": symbol,
            "instrument_key": STOCKS.get(symbol),
            "signal_timestamp": _stamp(signal_time).isoformat(),
            "entry_candle_time": _stamp(entry_candle_time).isoformat() if entry_candle_time is not None else None,
            "reference_price": reference_price,
            "ask_vs_reference_price": ask_vs_reference_price,
            "ask_vs_reference_bps": ask_vs_reference_bps,
            "required_quantity": required_quantity,
            "bid_price": bid_price,
            "bid_quantity": bid_quantity,
            "ask_price": ask_price,
            "ask_quantity": ask_quantity,
            "spread_price": spread_price,
            "spread_bps": spread_bps,
            "spread_available": spread_available,
            "spread_reason": spread_reason,
            "quote_age_ms": quote_age_ms,
            "quote_valid": quote_valid,
            "quote_reason": quote_reason,
            "paper_entry_eligible": eligible,
            "eligibility_reason": eligibility_reason,
            "simulated_entry_price": simulated_entry_price,
            "simulated_quantity": simulated_quantity,
        })

    def evaluate(self, stock_frames, index_frames, now, stock_observed_at=None,
                 execution_quote_provider=None):
        """Process completed signals with a fresh observable ask; all fills are simulated."""
        now = _stamp(now)
        stock_observed_at = stock_observed_at or {}
        day = now.date()
        daily = self.store.daily_state(day, self.initial_capital)
        self.store.commit()
        risk_config = RiskConfig(capital=float(daily["capital"]))
        decisions = []
        # Manage an existing position from completed OHLC bars, including gap-through stops.
        position = self.store.get_position()
        if position:
            frame = stock_frames.get(position["symbol"])
            if frame is not None:
                after = _stamp(position.get("last_bar_time", position.get("entry_candle_time", position["entry_time"])))
                later = frame.loc[frame.timestamp > after] if position.get("last_bar_time") else frame.loc[frame.timestamp >= after]
                for _, bar in later.iterrows():
                    position["bars_held"] += 1
                    reason, price = candle_exit(position, bar)
                    if reason:
                        trade = self._close(position, price, reason, bar.timestamp, daily)
                        decisions.append({"decision": "PAPER_EXIT", **trade})
                        position = None
                        break
                    position["last_bar_time"] = bar.timestamp.isoformat()
            if position:
                self.store.set_position(position)
                self.store.commit()

        if position is not None:
            return decisions

        candidates = []
        for symbol, frame in stock_frames.items():
            # Raw frames are retained only for next-candle lookup below; they are
            # not independent instruments and must not enter signal detection.
            if symbol.startswith("__raw__"):
                continue
            if frame is None or len(frame) < 35:
                self.store.record_audit(now.isoformat(), "missing_stock_data", f"insufficient_candles:{symbol}")
                continue
            if not _stock_feed_is_fresh(frame, now):
                self.store.record_audit(now.isoformat(), "stale_stock_data", f"stale_feed:{symbol}")
                continue
            enriched = detect_reversal_candidates(calculate_indicators(frame.copy()), decline_candles=3, min_decline_pct=1.0, rsi_threshold=25.0)
            # Look at recent completed signals while the next five-minute bar is forming;
            # its historical open is only a reference, never the simulated execution price.
            for i in range(max(0, len(enriched) - 3), len(enriched)):
                row = enriched.iloc[i]
                if not is_frozen_reversal(row):
                    continue
                signal_time = _stamp(row.timestamp)
                expected = signal_time + BAR
                open_observed_at = _stamp(stock_observed_at.get(symbol, now))
                if signal_time.time() < time(9, 15) or signal_time.time() >= time(15, 25) or expected.date() != signal_time.date():
                    self.store.record_audit(now.isoformat(), "session_boundary_rejection", f"{symbol}:{signal_time.isoformat()}")
                    self._record_execution_observation(
                        signal_time, symbol, expected, None, None, now, False, "session_boundary",
                    )
                    continue
                raw = stock_frames.get(f"__raw__{symbol}")
                if raw is None:
                    self.store.record_audit(now.isoformat(), "missing_next_candle", f"{symbol}:{expected.isoformat()}")
                    self._record_execution_observation(
                        signal_time, symbol, expected, None, None, now, False, "missing_next_candle",
                    )
                    continue
                next_rows = raw.loc[raw.timestamp == expected]
                if (next_rows.empty or not (expected <= open_observed_at < expected + BAR)
                        or not (expected <= now < expected + BAR)
                        or open_observed_at > now):
                    self.store.record_audit(now.isoformat(), "missing_next_candle", f"{symbol}:{expected.isoformat()}")
                    self._record_execution_observation(
                        signal_time, symbol, expected, None, None, now, False, "missing_or_late_entry_reference",
                    )
                    continue
                if not math.isfinite(float(next_rows.iloc[0].open)) or float(next_rows.iloc[0].open) <= 0:
                    self.store.record_audit(now.isoformat(), "invalid_next_candle", f"{symbol}:{expected.isoformat()}")
                    self._record_execution_observation(
                        signal_time, symbol, expected, None, None, now, False, "invalid_next_candle_open",
                    )
                    continue
                # Check three same-day signal intervals for gaps; overnight rolling window is retained.
                window = enriched.iloc[max(0, i-3):i+1].timestamp.tolist()
                if len(window) < 4 or any(b.date() == a.date() and b-a != BAR for a, b in zip(window, window[1:])):
                    self.store.record_audit(
                        now.isoformat(), "signal_gap_rejection", f"{symbol}:{signal_time.isoformat()}",
                    )
                    self._record_execution_observation(
                        signal_time, symbol, expected, None, None, now, False, "signal_candle_gap",
                    )
                    continue
                reference_price = float(next_rows.iloc[0].open)
                if not math.isfinite(reference_price) or reference_price <= 0:
                    self.store.record_audit(now.isoformat(), "invalid_next_candle", f"{symbol}:{expected.isoformat()}")
                    self._record_execution_observation(
                        signal_time, symbol, expected, None, None, now, False, "invalid_next_candle_open",
                    )
                    continue
                quote_error = None
                quote_error_time = now
                if execution_quote_provider is None:
                    quote = None
                else:
                    try:
                        quote = execution_quote_provider(symbol)
                    except Exception as exc:
                        quote_error = getattr(exc, "error_type", type(exc).__name__)
                        quote_error_time = getattr(exc, "observed_at", now)
                        self.store.record_audit(
                            _stamp(quote_error_time).isoformat(), "execution_quote_error",
                            f"{symbol}:{quote_error}",
                        )
                        LOG.warning("paper_execution_quote_error symbol=%s exception=%s",
                                    symbol, quote_error)
                        quote = None
                if quote is not None:
                    try:
                        now = max(now, _stamp(quote["observed_at"]))
                    except (KeyError, TypeError, ValueError):
                        pass
                if not (expected <= now < expected + BAR):
                    self.store.record_audit(now.isoformat(), "late_execution_quote", f"{symbol}:{expected.isoformat()}")
                    self._record_execution_observation(
                        signal_time, symbol, expected, reference_price, quote, now, False,
                        "late_execution_quote", quote_error,
                    )
                    continue
                if not _execution_quote_is_usable(quote, now):
                    self.store.record_audit(now.isoformat(), "unavailable_execution_price", f"{symbol}:{expected.isoformat()}")
                    unavailable_reason = (quote.get("quote_reason") if isinstance(quote, dict) else None)
                    self._record_execution_observation(
                        signal_time, symbol, expected, reference_price, quote, now, False,
                        unavailable_reason or (f"quote_request_error:{quote_error}" if quote_error
                                               else "unavailable_execution_price"), quote_error,
                        observation_time=quote_error_time,
                    )
                    continue
                candidates.append((signal_time, symbol, row, reference_price, quote, quote_error))
        for candidate_index, (signal_time, symbol, signal, reference_price, quote, quote_error) in enumerate(
            sorted(candidates, key=lambda x: (x[0], x[1]))
        ):
            sid = _signal_id(symbol, signal_time)
            seen = self.store.db.execute("SELECT 1 FROM decisions WHERE signal_id=?", (sid,)).fetchone()
            if seen:
                self.store.record_audit(now.isoformat(), "duplicate_signal", f"already_seen:{symbol}", {"signal_id": sid})
                self._record_execution_observation(
                    signal_time, symbol, signal_time + BAR, reference_price, quote, now, False,
                    "duplicate_signal", quote_error,
                )
                self.store.commit()
                continue
            ok, why, ctx = context_decision(signal_time, index_frames, now)
            outcome = "eligible" if ok else "rejected"
            if not ok:
                self._record_execution_observation(
                    signal_time, symbol, signal_time + BAR, reference_price, quote, now, False,
                    why, quote_error,
                )
                if self.store.record_decision(sid, signal_time.isoformat(), symbol, outcome, why, {"context": ctx}):
                    decisions.append({"decision": "REJECTED", "symbol": symbol, "reason": why})
                self.store.commit()
                continue
            daily = self.store.daily_state(day, self.initial_capital)
            self.store.commit()
            risk_config = RiskConfig(capital=float(daily["capital"]))
            limit = daily_limit_reason(daily)
            if limit:
                why = limit
                self._record_execution_observation(
                    signal_time, symbol, signal_time + BAR, reference_price, quote, now, False,
                    why, quote_error,
                )
                self.store.record_decision(sid, signal_time.isoformat(), symbol, "rejected", why, {"context": ctx})
                self.store.commit()
                decisions.append({"decision": "REJECTED", "symbol": symbol, "reason": why})
                continue
            entry = float(quote["price"])
            stop = entry * (1.0 - STOP_PCT / 100.0)
            size = calculate_position_size(risk_config, entry, stop)
            if size.quantity <= 0 or int(quote["quantity"]) < size.quantity:
                reason = "no_whole_share_capacity" if size.quantity <= 0 else "insufficient_ask_quantity"
                self._record_execution_observation(
                    signal_time, symbol, signal_time + BAR, reference_price, quote, now, False,
                    reason, quote_error,
                    required_quantity=size.quantity,
                )
                self.store.record_decision(sid, signal_time.isoformat(), symbol, "rejected", reason, {})
                self.store.commit()
                decisions.append({"decision": "REJECTED", "symbol": symbol, "reason": reason})
                continue
            pos = {"signal_id": sid, "symbol": symbol, "quantity": size.quantity,
                   "entry_price": entry, "entry_reference_price": reference_price,
                   "entry_reference_kind": "next_candle_open",
                   "execution_quote_time": quote["snapshot_at"].isoformat(),
                   "execution_observed_at": quote["observed_at"].isoformat(),
                   "stop_loss": stop, "signal_time": signal_time.isoformat(),
                   "entry_candle_time": expected.isoformat(), "entry_time": now.isoformat(),
                   "entry_observed_at": quote["observed_at"].isoformat(),
                   "bars_held": 0, "context": ctx, "strategy_version": "V1"}
            savepoint_created = False
            try:
                # The simulated fill is valid only if its observation can be persisted.
                # One SQLite savepoint makes the decision, position, daily counter, and
                # required observation an all-or-nothing unit.
                self.store.db.execute("SAVEPOINT paper_fill")
                savepoint_created = True
                filled = self.store.record_decision(
                    sid, signal_time.isoformat(), symbol, "paper_filled",
                    "simulated_observed_ask", pos,
                )
                if filled:
                    self.store.set_position(pos)
                    self.store.update_daily(
                        day.isoformat(), daily["loss"], daily["trades"] + 1, daily["capital"],
                    )
                    self._record_execution_observation(
                        signal_time, symbol, signal_time + BAR, reference_price, quote, now, True,
                        "simulated_paper_fill", quote_error, entry, size.quantity,
                        required_quantity=size.quantity,
                    )
                self.store.db.execute("RELEASE SAVEPOINT paper_fill")
                savepoint_created = False
            except Exception as exc:
                if savepoint_created:
                    try:
                        self.store.db.execute("ROLLBACK TO SAVEPOINT paper_fill")
                        self.store.db.execute("RELEASE SAVEPOINT paper_fill")
                    except Exception:
                        self.store.db.rollback()
                reason = "paper_fill_persistence_failed"
                error_type = type(exc).__name__
                self.store.record_audit(
                    now.isoformat(), "paper_fill_persistence_failure", error_type,
                    {"signal_id": sid},
                )
                self.store.record_decision(
                    sid, signal_time.isoformat(), symbol, "rejected", reason,
                    {"context": ctx, "exception_type": error_type},
                )
                self.store.commit()
                LOG.warning("paper_entry_rejected symbol=%s reason=%s exception=%s",
                            symbol, reason, error_type)
                decisions.append({"decision": "REJECTED", "symbol": symbol, "reason": reason})
                continue
            if filled:
                self.store.commit()
                decisions.append({"decision": "PAPER_FILL", **pos})
                ordered_candidates = sorted(candidates, key=lambda x: (x[0], x[1]))
                for pending in ordered_candidates[candidate_index + 1:]:
                    pending_signal_time, pending_symbol, _, pending_reference, pending_quote, pending_error = pending
                    self._record_execution_observation(
                        pending_signal_time, pending_symbol, pending_signal_time + BAR,
                        pending_reference, pending_quote, now, False,
                        "position_opened_earlier_this_scan", pending_error,
                    )
                break
            self.store.record_audit(now.isoformat(), "duplicate_signal", f"already_seen:{symbol}", {"signal_id": sid})
            self._record_execution_observation(
                signal_time, symbol, expected, reference_price, quote, now, False,
                "duplicate_signal", quote_error,
            )
            self.store.commit()
        return decisions

    def _close(self, position, exit_price, reason, exit_time, daily):
        gross = (exit_price - position["entry_price"]) * position["quantity"]
        costs = estimate_round_trip_costs(position["entry_price"], exit_price, position["quantity"], _stamp(exit_time).date())
        trade = {**position, "exit_time": _stamp(exit_time).isoformat(), "exit_price": exit_price,
                 "gross_pnl": gross, "estimated_costs": costs["total_cost"], "cost_breakdown": costs,
                 "net_pnl": gross-costs["total_cost"], "exit_reason": reason,
                 "strategy_version": "V1", "paper_fill": True}
        self.store.db.execute("BEGIN IMMEDIATE")
        self.store.save_trade(position["signal_id"], position["signal_id"], trade)
        loss = daily["loss"] + max(0.0, -trade["net_pnl"])
        capital = float(daily["capital"]) + trade["net_pnl"]
        self.store.update_daily(_stamp(exit_time).date().isoformat(), loss, daily["trades"], capital)
        self.store.set_position(None)
        self.store.commit()
        LOG.info("Paper trade closed: %s net=%s reason=%s", position["symbol"], trade["net_pnl"], reason)
        return trade


def _index_keys(environ=None):
    env = os.environ if environ is None else environ
    missing = [key for key in INDEX_ENV.values() if not env.get(key)]
    if missing:
        raise RuntimeError("Missing required four-index paper context configuration: " + ", ".join(missing))
    return {name: env[key] for name, key in INDEX_ENV.items()}


def run():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if not paper_mode_enabled():
        raise RuntimeError("PAPER_TRADING must be explicitly set to true; paper runner fails closed")
    settings = get_settings()
    if not settings.upstox_access_token:
        raise RuntimeError("UPSTOX_ACCESS_TOKEN is required for read-only market data")
    RiskConfig(capital=settings.paper_initial_capital)
    indexes = _index_keys()
    store = PaperStore()
    runner = PaperSession(store, settings.paper_initial_capital)
    print("Paper-only V1 session started. Upstox access is read-only; fills are simulated paper fills.", flush=True)
    scan_number = 0
    try:
        while True:
            scan_number += 1
            scan_clock = time_module.monotonic()
            scan_started = datetime.now(TZ)
            stock_requests = stock_successes = stock_failures = 0
            index_requests = index_successes = index_failures = 0
            latest_completed = {}
            LOG.info("paper_scan_start scan=%d at=%s", scan_number, scan_started.isoformat())
            frames, raw = {}, {}
            stock_observed_at = {}
            for symbol, key in STOCKS.items():
                stock_requests += 1
                try:
                    response = get_intraday_candles(key, 5)
                    candles = (response.get("data") or {}).get("candles", [])
                    if not isinstance(candles, list):
                        raise ValueError("invalid_candle_response")
                    stock_observed_at[symbol] = datetime.now(TZ)
                    raw[symbol] = candles_to_dataframe(candles) if candles else pd.DataFrame()
                    if not raw[symbol].empty:
                        raw[symbol]["timestamp"] = raw[symbol].timestamp.map(_stamp)
                        frames[symbol] = completed_frame(candles, stock_observed_at[symbol])
                        frames[f"__raw__{symbol}"] = raw[symbol]
                        latest_completed[symbol] = (
                            frames[symbol].iloc[-1].timestamp.isoformat()
                            if not frames[symbol].empty else None
                        )
                    else:
                        frames[symbol] = pd.DataFrame()
                        latest_completed[symbol] = None
                    stock_successes += 1
                except Exception as exc:
                    stock_failures += 1
                    frames[symbol] = pd.DataFrame()
                    latest_completed[symbol] = None
                    store.record_audit(datetime.now(TZ).isoformat(), "market_data_error", f"{symbol}:{type(exc).__name__}")
                    LOG.warning("paper_market_data_error scan=%d category=stock symbol=%s exception=%s",
                                scan_number, symbol, type(exc).__name__)
            idx_frames = {}
            for name, key in indexes.items():
                index_requests += 1
                try:
                    response = get_intraday_candles(key, 5)
                    candles = (response.get("data") or {}).get("candles", [])
                    if not isinstance(candles, list):
                        raise ValueError("invalid_candle_response")
                    index_observed_at = datetime.now(TZ)
                    idx_frames[name] = completed_frame(candles, index_observed_at) if candles else pd.DataFrame()
                    latest_completed[name] = (
                        idx_frames[name].iloc[-1].timestamp.isoformat()
                        if not idx_frames[name].empty else None
                    )
                    index_successes += 1
                except Exception as exc:
                    index_failures += 1
                    idx_frames[name] = pd.DataFrame()
                    store.record_audit(datetime.now(TZ).isoformat(), "market_data_error", f"{name}:{type(exc).__name__}")
                    LOG.warning("paper_market_data_error scan=%d category=index index=%s exception=%s",
                                scan_number, name, type(exc).__name__)
            now = datetime.now(TZ)
            try:
                def execution_quote(symbol):
                    try:
                        quote_response = get_quote(STOCKS[symbol])
                    except Exception as exc:
                        raise QuoteRequestFailure(type(exc).__name__, datetime.now(TZ)) from None
                    received_at = datetime.now(TZ)
                    return parse_upstox_full_quote(
                        quote_response, received_at,
                        max_age_seconds=MAX_EXECUTION_QUOTE_AGE.total_seconds(),
                    )

                for result in runner.evaluate(frames, idx_frames, now, stock_observed_at, execution_quote):
                    if result.get("decision") == "REJECTED":
                        LOG.info("paper_entry_rejected scan=%d symbol=%s reason=%s",
                                 scan_number, result.get("symbol", "unknown"), result.get("reason", "unknown"))
                    else:
                        LOG.info("paper_decision scan=%d decision=%s symbol=%s",
                                 scan_number, result.get("decision", "unknown"), result.get("symbol", "unknown"))
                    print(result, flush=True)
                store.write_daily_summary(now.date())
                store.commit()
            except Exception as exc:
                LOG.warning("Paper scan failed (%s); no new entry is permitted on this scan", type(exc).__name__)
                store.record_audit(now.isoformat(), "api_or_scan_error", type(exc).__name__)
                store.commit()
            LOG.info(
                "paper_scan_end scan=%d at=%s duration_seconds=%.2f "
                "stock_requests=%d stock_successes=%d stock_failures=%d "
                "index_requests=%d index_successes=%d index_failures=%d "
                "latest_completed_candles=%s",
                scan_number, datetime.now(TZ).isoformat(), time_module.monotonic() - scan_clock,
                stock_requests, stock_successes, stock_failures,
                index_requests, index_successes, index_failures, latest_completed,
            )
            time_module.sleep(60)
    except KeyboardInterrupt:
        print("Paper session stopped by user. SQLite state is retained for restart recovery.")
        store.write_daily_summary(datetime.now(TZ).date())
    except Exception as exc:
        LOG.error("paper_runner_stopped exception=%s; no further entries will be evaluated", type(exc).__name__)
        raise
    finally:
        store.close()


if __name__ == "__main__":
    run()
