import pandas as pd

from strategy_backtest import (
    STOCKS,
    collect_historical_candles,
)

from app.market_analysis import calculate_indicators
from app.reversal_detector import detect_reversal_candidates


DECLINE_CANDLES = 3
MIN_DECLINE_PCT = 0.5
RSI_THRESHOLD = 30

EVENT_GAP_MINUTES = 15


def classify_decline(decline_pct):
    decline = abs(decline_pct)

    if 0.5 <= decline < 1.0:
        return "Mild"
    elif 1.0 <= decline < 2.0:
        return "Moderate"
    elif decline >= 2.0:
        return "Strong"

    return None


def classify_rsi(rsi):
    if 25 <= rsi <= 30:
        return "RSI25-30"
    elif 20 <= rsi < 25:
        return "RSI20-25"
    elif rsi < 20:
        return "RSI<20"

    return None


def collect_signals():
    all_signals = []

    for stock, instrument_key in STOCKS.items():
        candles = collect_historical_candles(instrument_key)

        if not candles:
            continue

        # Existing function returns a list of candles.
        df = pd.DataFrame(
            candles,
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "open_interest",
            ],
        )

        df["timestamp"] = pd.to_datetime(
            df["timestamp"],
            errors="coerce",
        )

        df = calculate_indicators(df)
        candidates = detect_reversal_candidates(
            df,
            decline_candles=DECLINE_CANDLES,
            min_decline_pct=MIN_DECLINE_PCT,
            rsi_threshold=RSI_THRESHOLD,
        )

        candidates = candidates[
            candidates["reversal_candidate"]
        ].copy()

        for timestamp, row in candidates.iterrows():
            decline_group = classify_decline(row["decline_pct"])
            rsi_group = classify_rsi(row["rsi_14"])

            if decline_group != "Moderate":
                continue

            if rsi_group != "RSI20-25":
                continue

            all_signals.append(
                {
                    "stock": stock,
                    "timestamp": row["timestamp"],
                    "decline_pct": row["decline_pct"],
                    "rsi": row["rsi_14"],
                    "close": row["close"],
                }
            )

    return pd.DataFrame(all_signals)


def group_events(signals):
    if signals.empty:
        return signals

    signals = signals.copy()

    # Normalize timestamps without assuming a numeric unit.
    signals["timestamp"] = signals["timestamp"].map(
        lambda x: pd.Timestamp(x)
    )

    signals = signals.sort_values(
        ["stock", "timestamp"]
    ).copy()

    signals["previous_timestamp"] = (
        signals.groupby("stock")["timestamp"].shift(1)
    )

    signals["gap_minutes"] = (
        signals["timestamp"] - signals["previous_timestamp"]
    ).apply(
        lambda x: x.total_seconds() / 60
        if pd.notna(x)
        else float("nan")
    )

    signals["new_event"] = (
        signals["previous_timestamp"].isna()
        | (signals["gap_minutes"] > EVENT_GAP_MINUTES)
    )

    signals["event_number"] = (
        signals.groupby("stock")["new_event"].cumsum()
    )

    signals["event_id"] = (
        signals["stock"]
        + "_event_"
        + signals["event_number"].astype(str)
    )

    return signals


def main():
    signals = collect_signals()

    print("\n=== ORIGINAL SIGNAL COUNT ===")
    print("Signals:", len(signals))

    if signals.empty:
        print("No matching signals found.")
        return

    grouped = group_events(signals)

    print("\n=== SIGNALS WITH EVENT GROUPING ===")
    print(
        grouped[
            [
                "stock",
                "timestamp",
                "decline_pct",
                "rsi",
                "gap_minutes",
                "event_id",
            ]
        ].to_string(index=False)
    )

    events = (
        grouped.groupby("event_id")
        .agg(
            stock=("stock", "first"),
            first_signal=("timestamp", "first"),
            last_signal=("timestamp", "last"),
            signal_count=("timestamp", "count"),
        )
        .reset_index()
    )

    print("\n=== EVENT SUMMARY ===")
    print("Independent events:", len(events))
    print("Original signals:", len(grouped))
    print(
        "Signals merged into existing events:",
        len(grouped) - len(events),
    )

    print("\n=== EVENTS BY STOCK ===")
    print(
        events.groupby("stock")
        .size()
        .rename("events")
        .to_string()
    )

    print("\n=== MULTI-SIGNAL EVENTS ===")

    multi = events[events["signal_count"] > 1]

    if multi.empty:
        print("No multi-signal events found.")
    else:
        print(multi.to_string(index=False))


if __name__ == "__main__":
    main()
