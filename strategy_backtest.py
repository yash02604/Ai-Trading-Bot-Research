from app.upstox_client import get_historical_candles
from backtester import Backtester
from app.reversal_detector import detect_reversal_candidates
from app.candle_integrity import merge_raw_candle_batches


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


ANCHOR_DATES = [
    "2026-04-01",
    "2026-05-01",
    "2026-06-01",
    "2026-07-01",
    "2026-08-01",
    "2026-09-01",
]


STOP_LEVELS = [
    0.25,
    0.50,
    0.75,
    1.00,
    1.50,
    2.00,
    3.00,
]


HORIZON = 6


def collect_historical_candles(instrument_key):
    batches = []

    for date in ANCHOR_DATES:
        response = get_historical_candles(
            instrument_key,
            date,
            5,
        )

        candles = response.get("data", {}).get("candles", [])

        print(f"  {date}: {len(candles)} candles")

        batches.append((date, candles))

    if not any(candles for _, candles in batches):
        return []
    merged, audit = merge_raw_candle_batches(batches, instrument_key)
    if audit["duplicate_coverage"]["count"]:
        print(f"  Identical overlapping anchor candles: {audit['duplicate_coverage']['count']}; retained once")
    return merged


def simulate_stop_loss(df, signal_index, stop_pct):
    entry_price = float(
        df.loc[signal_index, "close"]
    )

    stop_price = entry_price * (
        1 - stop_pct / 100
    )

    future = df.loc[
        signal_index + 1:
        signal_index + HORIZON
    ]

    if future.empty:
        return None

    for _, candle in future.iterrows():

        candle_low = float(candle["low"])
        candle_open = float(candle["open"])

        if candle_low <= stop_price:

            # Conservative gap assumption.
            if candle_open <= stop_price:
                exit_price = candle_open
                execution = "gap_exit"
            else:
                exit_price = stop_price
                execution = "stop_exit"

            pnl_pct = (
                (exit_price - entry_price)
                / entry_price
            ) * 100

            return {
                "entry_price": entry_price,
                "exit_price": exit_price,
                "pnl_pct": pnl_pct,
                "stop_pct": stop_pct,
                "stop_price": stop_price,
                "exit_timestamp": candle["timestamp"],
                "exit_reason": execution,
            }

    # Stop was never touched.
    exit_candle = future.iloc[-1]

    exit_price = float(
        exit_candle["close"]
    )

    pnl_pct = (
        (exit_price - entry_price)
        / entry_price
    ) * 100

    return {
        "entry_price": entry_price,
        "exit_price": exit_price,
        "pnl_pct": pnl_pct,
        "stop_pct": stop_pct,
        "stop_price": stop_price,
        "exit_timestamp": exit_candle["timestamp"],
        "exit_reason": "horizon_exit",
    }


def analyze_stock(symbol, instrument_key):

    print()
    print("=" * 70)
    print(symbol)
    print("=" * 70)

    candles = collect_historical_candles(
        instrument_key
    )

    if not candles:
        print("No candles returned")
        return []

    backtester = Backtester(candles)

    validation = backtester.validate()

    print(
        f"Candles: {validation['candles']} | "
        f"Trading days: {validation['trading_days']}"
    )

    df = backtester.add_indicators()

    detected = detect_reversal_candidates(df)

    candidates = detected[
        detected["reversal_candidate"]
    ]

    print(
        "Reversal candidates:",
        len(candidates)
    )

    results = []

    for signal_index in candidates.index:

        for stop_pct in STOP_LEVELS:

            result = simulate_stop_loss(
                df,
                signal_index,
                stop_pct,
            )

            if result is None:
                continue

            result["symbol"] = symbol

            result["signal_timestamp"] = (
                df.loc[
                    signal_index,
                    "timestamp",
                ]
            )

            result["decline_pct"] = float(
                candidates.loc[
                    signal_index,
                    "decline_pct",
                ]
            )

            result["rsi_14"] = float(
                candidates.loc[
                    signal_index,
                    "rsi_14",
                ]
            )

            results.append(result)

    return results


def summarize(results):

    print()
    print("=" * 100)
    print("STRATEGY-ONLY BACKTEST")
    print("=" * 100)

    for stop_pct in STOP_LEVELS:

        rows = [
            row
            for row in results
            if row["stop_pct"] == stop_pct
        ]

        if not rows:
            continue

        winners = [
            row
            for row in rows
            if row["pnl_pct"] > 0
        ]

        losers = [
            row
            for row in rows
            if row["pnl_pct"] < 0
        ]

        breakeven = [
            row
            for row in rows
            if row["pnl_pct"] == 0
        ]

        win_rate = (
            len(winners)
            / len(rows)
        ) * 100

        average_return = (
            sum(
                row["pnl_pct"]
                for row in rows
            )
            / len(rows)
        )

        total_return = sum(
            row["pnl_pct"]
            for row in rows
        )

        best_trade = max(
            row["pnl_pct"]
            for row in rows
        )

        worst_trade = min(
            row["pnl_pct"]
            for row in rows
        )

        print()
        print(
            f"STOP {stop_pct:.2f}%"
        )
        print(
            f"Signals: {len(rows)}"
        )
        print(
            f"Winners: {len(winners)}"
        )
        print(
            f"Losers: {len(losers)}"
        )
        print(
            f"Breakeven: {len(breakeven)}"
        )
        print(
            f"Win rate: {win_rate:.2f}%"
        )
        print(
            f"Average return: "
            f"{average_return:.4f}%"
        )
        print(
            f"Sum of signal returns: "
            f"{total_return:.4f}%"
        )
        print(
            f"Best trade: "
            f"{best_trade:.4f}%"
        )
        print(
            f"Worst trade: "
            f"{worst_trade:.4f}%"
        )

    print()
    print("=" * 100)
    print("PER-STOCK RESULTS")
    print("=" * 100)

    for symbol in STOCKS:

        symbol_rows = [
            row
            for row in results
            if row["symbol"] == symbol
        ]

        if not symbol_rows:
            continue

        print()
        print(symbol)

        for stop_pct in STOP_LEVELS:

            rows = [
                row
                for row in symbol_rows
                if row["stop_pct"] == stop_pct
            ]

            if not rows:
                continue

            winners = sum(
                row["pnl_pct"] > 0
                for row in rows
            )

            avg_return = (
                sum(
                    row["pnl_pct"]
                    for row in rows
                )
                / len(rows)
            )

            print(
                f"  {stop_pct:.2f}% | "
                f"Signals={len(rows)} | "
                f"Winners={winners} | "
                f"WinRate="
                f"{(winners / len(rows)) * 100:.2f}% | "
                f"Avg="
                f"{avg_return:.4f}%"
            )


def main():

    all_results = []

    for symbol, instrument_key in STOCKS.items():

        try:

            results = analyze_stock(
                symbol,
                instrument_key,
            )

            all_results.extend(results)

        except Exception as exc:

            print(
                f"ERROR processing "
                f"{symbol}: {exc}"
            )

    summarize(all_results)


if __name__ == "__main__":
    main()
