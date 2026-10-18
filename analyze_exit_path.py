from pathlib import Path

import pandas as pd

from app.historical_validation import (
    load_historical_data,
    prepare_symbol_frames,
    _make_events,
)


# ============================================================
# STAGE 8.1 — EXIT PATH ANALYSIS
#
# DESCRIPTIVE ONLY
#
# No strategy parameters are changed.
# No OOS data is used.
# No new entry filter is introduced.
#
# We study how each historical portfolio trade behaves
# candle-by-candle after entry.
# ============================================================


SOURCE = Path(
    "data/research/extended_historical_robustness_candles.csv"
)

TRADES_FILE = Path(
    "backtests/validation/trades.csv"
)

OUTPUT = Path(
    "backtests/validation/exit_path_analysis.csv"
)


STOP_PCT = 1.0
HORIZON = 6


# ============================================================
# LOAD DATA
# ============================================================

print("=" * 72)
print("STAGE 8.1 — EXIT PATH ANALYSIS")
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
# HELPER
# ============================================================

def pct_return(entry_price, price):

    if entry_price == 0:
        return 0.0

    return (
        (price / entry_price) - 1.0
    ) * 100.0


# ============================================================
# ANALYZE EACH TRADE
# ============================================================

rows = []

missing_events = 0

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

    if (
        entry_index + HORIZON
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

    # --------------------------------------------------------
    # Determine actual trade outcome
    # --------------------------------------------------------

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
    # Track path statistics
    # --------------------------------------------------------

    max_high = entry_price
    min_low = entry_price

    first_positive_candle = None
    first_half_pct = None
    first_one_pct = None

    first_stop_touch = None

    # --------------------------------------------------------
    # Candle-by-candle analysis
    # --------------------------------------------------------

    for candle_number in range(
        1,
        HORIZON + 1,
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
        # Update MFE / MAE
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
        # First positive close
        # ----------------------------------------------------

        if (
            first_positive_candle is None
            and close_return_pct > 0
        ):

            first_positive_candle = (
                candle_number
            )

        # ----------------------------------------------------
        # First +0.50%
        # ----------------------------------------------------

        if (
            first_half_pct is None
            and mfe_pct >= 0.50
        ):

            first_half_pct = (
                candle_number
            )

        # ----------------------------------------------------
        # First +1.00%
        # ----------------------------------------------------

        if (
            first_one_pct is None
            and mfe_pct >= 1.00
        ):

            first_one_pct = (
                candle_number
            )

        # ----------------------------------------------------
        # First 1% stop touch
        # ----------------------------------------------------

        stop_price = (
            entry_price
            * (1.0 - STOP_PCT / 100.0)
        )

        if (
            first_stop_touch is None
            and low_price <= stop_price
        ):

            first_stop_touch = (
                candle_number
            )

        # ----------------------------------------------------
        # Save candle path
        # ----------------------------------------------------

        rows.append({

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
                low_price <= stop_price,

            "actual_exit_reason":
                exit_reason,

            "actual_net_pnl":
                actual_net_pnl,

            "outcome":
                outcome,

            "first_positive_candle":
                first_positive_candle,

            "first_0_50_pct_candle":
                first_half_pct,

            "first_1_00_pct_candle":
                first_one_pct,

            "first_stop_touch_candle":
                first_stop_touch,

        })


# ============================================================
# CREATE DATAFRAME
# ============================================================

analysis = pd.DataFrame(rows)

print(
    f"\nGenerated path rows: "
    f"{len(analysis):,}"
)

print(
    f"Missing event matches: "
    f"{missing_events}"
)


# ============================================================
# SAVE FULL CANDLE-BY-CANDLE DATA
# ============================================================

OUTPUT.parent.mkdir(
    parents=True,
    exist_ok=True,
)

analysis.to_csv(
    OUTPUT,
    index=False,
)


# ============================================================
# SUMMARY
# ============================================================

print("\n")
print("=" * 72)
print("STAGE 8.1 RESULTS")
print("=" * 72)


if analysis.empty:

    print(
        "\nERROR: No analysis rows generated."
    )

    raise SystemExit(1)


# ------------------------------------------------------------
# Trade-level summary
# ------------------------------------------------------------

trade_summary = (
    analysis
    .groupby(
        [
            "symbol",
            "signal_timestamp",
        ],
        as_index=False,
    )
    .agg(
        outcome=(
            "outcome",
            "first",
        ),
        actual_net_pnl=(
            "actual_net_pnl",
            "first",
        ),
        actual_exit_reason=(
            "actual_exit_reason",
            "first",
        ),
        max_mfe_pct=(
            "mfe_pct",
            "max",
        ),
        max_mae_pct=(
            "mae_pct",
            "min",
        ),
    )
)


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
# EXIT REASON SUMMARY
# ============================================================

print(
    "\nExit reason:"
)

print(
    trade_summary[
        "actual_exit_reason"
    ].value_counts().to_string()
)


# ============================================================
# MFE / MAE BY OUTCOME
# ============================================================

print(
    "\nMFE / MAE by outcome:"
)

outcome_summary = (
    trade_summary
    .groupby("outcome")
    .agg(
        trades=("outcome", "size"),
        avg_mfe_pct=("max_mfe_pct", "mean"),
        median_mfe_pct=("max_mfe_pct", "median"),
        max_mfe_pct=("max_mfe_pct", "max"),
        avg_mae_pct=("max_mae_pct", "mean"),
        median_mae_pct=("max_mae_pct", "median"),
        worst_mae_pct=("max_mae_pct", "min"),
        avg_net_pnl=("actual_net_pnl", "mean"),
    )
)

print(
    outcome_summary.to_string()
)


# ============================================================
# PATH BY CANDLE
# ============================================================

print(
    "\nPath statistics by candle:"
)

path_summary = (
    analysis
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
        median_mfe_pct=(
            "mfe_pct",
            "median",
        ),
        avg_mae_pct=(
            "mae_pct",
            "mean",
        ),
        median_mae_pct=(
            "mae_pct",
            "median",
        ),
        stop_touches=(
            "stop_touched",
            "sum",
        ),
    )
    .reset_index()
)

print(
    path_summary.to_string(
        index=False
    )
)


# ============================================================
# THRESHOLD REACHING
# ============================================================

print(
    "\nMFE threshold reach rates:"
)

trade_groups = analysis.groupby(
    [
        "symbol",
        "signal_timestamp",
    ]
)

threshold_rows = []

for threshold in [
    0.25,
    0.50,
    0.75,
    1.00,
]:

    for outcome in [
        "WIN",
        "LOSS",
    ]:

        subset = trade_summary[
            trade_summary["outcome"]
            == outcome
        ]

        reached = (
            subset["max_mfe_pct"]
            >= threshold
        )

        threshold_rows.append({

            "threshold_pct":
                threshold,

            "outcome":
                outcome,

            "trades":
                len(subset),

            "reached":
                int(reached.sum()),

            "reach_rate_pct":
                (
                    reached.mean() * 100
                    if len(subset)
                    else 0
                ),
        })


threshold_summary = pd.DataFrame(
    threshold_rows
)

print(
    threshold_summary.to_string(
        index=False
    )
)


# ============================================================
# STOP ANALYSIS
# ============================================================

print(
    "\nStop-loss analysis:"
)

stop_trade_rows = []

for (
    symbol,
    signal_timestamp,
), group in trade_groups:

    row = group.iloc[0]

    stop_touched = bool(
        group["stop_touched"].any()
    )

    first_stop = group[
        group["stop_touched"]
    ]

    first_stop_candle = (
        int(
            first_stop.iloc[0][
                "candle_number"
            ]
        )
        if not first_stop.empty
        else None
    )

    max_mfe_pct = float(
        group["mfe_pct"].max()
    )

    max_mae_pct = float(
        group["mae_pct"].min()
    )

    stop_trade_rows.append({

        "symbol":
            symbol,

        "signal_timestamp":
            signal_timestamp,

        "outcome":
            row["outcome"],

        "net_pnl":
            row["actual_net_pnl"],

        "max_mfe_pct":
            max_mfe_pct,

        "max_mae_pct":
            max_mae_pct,

        "stop_touched":
            stop_touched,

        "first_stop_candle":
            first_stop_candle,

    })


stop_summary = pd.DataFrame(
    stop_trade_rows
)

stop_touched = stop_summary[
    stop_summary["stop_touched"]
]

print(
    f"  Trades touching 1% stop: "
    f"{len(stop_touched)}"
)

if len(stop_touched):

    print(
        f"  Average net P&L: "
        f"₹{stop_touched['net_pnl'].mean():,.2f}"
    )

    print(
        f"  Average MFE: "
        f"{stop_touched['max_mfe_pct'].mean():.4f}%"
    )

    print(
        f"  Average MAE: "
        f"{stop_touched['max_mae_pct'].mean():.4f}%"
    )


# ============================================================
# FIRST POSITIVE CLOSE
# ============================================================

print(
    "\nFirst positive close:"
)

positive_rows = []

for (
    symbol,
    signal_timestamp,
), group in trade_groups:

    outcome = group.iloc[0][
        "outcome"
    ]

    positive = group[
        group["close_return_pct"] > 0
    ]

    first_positive = (
        int(
            positive.iloc[0][
                "candle_number"
            ]
        )
        if not positive.empty
        else None
    )

    positive_rows.append({

        "symbol":
            symbol,

        "signal_timestamp":
            signal_timestamp,

        "outcome":
            outcome,

        "first_positive_candle":
            first_positive,

    })


positive_summary = pd.DataFrame(
    positive_rows
)

for outcome in [
    "WIN",
    "LOSS",
]:

    subset = positive_summary[
        positive_summary["outcome"]
        == outcome
    ]

    reached = subset[
        subset["first_positive_candle"]
        .notna()
    ]

    print(
        f"  {outcome}: "
        f"{len(reached)}/{len(subset)} "
        f"eventually closed above entry"
    )

    if len(reached):

        print(
            f"    Median first positive candle: "
            f"{reached['first_positive_candle'].median():.1f}"
        )


# ============================================================
# FINAL INTERPRETATION
# ============================================================

print("\n")
print("=" * 72)
print("IMPORTANT")
print("=" * 72)

print(
    "\nThis is DESCRIPTIVE analysis only."
)

print(
    "No stop, horizon, target, or risk "
    "parameter has been changed."
)

print(
    "No OOS experiment has been performed."
)

print(
    "\nSaved:"
)

print(
    f"  {OUTPUT}"
)

print(
    "\nNext step:"
)

print(
    "Review the candle-by-candle path before "
    "creating any exit hypothesis."
)