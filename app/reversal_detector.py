import pandas as pd
import math


def detect_reversal_candidates(
    df,
    decline_candles=3,
    min_decline_pct=0.5,
    rsi_threshold=30,
):
    """
    Identify potential sharp upward reversals.

    This is a research detector only.
    It does not place trades.

    Conditions:
    1. Price has declined over the previous N candles.
    2. The decline reaches the minimum percentage.
    3. RSI is at or below the configured threshold.
    4. The current candle closes above the previous candle.
    """

    if df.empty:
        raise ValueError("Cannot detect reversals from empty data")
    if decline_candles < 1 or not math.isfinite(min_decline_pct) or min_decline_pct < 0:
        raise ValueError("Invalid decline window or threshold")
    if not math.isfinite(rsi_threshold) or not 0 <= rsi_threshold <= 100:
        raise ValueError("RSI threshold must be between 0 and 100")

    required_columns = [
        "close",
        "rsi_14",
    ]

    missing = [
        column
        for column in required_columns
        if column not in df.columns
    ]

    if missing:
        raise ValueError(
            f"Missing required columns: {missing}"
        )

    result = df.copy()

    result["decline_pct"] = (
        (
            result["close"]
            - result["close"].shift(decline_candles)
        )
        / result["close"].shift(decline_candles)
    ) * 100

    result["decline_condition"] = (
        result["decline_pct"] <= -min_decline_pct
    )

    result["oversold_condition"] = (
        result["rsi_14"] <= rsi_threshold
    )

    result["reversal_condition"] = (
        result["close"] > result["close"].shift(1)
    )

    result["reversal_candidate"] = (
        result["decline_condition"]
        & result["oversold_condition"]
        & result["reversal_condition"]
    )

    return result


def is_frozen_reversal(row):
    """The project's researched long setup: 1–2% decline, RSI 20–25, turn-up."""
    try:
        decline = float(row["decline_pct"])
        rsi = float(row["rsi_14"])
        reversal = bool(row["reversal_condition"])
    except (KeyError, TypeError, ValueError):
        return False
    return math.isfinite(decline) and math.isfinite(rsi) and -2.0 < decline <= -1.0 and 20.0 <= rsi < 25.0 and reversal


if __name__ == "__main__":
    print("Reversal detector module loaded successfully.")
