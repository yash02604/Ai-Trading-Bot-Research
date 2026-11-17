import pandas as pd

from app.upstox_client import get_historical_candles
from app.candle_integrity import merge_raw_candle_batches
from app.market_analysis import calculate_indicators
from app.reversal_detector import detect_reversal_candidates
from strategy_backtest import (
    STOCKS,
    simulate_stop_loss,
)


# ============================================================
# FROZEN STRATEGY — DO NOT CHANGE
# ============================================================

DECLINE_MIN = 1.0
DECLINE_MAX = 2.0
RSI_MIN = 20.0
RSI_MAX = 25.0

EVENT_GAP_MINUTES = 15
STOP_PCT = 1.0
HORIZON = 6

# Earlier historical robustness period.
# This period occurs BEFORE the original discovery period.
TEST_START = pd.Timestamp("2026-01-02", tz="Asia/Kolkata")
TEST_END = pd.Timestamp("2026-03-01 23:59:59", tz="Asia/Kolkata")
HISTORICAL_ANCHORS = [
    "2026-01-01",
    "2026-02-01",
    "2026-03-01",
]


# ============================================================
# EVENT GROUPING
# ============================================================

def assign_event_ids(signals):
    if signals.empty:
        return signals.copy()

    signals = signals.sort_values(
        ["stock", "timestamp"]
    ).reset_index(drop=True)

    event_ids = []
    event_counter = {}

    previous_timestamp = {}
    previous_stock = {}

    for _, row in signals.iterrows():
        stock = row["stock"]
        timestamp = row["timestamp"]

        new_event = (
            stock != previous_stock
            or previous_timestamp is None
            or (
                timestamp - previous_timestamp
            ).total_seconds() / 60
            > EVENT_GAP_MINUTES
        )

        if new_event:
            event_counter[stock] = (
                event_counter.get(stock, 0) + 1
            )

        event_id = f"{stock}_event_{event_counter[stock]}"

        event_ids.append(event_id)

        previous_stock = stock
        previous_timestamp = timestamp

    signals["event_id"] = event_ids

    return signals


# ============================================================
# LOAD DATA AND FIND SIGNALS
# ============================================================

def collect_early_historical_candles(instrument_key):
    all_candles = []

    for date in HISTORICAL_ANCHORS:
        response = get_historical_candles(
            instrument_key,
            date,
            5,
        )

        candles = response.get("data", {}).get("candles", [])
        print(f"  {date}: {len(candles)} candles")
        all_candles.extend(candles)

    if not all_candles:
        return []

    merged, _ = merge_raw_candle_batches([("historical", all_candles)], instrument_key)
    return merged


def build_signals():

    all_signals = []

    for stock, instrument_key in STOCKS.items():

        print(f"\nLoading {stock}...")

        candles = collect_early_historical_candles(
            instrument_key
        )

        if not candles:
            print("  No candles returned.")
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
                "oi",
            ],
        )

        df["timestamp"] = pd.to_datetime(
            df["timestamp"],
            utc=True,
        ).dt.tz_convert("Asia/Kolkata")

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

        df = df.dropna(
            subset=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
            ]
        ).reset_index(drop=True)

        # Calculate indicators using the existing implementation.
        df = calculate_indicators(df)

        # Existing reversal detector.
        df = detect_reversal_candidates(
            df,
            decline_candles=3,
            min_decline_pct=0.5,
            rsi_threshold=30,
        )

        # Apply the FROZEN strategy after indicator calculation.
        candidates = df[
            (df["decline_pct"] >= -DECLINE_MAX)
            & (df["decline_pct"] < -DECLINE_MIN)
            & (df["rsi_14"] >= RSI_MIN)
            & (df["rsi_14"] < RSI_MAX)
            & (df["reversal_condition"])
        ].copy()

        # Restrict to earlier historical test period.
        candidates = candidates[
            (candidates["timestamp"] >= TEST_START)
            & (candidates["timestamp"] <= TEST_END)
        ].copy()

        for _, row in candidates.iterrows():

            all_signals.append(
                {
                    "stock": stock,
                    "timestamp": row["timestamp"],
                    "decline_pct": float(
                        row["decline_pct"]
                    ),
                    "rsi": float(
                        row["rsi_14"]
                    ),
                    "candles": df,
                }
            )

    if not all_signals:
        return pd.DataFrame()

    signals = pd.DataFrame(all_signals)

    return assign_event_ids(signals)


# ============================================================
# SELECT FIRST SIGNAL PER EVENT
# ============================================================

def select_independent_events(signals):

    if signals.empty:
        return signals.copy()

    return (
        signals
        .sort_values(
            ["stock", "timestamp"]
        )
        .groupby(
            "event_id",
            as_index=False,
            sort=False,
        )
        .first()
    )


# ============================================================
# BACKTEST + AUDIT
# ============================================================

def run_backtest(events):

    results = []
    audit = []

    for _, event in events.iterrows():

        df = event["candles"]
        timestamp = event["timestamp"]

        matches = df.index[
            df["timestamp"] == timestamp
        ]

        base_audit = {
            "event_id": event["event_id"],
            "stock": event["stock"],
            "signal_timestamp": timestamp,
        }

        if len(matches) == 0:

            audit.append(
                {
                    **base_audit,
                    "status": "SKIPPED",
                    "reason": "timestamp_not_found",
                }
            )

            continue

        signal_position = df.index.get_loc(
            matches[0]
        )

        available_future_candles = (
            len(df) - signal_position - 1
        )

        if available_future_candles < HORIZON:

            audit.append(
                {
                    **base_audit,
                    "status": "SKIPPED",
                    "reason": (
                        "insufficient_future_candles"
                    ),
                    "available_future_candles":
                        available_future_candles,
                }
            )

            continue

        try:

            result = simulate_stop_loss(
                df,
                signal_position,
                stop_pct=STOP_PCT,
            )

        except Exception as exc:

            audit.append(
                {
                    **base_audit,
                    "status": "ERROR",
                    "reason": str(exc),
                }
            )

            continue

        if result is None:

            audit.append(
                {
                    **base_audit,
                    "status": "SKIPPED",
                    "reason": (
                        "simulate_stop_loss_returned_none"
                    ),
                    "available_future_candles":
                        available_future_candles,
                }
            )

            continue

        audit.append(
            {
                **base_audit,
                "status": "RESULT_RETURNED",
                "available_future_candles":
                    available_future_candles,
            }
        )

        result["stock"] = event["stock"]
        result["signal_timestamp"] = timestamp
        result["decline_pct"] = event[
            "decline_pct"
        ]
        result["rsi"] = event["rsi"]
        result["event_id"] = event["event_id"]

        results.append(result)

    return (
        pd.DataFrame(results),
        pd.DataFrame(audit),
    )


# ============================================================
# SUMMARY
# ============================================================

def print_summary(results, events):

    print("\n=== ROBUSTNESS TEST SUMMARY ===")

    print(
        f"Independent events: {len(events)}"
    )

    if results.empty:

        print("Completed events: 0")
        print("No completed simulations.")

        return

    winners = (
        results["pnl_pct"] > 0
    ).sum()

    losers = (
        results["pnl_pct"] < 0
    ).sum()

    breakeven = (
        results["pnl_pct"] == 0
    ).sum()

    print(
        f"Completed events: {len(results)}"
    )

    print(f"Winners: {winners}")
    print(f"Losers: {losers}")
    print(f"Breakeven: {breakeven}")

    print(
        f"Win rate: "
        f"{winners / len(results) * 100:.2f}%"
    )

    print(
        f"Average return: "
        f"{results['pnl_pct'].mean():.4f}%"
    )

    print(
        f"Median return: "
        f"{results['pnl_pct'].median():.4f}%"
    )

    print(
        f"Total return: "
        f"{results['pnl_pct'].sum():.4f}%"
    )

    print(
        f"Best return: "
        f"{results['pnl_pct'].max():.4f}%"
    )

    print(
        f"Worst return: "
        f"{results['pnl_pct'].min():.4f}%"
    )

    if "exit_reason" in results.columns:

        print(
            f"Stop exits: "
            f"{(results['exit_reason'] == 'stop_exit').sum()}"
        )

        print(
            f"Gap exits: "
            f"{(results['exit_reason'] == 'gap_exit').sum()}"
        )

        print(
            f"Horizon exits: "
            f"{(results['exit_reason'] == 'horizon_exit').sum()}"
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "\nHistorical robustness test"
    )

    print(
        "Period: "
        "2026-01-02 through 2026-03-01"
    )

    print(
        "Frozen rule: Moderate decline + RSI20-25"
    )

    print(
        f"Stop: {STOP_PCT:.2f}% | "
        f"Horizon: {HORIZON} candles | "
        f"Event gap: {EVENT_GAP_MINUTES} minutes"
    )

    signals = build_signals()

    if signals.empty:

        print(
            "\nNo qualifying signals found."
        )

        return

    print(
        "\n=== QUALIFYING SIGNALS ==="
    )

    print(
        f"Total signals: {len(signals)}"
    )

    events = select_independent_events(
        signals
    )

    print(
        f"Independent events: {len(events)}"
    )

    print(
        "\n=== INDEPENDENT EVENT INVENTORY ==="
    )

    print(
        events[
            [
                "event_id",
                "stock",
                "timestamp",
                "decline_pct",
                "rsi",
            ]
        ].to_string(index=False)
    )

    results, audit = run_backtest(
        events
    )

    print(
        "\n=== PER-EVENT BACKTEST AUDIT ==="
    )

    print(
        audit.to_string(index=False)
    )

    print_summary(
        results,
        events,
    )

    if not results.empty:

        print(
            "\n=== EVENT RESULTS ==="
        )

        print(
            results[
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

        print(
            "\n=== RESULTS BY STOCK ==="
        )

        stock_summary = (
            results
            .groupby("stock")
            .agg(
                events=("pnl_pct", "count"),
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

        print(
            stock_summary.to_string()
        )


if __name__ == "__main__":
    main()
