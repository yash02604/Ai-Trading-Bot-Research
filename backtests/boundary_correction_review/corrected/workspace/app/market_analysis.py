import pandas as pd
import ta
import numpy as np


REQUIRED_CANDLES = 35


def candles_to_dataframe(candles):
    if not candles:
        raise ValueError("No candles received")

    columns = [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "open_interest",
    ]

    df = pd.DataFrame(candles, columns=columns)

    df["timestamp"] = pd.to_datetime(df["timestamp"])

    for column in [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]:
        df[column] = pd.to_numeric(
            df[column],
            errors="coerce",
        )

    df = (
        df.sort_values("timestamp")
        .reset_index(drop=True)
    )

    return df


def calculate_indicators(df):
    if df.empty:
        raise ValueError("Cannot calculate indicators from empty data")

    required = {"close", "volume"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    if len(df) < REQUIRED_CANDLES:
        raise ValueError(
            f"Insufficient candles: received {len(df)}, "
            f"need at least {REQUIRED_CANDLES}"
        )

    df = df.copy()
    for column in ("close", "volume"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    if not np.isfinite(df[["close", "volume"]].to_numpy(dtype=float)).all():
        raise ValueError("Close and volume must contain finite numeric values")
    if (df["close"] <= 0).any() or (df["volume"] < 0).any():
        raise ValueError("Close must be positive and volume cannot be negative")

    # EMA 9
    df["ema_9"] = ta.trend.EMAIndicator(
        close=df["close"],
        window=9,
    ).ema_indicator()

    # EMA 20
    df["ema_20"] = ta.trend.EMAIndicator(
        close=df["close"],
        window=20,
    ).ema_indicator()

    # RSI 14
    df["rsi_14"] = ta.momentum.RSIIndicator(
        close=df["close"],
        window=14,
    ).rsi()

    # MACD
    macd = ta.trend.MACD(
        close=df["close"],
        window_fast=12,
        window_slow=26,
        window_sign=9,
    )

    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_histogram"] = macd.macd_diff()

    # Volume MA 20
    df["volume_ma_20"] = (
        df["volume"]
        .rolling(window=20)
        .mean()
    )

    indicator_columns = [
        "ema_9",
        "ema_20",
        "rsi_14",
        "macd",
        "macd_signal",
        "macd_histogram",
        "volume_ma_20",
    ]

    # Remove rows where indicators are incomplete.
    df = df.dropna(
        subset=indicator_columns
    ).reset_index(drop=True)

    if df.empty:
        raise ValueError(
            "No valid rows remain after indicator calculation"
        )

    return df
