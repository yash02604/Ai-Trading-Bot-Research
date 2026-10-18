from pathlib import Path

import pandas as pd

from app.historical_validation import (
    load_historical_data,
    prepare_symbol_frames,
    _make_events,
)


# ============================================================
# STAGE 8.2 — EARLY TRADE-PATH ANALYSIS
#
# DESCRIPTIVE ONLY
#
# Purpose:
# Determine whether information available during the first
# 1-2 candles after entry can distinguish eventual winners
# from eventual losers.
#
# IMPORTANT:
# - No future information is used for the diagnostic feature.
# - No strategy parameters are changed.
# - No OOS test is performed.
# - This does NOT create an exit rule.
# ============================================================


SOURCE = Path(
    "data/research/extended_historical_robustness_candles.csv"
)

TRADES_FILE = Path(
    "backtests/validation/trades.csv"
)

OUTPUT = Path(
    "backtests/validation/early_trade_path_analysis.csv"
)


# ============================================================
# LOAD DATA
# ============================================================

print("=" * 72)
print("STAGE 8.2 — EARLY TRADE-PATH ANALYSIS")
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
# LOAD PORTFOLIO TRADES
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
# HELPERS
# ============================================================

def pct_return(
    entry_price,
    price,
):

    if entry_price == 0:
        return 0.0

    return (
        (price / entry_price) - 1.0
    ) * 100.0


# ============================================================
# BUILD TRADE-LEVEL EARLY FEATURES
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

    # Need at least candles 1 and 2.
    if (
        entry_index + 2
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

    outcome = (
        "WIN"
        if float(trade["net_pnl"]) > 0
        else "LOSS"
    )

    # --------------------------------------------------------
    # CANDLE 1
    # --------------------------------------------------------

    c1 = frame.iloc[
        entry_index + 1
    ]

    c1_open = float(c1["open"])
    c1_high = float(c1["high"])
    c1_low = float(c1["low"])
    c1_close = float(c1["close"])

    c1_close_return = pct_return(
        entry_price,
        c1_close,
    )

    c1_high_return = pct_return(
        entry_price,
        c1_high,
    )

    c1_low_return = pct_return(
        entry_price,
        c1_low,
    )

    c1_range_pct = pct_return(
        c1_open,
        c1_high,
    ) - pct_return(
        c1_open,
        c1_low,
    )

    c1_bullish = (
        c1_close > c1_open
    )

    c1_above_entry = (
        c1_close > entry_price
    )

    c1_reached_025 = (
        c1_high_return >= 0.25
    )

    c1_reached_050 = (
        c1_high_return >= 0.50
    )

    c1_stop_approached = (
        c1_low_return <= -0.75
    )

    c1_stop_touched = (
        c1_low_return <= -1.00
    )

    # --------------------------------------------------------
    # CANDLE 2
    # --------------------------------------------------------

    c2 = frame.iloc[
        entry_index + 2
    ]

    c2_open = float(c2["open"])
    c2_high = float(c2["high"])
    c2_low = float(c2["low"])
    c2_close = float(c2["close"])

    c2_close_return = pct_return(
        entry_price,
        c2_close,
    )

    c2_high_return = pct_return(
        entry_price,
        c2_high,
    )

    c2_low_return = pct_return(
        entry_price,
        c2_low,
    )

    c2_bullish = (
        c2_close > c2_open
    )

    c2_above_entry = (
        c2_close > entry_price
    )

    c2_reached_025 = (
        c2_high_return >= 0.25
    )

    c2_reached_050 = (
        c2_high_return >= 0.50
    )

    c2_stop_approached = (
        c2_low_return <= -0.75
    )

    c2_stop_touched = (
        c2_low_return <= -1.00
    )

    # --------------------------------------------------------
    # TWO-CANDLE PATH FEATURES
    # --------------------------------------------------------

    two_candle_max_high = max(
        c1_high,
        c2_high,
    )

    two_candle_min_low = min(
        c1_low,
        c2_low,
    )

    two_candle_mfe = pct_return(
        entry_price,
        two_candle_max_high,
    )

    two_candle_mae = pct_return(
        entry_price,
        two_candle_min_low,
    )

    # Positive close by candle 2.
    positive_by_candle_2 = (
        c1_close > entry_price
        or c2_close > entry_price
    )

    # Bullish sequence.
    two_bullish = (
        c1_bullish
        and c2_bullish
    )

    # Favorable momentum:
    # candle 2 close is better than candle 1 close.
    improving_close = (
        c2_close_return
        > c1_close_return
    )

    # Deteriorating path:
    # candle 2 closes below candle 1.
    deteriorating_close = (
        c2_close_return
        < c1_close_return
    )

    rows.append({

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

        "net_pnl":
            float(trade["net_pnl"]),

        "actual_exit_reason":
            trade.get(
                "exit_reason",
                "",
            ),

        # Candle 1
        "c1_close_return_pct":
            c1_close_return,

        "c1_high_return_pct":
            c1_high_return,

        "c1_low_return_pct":
            c1_low_return,

        "c1_range_pct":
            c1_range_pct,

        "c1_bullish":
            c1_bullish,

        "c1_above_entry":
            c1_above_entry,

        "c1_reached_025":
            c1_reached_025,

        "c1_reached_050":
            c1_reached_050,

        "c1_stop_approached":
            c1_stop_approached,

        "c1_stop_touched":
            c1_stop_touched,

        # Candle 2
        "c2_close_return_pct":
            c2_close_return,

        "c2_high_return_pct":
            c2_high_return,

        "c2_low_return_pct":
            c2_low_return,

        "c2_bullish":
            c2_bullish,

        "c2_above_entry":
            c2_above_entry,

        "c2_reached_025":
            c2_reached_025,

        "c2_reached_050":
            c2_reached_050,

        "c2_stop_approached":
            c2_stop_approached,

        "c2_stop_touched":
            c2_stop_touched,

        # Combined path
        "two_candle_mfe_pct":
            two_candle_mfe,

        "two_candle_mae_pct":
            two_candle_mae,

        "positive_by_candle_2":
            positive_by_candle_2,

        "two_bullish":
            two_bullish,

        "improving_close":
            improving_close,

        "deteriorating_close":
            deteriorating_close,
    })


# ============================================================
# DATAFRAME
# ============================================================

analysis = pd.DataFrame(rows)

print(
    f"\nAnalysis rows: {len(analysis)}"
)

print(
    f"Missing event matches: "
    f"{missing_events}"
)


if analysis.empty:

    raise RuntimeError(
        "No analysis rows generated."
    )


OUTPUT.parent.mkdir(
    parents=True,
    exist_ok=True,
)

analysis.to_csv(
    OUTPUT,
    index=False,
)


# ============================================================
# BASIC OUTCOME SUMMARY
# ============================================================

print("\n")
print("=" * 72)
print("STAGE 8.2 RESULTS")
print("=" * 72)

print(
    "\nOutcome counts:"
)

print(
    analysis["outcome"]
    .value_counts()
    .to_string()
)


# ============================================================
# MEAN EARLY-PATH FEATURES
# ============================================================

print(
    "\nEarly-path averages:"
)

numeric_features = [

    "c1_close_return_pct",
    "c1_high_return_pct",
    "c1_low_return_pct",

    "c2_close_return_pct",
    "c2_high_return_pct",
    "c2_low_return_pct",

    "two_candle_mfe_pct",
    "two_candle_mae_pct",
]

mean_summary = (
    analysis
    .groupby("outcome")[numeric_features]
    .mean()
)

print(
    mean_summary.to_string()
)


# ============================================================
# MEDIANS
# ============================================================

print(
    "\nEarly-path medians:"
)

median_summary = (
    analysis
    .groupby("outcome")[numeric_features]
    .median()
)

print(
    median_summary.to_string()
)


# ============================================================
# BINARY FEATURE SEPARATION
# ============================================================

binary_features = [

    "c1_bullish",
    "c1_above_entry",
    "c1_reached_025",
    "c1_reached_050",
    "c1_stop_approached",
    "c1_stop_touched",

    "c2_bullish",
    "c2_above_entry",
    "c2_reached_025",
    "c2_reached_050",
    "c2_stop_approached",
    "c2_stop_touched",

    "positive_by_candle_2",
    "two_bullish",
    "improving_close",
    "deteriorating_close",
]


print(
    "\nBinary feature separation:"
)

binary_rows = []

for feature in binary_features:

    for outcome in [
        "WIN",
        "LOSS",
    ]:

        subset = analysis[
            analysis["outcome"]
            == outcome
        ]

        rate = (
            subset[feature]
            .astype(bool)
            .mean()
            * 100
        )

        binary_rows.append({

            "feature":
                feature,

            "outcome":
                outcome,

            "trades":
                len(subset),

            "true_count":
                int(
                    subset[feature]
                    .astype(bool)
                    .sum()
                ),

            "true_rate_pct":
                rate,

        })


binary_summary = pd.DataFrame(
    binary_rows
)

print(
    binary_summary.to_string(
        index=False
    )
)


# ============================================================
# SIMPLE CANDIDATE CONDITIONS
#
# These are NOT strategy rules.
# They are only descriptive comparisons.
# ============================================================

conditions = {

    "c1_close_positive":
        analysis[
            "c1_close_return_pct"
        ] > 0,

    "c1_close_ge_025":
        analysis[
            "c1_close_return_pct"
        ] >= 0.25,

    "c1_high_ge_025":
        analysis[
            "c1_high_return_pct"
        ] >= 0.25,

    "c1_high_ge_050":
        analysis[
            "c1_high_return_pct"
        ] >= 0.50,

    "c2_close_positive":
        analysis[
            "c2_close_return_pct"
        ] > 0,

    "c2_close_ge_025":
        analysis[
            "c2_close_return_pct"
        ] >= 0.25,

    "c2_high_ge_025":
        analysis[
            "c2_high_return_pct"
        ] >= 0.25,

    "c2_high_ge_050":
        analysis[
            "c2_high_return_pct"
        ] >= 0.50,

    "two_bullish":
        analysis[
            "two_bullish"
        ],

    "improving_close":
        analysis[
            "improving_close"
        ],

    "deteriorating_close":
        analysis[
            "deteriorating_close"
        ],

    "c1_stop_approached":
        analysis[
            "c1_stop_approached"
        ],

    "c2_stop_approached":
        analysis[
            "c2_stop_approached"
        ],
}


print(
    "\nCandidate condition outcome rates:"
)

condition_rows = []

for name, mask in conditions.items():

    selected = analysis[mask]

    if selected.empty:
        continue

    winners = (
        selected["outcome"]
        == "WIN"
    ).sum()

    losers = (
        selected["outcome"]
        == "LOSS"
    ).sum()

    win_rate = (
        winners
        / len(selected)
        * 100
    )

    avg_pnl = (
        selected["net_pnl"]
        .mean()
    )

    condition_rows.append({

        "condition":
            name,

        "trades":
            len(selected),

        "wins":
            winners,

        "losses":
            losers,

        "win_rate_pct":
            win_rate,

        "avg_net_pnl":
            avg_pnl,

    })


condition_summary = pd.DataFrame(
    condition_rows
)

print(
    condition_summary.to_string(
        index=False
    )
)


# ============================================================
# STOP APPROACH VS OUTCOME
# ============================================================

print(
    "\nStop approach analysis:"
)

for feature in [
    "c1_stop_approached",
    "c2_stop_approached",
]:

    selected = analysis[
        analysis[feature]
    ]

    if selected.empty:
        continue

    print(
        f"\n{feature}:"
    )

    print(
        f"  Trades: {len(selected)}"
    )

    print(
        f"  Winners: "
        f"{(selected['outcome'] == 'WIN').sum()}"
    )

    print(
        f"  Losers: "
        f"{(selected['outcome'] == 'LOSS').sum()}"
    )

    print(
        f"  Average net P&L: "
        f"₹{selected['net_pnl'].mean():,.2f}"
    )


# ============================================================
# SAVE SUMMARY TABLES
# ============================================================

mean_summary.to_csv(
    OUTPUT.parent
    / "early_path_means.csv"
)

median_summary.to_csv(
    OUTPUT.parent
    / "early_path_medians.csv"
)

binary_summary.to_csv(
    OUTPUT.parent
    / "early_path_binary_features.csv",
    index=False,
)

condition_summary.to_csv(
    OUTPUT.parent
    / "early_path_conditions.csv",
    index=False,
)


# ============================================================
# FINAL
# ============================================================

print("\n")
print("=" * 72)
print("IMPORTANT")
print("=" * 72)

print(
    "\nThis is DESCRIPTIVE analysis only."
)

print(
    "The conditions above are NOT trading rules."
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
    "  backtests\\validation\\"
    "early_path_means.csv"
)

print(
    "  backtests\\validation\\"
    "early_path_medians.csv"
)

print(
    "  backtests\\validation\\"
    "early_path_binary_features.csv"
)

print(
    "  backtests\\validation\\"
    "early_path_conditions.csv"
)

print(
    "\nNext step:"
)

print(
    "Review whether any early-path behavior "
    "is strong enough to justify ONE "
    "pre-registered exit hypothesis."
)