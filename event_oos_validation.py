from numbers import Integral

import pandas as pd

from strategy_backtest import (
    STOCKS,
    collect_historical_candles,
    simulate_stop_loss,
)

from app.market_analysis import calculate_indicators
from app.reversal_detector import detect_reversal_candidates


DECLINE_CANDLES = 3
MIN_DECLINE_PCT = 0.5
RSI_THRESHOLD = 30

TARGET_STOP_PCT = 1.00
HORIZON = 6
EVENT_GAP_MINUTES = 15

DISCOVERY_END = pd.Timestamp("2026-06-30 23:59:59", tz="Asia/Kolkata")
VALIDATION_START = pd.Timestamp("2026-07-01 00:00:00", tz="Asia/Kolkata")
VALIDATION_END = pd.Timestamp("2026-09-01 23:59:59", tz="Asia/Kolkata")


def collect_qualifying_signals():
    all_signals = []

    for stock, instrument_key in STOCKS.items():
        print(f"\nLoading {stock}...")

        candles = collect_historical_candles(instrument_key)

        if not candles:
            continue

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

        # Keep timestamp as a column because calculate_indicators()
        # resets the DataFrame index.
        df["timestamp"] = df["timestamp"].map(
            lambda x: pd.Timestamp(x)
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

        for _, row in candidates.iterrows():

            decline = abs(row["decline_pct"])
            rsi = row["rsi_14"]

            # Frozen discovered rule:
            # Moderate decline + RSI 20-25
            if not (1.0 <= decline < 2.0):
                continue

            if not (20 <= rsi < 25):
                continue

            all_signals.append(
                {
                    "stock": stock,
                    "timestamp": pd.Timestamp(row["timestamp"]),
                    "decline_pct": row["decline_pct"],
                    "rsi": rsi,
                    "close": row["close"],
                    "candles": df,
                }
            )

    return pd.DataFrame(all_signals)


def group_events(signals):
    if signals.empty:
        return signals

    signals = signals.copy()

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


def split_period(timestamp):
    if timestamp <= DISCOVERY_END:
        return "DISCOVERY"

    if VALIDATION_START <= timestamp <= VALIDATION_END:
        return "VALIDATION"

    return "OUTSIDE_PERIOD"


def run_backtest(events):
    results = []
    audit = []

    for _, event in events.iterrows():
        df = event["candles"]
        timestamp = event["timestamp"]
        audit_row = {
            "event_id": event["event_id"],
            "stock": event["stock"],
            "signal_timestamp": timestamp,
            "decline_pct": event["decline_pct"],
            "rsi": event["rsi"],
            "period": event["period"],
            "available_future_candles": None,
            "status": None,
            "reason": "",
        }

        if event["period"] == "OUTSIDE_PERIOD":
            audit_row["status"] = "SKIPPED"
            audit_row["reason"] = "Event timestamp is outside the frozen periods."
            audit.append(audit_row)
            continue

        if "timestamp" not in df.columns:
            audit_row["status"] = "SKIPPED"
            audit_row["reason"] = "DataFrame has no timestamp column."
            audit.append(audit_row)
            continue

        if not isinstance(df.index, pd.RangeIndex) or df.index.start != 0 or df.index.step != 1:
            audit_row["status"] = "SKIPPED"
            audit_row["reason"] = (
                "Unexpected DataFrame index; expected the zero-based RangeIndex "
                "produced by calculate_indicators()."
            )
            audit.append(audit_row)
            continue

        # Find the original candle corresponding to the signal.
        matches = df.index[
            df["timestamp"] == timestamp
        ]

        if len(matches) == 0:
            audit_row["status"] = "SKIPPED"
            audit_row["reason"] = (
                "Timestamp/index mismatch: no candle timestamp matches the signal."
            )
            audit.append(audit_row)
            continue

        if len(matches) > 1:
            audit_row["status"] = "SKIPPED"
            audit_row["reason"] = (
                f"Timestamp/index mismatch: {len(matches)} candles match the signal."
            )
            audit.append(audit_row)
            continue

        try:
            signal_position = df.index.get_loc(matches[0])

            if not isinstance(signal_position, Integral):
                audit_row["status"] = "SKIPPED"
                audit_row["reason"] = (
                    "Invalid signal position: index lookup did not return one integer."
                )
                audit.append(audit_row)
                continue

            signal_position = int(signal_position)
            if signal_position < 0 or signal_position >= len(df):
                audit_row["status"] = "SKIPPED"
                audit_row["reason"] = (
                    f"Invalid signal position {signal_position} for "
                    f"DataFrame length {len(df)}."
                )
                audit.append(audit_row)
                continue

            audit_row["available_future_candles"] = (
                len(df) - signal_position - 1
            )

            # Preserve the existing positional call and simulator signature.
            result = simulate_stop_loss(
                df,
                signal_position,
                stop_pct=TARGET_STOP_PCT,
            )
        except Exception as exc:
            audit_row["status"] = "ERROR"
            audit_row["reason"] = f"{type(exc).__name__}: {exc}"
            audit.append(audit_row)
            continue

        if result is None:
            audit_row["status"] = "SKIPPED"
            available = audit_row["available_future_candles"]
            if available == 0:
                audit_row["reason"] = (
                    "simulate_stop_loss() returned None: no candles after the signal."
                )
            else:
                audit_row["reason"] = (
                    "simulate_stop_loss() returned None despite "
                    f"{available} available future candles."
                )
            audit.append(audit_row)
            continue

        result["stock"] = event["stock"]
        result["signal_timestamp"] = timestamp
        result["decline_pct"] = event["decline_pct"]
        result["rsi"] = event["rsi"]
        result["period"] = split_period(timestamp)
        result["event_id"] = event["event_id"]
        available = audit_row["available_future_candles"]
        if available < HORIZON:
            audit_row["status"] = "RESULT_RETURNED_PARTIAL_HORIZON"
            audit_row["reason"] = (
                f"simulate_stop_loss() returned a result with only "
                f"{available} of {HORIZON} future candles available."
            )
        else:
            audit_row["status"] = "RESULT_RETURNED"
            audit_row["reason"] = ""

        results.append(result)
        audit.append(audit_row)

    return pd.DataFrame(results), pd.DataFrame(audit)


def print_event_inventory(events):
    print("\n=== INDEPENDENT EVENT INVENTORY (BEFORE BACKTEST) ===")
    inventory = events.rename(
        columns={
            "timestamp": "signal_timestamp",
            "rsi": "RSI",
        }
    )
    print(
        inventory[
            [
                "event_id",
                "stock",
                "signal_timestamp",
                "decline_pct",
                "RSI",
                "period",
            ]
        ].to_string(index=False)
    )


def print_backtest_audit(audit):
    print("\n=== PER-EVENT BACKTEST AUDIT ===")
    if audit.empty:
        print("No events were passed to run_backtest().")
        return

    print(
        audit[
            [
                "event_id",
                "stock",
                "signal_timestamp",
                "period",
                "available_future_candles",
                "status",
                "reason",
            ]
        ].to_string(index=False)
    )


def print_summary(results, events, period):
    period_events = events[events["period"] == period]
    if results.empty:
        data = results.copy()
    else:
        data = results[results["period"] == period].copy()

    print(f"\n=== {period} SUMMARY ===")
    print("Independent events:", len(period_events))
    print("Completed events:", len(data))
    print("Skipped events:", len(period_events) - len(data))

    if data.empty:
        print("No completed events.")
        return

    winners = (data["pnl_pct"] > 0).sum()
    losers = (data["pnl_pct"] < 0).sum()
    breakeven = (data["pnl_pct"] == 0).sum()

    stop_exits = (
        data["exit_reason"] == "stop_exit"
    ).sum()

    gap_exits = (
        data["exit_reason"] == "gap_exit"
    ).sum()

    horizon_exits = (
        data["exit_reason"] == "horizon_exit"
    ).sum()

    print("Winners:", winners)
    print("Losers:", losers)
    print("Breakeven:", breakeven)

    print(
        "Win rate:",
        f"{(winners / len(data)) * 100:.2f}%"
    )

    print(
        "Average return:",
        f"{data['pnl_pct'].mean():.4f}%"
    )

    print(
        "Median return:",
        f"{data['pnl_pct'].median():.4f}%"
    )

    print(
        "Total return:",
        f"{data['pnl_pct'].sum():.4f}%"
    )

    print(
        "Best return:",
        f"{data['pnl_pct'].max():.4f}%"
    )

    print(
        "Worst return:",
        f"{data['pnl_pct'].min():.4f}%"
    )

    print("Stop exits:", stop_exits)
    print("Gap exits:", gap_exits)
    print("Horizon exits:", horizon_exits)


def main():

    print(
        "\nOOS validation: "
        "Moderate decline + RSI20-25"
    )

    print(
        "Discovery:  2026-03-02 through 2026-06-30"
    )

    print(
        "Validation: 2026-07-01 through 2026-09-01"
    )

    print(
        "Stop: 1.00% | Horizon: 6 candles | "
        "Event gap: 15 minutes"
    )

    signals = collect_qualifying_signals()

    print(
        "\n=== QUALIFYING SIGNALS ==="
    )

    print(
        "Total signals:",
        len(signals)
    )

    if signals.empty:
        print("No qualifying signals found.")
        return

    events = group_events(signals)

    # First signal from each event only.
    events = (
        events
        .sort_values(["stock", "timestamp"])
        .drop_duplicates("event_id", keep="first")
        .reset_index(drop=True)
    )

    events["period"] = events["timestamp"].map(split_period)

    print(
        "Independent events:",
        len(events)
    )

    print_event_inventory(events)

    results, audit = run_backtest(events)
    print_backtest_audit(audit)

    print_summary(results, events, "DISCOVERY")
    print_summary(results, events, "VALIDATION")

    print("\n=== VALIDATION EVENTS ===")

    if results.empty:
        validation = results.copy()
    else:
        validation = results[
            results["period"] == "VALIDATION"
        ].copy()

    if validation.empty:
        print("No validation events.")

    else:
        print(
            validation[
                [
                    "event_id",
                    "stock",
                    "signal_timestamp",
                    "decline_pct",
                    "rsi",
                    "entry_price",
                    "exit_price",
                    "pnl_pct",
                    "exit_reason",
                ]
            ].to_string(index=False)
        )

    print("\n=== VALIDATION BY STOCK ===")

    if not validation.empty:

        grouped = (
            validation
            .groupby("stock")
            .agg(
                events=("event_id", "count"),
                winners=(
                    "pnl_pct",
                    lambda x: (x > 0).sum(),
                ),
                losers=(
                    "pnl_pct",
                    lambda x: (x < 0).sum(),
                ),
                average_return=(
                    "pnl_pct",
                    "mean",
                ),
                total_return=(
                    "pnl_pct",
                    "sum",
                ),
            )
        )

        print(grouped.to_string())
    else:
        print("No completed validation events.")


if __name__ == "__main__":
    main()
