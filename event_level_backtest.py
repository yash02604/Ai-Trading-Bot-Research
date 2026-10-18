import pandas as pd

from app.market_analysis import calculate_indicators
from app.reversal_detector import detect_reversal_candidates
from stop_loss_backtest import (
    HORIZON,
    STOCKS,
    collect_historical_candles,
    simulate_stop_loss,
)


DECLINE_CANDLES = 3
MIN_DECLINE_PCT = 0.5
RSI_THRESHOLD = 30
MIN_MODERATE_DECLINE_PCT = 1.0
MAX_MODERATE_DECLINE_PCT = 2.0
MIN_RSI = 20
MAX_RSI = 25
EVENT_GAP_MINUTES = 15
STOP_PCT = 1.00


def format_timestamp(value):
    if value is None or pd.isna(value):
        return "N/A"

    return pd.Timestamp(value).isoformat(sep=" ")


def analyze_stock(stock, instrument_key):
    print(f"\nLoading {stock}...")
    candles = collect_historical_candles(instrument_key)

    if not candles:
        print("No candles returned")
        return []

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

    for column in ["open", "high", "low", "close", "volume"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df = (
        df.dropna(
            subset=["timestamp", "open", "high", "low", "close", "volume"]
        )
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    if df.empty:
        print("No valid candles returned")
        return []

    # Keep timestamps as a column; calculate_indicators resets the row index.
    df = calculate_indicators(df)

    detected = detect_reversal_candidates(
        df,
        decline_candles=DECLINE_CANDLES,
        min_decline_pct=MIN_DECLINE_PCT,
        rsi_threshold=RSI_THRESHOLD,
    )

    qualifying = detected.loc[
        detected["reversal_candidate"]
        & detected["decline_pct"].abs().ge(MIN_MODERATE_DECLINE_PCT)
        & detected["decline_pct"].abs().lt(MAX_MODERATE_DECLINE_PCT)
        & detected["rsi_14"].ge(MIN_RSI)
        & detected["rsi_14"].lt(MAX_RSI)
    ].copy()

    if qualifying.empty:
        print("No qualifying signals")
        return []

    qualifying = qualifying.sort_values("timestamp").copy()
    gaps = qualifying["timestamp"].diff().dt.total_seconds() / 60
    qualifying["event_number"] = (
        gaps.isna() | gaps.gt(EVENT_GAP_MINUTES)
    ).cumsum()

    event_results = []

    for event_number, event_signals in qualifying.groupby(
        "event_number",
        sort=True,
    ):
        # The earliest qualifying signal is the event's entry; no hindsight selection.
        entry = event_signals.iloc[0]
        entry_index = event_signals.index[0]
        simulation = simulate_stop_loss(df, entry_index, STOP_PCT)
        entry_price = float(entry["close"])
        stop_price = entry_price * (1 - STOP_PCT / 100)

        if simulation is None:
            event_results.append(
                {
                    "stock": stock,
                    "event_number": int(event_number),
                    "entry_timestamp": entry["timestamp"],
                    "entry_price": entry_price,
                    "decline_pct": float(entry["decline_pct"]),
                    "rsi": float(entry["rsi_14"]),
                    "stop_price": stop_price,
                    "exit_timestamp": None,
                    "exit_price": None,
                    "exit_reason": "no_future_data",
                    "return_pct": None,
                    "outcome": "NO_FUTURE_DATA",
                }
            )
            continue

        return_pct = float(simulation["pnl_pct"])
        outcome = (
            "WINNER"
            if return_pct > 0
            else "LOSER"
            if return_pct < 0
            else "BREAKEVEN"
        )

        event_results.append(
            {
                "stock": stock,
                "event_number": int(event_number),
                "entry_timestamp": entry["timestamp"],
                "entry_price": float(simulation["entry_price"]),
                "decline_pct": float(entry["decline_pct"]),
                "rsi": float(entry["rsi_14"]),
                "stop_price": float(simulation["stop_price"]),
                "exit_timestamp": simulation["exit_timestamp"],
                "exit_price": float(simulation["exit_price"]),
                "exit_reason": simulation["exit_reason"],
                "return_pct": return_pct,
                "outcome": outcome,
            }
        )

    return event_results


def print_event_results(results):
    print("\n=== EVENT-LEVEL TRADE RESULTS ===")

    if not results:
        print("No events to report.")
        return

    for event in results:
        print(
            f"\n{event['stock']} event {event['event_number']}"
        )
        print(f"  Entry timestamp: {format_timestamp(event['entry_timestamp'])}")
        print(f"  Entry price: {event['entry_price']:.4f}")
        print(f"  Decline: {event['decline_pct']:.4f}%")
        print(f"  RSI: {event['rsi']:.4f}")
        print(f"  Stop price: {event['stop_price']:.4f}")
        print(f"  Exit timestamp: {format_timestamp(event['exit_timestamp'])}")
        exit_price = event["exit_price"]
        print(
            "  Exit price: "
            + (f"{exit_price:.4f}" if exit_price is not None else "N/A")
        )
        print(f"  Exit reason: {event['exit_reason']}")
        return_pct = event["return_pct"]
        print(
            "  Return: "
            + (f"{return_pct:.4f}%" if return_pct is not None else "N/A")
        )
        print(f"  Result: {event['outcome']}")


def print_overall_summary(results):
    completed = [event for event in results if event["return_pct"] is not None]
    returns = [event["return_pct"] for event in completed]
    winners = sum(event["outcome"] == "WINNER" for event in completed)
    losers = sum(event["outcome"] == "LOSER" for event in completed)
    breakeven = sum(event["outcome"] == "BREAKEVEN" for event in completed)

    print("\n=== OVERALL SUMMARY ===")
    print(f"Independent events: {len(results)}")
    print(f"Completed events: {len(completed)}")
    print(f"Winners: {winners}")
    print(f"Losers: {losers}")
    print(f"Breakeven: {breakeven}")

    if not returns:
        print("Win rate: N/A")
        print("Average return: N/A")
        print("Median return: N/A")
        print("Total return: N/A")
        print("Best return: N/A")
        print("Worst return: N/A")
        return

    print(f"Win rate: {winners / len(completed) * 100:.2f}%")
    print(f"Average return: {sum(returns) / len(returns):.4f}%")
    print(f"Median return: {pd.Series(returns).median():.4f}%")
    print(f"Total return: {sum(returns):.4f}%")
    print(f"Best return: {max(returns):.4f}%")
    print(f"Worst return: {min(returns):.4f}%")


def print_stock_summary(results):
    print("\n=== RESULTS BY STOCK ===")

    for stock in STOCKS:
        stock_results = [event for event in results if event["stock"] == stock]

        if not stock_results:
            continue

        completed = [
            event for event in stock_results if event["return_pct"] is not None
        ]
        returns = [event["return_pct"] for event in completed]
        winners = sum(event["outcome"] == "WINNER" for event in completed)
        losers = sum(event["outcome"] == "LOSER" for event in completed)

        if returns:
            average_return = sum(returns) / len(returns)
            total_return = sum(returns)
            print(
                f"{stock}: events={len(stock_results)}, "
                f"winners={winners}, losers={losers}, "
                f"average={average_return:.4f}%, total={total_return:.4f}%"
            )
        else:
            print(
                f"{stock}: events={len(stock_results)}, "
                "no completed event simulations"
            )

        for event in stock_results:
            entry_timestamp = format_timestamp(event["entry_timestamp"])
            return_pct = event["return_pct"]
            return_text = (
                f"{return_pct:.4f}%" if return_pct is not None else "N/A"
            )
            print(
                f"  Event {event['event_number']}: "
                f"entry={entry_timestamp}, return={return_text}, "
                f"result={event['outcome']}"
            )


def main():
    print(
        f"Event-level backtest: stop={STOP_PCT:.2f}%, "
        f"horizon={HORIZON} candles, gap={EVENT_GAP_MINUTES} minutes"
    )

    results = []

    for stock, instrument_key in STOCKS.items():
        try:
            results.extend(analyze_stock(stock, instrument_key))
        except Exception as exc:
            print(f"ERROR processing {stock}: {exc}")

    print_event_results(results)
    print_overall_summary(results)
    print_stock_summary(results)


if __name__ == "__main__":
    main()
