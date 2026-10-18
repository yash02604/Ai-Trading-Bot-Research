from pathlib import Path
from collections import Counter

import pandas as pd

from app.cost_model import (
    CostAssumptions,
    estimate_round_trip_costs,
)

from app.historical_validation import (
    load_historical_data,
    prepare_symbol_frames,
    _make_events,
    _simulate_exit,
    calculate_metrics,
)

from app.risk_engine import (
    RiskConfig,
    RiskState,
    check_trade_allowed,
    calculate_position_size,
)

from strategy_backtest import STOCKS


# ============================================================
# STAGE 9 — CANDIDATE COMBINED STRATEGY
# ============================================================

SOURCE = Path(
    "scripts/data/research/"
    "extended_historical_robustness_oos_candles.csv"
)

OOS_START = pd.Timestamp(
    "2025-12-02 00:00:00",
    tz="Asia/Kolkata",
)

OOS_END = pd.Timestamp(
    "2026-10-01 23:59:59",
    tz="Asia/Kolkata",
)

OUTPUT = Path(
    "backtests/stage9_candidate"
)


# ============================================================
# FROZEN STRATEGY PARAMETERS
# ============================================================

STOP_PCT = 1.0
HORIZON = 6

INITIAL_CAPITAL = 100_000.0

RISK_PER_TRADE = 0.01
DAILY_LOSS_LIMIT = 0.02

MAX_OPEN_POSITIONS = 1
MAX_TRADES_PER_DAY = 3

SLIPPAGE_BPS_PER_SIDE = 5.0


# ============================================================
# MARKET CONTEXT FILTER
# ============================================================

# Pre-registered rule:
# Reject a signal when >= 3 of 4 equity indices
# are DOWN on the latest completed signal candle.

CONTEXT_DOWN_THRESHOLD = 3

CONTEXT_SYMBOLS = [
    "NIFTY50",
    "NIFTY_BANK",
    "NIFTY_IT",
    "SENSEX",
]

CONTEXT_DIR = Path(
    "data/research/market_context"
)

CONTEXT_FILES = {
    "NIFTY50": CONTEXT_DIR / "nifty50_5min.csv",
    "NIFTY_BANK": CONTEXT_DIR / "nifty_bank_5min.csv",
    "NIFTY_IT": CONTEXT_DIR / "nifty_it_5min.csv",
    "SENSEX": CONTEXT_DIR / "sensex_5min.csv",
}


# ============================================================
# LOAD MARKET CONTEXT
# ============================================================

def load_context(path):
    """
    Load one market-context dataset.

    Timestamps are normalized to Asia/Kolkata.
    Duplicate timestamps are removed.
    """

    df = pd.read_csv(path)

    required_columns = {
        "timestamp",
        "open",
        "high",
        "low",
        "close",
    }

    missing = required_columns - set(df.columns)

    if missing:
        raise ValueError(
            f"{path}: missing columns "
            f"{sorted(missing)}"
        )

    df["timestamp"] = (
        pd.to_datetime(
            df["timestamp"],
            errors="coerce",
            utc=True,
        )
        .dt.tz_convert("Asia/Kolkata")
    )

    df["open"] = pd.to_numeric(
        df["open"],
        errors="coerce",
    )

    df["high"] = pd.to_numeric(
        df["high"],
        errors="coerce",
    )

    df["low"] = pd.to_numeric(
        df["low"],
        errors="coerce",
    )

    df["close"] = pd.to_numeric(
        df["close"],
        errors="coerce",
    )

    df = (
        df[
            [
                "timestamp",
                "open",
                "high",
                "low",
                "close",
            ]
        ]
        .dropna(
            subset=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
            ]
        )
        .drop_duplicates("timestamp")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    return df


# ============================================================
# BUILD MARKET CONTEXT LOOKUP
# ============================================================

def build_context_lookup(context):
    """
    For every signal timestamp, find the latest completed
    index candle at or before that timestamp.

    This deliberately uses signal_timestamp rather than
    entry_timestamp.
    """

    rows = []

    for name, frame in context.items():

        temp = frame[
            [
                "timestamp",
                "open",
                "close",
            ]
        ].copy()

        temp["direction"] = temp.apply(
            lambda row:
                "up"
                if row["close"] > row["open"]
                else (
                    "down"
                    if row["close"] < row["open"]
                    else "flat"
                ),
            axis=1,
        )

        temp = temp[
            [
                "timestamp",
                "direction",
            ]
        ].rename(
            columns={
                "direction": f"{name}_direction"
            }
        )

        rows.append(temp)

    return rows


# ============================================================
# CLASSIFY EVENTS WITH MARKET CONTEXT
# ============================================================

def classify_events(events, context):
    """
    Attach completed signal-candle index context.

    A candidate is vetoed when >=3 of the 4 equity
    indices are down on the latest completed candle
    at or before the signal timestamp.
    """

    lookup_frames = build_context_lookup(
        context
    )

    events_df = pd.DataFrame(events)

    if events_df.empty:
        return events, []

    events_df["signal_timestamp"] = (
        pd.to_datetime(
            events_df["signal_timestamp"],
            errors="coerce",
            utc=True,
        )
        .dt.tz_convert("Asia/Kolkata")
    )

    for lookup in lookup_frames:

        events_df = pd.merge_asof(
            events_df.sort_values(
                "signal_timestamp"
            ),
            lookup.sort_values(
                "timestamp"
            ),
            left_on="signal_timestamp",
            right_on="timestamp",
            direction="backward",
            allow_exact_matches=True,
        )

        events_df = events_df.drop(
            columns=["timestamp"]
        )

    direction_columns = [
        "NIFTY50_direction",
        "NIFTY_BANK_direction",
        "NIFTY_IT_direction",
        "SENSEX_direction",
    ]

    # --------------------------------------------------------
    # Make sure all four context datasets matched.
    # --------------------------------------------------------

    missing_context = events_df[
        direction_columns
    ].isna().any(axis=1)

    if missing_context.any():
        missing_count = int(
            missing_context.sum()
        )

        raise RuntimeError(
            f"{missing_count} OOS events "
            "are missing one or more index "
            "context candles."
        )

    # --------------------------------------------------------
    # Count DOWN indices.
    # --------------------------------------------------------

    events_df["index_down_count"] = (
        events_df[direction_columns] == "down"
    ).sum(axis=1)

    # --------------------------------------------------------
    # Market-context veto.
    # --------------------------------------------------------

    events_df["market_context_veto"] = (
        events_df["index_down_count"]
        >= CONTEXT_DOWN_THRESHOLD
    )

    # --------------------------------------------------------
    # Agreement classification.
    # --------------------------------------------------------

    events_df["index_agreement"] = "mixed"

    events_df.loc[
        events_df["index_down_count"] >= 3,
        "index_agreement",
    ] = "bearish_alignment"

    events_df.loc[
        (
            events_df[
                [
                    "NIFTY50_direction",
                    "NIFTY_BANK_direction",
                    "NIFTY_IT_direction",
                    "SENSEX_direction",
                ]
            ]
            == "up"
        )
        .sum(axis=1)
        >= 3,
        "index_agreement",
    ] = "bullish_alignment"

    # --------------------------------------------------------
    # Convert back to event dictionaries.
    # --------------------------------------------------------

    result = []

    for _, row in events_df.iterrows():

        event = {
            column: row[column]
            for column in events_df.columns
            if column not in direction_columns
        }

        for column in direction_columns:
            event[column] = row[column]

        event["index_down_count"] = int(
            row["index_down_count"]
        )

        event["market_context_veto"] = bool(
            row["market_context_veto"]
        )

        result.append(event)

    return result, direction_columns


# ============================================================
# SIMULATE STAGE 9 PORTFOLIO
# ============================================================

def simulate_stage9_book(
    frames,
    events,
    initial_capital=100_000.0,
    stop_pct=1.0,
    horizon=6,
    costs=None,
):
    """
    Run the exact frozen portfolio mechanics with
    the market-context veto applied before risk checks.
    """

    costs = costs or CostAssumptions(
        slippage_bps_per_side=5.0
    )

    # --------------------------------------------------------
    # Separate context-vetoed and context-passed events.
    # --------------------------------------------------------

    oos_events = events

    vetoed_signals = [
        event
        for event in oos_events
        if event["market_context_veto"]
    ]

    accepted_events = [
        event
        for event in oos_events
        if not event["market_context_veto"]
    ]

    # --------------------------------------------------------
    # Symbol counters.
    # --------------------------------------------------------

    symbol_counts = {
        symbol: Counter()
        for symbol in frames
    }

    for event in oos_events:

        symbol = event["symbol"]

        symbol_counts[symbol][
            "frozen_signals"
        ] += 1

    for event in accepted_events:

        symbol = event["symbol"]

        symbol_counts[symbol][
            "context_passed"
        ] += 1

    for event in vetoed_signals:

        symbol = event["symbol"]

        symbol_counts[symbol][
            "context_vetoed"
        ] += 1

    # --------------------------------------------------------
    # Portfolio state.
    # --------------------------------------------------------

    book_trades = []

    reject_reasons = Counter()

    risk_config = RiskConfig(
        capital=initial_capital
    )

    capital = initial_capital

    last_exit = None

    daily_trades = Counter()

    daily_losses = Counter()

    max_exposure = 0.0

    # --------------------------------------------------------
    # Process accepted events in original event order.
    # --------------------------------------------------------

    for event in accepted_events:

        symbol = event["symbol"]

        entry_time = event[
            "entry_timestamp"
        ]

        # ----------------------------------------------------
        # Maximum one open position.
        # ----------------------------------------------------

        if (
            last_exit is not None
            and entry_time <= last_exit
        ):

            reject_reasons[
                "maximum_open_positions"
            ] += 1

            symbol_counts[symbol][
                "risk_rejected"
            ] += 1

            continue

        day = entry_time.date()

        # ----------------------------------------------------
        # Capital check.
        # ----------------------------------------------------

        if capital <= 0:

            reject_reasons[
                "insufficient_capital"
            ] += 1

            symbol_counts[symbol][
                "risk_rejected"
            ] += 1

            continue

        # ----------------------------------------------------
        # Dynamic risk configuration.
        # ----------------------------------------------------

        active_config = RiskConfig(
            capital=capital,
            max_risk_per_trade=(
                risk_config.max_risk_per_trade
            ),
            max_daily_loss=(
                risk_config.max_daily_loss
            ),
            max_positions=(
                risk_config.max_positions
            ),
            max_trades_per_day=(
                risk_config.max_trades_per_day
            ),
        )

        state = RiskState(
            daily_loss=daily_losses[day],
            trades_today=daily_trades[day],
        )

        # ----------------------------------------------------
        # Risk engine.
        # ----------------------------------------------------

        allowed, reason = (
            check_trade_allowed(
                active_config,
                state,
            )
        )

        if not allowed:

            reject_reasons[reason] += 1

            symbol_counts[symbol][
                "risk_rejected"
            ] += 1

            continue

        # ----------------------------------------------------
        # Entry price.
        # ----------------------------------------------------

        frame = frames[symbol]

        entry = float(
            frame.at[
                event["entry_index"],
                "open",
            ]
        )

        # ----------------------------------------------------
        # Frozen 1% stop.
        # ----------------------------------------------------

        stop = entry * (
            1.0 - stop_pct / 100.0
        )

        # ----------------------------------------------------
        # Position sizing.
        # ----------------------------------------------------

        size = calculate_position_size(
            active_config,
            entry,
            stop,
        )

        if size.quantity <= 0:

            reject_reasons[
                "insufficient_capital_for_one_share"
            ] += 1

            symbol_counts[symbol][
                "risk_rejected"
            ] += 1

            continue

        # ----------------------------------------------------
        # Frozen exit simulation.
        #
        # Next-candle entry + 1% stop + 6 candles.
        # ----------------------------------------------------

        trade = _simulate_exit(
            frame,
            event,
            stop_pct,
            horizon,
        )

        # ----------------------------------------------------
        # Position information.
        # ----------------------------------------------------

        trade["quantity"] = size.quantity

        trade["planned_risk"] = (
            size.planned_risk
        )

        trade["capital_exposure"] = (
            size.capital_required
        )

        # ----------------------------------------------------
        # Gross P&L.
        # ----------------------------------------------------

        trade["gross_pnl"] = (
            trade["exit_price"]
            - trade["entry_price"]
        ) * size.quantity

        # ----------------------------------------------------
        # Exact project cost model.
        # ----------------------------------------------------

        cost_values = (
            estimate_round_trip_costs(
                trade["entry_price"],
                trade["exit_price"],
                size.quantity,
                trade["entry_timestamp"],
                assumptions=costs,
            )
        )

        trade.update(cost_values)

        # ----------------------------------------------------
        # P&L fields.
        # ----------------------------------------------------

        trade["pnl"] = trade[
            "gross_pnl"
        ]

        trade["net_pnl"] = (
            trade["gross_pnl"]
            - trade["total_cost"]
        )

        trade["r_multiple"] = (
            trade["net_pnl"]
            / size.planned_risk
            if size.planned_risk
            else 0.0
        )

        trade["book"] = (
            "stage9_candidate"
        )

        trade["ai_replay"] = (
            "unavailable"
        )

        # ----------------------------------------------------
        # Preserve market-context information.
        # ----------------------------------------------------

        trade["index_down_count"] = (
            event["index_down_count"]
        )

        trade["index_agreement"] = (
            event["index_agreement"]
        )

        trade["market_context_veto"] = False

        # ----------------------------------------------------
        # Save trade.
        # ----------------------------------------------------

        book_trades.append(trade)

        symbol_counts[symbol][
            "risk_accepted"
        ] += 1

        symbol_counts[symbol][
            "quantitative_buy_entries"
        ] += 1

        # ----------------------------------------------------
        # Exposure.
        # ----------------------------------------------------

        max_exposure = max(
            max_exposure,
            size.capital_required,
        )

        # ----------------------------------------------------
        # Update capital.
        # ----------------------------------------------------

        capital += trade[
            "net_pnl"
        ]

        # ----------------------------------------------------
        # Daily loss accounting.
        # ----------------------------------------------------

        if trade["net_pnl"] < 0:

            daily_losses[day] += abs(
                trade["net_pnl"]
            )

        daily_trades[day] += 1

        # ----------------------------------------------------
        # One-position book.
        # ----------------------------------------------------

        last_exit = trade[
            "exit_timestamp"
        ]

    # --------------------------------------------------------
    # Data rejection information.
    # --------------------------------------------------------

    # Generate the frozen data-rejection information separately.
    # This does not change the strategy or trade selection.

    _, _, data_rejects = _make_events(
        frames
    )

    for symbol in frames:

        symbol_counts[symbol][
            "data_rejected"
        ] = sum(
            count
            for (
                s,
                _,
            ), count in data_rejects.items()
            if s == symbol
        )

        symbol_counts[symbol][
            "sell_signals"
        ] = 0

    # --------------------------------------------------------
    # Trading days.
    # --------------------------------------------------------

    trading_days = len(
        set(
            event["entry_timestamp"].date()
            for event in accepted_events
        )
    )

    # --------------------------------------------------------
    # Counts.
    # --------------------------------------------------------

    counts = {
        "frozen_reversal_signals": len(
            oos_events
        ),

        "context_vetoed_signals": len(
            vetoed_signals
        ),

        "context_passed_signals": len(
            accepted_events
        ),

        "risk_accepted_entries": len(
            book_trades
        ),

        "risk_rejected_signals": int(
            sum(
                reject_reasons.values()
            )
        ),

        "data_rejected_signals": int(
            sum(
                count
                for (
                    symbol,
                    reason,
                ), count
                in data_rejects.items()
                if any(
                    event["symbol"]
                    == symbol
                    for event in oos_events
                )
            )
        ),

        "ai_confirmed_buy_signals": None,

        "ai_rejected_signals": None,

        "sell_signals": 0,

        "hold_signals": None,

        "ai_replay": "unavailable",

        "risk_rejection_reasons": dict(
            reject_reasons
        ),

        "data_rejection_reasons": {
            f"{symbol}:{reason}": int(
                value
            )
            for (
                symbol,
                reason,
            ), value
            in data_rejects.items()
        },

        "per_symbol": {
            symbol: dict(count)
            for symbol, count
            in symbol_counts.items()
        },
    }

    # --------------------------------------------------------
    # Metrics.
    # --------------------------------------------------------

    metrics = calculate_metrics(
        book_trades,
        initial_capital,
        trading_days,
        counts,
        "stage9_candidate",
    )

    metrics[
        "maximum_capital_exposure"
    ] = max_exposure

    metrics[
        "maximum_capital_exposure_pct"
    ] = (
        max_exposure
        / initial_capital
        * 100
        if initial_capital
        else 0.0
    )

    return (
        book_trades,
        metrics,
        counts,
        vetoed_signals,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 72)
    print("STAGE 9 — CANDIDATE COMBINED STRATEGY")
    print("=" * 72)

    print()

    print(
        f"Warm-up data: {SOURCE}"
    )

    print(
        f"OOS period: "
        f"{OOS_START} -> {OOS_END}"
    )

    print()

    print("=== FROZEN ENTRY ===")

    print(
        "Sharp-reversal strategy"
    )

    print(
        "Decline: 1.0% <= decline < 2.0%"
    )

    print(
        "RSI: 20 <= RSI < 25"
    )

    print(
        "Existing reversal condition"
    )

    print(
        "Entry: next 5-minute candle open"
    )

    print()

    print("=== FROZEN EXIT ===")

    print(
        "Stop: 1.0%"
    )

    print(
        "Horizon: 6 candles"
    )

    print()

    print("=== FROZEN RISK ===")

    print(
        "Risk/trade: 1%"
    )

    print(
        "Daily loss limit: 2%"
    )

    print(
        "Max open positions: 1"
    )

    print(
        "Max trades/day: 3"
    )

    print()

    print("=== MARKET CONTEXT OVERLAY ===")

    print(
        "Veto when >= 3 of 4 equity indices "
        "are DOWN"
    )

    print(
        "Context timestamp: signal_timestamp"
    )

    print(
        "Indices: NIFTY50, NIFTY_BANK, "
        "NIFTY_IT, SENSEX"
    )

    print()

    print(
        "AI replay: unavailable"
    )

    print()

    # ========================================================
    # LOAD STOCK DATA
    # ========================================================

    data, audit = load_historical_data(
        SOURCE
    )

    print(
        f"Loaded rows: {len(data)}"
    )

    print(
        f"Loaded symbols: "
        f"{len(data.stock.unique())}"
    )

    print(
        f"Loaded first: "
        f"{data.timestamp.min()}"
    )

    print(
        f"Loaded last: "
        f"{data.timestamp.max()}"
    )

    print()

    missing = sorted(
        set(STOCKS)
        - set(data.stock.unique())
    )

    if missing:

        raise RuntimeError(
            f"Missing expected symbols: "
            f"{missing}"
        )

    # ========================================================
    # PREPARE STOCK FRAMES
    # ========================================================

    frames = prepare_symbol_frames(
        data
    )

    missing_frames = sorted(
        set(STOCKS)
        - set(frames)
    )

    if missing_frames:

        raise RuntimeError(
            f"Missing prepared frames: "
            f"{missing_frames}"
        )

    # ========================================================
    # GENERATE FROZEN OOS EVENTS
    # ========================================================

    all_events, raw_signals, data_rejects = (
        _make_events(frames)
    )

    oos_events = [
        event
        for event in all_events
        if (
            OOS_START
            <= event["signal_timestamp"]
            <= OOS_END
        )
    ]

    print(
        f"Frozen OOS events: "
        f"{len(oos_events)}"
    )

    print()

    # ========================================================
    # LOAD MARKET CONTEXT
    # ========================================================

    print(
        "Loading market-context datasets..."
    )

    context = {}

    for name, path in CONTEXT_FILES.items():

        if not path.exists():
            raise FileNotFoundError(
                f"Missing market-context file: "
                f"{path}"
            )

        context[name] = load_context(
            path
        )

        print(
            f"Loaded {name}: "
            f"{len(context[name]):,} candles"
        )

    # ========================================================
    # APPLY MARKET CONTEXT
    # ========================================================

    (
        enriched_events,
        direction_columns,
    ) = classify_events(
        oos_events,
        context,
    )

    print(
        "Market context attached using "
        "latest completed candle at or before "
        "signal_timestamp."
    )

    missing_context = 0

    for event in enriched_events:

        if any(
            pd.isna(event[column])
            for column in direction_columns
        ):

            missing_context += 1

    if missing_context:

        raise RuntimeError(
            f"{missing_context} OOS events "
            "are missing one or more index "
            "context candles."
        )

    # ========================================================
    # COST MODEL
    # ========================================================

    costs = CostAssumptions(
        slippage_bps_per_side=(
            SLIPPAGE_BPS_PER_SIDE
        )
    )

    # ========================================================
    # RUN STAGE 9
    # ========================================================

    (
        trades,
        metrics,
        counts,
        vetoed_signals,
    ) = simulate_stage9_book(
        frames,
        enriched_events,
        initial_capital=(
            INITIAL_CAPITAL
        ),
        stop_pct=STOP_PCT,
        horizon=HORIZON,
        costs=costs,
    )

    # ========================================================
    # OUTPUT DIRECTORY
    # ========================================================

    OUTPUT.mkdir(
        parents=True,
        exist_ok=True,
    )

    trades_path = (
        OUTPUT / "trades.csv"
    )

    metrics_path = (
        OUTPUT / "metrics.csv"
    )

    veto_path = (
        OUTPUT / "vetoed_signals.csv"
    )

    counts_path = (
        OUTPUT / "counts.csv"
    )

    # ========================================================
    # SAVE RESULTS
    # ========================================================

    pd.DataFrame(
        trades
    ).to_csv(
        trades_path,
        index=False,
    )

    pd.DataFrame(
        [metrics]
    ).to_csv(
        metrics_path,
        index=False,
    )

    pd.DataFrame(
        vetoed_signals
    ).to_csv(
        veto_path,
        index=False,
    )

    pd.DataFrame(
        [
            {
                "metric": key,
                "value": value,
            }
            for key, value
            in counts.items()
            if not isinstance(
                value,
                dict,
            )
        ]
    ).to_csv(
        counts_path,
        index=False,
    )

    # ========================================================
    # RESULTS
    # ========================================================

    print()

    print("=" * 72)
    print("STAGE 9 RESULTS")
    print("=" * 72)

    print()

    print(
        f"Frozen OOS signals: "
        f"{counts['frozen_reversal_signals']}"
    )

    print(
        f"Context vetoed: "
        f"{counts['context_vetoed_signals']}"
    )

    print(
        f"Context passed: "
        f"{counts['context_passed_signals']}"
    )

    print(
        f"Accepted entries: "
        f"{counts['risk_accepted_entries']}"
    )

    print(
        f"Risk rejected: "
        f"{counts['risk_rejected_signals']}"
    )

    print()

    print(
        f"Net P&L: "
        f"₹{metrics['net_pnl']:.2f}"
    )

    print(
        f"Net return: "
        f"{metrics['net_return_pct']:.2f}%"
    )

    print(
        f"Win rate: "
        f"{metrics['win_rate'] * 100:.2f}%"
    )

    print(
        f"Profit factor: "
        f"{metrics['profit_factor']}"
    )

    print(
        f"Total estimated costs: "
        f"₹{metrics['total_estimated_costs']:.2f}"
    )

    print()

    print(
        "=== OUTPUT FILES ==="
    )

    print(
        f"Trades: "
        f"{trades_path}"
    )

    print(
        f"Metrics: "
        f"{metrics_path}"
    )

    print(
        f"Vetoed signals: "
        f"{veto_path}"
    )

    print(
        f"Counts: "
        f"{counts_path}"
    )

    print()

    print(
        "=" * 72
    )

    print(
        "Stage 9 candidate run complete."
    )

    print(
        "No AI replay was used."
    )

    print(
        "No live broker orders were used."
    )

    print(
        "No frozen strategy parameters were tuned."
    )

    print(
        "=" * 72
    )


if __name__ == "__main__":
    main()