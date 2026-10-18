import pandas as pd
import numpy as np


DATA_FILE = "data/research/extended_historical_robustness_candles.csv"
TRADES_FILE = "backtests/validation/trades.csv"

OUTPUT_FILE = (
    "backtests/validation/entry_quality_analysis.csv"
)


# ============================================================
# LOAD DATA
# ============================================================

print("=== ENTRY QUALITY DIAGNOSTIC ===")

data = pd.read_csv(DATA_FILE)

data["timestamp"] = pd.to_datetime(
    data["timestamp"],
    utc=True,
).dt.tz_convert("Asia/Kolkata")

data = data.sort_values(
    ["stock", "timestamp"]
).reset_index(drop=True)


# ============================================================
# CALCULATE READ-ONLY FEATURES
# ============================================================

data["volume_ma20"] = (
    data.groupby("stock")["volume"]
    .transform(lambda x: x.rolling(20).mean())
)

data["ema9"] = (
    data.groupby("stock")["close"]
    .transform(lambda x: x.ewm(span=9, adjust=False).mean())
)

data["ema20"] = (
    data.groupby("stock")["close"]
    .transform(lambda x: x.ewm(span=20, adjust=False).mean())
)

# RSI 14
delta = data.groupby("stock")["close"].diff()

gain = delta.clip(lower=0)
loss = -delta.clip(upper=0)

avg_gain = (
    gain.groupby(data["stock"])
    .transform(lambda x: x.rolling(14).mean())
)

avg_loss = (
    loss.groupby(data["stock"])
    .transform(lambda x: x.rolling(14).mean())
)

rs = avg_gain / avg_loss.replace(0, np.nan)

data["rsi_14_calc"] = (
    100 - (100 / (1 + rs))
)


# ============================================================
# INDEX BY SYMBOL/TIMESTAMP
# ============================================================

groups = {
    symbol: frame.set_index("timestamp")
    for symbol, frame in data.groupby("stock")
}


# ============================================================
# LOAD PORTFOLIO TRADES
# ============================================================

trades = pd.read_csv(TRADES_FILE)

trades = trades[
    trades["book"] == "portfolio"
].copy()

trades["signal_timestamp"] = pd.to_datetime(
    trades["signal_timestamp"],
    utc=True,
).dt.tz_convert("Asia/Kolkata")

trades["entry_timestamp"] = pd.to_datetime(
    trades["entry_timestamp"],
    utc=True,
).dt.tz_convert("Asia/Kolkata")

trades["exit_timestamp"] = pd.to_datetime(
    trades["exit_timestamp"],
    utc=True,
).dt.tz_convert("Asia/Kolkata")


# ============================================================
# BUILD ENTRY-QUALITY RECORDS
# ============================================================

records = []


for trade in trades.itertuples():

    symbol = trade.symbol

    if symbol not in groups:
        continue

    frame = groups[symbol]

    signal_time = trade.signal_timestamp
    entry_time = trade.entry_timestamp
    exit_time = trade.exit_timestamp

    if signal_time not in frame.index:
        continue

    if entry_time not in frame.index:
        continue

    signal = frame.loc[signal_time]
    entry = frame.loc[entry_time]

    bars = frame.loc[
        entry_time:exit_time
    ]

    if bars.empty:
        continue

    # --------------------------------------------------------
    # Entry gap
    # --------------------------------------------------------

    entry_gap_pct = (
        (entry["open"] / signal["close"]) - 1
    ) * 100

    # --------------------------------------------------------
    # Volume ratio
    # --------------------------------------------------------

    if (
        pd.notna(signal["volume_ma20"])
        and signal["volume_ma20"] > 0
    ):
        volume_ratio = (
            signal["volume"]
            / signal["volume_ma20"]
        )
    else:
        volume_ratio = np.nan

    # --------------------------------------------------------
    # EMA relationship
    # --------------------------------------------------------

    ema9_distance_pct = (
        (signal["close"] / signal["ema9"]) - 1
    ) * 100

    ema20_distance_pct = (
        (signal["close"] / signal["ema20"]) - 1
    ) * 100

    ema9_vs_ema20_pct = (
        (signal["ema9"] / signal["ema20"]) - 1
    ) * 100

    # --------------------------------------------------------
    # Signal candle structure
    # --------------------------------------------------------

    signal_range_pct = (
        (signal["high"] - signal["low"])
        / signal["close"]
    ) * 100

    signal_body_pct = (
        abs(signal["close"] - signal["open"])
        / signal["close"]
    ) * 100

    # --------------------------------------------------------
    # MFE / MAE
    # --------------------------------------------------------

    mfe_pct = (
        (bars["high"].max() / entry["open"]) - 1
    ) * 100

    mae_pct = (
        (bars["low"].min() / entry["open"]) - 1
    ) * 100

    # --------------------------------------------------------
    # First-bar movement
    # --------------------------------------------------------

    first_bar = bars.iloc[0]

    first_bar_return_pct = (
        (first_bar["close"] / entry["open"]) - 1
    ) * 100

    # --------------------------------------------------------
    # Record
    # --------------------------------------------------------

    records.append(
        {
            "symbol": symbol,
            "signal_timestamp": signal_time,
            "entry_timestamp": entry_time,
            "exit_timestamp": exit_time,

            "decline_pct": trade.decline_pct,
            "rsi_14": trade.rsi_14,

            "entry_gap_pct": entry_gap_pct,

            "signal_volume_ratio": volume_ratio,

            "ema9_distance_pct": ema9_distance_pct,
            "ema20_distance_pct": ema20_distance_pct,
            "ema9_vs_ema20_pct": ema9_vs_ema20_pct,

            "signal_range_pct": signal_range_pct,
            "signal_body_pct": signal_body_pct,

            "first_bar_return_pct": first_bar_return_pct,

            "mfe_pct": mfe_pct,
            "mae_pct": mae_pct,

            "gross_pnl": trade.gross_pnl,
            "total_cost": trade.total_cost,
            "net_pnl": trade.net_pnl,

            "exit_reason": trade.exit_reason,

            "result": (
                "WIN"
                if trade.net_pnl > 0
                else "LOSS"
            ),
        }
    )


result = pd.DataFrame(records)


# ============================================================
# SAVE
# ============================================================

result.to_csv(
    OUTPUT_FILE,
    index=False,
)


# ============================================================
# SUMMARY
# ============================================================

print(
    f"\nAnalyzed trades: {len(result)}"
)

print(
    f"Saved: {OUTPUT_FILE}"
)


# ============================================================
# ALL TRADES
# ============================================================

print("\n=== ALL TRADES ===")

print(
    result[
        [
            "decline_pct",
            "rsi_14",
            "entry_gap_pct",
            "signal_volume_ratio",
            "ema9_distance_pct",
            "ema20_distance_pct",
            "ema9_vs_ema20_pct",
            "signal_range_pct",
            "signal_body_pct",
            "first_bar_return_pct",
            "mfe_pct",
            "mae_pct",
            "net_pnl",
        ]
    ]
    .describe()
    .round(3)
    .to_string()
)


# ============================================================
# WINNER VS LOSER
# ============================================================

print("\n=== WINNER VS LOSER ===")

summary = (
    result
    .groupby("result")
    .agg(
        trades=("net_pnl", "size"),

        avg_net_pnl=("net_pnl", "mean"),

        avg_decline=("decline_pct", "mean"),
        avg_rsi=("rsi_14", "mean"),

        avg_entry_gap=("entry_gap_pct", "mean"),

        avg_volume_ratio=(
            "signal_volume_ratio",
            "mean",
        ),

        avg_ema9_distance=(
            "ema9_distance_pct",
            "mean",
        ),

        avg_ema20_distance=(
            "ema20_distance_pct",
            "mean",
        ),

        avg_ema9_vs_ema20=(
            "ema9_vs_ema20_pct",
            "mean",
        ),

        avg_signal_range=(
            "signal_range_pct",
            "mean",
        ),

        avg_signal_body=(
            "signal_body_pct",
            "mean",
        ),

        avg_first_bar_return=(
            "first_bar_return_pct",
            "mean",
        ),

        avg_mfe=("mfe_pct", "mean"),
        avg_mae=("mae_pct", "mean"),
    )
)

print(
    summary.round(4).to_string()
)


# ============================================================
# EXIT REASON
# ============================================================

print("\n=== EXIT REASON ===")

print(
    result
    .groupby("exit_reason")
    .agg(
        trades=("net_pnl", "size"),
        avg_net=("net_pnl", "mean"),
        avg_mfe=("mfe_pct", "mean"),
        avg_mae=("mae_pct", "mean"),
        avg_volume_ratio=(
            "signal_volume_ratio",
            "mean",
        ),
    )
    .round(4)
    .to_string()
)


# ============================================================
# MFE THRESHOLDS
# ============================================================

print("\n=== MFE THRESHOLDS ===")

for threshold in [
    0.25,
    0.50,
    0.75,
    1.00,
]:

    winners = (
        (result["net_pnl"] > 0)
        & (result["mfe_pct"] >= threshold)
    ).sum()

    losers = (
        (result["net_pnl"] <= 0)
        & (result["mfe_pct"] >= threshold)
    ).sum()

    print(
        f"MFE >= {threshold:.2f}%: "
        f"winners={winners}, "
        f"losers={losers}"
    )


# ============================================================
# FIRST-BAR FOLLOW-THROUGH
# ============================================================

print("\n=== FIRST-BAR FOLLOW-THROUGH ===")

print(
    result
    .groupby("result")["first_bar_return_pct"]
    .agg(
        [
            "count",
            "mean",
            "median",
            "min",
            "max",
        ]
    )
    .round(4)
    .to_string()
)


# ============================================================
# NO STRATEGY CHANGES
# ============================================================

print(
    "\nNOTE:"
    "\nThis is descriptive analysis only."
    "\nNo frozen-strategy parameters were changed."
    "\nNo threshold was optimized."
)