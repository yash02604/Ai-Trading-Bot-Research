import pandas as pd
import ta
import numpy as np
from app.candle_integrity import CandleDataError, require_valid_candles, validate_candles


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

    report = validate_candles(df)
    timestamp_errors = [e for e in report.errors if e["reason"] in {
        "invalid_timestamp", "timezone_naive_timestamp", "timestamp_not_five_minute_aligned",
        "duplicate_instrument_timestamp", "nonfinite_or_nonnumeric_ohlcv",
    }]
    if timestamp_errors:
        raise CandleDataError("Invalid candle timestamps or duplicate candle keys", report)
    df = report.frame

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

    df = df.sort_values("timestamp", kind="stable").reset_index(drop=True)

    return df


def calculate_indicators(df):
    if df.empty:
        raise ValueError("Cannot calculate indicators from empty data")

    if len(df) < REQUIRED_CANDLES:
        raise ValueError(
            f"Insufficient candles: received {len(df)}, "
            f"need at least {REQUIRED_CANDLES}"
        )

    df, _ = require_valid_candles(df)
    df = df.sort_values("timestamp", kind="stable").reset_index(drop=True)

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
