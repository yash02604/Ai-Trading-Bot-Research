from collections import Counter
from datetime import time
from pathlib import Path
import sys

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.cost_model import CostAssumptions, estimate_round_trip_costs
from app.historical_validation import (
    _make_events,
    calculate_metrics,
    load_historical_data,
)
from app.market_analysis import calculate_indicators
from app.reversal_detector import detect_reversal_candidates, is_frozen_reversal
from app.risk_engine import RiskConfig, RiskState, calculate_position_size, check_trade_allowed
from strategy_backtest import STOCKS


RESEARCH_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = RESEARCH_DIR
HISTORICAL_DATA = PROJECT_ROOT / "data/research/extended_historical_robustness_candles.csv"
STAGE9_TRADES = PROJECT_ROOT / "backtests/stage9_candidate/trades.csv"
CONTEXT_DIR = PROJECT_ROOT / "data/research/market_context"
CONTEXT_FILES = {
    "NIFTY50": CONTEXT_DIR / "nifty50_5min.csv",
    "NIFTY_BANK": CONTEXT_DIR / "nifty_bank_5min.csv",
    "NIFTY_IT": CONTEXT_DIR / "nifty_it_5min.csv",
    "SENSEX": CONTEXT_DIR / "sensex_5min.csv",
}

INITIAL_CAPITAL = 100_000.0
STOP_PCT = 1.0
RISK_PER_TRADE = 0.01
DAILY_LOSS_LIMIT = 0.02
MAX_OPEN_POSITIONS = 1
MAX_TRADES_PER_DAY = 3
SLIPPAGE_BPS_PER_SIDE = 5.0
CONTEXT_DOWN_THRESHOLD = 3
BOOTSTRAP_SEED = 20261007

SPLITS = [
    {
        "split": "development_2023_2024",
        "role": "development",
        "start": pd.Timestamp("2023-12-01 00:00:00+05:30"),
        "end": pd.Timestamp("2024-12-31 23:59:59+05:30"),
    },
    {
        "split": "walk_forward_2025_Q1",
        "role": "walk_forward",
        "start": pd.Timestamp("2025-01-01 00:00:00+05:30"),
        "end": pd.Timestamp("2025-03-31 23:59:59+05:30"),
    },
    {
        "split": "walk_forward_2025_Q2",
        "role": "walk_forward",
        "start": pd.Timestamp("2025-04-01 00:00:00+05:30"),
        "end": pd.Timestamp("2025-06-30 23:59:59+05:30"),
    },
    {
        "split": "walk_forward_2025_Q3",
        "role": "walk_forward",
        "start": pd.Timestamp("2025-07-01 00:00:00+05:30"),
        "end": pd.Timestamp("2025-09-30 23:59:59+05:30"),
    },
    {
        "split": "walk_forward_2025_Q4_partial",
        "role": "walk_forward",
        "start": pd.Timestamp("2025-10-01 00:00:00+05:30"),
        "end": pd.Timestamp("2025-12-01 23:59:59+05:30"),
    },
]

EXIT_VARIANTS = [
    {"variant": "baseline_stop_1pct_h6", "horizon": 6, "target_r": None, "trailing": False},
    {"variant": "target_1_5R_h6", "horizon": 6, "target_r": 1.5, "trailing": False},
    {"variant": "target_2R_h6", "horizon": 6, "target_r": 2.0, "trailing": False},
    {"variant": "target_3R_h6", "horizon": 6, "target_r": 3.0, "trailing": False},
    {"variant": "trailing_1R_activate_h24", "horizon": 24, "target_r": None, "trailing": True},
    {"variant": "baseline_stop_1pct_h12", "horizon": 12, "target_r": None, "trailing": False},
    {"variant": "baseline_stop_1pct_h24", "horizon": 24, "target_r": None, "trailing": False},
]

FILTER_VARIANTS = [
    {"variant": "no_filter", "filter": "none"},
    {"variant": "atr_at_least_0_5pct", "filter": "atr_0_5"},
    {"variant": "atr_at_least_1pct", "filter": "atr_1_0"},
    {"variant": "first_90_minutes", "filter": "first_90"},
    {"variant": "20bar_turnover_at_least_25lakh", "filter": "turnover_25l"},
    {"variant": "combined_predeclared_filters", "filter": "combined"},
]

FAMILY_VARIANTS = [
    {"variant": "15m_frozen_reversal", "timeframe_minutes": 15, "family": "reversal"},
    {"variant": "5m_opening_range_breakout", "timeframe_minutes": 5, "family": "opening_range_breakout"},
]


def _timestamp_series(values):
    return pd.to_datetime(values, errors="coerce", utc=True).dt.tz_convert("Asia/Kolkata")


def load_inputs():
    data, audit = load_historical_data(HISTORICAL_DATA)
    context = {}
    for name, path in CONTEXT_FILES.items():
        if not path.exists():
            raise FileNotFoundError(f"Required context file is missing: {path}")
        frame = pd.read_csv(path)
        frame["timestamp"] = _timestamp_series(frame["timestamp"])
        for column in ("open", "close"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.dropna(subset=["timestamp", "open", "close"])
        frame = frame.drop_duplicates("timestamp").sort_values("timestamp")
        frame["direction"] = np.where(
            frame["close"] > frame["open"],
            "up",
            np.where(frame["close"] < frame["open"], "down", "flat"),
        )
        context[name] = frame[["timestamp", "direction"]].reset_index(drop=True)
    return data, context, audit


def _add_research_features(frame):
    frame = frame.copy()
    previous_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    frame["atr_14"] = true_range.rolling(14, min_periods=14).mean()
    frame["atr_pct"] = frame["atr_14"] / frame["close"] * 100
    frame["turnover_ma_20"] = (
        frame["close"] * frame["volume"]
    ).rolling(20, min_periods=20).mean()
    return frame


def _resample_15m(raw):
    records = []
    for symbol, group in raw.groupby("stock", sort=False):
        group = group.sort_values("timestamp").copy()
        local_time = group["timestamp"].dt.hour * 60 + group["timestamp"].dt.minute
        session_offset = local_time - (9 * 60 + 15)
        group["slot"] = (session_offset // 15).astype(int)
        group = group[(session_offset >= 0) & (session_offset < 375)]
        group["session_date"] = group["timestamp"].dt.date
        for (_, _), bars in group.groupby(["session_date", "slot"], sort=True):
            if len(bars) != 3:
                continue
            first = bars.iloc[0]
            last = bars.iloc[-1]
            records.append(
                {
                    "stock": symbol,
                    "timestamp": last["timestamp"],
                    "bar_open_timestamp": first["timestamp"],
                    "bar_end_timestamp": last["timestamp"] + pd.Timedelta(minutes=5),
                    "bar_count": len(bars),
                    "open": float(first["open"]),
                    "high": float(bars["high"].max()),
                    "low": float(bars["low"].min()),
                    "close": float(last["close"]),
                    "volume": float(bars["volume"].sum()),
                    "open_interest": 0.0,
                }
            )
    return pd.DataFrame(records).sort_values(["stock", "timestamp"]).reset_index(drop=True)


def prepare_frames(data, timeframe_minutes=5):
    raw = data.copy()
    if timeframe_minutes == 15:
        raw = _resample_15m(raw)
    elif timeframe_minutes != 5:
        raise ValueError("Only 5-minute and 15-minute bars are supported.")

    frames = {}
    for symbol in STOCKS:
        candles = raw.loc[raw["stock"] == symbol].copy()
        if len(candles) < 35:
            continue
        if "bar_open_timestamp" not in candles:
            candles["bar_open_timestamp"] = candles["timestamp"]
            candles["bar_end_timestamp"] = candles["timestamp"]
            candles["bar_count"] = 1
        indicators = calculate_indicators(candles.reset_index(drop=True))
        indicators = _add_research_features(indicators)
        if timeframe_minutes == 15:
            detected = detect_reversal_candidates(
                indicators,
                decline_candles=3,
                min_decline_pct=1.0,
                rsi_threshold=25.0,
            )
            detected["frozen_candidate"] = detected.apply(is_frozen_reversal, axis=1)
        else:
            detected = detect_reversal_candidates(
                indicators,
                decline_candles=3,
                min_decline_pct=1.0,
                rsi_threshold=25.0,
            )
            detected["frozen_candidate"] = detected.apply(is_frozen_reversal, axis=1)
        frames[symbol] = detected.reset_index(drop=True)
    return frames


def _contextualize_events(events, context):
    if not events:
        return []
    result = pd.DataFrame(events)
    result["signal_timestamp"] = _timestamp_series(result["signal_timestamp"])
    for name, frame in context.items():
        result = pd.merge_asof(
            result.sort_values("signal_timestamp"),
            frame.rename(columns={"direction": f"{name}_direction"}),
            left_on="signal_timestamp",
            right_on="timestamp",
            direction="backward",
            allow_exact_matches=True,
        ).drop(columns="timestamp")
    direction_columns = [f"{name}_direction" for name in CONTEXT_FILES]
    if result[direction_columns].isna().any(axis=None):
        raise RuntimeError("Some research signals lack completed signal-time market context.")
    result["down_count"] = (result[direction_columns] == "down").sum(axis=1)
    return result.to_dict(orient="records")


def _reversal_events(frames, start, end, timeframe_minutes):
    events = []
    duration = pd.Timedelta(minutes=timeframe_minutes)
    for symbol, original in frames.items():
        frame = original.loc[original["timestamp"] <= end].copy()
        if timeframe_minutes == 5:
            frame["timestamp"] = _timestamp_series(frame["timestamp"])
            frame["bar_open_timestamp"] = _timestamp_series(frame["bar_open_timestamp"])
            frame["bar_end_timestamp"] = _timestamp_series(frame["bar_end_timestamp"])
            prepared = {symbol: frame}
            frame_events, _, _ = _make_events(prepared)
            for event in frame_events:
                timestamp = pd.Timestamp(event["signal_timestamp"])
                if not start <= timestamp <= end:
                    continue
                index = event["signal_index"]
                entry_index = event["entry_index"]
                features = frame.loc[index]
                events.append(
                    {
                        **event,
                        "entry_timestamp": frame.at[entry_index, "bar_open_timestamp"],
                        "atr_pct": float(features["atr_pct"]),
                        "turnover_ma_20": float(features["turnover_ma_20"]),
                        "time_of_day": timestamp.time(),
                        "strategy_family": "reversal",
                        "timeframe_minutes": 5,
                    }
                )
            continue

        frame = frame.reset_index(drop=True)
        for index in frame.index[frame["frozen_candidate"]]:
            if index < 3 or index + 1 >= len(frame):
                continue
            signal_time = pd.Timestamp(frame.at[index, "timestamp"])
            entry_time = pd.Timestamp(frame.at[index + 1, "bar_open_timestamp"])
            if not start <= signal_time <= end:
                continue
            window = frame.iloc[index - 3 : index + 1]
            contiguous = all(
                right.timestamp.date() != left.timestamp.date()
                or right.timestamp - left.timestamp == duration
                for left, right in zip(window.iloc[:-1].itertuples(), window.iloc[1:].itertuples())
            )
            if not contiguous or signal_time.date() != entry_time.date():
                continue
            if int(frame.at[index, "bar_count"]) != 3 or int(frame.at[index + 1, "bar_count"]) != 3:
                continue
            events.append(
                {
                    "symbol": symbol,
                    "signal_index": int(index),
                    "entry_index": int(index + 1),
                    "signal_timestamp": signal_time,
                    "entry_timestamp": entry_time,
                    "priority": list(STOCKS).index(symbol),
                    "decline_pct": float(frame.at[index, "decline_pct"]),
                    "rsi_14": float(frame.at[index, "rsi_14"]),
                    "atr_pct": float(frame.at[index, "atr_pct"]),
                    "turnover_ma_20": float(frame.at[index, "turnover_ma_20"]),
                    "time_of_day": signal_time.time(),
                    "strategy_family": "reversal",
                    "timeframe_minutes": 15,
                }
            )
    events.sort(key=lambda event: (event["signal_timestamp"], event["priority"]))
    return events


def _opening_range_events(frames, start, end):
    events = []
    for symbol, original in frames.items():
        frame = original.loc[original["timestamp"] <= end].copy().reset_index(drop=True)
        frame["timestamp"] = _timestamp_series(frame["timestamp"])
        frame["bar_open_timestamp"] = _timestamp_series(frame["bar_open_timestamp"])
        frame["bar_end_timestamp"] = _timestamp_series(frame["bar_end_timestamp"])
        frame["session_date"] = frame["timestamp"].dt.date
        triggered_dates = set()
        for session_date, session in frame.groupby("session_date", sort=True):
            opening = session[
                (session["timestamp"].dt.time >= time(9, 15))
                & (session["timestamp"].dt.time < time(9, 30))
            ]
            if len(opening) != 3:
                continue
            opening_high = float(opening["high"].max())
            for index in session.index:
                signal_time = frame.at[index, "timestamp"]
                if signal_time.time() < time(9, 30) or index + 1 >= len(frame):
                    continue
                if session_date in triggered_dates or not start <= signal_time <= end:
                    continue
                if frame.at[index, "close"] <= opening_high:
                    continue
                entry_time = frame.at[index + 1, "bar_open_timestamp"]
                if entry_time.date() != session_date or entry_time - signal_time != pd.Timedelta(minutes=5):
                    continue
                events.append(
                    {
                        "symbol": symbol,
                        "signal_index": int(index),
                        "entry_index": int(index + 1),
                        "signal_timestamp": signal_time,
                        "entry_timestamp": entry_time,
                        "priority": list(STOCKS).index(symbol),
                        "decline_pct": np.nan,
                        "rsi_14": float(frame.at[index, "rsi_14"]),
                        "atr_pct": float(frame.at[index, "atr_pct"]),
                        "turnover_ma_20": float(frame.at[index, "turnover_ma_20"]),
                        "time_of_day": signal_time.time(),
                        "strategy_family": "opening_range_breakout",
                        "timeframe_minutes": 5,
                    }
                )
                triggered_dates.add(session_date)
    events.sort(key=lambda event: (event["signal_timestamp"], event["priority"]))
    return events


def make_events(frames, context, split, family="reversal", timeframe_minutes=5):
    if family == "reversal":
        events = _reversal_events(
            frames,
            split["start"],
            split["end"],
            timeframe_minutes,
        )
    elif family == "opening_range_breakout":
        events = _opening_range_events(frames, split["start"], split["end"])
    else:
        raise ValueError(f"Unknown strategy family: {family}")
    return _contextualize_events(events, context)


def _passes_filter(event, filter_name):
    if filter_name == "none":
        return True
    if filter_name == "atr_0_5":
        return event["atr_pct"] >= 0.5
    if filter_name == "atr_1_0":
        return event["atr_pct"] >= 1.0
    if filter_name == "first_90":
        return time(9, 15) <= event["time_of_day"] < time(10, 45)
    if filter_name == "turnover_25l":
        return event["turnover_ma_20"] >= 2_500_000
    if filter_name == "combined":
        return (
            event["atr_pct"] >= 0.5
            and time(9, 15) <= event["time_of_day"] < time(10, 45)
            and event["turnover_ma_20"] >= 2_500_000
        )
    raise ValueError(f"Unknown filter: {filter_name}")


def _exit_trade(frame, event, exit_spec):
    entry_index = event["entry_index"]
    entry_price = float(frame.at[entry_index, "open"])
    initial_stop = entry_price * (1 - STOP_PCT / 100)
    stop_distance = entry_price - initial_stop
    target_r = exit_spec.get("target_r")
    target_price = entry_price + stop_distance * target_r if target_r else None
    trailing = bool(exit_spec.get("trailing", False))
    horizon = int(exit_spec.get("horizon", 6))
    final_index = min(entry_index + horizon - 1, len(frame) - 1)
    signal_day = pd.Timestamp(event["signal_timestamp"]).date()
    exit_index = entry_index
    exit_price = None
    exit_reason = "horizon_exit"
    highest_seen = entry_price

    for index in range(entry_index, final_index + 1):
        candle = frame.loc[index]
        if pd.Timestamp(candle["timestamp"]).date() != signal_day:
            exit_reason = "session_exit"
            break

        active_stop = initial_stop
        if trailing and highest_seen >= entry_price + stop_distance:
            active_stop = max(active_stop, highest_seen * (1 - STOP_PCT / 100))

        if float(candle["low"]) <= active_stop:
            exit_index = index
            exit_price = min(float(candle["open"]), active_stop)
            exit_reason = "trailing_stop" if active_stop > initial_stop else "stop_exit"
            break
        if target_price is not None and float(candle["high"]) >= target_price:
            exit_index = index
            exit_price = target_price
            exit_reason = f"target_{target_r:g}R"
            break

        highest_seen = max(highest_seen, float(candle["high"]))
        exit_index = index

    if exit_price is None:
        exit_price = float(frame.at[exit_index, "close"])
        if exit_reason == "session_exit":
            exit_price = float(frame.at[exit_index, "close"])
        elif exit_index == final_index and entry_index + horizon - 1 >= len(frame):
            exit_reason = "data_end_exit"

    excursion = frame.loc[entry_index:exit_index]
    exit_time = pd.Timestamp(frame.at[exit_index, "bar_end_timestamp"])
    entry_time = pd.Timestamp(event["entry_timestamp"])
    return {
        "symbol": event["symbol"],
        "signal_timestamp": pd.Timestamp(event["signal_timestamp"]),
        "entry_timestamp": entry_time,
        "exit_timestamp": exit_time,
        "entry_price": entry_price,
        "exit_price": float(exit_price),
        "stop_loss": initial_stop,
        "exit_reason": exit_reason,
        "holding_minutes": max(0.0, (exit_time - entry_time).total_seconds() / 60),
        "mfe_pct": (float(excursion["high"].max()) / entry_price - 1) * 100,
        "mae_pct": (float(excursion["low"].min()) / entry_price - 1) * 100,
        "index_down_count": int(event["down_count"]),
        "atr_pct": float(event["atr_pct"]),
        "turnover_ma_20": float(event["turnover_ma_20"]),
    }


def simulate_window(
    frames,
    context,
    split,
    variant,
    *,
    family="reversal",
    timeframe_minutes=5,
    filter_name="none",
    cost_multiplier=1.0,
    keep_trades=False,
):
    events = make_events(frames, context, split, family, timeframe_minutes)
    config = RiskConfig(
        capital=INITIAL_CAPITAL,
        max_risk_per_trade=RISK_PER_TRADE,
        max_daily_loss=DAILY_LOSS_LIMIT,
        max_positions=MAX_OPEN_POSITIONS,
        max_trades_per_day=MAX_TRADES_PER_DAY,
    )
    capital = INITIAL_CAPITAL
    daily_losses = Counter()
    daily_trades = Counter()
    last_exit = None
    trades = []
    filter_rejections = 0
    risk_rejections = 0
    veto_count = 0
    exit_spec = variant if isinstance(variant, dict) else {"horizon": 6}

    for event in events:
        if event["down_count"] >= CONTEXT_DOWN_THRESHOLD:
            veto_count += 1
            continue
        if not _passes_filter(event, filter_name):
            filter_rejections += 1
            continue

        entry_time = pd.Timestamp(event["entry_timestamp"])
        if last_exit is not None and entry_time <= last_exit:
            risk_rejections += 1
            continue
        day = entry_time.date()
        active_config = RiskConfig(
            capital=capital,
            max_risk_per_trade=RISK_PER_TRADE,
            max_daily_loss=DAILY_LOSS_LIMIT,
            max_positions=MAX_OPEN_POSITIONS,
            max_trades_per_day=MAX_TRADES_PER_DAY,
        )
        state = RiskState(
            daily_loss=daily_losses[day],
            trades_today=daily_trades[day],
        )
        allowed, _ = check_trade_allowed(active_config, state)
        if not allowed:
            risk_rejections += 1
            continue

        frame = frames[event["symbol"]]
        entry_price = float(frame.at[event["entry_index"], "open"])
        stop = entry_price * (1 - STOP_PCT / 100)
        size = calculate_position_size(active_config, entry_price, stop)
        if size.quantity <= 0:
            risk_rejections += 1
            continue

        trade = _exit_trade(frame, event, exit_spec)
        trade["quantity"] = size.quantity
        trade["planned_risk"] = size.planned_risk
        trade["capital_exposure"] = size.capital_required
        trade["gross_pnl"] = (trade["exit_price"] - entry_price) * size.quantity
        cost_values = estimate_round_trip_costs(
            entry_price,
            trade["exit_price"],
            size.quantity,
            trade["entry_timestamp"],
            assumptions=CostAssumptions(slippage_bps_per_side=SLIPPAGE_BPS_PER_SIDE),
        )
        for key in list(cost_values):
            cost_values[key] *= cost_multiplier
        trade.update(cost_values)
        trade["net_pnl"] = trade["gross_pnl"] - trade["total_cost"]
        trade["r_multiple"] = trade["net_pnl"] / size.planned_risk if size.planned_risk else 0
        trade["variant"] = variant.get("variant", "baseline") if isinstance(variant, dict) else str(variant)
        trade["split"] = split["split"]
        trades.append(trade)
        capital += trade["net_pnl"]
        if trade["net_pnl"] < 0:
            daily_losses[day] += abs(trade["net_pnl"])
        daily_trades[day] += 1
        last_exit = trade["exit_timestamp"]

    metrics = calculate_metrics(
        trades,
        INITIAL_CAPITAL,
        max(1, len(set(pd.Timestamp(event["entry_timestamp"]).date() for event in events))),
        book=variant.get("variant", "baseline") if isinstance(variant, dict) else str(variant),
    )
    row = {
        "variant": variant.get("variant", "baseline") if isinstance(variant, dict) else str(variant),
        "split": split["split"],
        "role": split["role"],
        "family": family,
        "timeframe_minutes": timeframe_minutes,
        "signals": len(events),
        "context_vetoed": veto_count,
        "filter_rejected": filter_rejections,
        "risk_rejected": risk_rejections,
        "trades": len(trades),
        "wins": int(metrics["winning_trades"]),
        "losses": int(metrics["losing_trades"]),
        "win_rate_pct": float(metrics["win_rate"] * 100),
        "gross_pnl": float(metrics["gross_pnl"]),
        "costs": float(metrics["total_estimated_costs"]),
        "net_pnl": float(metrics["net_pnl"]),
        "net_pnl_per_trade": float(metrics["average_pnl_per_trade"]),
        "profit_factor": metrics["profit_factor"],
        "max_drawdown_pct": float(metrics["maximum_drawdown_pct"]),
        "cost_multiplier": cost_multiplier,
    }
    return row, trades if keep_trades else []


def run_experiment_set(category, variants):
    data, context, audit = load_inputs()
    timeframes = (
        sorted({variant["timeframe_minutes"] for variant in variants})
        if category == "strategy"
        else [5]
    )
    frames_by_timeframe = {
        timeframe: prepare_frames(data, timeframe_minutes=timeframe)
        for timeframe in timeframes
    }
    def run_one(variant, split):
        timeframe_minutes = variant.get("timeframe_minutes", 5)
        frames = frames_by_timeframe[timeframe_minutes]
        simulation_variant = (
            variant
            if category == "exit"
            else {"variant": variant["variant"], "horizon": 6}
        )
        family = variant.get("family", "reversal")
        filter_name = variant.get("filter", "none")
        row, _ = simulate_window(
            frames,
            context,
            split,
            simulation_variant,
            family=family,
            timeframe_minutes=timeframe_minutes,
            filter_name=filter_name,
        )
        row["category"] = category
        row["variants_in_category"] = len(variants)
        row["historical_rows"] = audit["valid_rows"]
        return row

    development = SPLITS[0]
    walk_forward = SPLITS[1:]
    development_rows = [run_one(variant, development) for variant in variants]
    development_frame = pd.DataFrame(development_rows)
    development_frame["is_development_finalist"] = False
    development_frame["development_selection_rank"] = np.nan

    if category == "exit":
        baseline_name = "baseline_stop_1pct_h6"
    elif category == "filter":
        baseline_name = "no_filter"
    else:
        baseline_name = None

    selected_names = []
    if baseline_name and baseline_name in set(development_frame["variant"]):
        selected_names.append(baseline_name)

    if baseline_name:
        eligible = development_frame[
            (development_frame["trades"] >= 10)
            & (~development_frame["variant"].isin(selected_names))
        ].sort_values("net_pnl", ascending=False, kind="stable")
        if not eligible.empty:
            selected_names.append(str(eligible.iloc[0]["variant"]))
    else:
        eligible = development_frame[development_frame["trades"] >= 10].sort_values(
            "net_pnl",
            ascending=False,
            kind="stable",
        )
        if not eligible.empty:
            selected_names.append(str(eligible.iloc[0]["variant"]))

    for rank, name in enumerate(selected_names, start=1):
        development_frame.loc[
            development_frame["variant"] == name,
            "is_development_finalist",
        ] = True
        development_frame.loc[
            development_frame["variant"] == name,
            "development_selection_rank",
        ] = rank

    finalist_rows = []
    for name in selected_names:
        variant = next(item for item in variants if item["variant"] == name)
        rank = selected_names.index(name) + 1
        for split in walk_forward:
            row = run_one(variant, split)
            row["is_development_finalist"] = True
            row["development_selection_rank"] = rank
            finalist_rows.append(row)

    return pd.concat(
        [development_frame, pd.DataFrame(finalist_rows)],
        ignore_index=True,
    )


def stage9_break_even():
    trades = pd.read_csv(STAGE9_TRADES)
    required = {"gross_pnl", "net_pnl", "total_cost", "symbol", "signal_timestamp"}
    missing = required - set(trades.columns)
    if missing:
        raise ValueError(f"Stage 9 trades are missing columns: {sorted(missing)}")
    gross = pd.to_numeric(trades["gross_pnl"], errors="coerce")
    net = pd.to_numeric(trades["net_pnl"], errors="coerce")
    costs = pd.to_numeric(trades["total_cost"], errors="coerce")
    gross_wins = gross[gross > 0]
    gross_losses = gross[gross < 0]
    average_cost = float(costs.mean())
    average_gross_win = float(gross_wins.mean())
    average_gross_loss = float(abs(gross_losses.mean()))
    break_even_win_rate = minimum_gross_win_rate(
        average_gross_win,
        average_gross_loss,
        average_cost,
    )
    exposure = (
        pd.to_numeric(trades["capital_exposure"], errors="coerce")
        if "capital_exposure" in trades
        else pd.Series(dtype=float)
    )
    average_exposure = float(exposure.mean()) if exposure.notna().any() else np.nan
    values = [
        ("stage9_trade_count", len(trades), "trades"),
        ("average_cost_per_trade", average_cost, "INR/trade"),
        ("average_gross_win", average_gross_win, "INR/winning gross trade"),
        ("average_gross_loss_abs", average_gross_loss, "INR/losing gross trade"),
        ("gross_win_rate", float((gross > 0).mean() * 100), "percent of trades"),
        ("gross_pnl_per_trade", float(gross.mean()), "INR/trade"),
        ("net_pnl_per_trade", float(net.mean()), "INR/trade"),
        ("break_even_win_rate_including_average_cost", float(break_even_win_rate * 100), "percent, using average gross win/loss"),
        ("gross_pnl_per_trade_needed_to_cover_costs", average_cost, "INR/trade"),
        ("average_gross_move_needed_to_cover_costs", average_cost, "INR/trade, currency P&L proxy"),
        ("average_capital_exposure", average_exposure, "INR/trade"),
        ("average_cost_as_pct_of_exposure", average_cost / average_exposure * 100 if average_exposure else np.nan, "percent of average deployed capital"),
        ("observed_gross_pnl", float(gross.sum()), "INR"),
        ("observed_costs", float(costs.sum()), "INR"),
        ("observed_net_pnl", float(net.sum()), "INR"),
    ]
    return pd.DataFrame(values, columns=["metric", "value", "unit"])


def minimum_gross_win_rate(average_gross_win, average_gross_loss_abs, average_cost):
    """Break-even winning fraction under fixed mean win/loss/cost assumptions."""
    denominator = average_gross_win + average_gross_loss_abs
    if denominator <= 0 or average_cost < 0:
        return np.nan
    return (average_gross_loss_abs + average_cost) / denominator


def trial_log(experiment_frames):
    rows = []
    trial_id = 0
    for category, frame in experiment_frames.items():
        if frame.empty:
            continue
        variants = sorted(frame["variant"].unique())
        for row in frame.itertuples(index=False):
            trial_id += 1
            rows.append(
                {
                    "trial_id": trial_id,
                    "category": category,
                    "variant": row.variant,
                    "variant_count_in_category": len(variants),
                    "split": row.split,
                    "role": row.role,
                    "trades": row.trades,
                    "net_pnl": row.net_pnl,
                    "finalist_selected_on_development": row.is_development_finalist,
                    "holdout_status": "NOT RUN: no uncontaminated holdout in available history",
                }
            )
    return pd.DataFrame(rows)