from pathlib import Path

import numpy as np
import pandas as pd

from app.market_analysis import calculate_indicators


ROOT = Path(__file__).resolve().parent

CANDLE_FILE = (
    ROOT
    / "data"
    / "research"
    / "extended_historical_robustness_candles.csv"
)

TRADES_FILE = (
    ROOT
    / "backtests"
    / "validation"
    / "trades.csv"
)

OUTPUT_FILE = (
    ROOT
    / "backtests"
    / "validation"
    / "signal_candle_quality_analysis.csv"
)


def calculate_signal_features(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate only information available at each completed signal candle."""

    df = df.copy()

    # Raw candle structure
    candle_range = df["high"] - df["low"]

    df["body_pct"] = (
        (df["close"] - df["open"])
        / df["open"]
        * 100
    )

    df["body_abs_pct"] = (
        (df["close"] - df["open"]).abs()
        / df["open"]
        * 100
    )

    df["bullish_candle"] = (
        df["close"] > df["open"]
    ).astype(int)

    df["range_pct"] = (
        candle_range
        / df["open"]
        * 100
    )

    # Close location:
    # 0 = close at low
    # 1 = close at high
    df["close_location"] = np.where(
        candle_range > 0,
        (df["close"] - df["low"]) / candle_range,
        0.5,
    )

    df["lower_wick_pct"] = (
        (
            df[["open", "close"]].min(axis=1)
            - df["low"]
        )
        / df["open"]
        * 100
    )

    df["upper_wick_pct"] = (
        (
            df["high"]
            - df[["open", "close"]].max(axis=1)
        )
        / df["open"]
        * 100
    )

    # Price momentum available at signal time
    df["previous_close_return_pct"] = (
        df["close"].pct_change()
        * 100
    )

    df["recent_3bar_return_pct"] = (
        df["close"].pct_change(3)
        * 100
    )

    # Consecutive declining closes ending at current candle
    close_down = df["close"] < df["close"].shift(1)

    streak = []
    current_streak = 0

    for value in close_down:
        if bool(value):
            current_streak += 1
        else:
            current_streak = 0
        streak.append(current_streak)

    df["consecutive_declining_closes"] = streak

    # Indicator changes
    df["rsi_change"] = (
        df["rsi_14"]
        - df["rsi_14"].shift(1)
    )

    df["macd_histogram_change"] = (
        df["macd_histogram"]
        - df["macd_histogram"].shift(1)
    )

    df["macd_change"] = (
        df["macd"]
        - df["macd"].shift(1)
    )

    df["macd_signal_change"] = (
        df["macd_signal"]
        - df["macd_signal"].shift(1)
    )

    # Existing indicator relationships
    df["ema9_distance_pct"] = (
        (df["close"] - df["ema_9"])
        / df["close"]
        * 100
    )

    df["ema20_distance_pct"] = (
        (df["close"] - df["ema_20"])
        / df["close"]
        * 100
    )

    df["ema9_vs_ema20_pct"] = (
        (df["ema_9"] - df["ema_20"])
        / df["ema_20"]
        * 100
    )

    df["volume_ratio"] = np.where(
        df["volume_ma_20"] > 0,
        df["volume"] / df["volume_ma_20"],
        np.nan,
    )

    return df


def prepare_candles() -> pd.DataFrame:
    print("Loading historical candles...")

    candles = pd.read_csv(CANDLE_FILE)

    candles["timestamp"] = pd.to_datetime(
        candles["timestamp"]
    )

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]

    for column in numeric_columns:
        candles[column] = pd.to_numeric(
            candles[column],
            errors="coerce",
        )

    candles = candles.sort_values(
        ["stock", "timestamp"]
    ).reset_index(drop=True)

    frames = []

    for symbol, group in candles.groupby(
        "stock",
        sort=False,
    ):
        group = group.copy()

        # Reuse the exact project indicator implementation.
        group = calculate_indicators(group)

        group = calculate_signal_features(group)

        frames.append(group)

    result = pd.concat(
        frames,
        ignore_index=True,
    )

    return result


def load_trades() -> pd.DataFrame:
    print("Loading validation trades...")

    trades = pd.read_csv(TRADES_FILE)

    trades["signal_timestamp"] = pd.to_datetime(
        trades["signal_timestamp"]
    )

    return trades


def main():
    candles = prepare_candles()
    trades = load_trades()

    trades = trades[
        trades["book"] == "portfolio"
    ].copy()

    print(f"Portfolio validation trades: {len(trades)}")

    print(f"Prepared candle rows: {len(candles):,}")
    print(f"Validation trades: {len(trades)}")

    # Only signal-time candle information is joined.
    signal_columns = [
        "stock",
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "body_pct",
        "body_abs_pct",
        "bullish_candle",
        "range_pct",
        "close_location",
        "lower_wick_pct",
        "upper_wick_pct",
        "previous_close_return_pct",
        "recent_3bar_return_pct",
        "consecutive_declining_closes",
        "rsi_change",
        "macd_change",
        "macd_signal_change",
        "macd_histogram_change",
        "volume_ratio",
        "ema9_distance_pct",
        "ema20_distance_pct",
        "ema9_vs_ema20_pct",
        "rsi_14",
        "macd",
        "macd_signal",
        "macd_histogram",
    ]

    signal_features = candles[signal_columns].copy()

    signal_features = signal_features.rename(
        columns={
            "stock": "symbol",
            "timestamp": "signal_timestamp",
        }
    )

    result = trades.merge(
        signal_features,
        on=["symbol", "signal_timestamp"],
        how="left",
        validate="one_to_one",
        suffixes=("", "_candle"),
    )

    if "result" not in result.columns:
        result["result"] = np.where(
            result["net_pnl"].fillna(0) > 0,
            "WIN",
            "LOSS",
        )

    feature_columns = [
        "body_pct",
        "body_abs_pct",
        "bullish_candle",
        "range_pct",
        "close_location",
        "lower_wick_pct",
        "upper_wick_pct",
        "previous_close_return_pct",
        "recent_3bar_return_pct",
        "consecutive_declining_closes",
        "rsi_change",
        "macd_change",
        "macd_signal_change",
        "macd_histogram_change",
        "volume_ratio",
        "ema9_distance_pct",
        "ema20_distance_pct",
        "ema9_vs_ema20_pct",
        "rsi_14",
        "macd_histogram",
    ]

    missing_matches = result[feature_columns].isna().all(axis=1).sum()

    if missing_matches:
        raise RuntimeError(
            f"Could not match {missing_matches} validation trades "
            "to their signal candles."
        )

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    result.to_csv(
        OUTPUT_FILE,
        index=False,
    )

    print()
    print("Signal-candle analysis created successfully.")
    print(f"Output: {OUTPUT_FILE}")
    print(f"Rows: {len(result)}")
    print()

    print("Result counts:")
    print(
        result["result"]
        .value_counts()
        .to_string()
    )

    print()
    print("Signal-time feature summary:")
    print(
        result.groupby("result")[feature_columns]
        .mean(numeric_only=True)
        .round(4)
        .to_string()
    )


if __name__ == "__main__":
    main()