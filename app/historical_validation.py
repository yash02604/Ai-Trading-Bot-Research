"""Reproducible, cost-aware historical evaluation of the frozen strategy."""

from collections import Counter
from dataclasses import asdict
from datetime import date
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from app.cost_model import CostAssumptions, estimate_round_trip_costs
from app.market_analysis import REQUIRED_CANDLES, calculate_indicators
from app.candle_integrity import validate_candles
from app.reversal_detector import detect_reversal_candidates, is_frozen_reversal
from app.risk_engine import RiskConfig, RiskState, calculate_position_size, check_trade_allowed
from strategy_backtest import STOCKS


TIMEFRAME = pd.Timedelta(minutes=5)
DEFAULT_DATA = Path(__file__).resolve().parents[1] / "data/research/extended_historical_robustness_candles.csv"


def load_historical_data(path=DEFAULT_DATA):
    """Load local Upstox research candles and explicitly remove invalid OHLC rows."""
    frame = pd.read_csv(path)
    required = {"stock", "timestamp", "open", "high", "low", "close", "volume"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Historical data is missing columns: {sorted(missing)}")
    frame["stock"] = frame["stock"].astype(str).str.upper()
    duplicate_keys = ["instrument_key"] if "instrument_key" in frame else ["stock"]
    integrity = validate_candles(frame, key_columns=duplicate_keys)
    frame = integrity.frame
    expected_keys = {symbol: key for symbol, key in STOCKS.items()}
    if "instrument_key" in frame:
        for pos, row in frame.iterrows():
            expected = expected_keys.get(row.stock)
            if expected is not None and row.instrument_key != expected:
                integrity.errors.append({"row": int(pos), "reason": "instrument_key_symbol_mismatch"})
    duplicate_errors = [e for e in integrity.errors if e["reason"] == "duplicate_instrument_timestamp"]
    if duplicate_errors:
        raise ValueError(f"Historical data has {len(duplicate_errors)} duplicate instrument/timestamp rows")
    row_reasons = {}
    for error in integrity.errors:
        if error.get("row") is not None:
            row_reasons.setdefault(error["row"], set()).add(error["reason"])
    invalid = pd.Series([i in row_reasons for i in range(len(frame))], index=frame.index)
    excluded = frame.loc[invalid, ["stock", "timestamp"]].copy()
    excluded["reason"] = [";".join(sorted(row_reasons[i])) for i in excluded.index]
    valid = frame.loc[~invalid].copy()
    valid = valid[valid.stock.isin(STOCKS)]
    valid = valid.sort_values(["stock", "timestamp"], kind="stable").reset_index(drop=True)
    duplicate_count = int(valid.duplicated([c for c in ("instrument_key", "stock", "timestamp") if c in valid]).sum())
    if duplicate_count:
        raise ValueError(f"Historical data has {duplicate_count} duplicate symbol/timestamp rows")
    unknown = sorted(set(frame.stock) - set(STOCKS))
    audit = {
        "source": str(Path(path).resolve()),
        "raw_rows": int(len(frame)),
        "valid_rows": int(len(valid)),
        "invalid_rows_excluded": int(invalid.sum()),
        "invalid_row_details": [
            {"symbol": row.stock, "timestamp": str(row.timestamp), "reason": row.reason}
            for row in excluded.itertuples(index=False)
        ],
        "duplicate_rows": duplicate_count,
        "zero_volume_rows_retained": int((valid.volume == 0).sum()),
        "validation_warning_counts": dict(Counter(item["reason"] for item in integrity.warnings)),
        "unknown_symbols_ignored": unknown,
        "symbols": sorted(valid.stock.unique().tolist()),
        "timeframe_minutes": 5,
        "start": valid.timestamp.min().isoformat(),
        "end": valid.timestamp.max().isoformat(),
        "candles_by_symbol": {str(k): int(v) for k, v in valid.groupby("stock").size().items()},
        "same_day_gaps_over_5m": 0,
    }
    deltas = valid.groupby("stock").timestamp.diff()
    same_day = valid.timestamp.dt.date.eq(valid.groupby("stock").timestamp.shift().dt.date)
    audit["same_day_gaps_over_5m"] = int(((deltas > TIMEFRAME) & same_day).sum())
    return valid, audit


def prepare_symbol_frames(data):
    """Calculate only backward-looking indicators and frozen candidate flags."""
    frames = {}
    for symbol in STOCKS:
        candles = data.loc[data.stock == symbol].copy()
        if len(candles) < REQUIRED_CANDLES:
            continue
        candles = candles.rename(columns={"oi": "open_interest"})
        if "open_interest" not in candles:
            candles["open_interest"] = 0
        indicators = calculate_indicators(candles.reset_index(drop=True))
        detected = detect_reversal_candidates(
            indicators, decline_candles=3, min_decline_pct=1.0, rsi_threshold=25.0
        )
        detected["frozen_candidate"] = detected.apply(is_frozen_reversal, axis=1)
        frames[symbol] = detected.reset_index(drop=True)
    return frames


def _make_events(frames):
    events = []
    data_rejects = Counter()
    signal_counts = Counter()
    priority = {symbol: rank for rank, symbol in enumerate(STOCKS)}
    for symbol, frame in frames.items():
        for index in frame.index[frame.frozen_candidate]:
            signal_counts[symbol] += 1
            signal_time = frame.at[index, "timestamp"]
            signal_window = frame.loc[index - 3:index, "timestamp"].tolist() if index >= 3 else []
            # Preserve the strategy's existing observation-bar rule across overnight
            # sessions, while rejecting missing bars inside a trading session.
            contiguous = len(signal_window) == 4 and all(
                right.date() != left.date() or right - left == TIMEFRAME
                for left, right in zip(signal_window, signal_window[1:])
            )
            entry_index = index + 1
            if not contiguous:
                data_rejects[(symbol, "intrasession_signal_data_gap")] += 1
                continue
            if entry_index >= len(frame):
                data_rejects[(symbol, "no_next_candle")] += 1
                continue
            entry_time = frame.at[entry_index, "timestamp"]
            if entry_time.date() != signal_time.date() or entry_time - signal_time != TIMEFRAME:
                data_rejects[(symbol, "entry_candle_gap_or_session_end")] += 1
                continue
            events.append({
                "symbol": symbol,
                "signal_index": int(index),
                "entry_index": int(entry_index),
                "signal_timestamp": signal_time,
                "entry_timestamp": entry_time,
                "decline_pct": float(frame.at[index, "decline_pct"]),
                "rsi_14": float(frame.at[index, "rsi_14"]),
                "priority": priority[symbol],
            })
    events.sort(key=lambda event: (event["signal_timestamp"], event["priority"]))
    return events, signal_counts, data_rejects


def _simulate_exit(frame, event, stop_pct, horizon):
    signal_index, entry_index = event["signal_index"], event["entry_index"]
    signal_day = event["signal_timestamp"].date()
    entry = float(frame.at[entry_index, "open"])
    stop = entry * (1.0 - stop_pct / 100.0)
    last_index = min(entry_index + horizon - 1, len(frame) - 1)
    exit_index = entry_index
    exit_price = None
    exit_reason = "horizon_exit"
    session_end = False
    for candle_index in range(entry_index, last_index + 1):
        candle = frame.iloc[candle_index]
        if candle.timestamp.date() != signal_day:
            session_end = True
            break
        if float(candle.low) <= stop:
            exit_index = candle_index
            exit_price = min(float(candle.open), stop)
            exit_reason = "gap_exit" if float(candle.open) <= stop else "stop_exit"
            break
        exit_index = candle_index
    if exit_price is None:
        exit_price = float(frame.at[exit_index, "close"])
        if session_end:
            exit_reason = "session_exit"
        elif entry_index + horizon - 1 >= len(frame):
            exit_reason = "data_end_exit"
    exit_time = frame.at[exit_index, "timestamp"]
    excursion_frame = frame.iloc[entry_index:exit_index + 1]
    highest_price = float(excursion_frame["high"].max())
    lowest_price = float(excursion_frame["low"].min())
    mfe_pct = ((highest_price - entry) / entry) * 100.0
    mae_pct = ((lowest_price - entry) / entry) * 100.0


    return {
        "symbol": event["symbol"],
        "signal_timestamp": event["signal_timestamp"],
        "entry_timestamp": frame.at[entry_index, "timestamp"],
        "exit_timestamp": exit_time,
        "decline_pct": event["decline_pct"],
        "rsi_14": event["rsi_14"],
        "entry_price": entry,
        "exit_price": exit_price,
        "stop_loss": stop,
        "mfe_price": highest_price,
        "mae_price": lowest_price,
        "mfe_pct": mfe_pct,
        "mae_pct": mae_pct,
        "target_price": None,
        "quantity": None,
        "planned_risk": None,
        "capital_exposure": None,
        "gross_pnl": None,
        "pnl": None,
        "net_pnl": None,
        "exit_reason": exit_reason,
        "holding_minutes": max(0.0, (exit_time - frame.at[entry_index, "timestamp"]).total_seconds() / 60),
        "book": None,
    }


def _max_streak(values, winning):
    longest = current = 0
    for value in values:
        if (value > 0) == winning and value != 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def calculate_metrics(trades, initial_capital, trading_days, signals=None, book="portfolio"):
    frame = pd.DataFrame(trades)
    if frame.empty:
        frame = pd.DataFrame(columns=["gross_pnl", "net_pnl", "pnl", "holding_minutes", "planned_risk", "capital_exposure"])
    net = pd.to_numeric(frame.net_pnl, errors="coerce").fillna(0)
    gross = pd.to_numeric(frame.gross_pnl, errors="coerce").fillna(0)
    winners = net[net > 0]
    losers = net[net < 0]
    gross_winners, gross_losers = gross[gross > 0], gross[gross < 0]
    equity = initial_capital + net.cumsum()
    peaks = pd.concat([pd.Series([initial_capital]), equity.reset_index(drop=True)], ignore_index=True).cummax()
    equity_with_start = pd.concat([pd.Series([initial_capital]), equity.reset_index(drop=True)], ignore_index=True)
    drawdowns = equity_with_start / peaks - 1.0
    net_positive = float(winners.sum())
    net_negative = float(-losers.sum())
    gross_profit = float(gross_winners.sum())
    gross_loss = float(-gross_losers.sum())
    max_loss = float(abs(losers.min())) if not losers.empty else 0.0
    avg_win = float(winners.mean()) if not winners.empty else 0.0
    costs = frame.get("total_cost", pd.Series(dtype=float)).fillna(0)
    cost_breakdown = {
        column: float(frame[column].fillna(0).sum())
        for column in ("brokerage", "stt", "exchange", "ipft", "sebi", "stamp_duty", "gst", "slippage")
        if column in frame
    }
    entry_times = pd.to_datetime(frame.get("entry_timestamp", pd.Series(dtype="datetime64[ns]")))
    gap_days = entry_times.sort_values().diff().dt.total_seconds().div(86400).dropna()
    return {
        "book": book,
        "total_trades": int(len(frame)),
        "winning_trades": int((net > 0).sum()),
        "losing_trades": int((net < 0).sum()),
        "breakeven_trades": int((net == 0).sum()),
        "win_rate": float((net > 0).mean()) if len(net) else 0.0,
        "gross_win_rate": float((gross > 0).mean()) if len(gross) else 0.0,
        "average_winning_trade": avg_win,
        "average_losing_trade": float(losers.mean()) if not losers.empty else 0.0,
        "largest_winner": float(winners.max()) if not winners.empty else 0.0,
        "largest_loser": float(losers.min()) if not losers.empty else 0.0,
        "average_holding_minutes": float(frame.holding_minutes.mean()) if len(frame) else 0.0,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "gross_pnl": float(gross.sum()),
        "gross_return_pct": float(gross.sum() / initial_capital * 100) if initial_capital else 0.0,
        "net_profit": net_positive,
        "net_loss": net_negative,
        "net_pnl": float(net.sum()),
        "net_return_pct": float(net.sum() / initial_capital * 100) if initial_capital else 0.0,
        "average_pnl_per_trade": float(net.mean()) if len(net) else 0.0,
        "profit_factor": net_positive / net_negative if net_negative else (None if not net_positive else "Infinity"),
        "gross_profit_factor": gross_profit / gross_loss if gross_loss else (None if not gross_profit else "Infinity"),
        "maximum_drawdown_pct": float(drawdowns.min() * 100) if len(drawdowns) else 0.0,
        "maximum_consecutive_losses": _max_streak(net.tolist(), winning=False),
        "maximum_consecutive_wins": _max_streak(net.tolist(), winning=True),
        "initial_capital": float(initial_capital),
        "ending_capital": float(initial_capital + net.sum()),
        "maximum_risk_per_trade_pct": 1.0,
        "maximum_planned_risk": float(frame.planned_risk.max()) if len(frame) else 0.0,
        "maximum_capital_exposure": float(frame.capital_exposure.max()) if len(frame) else 0.0,
        "maximum_capital_exposure_pct": float(frame.capital_exposure.max() / initial_capital * 100) if len(frame) and initial_capital else 0.0,
        "total_estimated_costs": float(costs.sum()),
        "estimated_cost_breakdown": cost_breakdown,
        "trades_per_trading_day": float(len(frame) / trading_days) if trading_days else 0.0,
        "longest_gap_between_entries_days": float(gap_days.max()) if len(gap_days) else None,
        "trades_to_recover_largest_loss_at_average_winner": int(math.ceil(max_loss / avg_win)) if max_loss and avg_win > 0 else None,
        "top_1_trade_share_of_net_profit_pct": float(frame.sort_values("net_pnl", ascending=False).head(1).net_pnl.clip(lower=0).sum() / net_positive * 100) if net_positive else 0.0,
        "top_5_trades_share_of_net_profit_pct": float(frame.sort_values("net_pnl", ascending=False).head(5).net_pnl.clip(lower=0).sum() / net_positive * 100) if net_positive else 0.0,
        "signals": signals or {},
    }


def simulate_book(frames, initial_capital=100_000.0, stop_pct=1.0, horizon=6, costs=None, book="portfolio"):
    """Simulate a shared, single-position book or one symbol's standalone book."""
    costs = costs or CostAssumptions()
    events, raw_signals, data_rejects = _make_events(frames)
    symbol_counts = {symbol: Counter(frozen_signals=count) for symbol, count in raw_signals.items()}
    book_trades = []
    reject_reasons = Counter()
    risk_config = RiskConfig(capital=initial_capital)
    capital = initial_capital
    last_exit = None
    daily_trades, daily_losses = Counter(), Counter()
    max_exposure = 0.0
    for event in events:
        symbol = event["symbol"]
        if book.startswith("standalone:") and symbol != book.split(":", 1)[1]:
            continue
        entry_time = event["entry_timestamp"]
        if last_exit is not None and entry_time <= last_exit:
            reject_reasons["maximum_open_positions"] += 1
            symbol_counts[symbol]["risk_rejected"] += 1
            continue
        day = entry_time.date()
        if capital <= 0:
            reject_reasons["insufficient_capital"] += 1
            symbol_counts[symbol]["risk_rejected"] += 1
            continue
        active_config = RiskConfig(
            capital=capital,
            max_risk_per_trade=risk_config.max_risk_per_trade,
            max_daily_loss=risk_config.max_daily_loss,
            max_positions=risk_config.max_positions,
            max_trades_per_day=risk_config.max_trades_per_day,
        )
        state = RiskState(daily_loss=daily_losses[day], trades_today=daily_trades[day])
        allowed, reason = check_trade_allowed(active_config, state)
        if not allowed:
            reject_reasons[reason] += 1
            symbol_counts[symbol]["risk_rejected"] += 1
            continue
        frame = frames[symbol]
        entry = float(frame.at[event["entry_index"], "open"])
        stop = entry * (1.0 - stop_pct / 100.0)
        size = calculate_position_size(active_config, entry, stop)
        if size.quantity <= 0:
            reject_reasons["insufficient_capital_for_one_share"] += 1
            symbol_counts[symbol]["risk_rejected"] += 1
            continue
        trade = _simulate_exit(frame, event, stop_pct, horizon)
        trade["quantity"] = size.quantity
        trade["planned_risk"] = size.planned_risk
        trade["capital_exposure"] = size.capital_required
        trade["gross_pnl"] = (trade["exit_price"] - trade["entry_price"]) * size.quantity
        cost_values = estimate_round_trip_costs(
            trade["entry_price"], trade["exit_price"], size.quantity,
            trade["entry_timestamp"], assumptions=costs,
        )
        trade.update(cost_values)
        trade["pnl"] = trade["gross_pnl"]
        trade["net_pnl"] = trade["gross_pnl"] - trade["total_cost"]
        trade["r_multiple"] = trade["net_pnl"] / size.planned_risk if size.planned_risk else 0.0
        trade["book"] = book
        trade["ai_replay"] = "unavailable"
        book_trades.append(trade)
        symbol_counts[symbol]["risk_accepted"] += 1
        symbol_counts[symbol]["quantitative_buy_entries"] += 1
        max_exposure = max(max_exposure, size.capital_required)
        capital += trade["net_pnl"]
        if trade["net_pnl"] < 0:
            daily_losses[day] += abs(trade["net_pnl"])
        daily_trades[day] += 1
        last_exit = trade["exit_timestamp"]
    for symbol in frames:
        symbol_counts[symbol]["data_rejected"] = sum(
            count for (s, _), count in data_rejects.items() if s == symbol
        )
        symbol_counts[symbol]["sell_signals"] = 0  # Existing strategy is long-only.
    used_symbols = sorted(frames)
    trading_days = 0
    if used_symbols:
        date_sets = [set(frames[s].timestamp.dt.date) for s in used_symbols]
        trading_days = len(set.union(*date_sets))
    counts = {
        "frozen_reversal_signals": int(sum(raw_signals.values())),
        "risk_accepted_entries": len(book_trades),
        "risk_rejected_signals": int(sum(reject_reasons.values())),
        "data_rejected_signals": int(sum(data_rejects.values())),
        "ai_confirmed_buy_signals": None,
        "ai_rejected_signals": None,
        "sell_signals": 0,
        "hold_signals": None,
        "ai_replay": "unavailable; quantitative/risk backtest is reported independently",
        "risk_rejection_reasons": dict(reject_reasons),
        "data_rejection_reasons": {f"{symbol}:{reason}": int(value) for (symbol, reason), value in data_rejects.items()},
        "per_symbol": {s: dict(c) for s, c in symbol_counts.items()},
    }
    metrics = calculate_metrics(book_trades, initial_capital, trading_days, counts, book)
    metrics["maximum_capital_exposure"] = max_exposure
    metrics["maximum_capital_exposure_pct"] = max_exposure / initial_capital * 100 if initial_capital else 0.0
    return book_trades, metrics, counts


def build_market_regimes(data):
    """Define trend/volatility regimes using only information through prior day."""
    daily = data.copy()
    daily["_date"] = daily.timestamp.dt.date
    daily = daily.sort_values("timestamp").groupby(["stock", "_date"], sort=True).tail(1)
    closes = daily.pivot(index="_date", columns="stock", values="close").sort_index()
    returns = closes.pct_change(fill_method=None)
    basket = returns.mean(axis=1, skipna=True)
    trend_60 = (1.0 + basket).rolling(60, min_periods=40).apply(np.prod, raw=True).sub(1).shift(1)
    volatility_20 = basket.rolling(20, min_periods=15).std().mul(np.sqrt(252)).shift(1)
    rows = []
    for day in closes.index:
        trend = trend_60.get(day)
        vol = volatility_20.get(day)
        if pd.isna(trend) or pd.isna(vol):
            trend_label, vol_label = "warmup", "warmup"
        else:
            trend_label = "bullish" if trend > 0.05 else "bearish" if trend < -0.05 else "sideways"
            vol_label = "high_volatility" if vol >= 0.20 else "low_volatility"
        rows.append({"date": day, "trend_regime": trend_label, "volatility_regime": vol_label,
                     "prior_60_session_return_pct": None if pd.isna(trend) else float(trend * 100),
                     "prior_20_session_annualized_volatility_pct": None if pd.isna(vol) else float(vol * 100)})
    return pd.DataFrame(rows)


def add_trade_regimes(trades, regimes):
    if not trades:
        return trades
    mapping = regimes.set_index("date").to_dict("index")
    for trade in trades:
        regime = mapping.get(pd.Timestamp(trade["entry_timestamp"]).date(), {})
        trade.update({
            "trend_regime": regime.get("trend_regime", "unclassified"),
            "volatility_regime": regime.get("volatility_regime", "unclassified"),
            "market_regime_return_pct": regime.get("prior_60_session_return_pct"),
            "market_regime_volatility_pct": regime.get("prior_20_session_annualized_volatility_pct"),
        })
    return trades


def metrics_by_group(trades, initial_capital, trading_days, group_key, book_prefix):
    groups = {}
    frame = pd.DataFrame(trades)
    if frame.empty or group_key not in frame:
        return groups
    for key, part in frame.groupby(group_key, dropna=False):
        groups[str(key)] = calculate_metrics(part.to_dict("records"), initial_capital, trading_days, book=f"{book_prefix}:{key}")
    return groups
