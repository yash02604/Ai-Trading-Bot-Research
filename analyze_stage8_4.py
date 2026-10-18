from pathlib import Path

import pandas as pd

from app.historical_validation import (
    load_historical_data,
    prepare_symbol_frames,
    _make_events,
)


# ============================================================
# STAGE 8.4 — HOLDING-PERIOD / STOP-LOSS ANALYSIS
#
# DESCRIPTIVE ONLY
#
# No strategy parameters are changed.
# No OOS data is used.
# No new exit rule is introduced.
#
# Purpose:
#   1. Examine what happens after the existing 6-candle horizon.
#   2. Examine how close trades come to the existing 1% stop.
#   3. Determine whether there is enough evidence for ONE
#      clearly defined exit hypothesis.
#
# This script does NOT change:
#   STOP = 1%
#   HORIZON = 6 candles
# ============================================================


SOURCE = Path(
    "data/research/extended_historical_robustness_candles.csv"
)

TRADES_FILE = Path(
    "backtests/validation/trades.csv"
)

OUTPUT = Path(
    "backtests/validation/stage8_4_holding_period_analysis.csv"
)

TRADE_SUMMARY_OUTPUT = Path(
    "backtests/validation/stage8_4_trade_summary.csv"
)

STOP_PCT = 1.0
ORIGINAL_HORIZON = 6

# Look beyond the original 6-candle horizon.
# We examine up to 12 candles after entry where data exists.
EXTENDED_HORIZON = 12


# ============================================================
# HELPERS
# ============================================================

def pct_return(entry_price, price):
    if entry_price == 0:
        return 0.0

    return (
        (price / entry_price) - 1.0
    ) * 100.0


# ============================================================
# LOAD DATA
# ============================================================

print("=" * 72)
print("STAGE 8.4 — HOLDING-PERIOD / STOP-LOSS ANALYSIS")
print("=" * 72)

print("\nLoading historical candles...")

data, audit = load_historical_data(
    SOURCE
)

print(
    f"Loaded candles: {len(data):,}"
)

frames = prepare_symbol_frames(
    data
)

print(
    f"Prepared symbols: {len(frames)}"
)


# ============================================================
# LOAD HISTORICAL PORTFOLIO TRADES
# ============================================================

trades = pd.read_csv(
    TRADES_FILE
)

portfolio_trades = trades[
    trades["book"] == "portfolio"
].copy()

print(
    f"Historical portfolio trades: "
    f"{len(portfolio_trades)}"
)


# ============================================================
# BUILD FROZEN EVENTS
# ============================================================

print(
    "\nBuilding frozen reversal events..."
)

events, raw_signals, data_rejects = _make_events(
    frames
)

event_lookup = {}

for event in events:

    key = (
        event["symbol"],
        str(event["signal_timestamp"]),
    )

    event_lookup[key] = event


# ============================================================
# ANALYZE EACH TRADE
# ============================================================

path_rows = []
trade_rows = []

missing_events = 0
insufficient_extended_data = 0


for _, trade in portfolio_trades.iterrows():

    symbol = trade["symbol"]

    signal_timestamp = pd.Timestamp(
        trade["signal_timestamp"]
    )

    key = (
        symbol,
        str(signal_timestamp),
    )

    event = event_lookup.get(key)

    if event is None:

        missing_events += 1
        continue

    frame = frames[symbol]

    entry_index = int(
        event["entry_index"]
    )

    # Need at least the original six candles.
    if (
        entry_index + ORIGINAL_HORIZON
        >= len(frame)
    ):

        missing_events += 1
        continue

    entry_price = float(
        frame.at[
            entry_index,
            "open",
        ]
    )

    actual_net_pnl = float(
        trade["net_pnl"]
    )

    outcome = (
        "WIN"
        if actual_net_pnl > 0
        else "LOSS"
    )

    exit_reason = trade.get(
        "exit_reason",
        ""
    )

    # --------------------------------------------------------
    # Determine how many candles are actually available.
    # --------------------------------------------------------

    available_candles = min(
        EXTENDED_HORIZON,
        len(frame) - entry_index - 1,
    )

    if available_candles < EXTENDED_HORIZON:
        insufficient_extended_data += 1

    # --------------------------------------------------------
    # Running path statistics
    # --------------------------------------------------------

    max_high = entry_price
    min_low = entry_price

    first_stop_touch = None

    horizon6_close = None
    horizon6_mfe = None
    horizon6_mae = None

    # Values after original horizon
    post6_max_high = entry_price
    post6_min_low = entry_price

    post6_best_return = None
    post6_worst_return = None

    # --------------------------------------------------------
    # Candle-by-candle analysis
    # --------------------------------------------------------

    for candle_number in range(
        1,
        available_candles + 1,
    ):

        candle_index = (
            entry_index
            + candle_number
        )

        candle = frame.iloc[
            candle_index
        ]

        open_price = float(
            candle["open"]
        )

        high_price = float(
            candle["high"]
        )

        low_price = float(
            candle["low"]
        )

        close_price = float(
            candle["close"]
        )

        timestamp = candle[
            "timestamp"
        ]

        # ----------------------------------------------------
        # Running MFE / MAE
        # ----------------------------------------------------

        max_high = max(
            max_high,
            high_price,
        )

        min_low = min(
            min_low,
            low_price,
        )

        mfe_pct = pct_return(
            entry_price,
            max_high,
        )

        mae_pct = pct_return(
            entry_price,
            min_low,
        )

        close_return_pct = pct_return(
            entry_price,
            close_price,
        )

        open_return_pct = pct_return(
            entry_price,
            open_price,
        )

        # ----------------------------------------------------
        # Stop touch
        # ----------------------------------------------------

        stop_price = (
            entry_price
            * (1.0 - STOP_PCT / 100.0)
        )

        stop_touched = (
            low_price <= stop_price
        )

        if (
            first_stop_touch is None
            and stop_touched
        ):

            first_stop_touch = candle_number

        # ----------------------------------------------------
        # Save candle path
        # ----------------------------------------------------

        path_rows.append({

            "symbol":
                symbol,

            "signal_timestamp":
                signal_timestamp,

            "entry_timestamp":
                trade["entry_timestamp"],

            "entry_price":
                entry_price,

            "candle_number":
                candle_number,

            "candle_timestamp":
                timestamp,

            "open_price":
                open_price,

            "high_price":
                high_price,

            "low_price":
                low_price,

            "close_price":
                close_price,

            "open_return_pct":
                open_return_pct,

            "close_return_pct":
                close_return_pct,

            "mfe_pct":
                mfe_pct,

            "mae_pct":
                mae_pct,

            "stop_price":
                stop_price,

            "stop_touched":
                stop_touched,

            "actual_exit_reason":
                exit_reason,

            "actual_net_pnl":
                actual_net_pnl,

            "outcome":
                outcome,

        })

        # ----------------------------------------------------
        # Capture exact 6-candle state
        # ----------------------------------------------------

        if candle_number == ORIGINAL_HORIZON:

            horizon6_close = close_return_pct
            horizon6_mfe = mfe_pct
            horizon6_mae = mae_pct

        # ----------------------------------------------------
        # Analyze movement AFTER candle 6
        # ----------------------------------------------------

        if candle_number > ORIGINAL_HORIZON:

            post6_max_high = max(
                post6_max_high,
                high_price,
            )

            post6_min_low = min(
                post6_min_low,
                low_price,
            )

            post6_best_return = pct_return(
                entry_price,
                post6_max_high,
            )

            post6_worst_return = pct_return(
                entry_price,
                post6_min_low,
            )

    # ========================================================
    # TRADE-LEVEL SUMMARY
    # ========================================================

    # We need the actual candle-6 values.
    if horizon6_close is None:
        continue

    # Maximum favorable movement AFTER candle 6.
    post6_group = [
        row
        for row in path_rows
        if (
            row["symbol"] == symbol
            and row["signal_timestamp"] == signal_timestamp
            and row["candle_number"] > ORIGINAL_HORIZON
        )
    ]

    if post6_group:

        post6_high = max(
            row["high_price"]
            for row in post6_group
        )

        post6_low = min(
            row["low_price"]
            for row in post6_group
        )

        post6_best_return = pct_return(
            entry_price,
            post6_high,
        )

        post6_worst_return = pct_return(
            entry_price,
            post6_low,
        )

        post6_final_close = post6_group[-1][
            "close_return_pct"
        ]

    else:

        post6_best_return = None
        post6_worst_return = None
        post6_final_close = None

    # Movement available AFTER candle 6 relative to
    # the candle-6 close.
    if post6_group:

        post6_best_from_horizon = max(
            row["close_return_pct"]
            for row in post6_group
        )

        post6_worst_from_horizon = min(
            row["close_return_pct"]
            for row in post6_group
        )

    else:

        post6_best_from_horizon = None
        post6_worst_from_horizon = None

    trade_rows.append({

        "symbol":
            symbol,

        "signal_timestamp":
            signal_timestamp,

        "entry_timestamp":
            trade["entry_timestamp"],

        "entry_price":
            entry_price,

        "outcome":
            outcome,

        "actual_exit_reason":
            exit_reason,

        "actual_net_pnl":
            actual_net_pnl,

        "first_stop_touch_candle":
            first_stop_touch,

        "horizon6_close_return_pct":
            horizon6_close,

        "horizon6_mfe_pct":
            horizon6_mfe,

        "horizon6_mae_pct":
            horizon6_mae,

        "post6_best_return_pct":
            post6_best_return,

        "post6_worst_return_pct":
            post6_worst_return,

        "post6_final_close_return_pct":
            post6_final_close,

        "post6_best_from_horizon_pct":
            post6_best_from_horizon,

        "post6_worst_from_horizon_pct":
            post6_worst_from_horizon,

        "extended_candles_available":
            available_candles,

    })


# ============================================================
# CREATE DATAFRAMES
# ============================================================

analysis = pd.DataFrame(
    path_rows
)

trade_summary = pd.DataFrame(
    trade_rows
)

print(
    f"\nGenerated path rows: "
    f"{len(analysis):,}"
)

print(
    f"Trade summaries: "
    f"{len(trade_summary):,}"
)

print(
    f"Missing event matches: "
    f"{missing_events}"
)

print(
    f"Trades with fewer than "
    f"{EXTENDED_HORIZON} candles available: "
    f"{insufficient_extended_data}"
)


if trade_summary.empty:

    print(
        "\nERROR: No trade summaries generated."
    )

    raise SystemExit(1)


# ============================================================
# SAVE RESULTS
# ============================================================

OUTPUT.parent.mkdir(
    parents=True,
    exist_ok=True,
)

analysis.to_csv(
    OUTPUT,
    index=False,
)

trade_summary.to_csv(
    TRADE_SUMMARY_OUTPUT,
    index=False,
)


# ============================================================
# RESULTS
# ============================================================

print("\n")
print("=" * 72)
print("STAGE 8.4 RESULTS")
print("=" * 72)


# ============================================================
# BASIC TRADE COUNTS
# ============================================================

print(
    "\nTrade-level results:"
)

print(
    f"  Trades analysed: "
    f"{len(trade_summary)}"
)

print(
    f"  Winners: "
    f"{(trade_summary['outcome'] == 'WIN').sum()}"
)

print(
    f"  Losers: "
    f"{(trade_summary['outcome'] == 'LOSS').sum()}"
)


# ============================================================
# EXIT REASONS
# ============================================================

print(
    "\nActual exit reasons:"
)

print(
    trade_summary[
        "actual_exit_reason"
    ].value_counts().to_string()
)


# ============================================================
# STOP-LOSS ANALYSIS
# ============================================================

print(
    "\n1% stop-loss analysis:"
)

stop_touched = trade_summary[
    trade_summary["first_stop_touch_candle"]
    .notna()
].copy()

print(
    f"  Trades touching 1% stop: "
    f"{len(stop_touched)}"
)

if len(stop_touched):

    print(
        f"  Winners: "
        f"{(stop_touched['outcome'] == 'WIN').sum()}"
    )

    print(
        f"  Losers: "
        f"{(stop_touched['outcome'] == 'LOSS').sum()}"
    )

    print(
        f"  Average net P&L: "
        f"₹{stop_touched['actual_net_pnl'].mean():,.2f}"
    )

    print(
        f"  Average MFE by horizon: "
        f"{stop_touched['horizon6_mfe_pct'].mean():.4f}%"
    )

    print(
        f"  Average MAE by horizon: "
        f"{stop_touched['horizon6_mae_pct'].mean():.4f}%"
    )

    print(
        "\n  Stop-touch candle:"
    )

    print(
        stop_touched[
            "first_stop_touch_candle"
        ].value_counts().sort_index().to_string()
    )


# ============================================================
# NON-STOP TRADES THAT APPROACHED STOP
# ============================================================

print(
    "\nNear-stop analysis:"
)

near_stop_threshold = -0.75

near_stop = trade_summary[
    (
        trade_summary["horizon6_mae_pct"]
        <= near_stop_threshold
    )
    &
    (
        trade_summary["first_stop_touch_candle"]
        .isna()
    )
].copy()

print(
    f"  Non-stop trades reaching "
    f"{near_stop_threshold:.2f}% MAE or worse: "
    f"{len(near_stop)}"
)

if len(near_stop):

    print(
        f"  Winners: "
        f"{(near_stop['outcome'] == 'WIN').sum()}"
    )

    print(
        f"  Losers: "
        f"{(near_stop['outcome'] == 'LOSS').sum()}"
    )

    print(
        f"  Average net P&L: "
        f"₹{near_stop['actual_net_pnl'].mean():,.2f}"
    )


# ============================================================
# SIX-CANDLE HORIZON ANALYSIS
# ============================================================

print(
    "\nSix-candle horizon:"
)

horizon_summary = (
    trade_summary
    .groupby("outcome")
    .agg(
        trades=("outcome", "size"),
        avg_close_return_pct=(
            "horizon6_close_return_pct",
            "mean",
        ),
        median_close_return_pct=(
            "horizon6_close_return_pct",
            "median",
        ),
        avg_mfe_pct=(
            "horizon6_mfe_pct",
            "mean",
        ),
        avg_mae_pct=(
            "horizon6_mae_pct",
            "mean",
        ),
        avg_net_pnl=(
            "actual_net_pnl",
            "mean",
        ),
    )
)

print(
    horizon_summary.to_string()
)


# ============================================================
# POST-HORIZON MOVEMENT
# ============================================================

print(
    "\nMovement after the original 6-candle horizon:"
)

post6_available = trade_summary[
    trade_summary["post6_best_return_pct"]
    .notna()
].copy()

print(
    f"  Trades with post-horizon data: "
    f"{len(post6_available)}"
)

if len(post6_available):

    post6_summary = (
        post6_available
        .groupby("outcome")
        .agg(
            trades=("outcome", "size"),

            avg_best_return_pct=(
                "post6_best_return_pct",
                "mean",
            ),

            median_best_return_pct=(
                "post6_best_return_pct",
                "median",
            ),

            avg_worst_return_pct=(
                "post6_worst_return_pct",
                "mean",
            ),

            median_worst_return_pct=(
                "post6_worst_return_pct",
                "median",
            ),

            avg_final_return_pct=(
                "post6_final_close_return_pct",
                "mean",
            ),

            avg_best_from_horizon_pct=(
                "post6_best_from_horizon_pct",
                "mean",
            ),

            avg_worst_from_horizon_pct=(
                "post6_worst_from_horizon_pct",
                "mean",
            ),
        )
    )

    print(
        post6_summary.to_string()
    )


# ============================================================
# POST-HORIZON DIRECTION
# ============================================================

print(
    "\nPost-horizon directional behavior:"
)

if len(post6_available):

    post6_available[
        "post6_improved"
    ] = (
        post6_available[
            "post6_final_close_return_pct"
        ]
        >
        post6_available[
            "horizon6_close_return_pct"
        ]
    )

    post6_available[
        "post6_reversed"
    ] = (
        post6_available[
            "post6_final_close_return_pct"
        ]
        <
        post6_available[
            "horizon6_close_return_pct"
        ]
    )

    for outcome in [
        "WIN",
        "LOSS",
    ]:

        subset = post6_available[
            post6_available["outcome"]
            == outcome
        ]

        if len(subset):

            improved = int(
                subset["post6_improved"].sum()
            )

            reversed_count = int(
                subset["post6_reversed"].sum()
            )

            print(
                f"  {outcome}: "
                f"{improved}/{len(subset)} "
                f"finished better than candle 6; "
                f"{reversed_count}/{len(subset)} "
                f"finished worse"
            )


# ============================================================
# CANDLE-BY-CANDLE EXTENDED PATH
# ============================================================

print(
    "\nExtended path statistics:"
)

extended_path = (
    analysis[
        analysis["candle_number"]
        <= EXTENDED_HORIZON
    ]
    .groupby(
        [
            "outcome",
            "candle_number",
        ]
    )
    .agg(
        trades=(
            "symbol",
            "size",
        ),

        avg_close_return_pct=(
            "close_return_pct",
            "mean",
        ),

        median_close_return_pct=(
            "close_return_pct",
            "median",
        ),

        avg_mfe_pct=(
            "mfe_pct",
            "mean",
        ),

        avg_mae_pct=(
            "mae_pct",
            "mean",
        ),
    )
    .reset_index()
)

print(
    extended_path.to_string(
        index=False
    )
)


# ============================================================
# PRELIMINARY HYPOTHESIS CHECK
# ============================================================

print("\n")
print("=" * 72)
print("STAGE 8.4 INTERPRETATION CHECK")
print("=" * 72)

print(
    """
This is DESCRIPTIVE analysis only.

The existing strategy remains frozen:
  Stop = 1%
  Horizon = 6 candles
  Risk/trade = 1%
  Daily loss limit = 2%
  Max open positions = 1
  Max trades/day = 3

No alternative stop or holding period has been tested.

The purpose is to determine whether the historical
trade paths provide enough evidence for ONE clearly
defined exit hypothesis.

Do NOT optimize parameters from these results.

If the evidence is weak or contradictory, Stage 8.4
will be closed without changing the strategy.
"""
)

print(
    "\nSaved:"
)

print(
    f"  {OUTPUT}"
)

print(
    f"  {TRADE_SUMMARY_OUTPUT}"
)