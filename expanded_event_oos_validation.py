from datetime import datetime
import pandas as pd

from app.upstox_client import get_historical_candles
from backtester import Backtester
from app.reversal_detector import detect_reversal_candidates
from stop_loss_backtest import simulate_stop_loss


# 14-stock universe. Instrument keys were verified with Upstox.
STOCKS = {
    "RELIANCE": "NSE_EQ|INE002A01018",
    "HDFCBANK": "NSE_EQ|INE040A01034",
    "ICICIBANK": "NSE_EQ|INE090A01021",
    "INFY": "NSE_EQ|INE009A01021",
    "TCS": "NSE_EQ|INE467B01029",
    "SBIN": "NSE_EQ|INE062A01020",
    "BHARTIARTL": "NSE_EQ|INE397D01024",
    "ITC": "NSE_EQ|INE154A01025",
    "KOTAKBANK": "NSE_EQ|INE237A01036",
    "LT": "NSE_EQ|INE018A01030",
    "AXISBANK": "NSE_EQ|INE238A01034",
    "MARUTI": "NSE_EQ|INE585B01010",
    "SUNPHARMA": "NSE_EQ|INE044A01036",
    "TITAN": "NSE_EQ|INE280A01028",
}

# Use the same broad historical window as the existing research.
# March is included so the discovery period has its full requested start.
ANCHOR_DATES = [
    "2026-03-01",
    "2026-04-01",
    "2026-05-01",
    "2026-06-01",
    "2026-07-01",
    "2026-08-01",
    "2026-09-01",
    "2026-09-29",
]

DISCOVERY_START = pd.Timestamp("2026-03-02", tz="Asia/Kolkata")
DISCOVERY_END = pd.Timestamp("2026-06-30 23:59:59", tz="Asia/Kolkata")
VALIDATION_START = pd.Timestamp("2026-07-01", tz="Asia/Kolkata")
VALIDATION_END = pd.Timestamp("2026-09-28 23:59:59", tz="Asia/Kolkata")

DECLINE_MIN = 1.0
DECLINE_MAX = 2.0
RSI_MIN = 20.0
RSI_MAX = 25.0
EVENT_GAP_MINUTES = 15
STOP_PCT = 1.0
HORIZON = 6


def collect_historical_candles(instrument_key):
    all_candles = []

    for date in ANCHOR_DATES:
        response = get_historical_candles(instrument_key, date, 5)
        candles = response.get("data", {}).get("candles", [])
        print(f"    {date}: {len(candles)} candles")
        all_candles.extend(candles)

    unique = {candle[0]: candle for candle in all_candles}
    return [unique[t] for t in sorted(unique)]


def build_dataframe(symbol, instrument_key):
    candles = collect_historical_candles(instrument_key)

    if not candles:
        return None

    backtester = Backtester(candles)
    validation = backtester.validate()

    print(
        f"    Dataset: {validation['candles']} candles | "
        f"{validation['trading_days']} trading days"
    )

    df = backtester.add_indicators()

    detected = detect_reversal_candidates(df)

    # Freeze the exact research rule:
    # decline 1% to <2%, RSI 20 to <25.
    candidates = detected[
        (detected["decline_pct"] <= -DECLINE_MIN)
        & (detected["decline_pct"] > -DECLINE_MAX)
        & (detected["rsi_14"] >= RSI_MIN)
        & (detected["rsi_14"] < RSI_MAX)
        & detected["reversal_condition"]
    ].copy()

    candidates["symbol"] = symbol
    return df, candidates


def group_events(df, candidates):
    """
    Group qualifying signals into independent events.

    Signals for the same stock are merged when consecutive qualifying
    signals are no more than 15 minutes apart. The first qualifying
    signal in each event is retained.
    """
    if candidates.empty:
        return []

    required_columns = [
        "timestamp",
        "decline_pct",
        "rsi_14",
    ]
    missing_columns = [
        column
        for column in required_columns
        if column not in candidates.columns
    ]
    if missing_columns:
        raise KeyError(
            f"Candidates are missing event fields: {missing_columns}"
        )

    candidates = candidates.sort_values("timestamp")

    events = []
    current_event = []

    previous_timestamp = None

    for signal_position in candidates.index:
        timestamp = pd.Timestamp(
            candidates.loc[signal_position, "timestamp"]
        )

        if previous_timestamp is None:
            current_event = [signal_position]
        else:
            gap_minutes = (
                timestamp - previous_timestamp
            ).total_seconds() / 60.0

            if gap_minutes <= EVENT_GAP_MINUTES:
                current_event.append(signal_position)
            else:
                events.append(current_event)
                current_event = [signal_position]

        previous_timestamp = timestamp

    if current_event:
        events.append(current_event)

    # Preserve the first qualifying candidate's data for inventory and simulation.
    event_records = []
    for event in events:
        signal_position = event[0]
        signal = candidates.loc[signal_position]
        event_records.append(
            {
                "symbol": signal["symbol"],
                "signal_position": signal_position,
                "signal_timestamp": pd.Timestamp(signal["timestamp"]),
                "decline_pct": float(signal["decline_pct"]),
                "rsi_14": float(signal["rsi_14"]),
            }
        )

    return event_records


def classify_period(timestamp):
    timestamp = pd.Timestamp(timestamp)

    if DISCOVERY_START <= timestamp <= DISCOVERY_END:
        return "DISCOVERY"

    if VALIDATION_START <= timestamp <= VALIDATION_END:
        return "VALIDATION"

    return None


def run_event(symbol, df, event):
    signal_position = event["signal_position"]
    signal_timestamp = event["signal_timestamp"]

    result = simulate_stop_loss(
        df,
        signal_position,
        STOP_PCT,
    )

    if result is None:
        return None

    return {
        "symbol": symbol,
        "signal_timestamp": signal_timestamp,
        "entry_price": result["entry_price"],
        "exit_price": result["exit_price"],
        "pnl_pct": result["pnl_pct"],
        "stop_pct": result["stop_pct"],
        "stop_price": result["stop_price"],
        "exit_timestamp": result["exit_timestamp"],
        "exit_reason": result["exit_reason"],
        "stopped": result["stopped"],
        "decline_pct": event["decline_pct"],
        "rsi_14": event["rsi_14"],
        "future_candles_available": len(
            df.loc[signal_position + 1: signal_position + HORIZON]
        ),
        "period": classify_period(signal_timestamp),
    }


def summarize(rows, label):
    subset = [
        row for row in rows
        if row["period"] == label
    ]

    if not subset:
        print(f"\n{label}: 0 completed events")
        return

    returns = [row["pnl_pct"] for row in subset]
    winners = [value for value in returns if value > 0]
    losers = [value for value in returns if value < 0]

    print(f"\n{label}")
    print("-" * 70)
    print("Events:", len(subset))
    print("Winners:", len(winners))
    print("Losers:", len(losers))
    print(
        "Win rate:",
        round((len(winners) / len(subset)) * 100, 2),
        "%",
    )
    print("Average return:", round(sum(returns) / len(returns), 4), "%")
    print("Median return:", round(pd.Series(returns).median(), 4), "%")
    print("Total signal return:", round(sum(returns), 4), "%")
    print("Best:", round(max(returns), 4), "%")
    print("Worst:", round(min(returns), 4), "%")
    print(
        "Stop exits:",
        sum(row["exit_reason"] == "stop_exit" for row in subset),
    )
    print(
        "Gap exits:",
        sum(row["exit_reason"] == "gap_exit" for row in subset),
    )
    print(
        "Horizon exits:",
        sum(row["exit_reason"] == "horizon_exit" for row in subset),
    )


def main():
    all_events = []
    inventory = []

    print("=" * 80)
    print("14-STOCK EVENT-LEVEL OOS VALIDATION")
    print("=" * 80)
    print(
        f"Discovery:  {DISCOVERY_START.date()} -> {DISCOVERY_END.date()}"
    )
    print(
        f"Validation: {VALIDATION_START.date()} -> {VALIDATION_END.date()}"
    )
    print(
        f"Rule: decline {DECLINE_MIN}% to <{DECLINE_MAX}%, "
        f"RSI {RSI_MIN} to <{RSI_MAX}, "
        f"event gap {EVENT_GAP_MINUTES}m, "
        f"stop {STOP_PCT}%, horizon {HORIZON}"
    )

    for symbol, instrument_key in STOCKS.items():
        print("\n" + "=" * 70)
        print(symbol)
        print("=" * 70)

        try:
            built = build_dataframe(symbol, instrument_key)

            if built is None:
                print("    No data")
                continue

            df, candidates = built

            events = group_events(df, candidates)

            print("    Qualifying signals:", len(candidates))
            print("    Independent events:", len(events))

            for event in events:
                timestamp = event["signal_timestamp"]
                period = classify_period(timestamp)

                if period is None:
                    continue

                inventory.append(
                    {
                        "symbol": symbol,
                        "timestamp": timestamp,
                        "period": period,
                        "signal_position": event["signal_position"],
                        "decline_pct": event["decline_pct"],
                        "rsi_14": event["rsi_14"],
                    }
                )

                result = run_event(
                    symbol,
                    df,
                    event,
                )

                if result is not None:
                    all_events.append(result)

        except Exception as exc:
            print("    ERROR:", repr(exc))

    print("\n" + "=" * 80)
    print("INDEPENDENT EVENT INVENTORY")
    print("=" * 80)

    for row in sorted(
        inventory,
        key=lambda item: item["timestamp"],
    ):
        print(
            f"{row['timestamp']} | "
            f"{row['symbol']:<11} | "
            f"{row['period']:<10} | "
            f"Decline={row['decline_pct']:.4f}% | "
            f"RSI={row['rsi_14']:.4f} | "
            f"Index={row['signal_position']}"
        )

    print("\n" + "=" * 80)
    print("AUDIT")
    print("=" * 80)

    skipped = 0

    for row in all_events:
        if row["future_candles_available"] < HORIZON:
            skipped += 1

    print("Inventory events:", len(inventory))
    print("Completed events:", len(all_events))
    print("Events with insufficient future candles:", skipped)

    summarize(all_events, "DISCOVERY")
    summarize(all_events, "VALIDATION")

    print("\n" + "=" * 80)
    print("EVENT RESULTS")
    print("=" * 80)

    for row in sorted(
        all_events,
        key=lambda item: item["signal_timestamp"],
    ):
        print(
            f"{row['signal_timestamp']} | "
            f"{row['symbol']:<11} | "
            f"{row['period']:<10} | "
            f"Entry={row['entry_price']:.2f} | "
            f"Exit={row['exit_price']:.2f} | "
            f"Return={row['pnl_pct']:.4f}% | "
            f"Reason={row['exit_reason']}"
        )


if __name__ == "__main__":
    main()
