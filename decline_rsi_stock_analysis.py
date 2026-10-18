from strategy_backtest import (
    STOCKS,
    ANCHOR_DATES,
    collect_historical_candles,
    simulate_stop_loss,
)
from backtester import Backtester
from app.reversal_detector import detect_reversal_candidates


STOP_PCT = 1.00


def analyze_stock(symbol, instrument_key):

    print()
    print("=" * 70)
    print(symbol)
    print("=" * 70)

    candles = collect_historical_candles(
        instrument_key
    )

    if not candles:
        return []

    backtester = Backtester(candles)
    df = backtester.add_indicators()

    detected = detect_reversal_candidates(df)

    candidates = detected[
        detected["reversal_candidate"]
    ]

    results = []

    for signal_index in candidates.index:

        result = simulate_stop_loss(
            df,
            signal_index,
            STOP_PCT,
        )

        if result is None:
            continue

        decline_pct = float(
            candidates.loc[
                signal_index,
                "decline_pct",
            ]
        )

        rsi = float(
            candidates.loc[
                signal_index,
                "rsi_14",
            ]
        )

        if not (
            1.0 <= abs(decline_pct) < 2.0
            and 20.0 <= rsi < 25.0
        ):
            continue

        results.append(
            {
                "symbol": symbol,
                "timestamp": df.loc[
                    signal_index,
                    "timestamp",
                ],
                "decline_pct": decline_pct,
                "rsi": rsi,
                "pnl_pct": result["pnl_pct"],
            }
        )

    return results


def main():

    all_results = []

    for symbol, instrument_key in STOCKS.items():

        results = analyze_stock(
            symbol,
            instrument_key,
        )

        all_results.extend(results)

    print()
    print("=" * 100)
    print("MODERATE DECLINE + RSI 20-25 BY STOCK")
    print("=" * 100)

    for symbol in STOCKS:

        rows = [
            row
            for row in all_results
            if row["symbol"] == symbol
        ]

        if not rows:
            print()
            print(
                f"{symbol}: No signals"
            )
            continue

        signals = len(rows)

        winners = sum(
            row["pnl_pct"] > 0
            for row in rows
        )

        losers = sum(
            row["pnl_pct"] < 0
            for row in rows
        )

        average_return = (
            sum(row["pnl_pct"] for row in rows)
            / signals
        )

        total_return = sum(
            row["pnl_pct"]
            for row in rows
        )

        win_rate = (
            winners / signals * 100
        )

        print()
        print(
            f"{symbol:10} | "
            f"Signals={signals:2} | "
            f"Winners={winners:2} | "
            f"Losers={losers:2} | "
            f"WinRate={win_rate:6.2f}% | "
            f"Avg={average_return:8.4f}% | "
            f"Total={total_return:8.4f}%"
        )

        for row in rows:
            print(
                f"    {row['timestamp']} | "
                f"Decline={row['decline_pct']:.3f}% | "
                f"RSI={row['rsi']:.2f} | "
                f"Return={row['pnl_pct']:.4f}%"
            )


if __name__ == "__main__":
    main()