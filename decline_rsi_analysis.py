from strategy_backtest import (
    STOCKS,
    ANCHOR_DATES,
    collect_historical_candles,
    simulate_stop_loss,
)
from backtester import Backtester
from app.reversal_detector import detect_reversal_candidates


HORIZON = 6
STOP_PCT = 1.00


def classify_decline(decline_pct):
    decline = abs(decline_pct)

    if decline < 1.0:
        return "Mild"
    elif decline < 2.0:
        return "Moderate"
    else:
        return "Strong"


def classify_rsi(rsi):
    if rsi >= 25:
        return "RSI 25-30"
    elif rsi >= 20:
        return "RSI 20-25"
    else:
        return "RSI <20"


def analyze_stock(symbol, instrument_key):
    print()
    print("=" * 70)
    print(symbol)
    print("=" * 70)

    all_candles = []

    for date in ANCHOR_DATES:
        response = collect_historical_candles(instrument_key)
        all_candles = response
        break

    if not all_candles:
        print("No candles returned")
        return []

    backtester = Backtester(all_candles)

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

    print("Reversal candidates:", len(candidates))

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

        results.append(
            {
                "symbol": symbol,
                "signal_timestamp": df.loc[
                    signal_index,
                    "timestamp",
                ],
                "decline_pct": decline_pct,
                "rsi": rsi,
                "decline_category": classify_decline(
                    decline_pct
                ),
                "rsi_category": classify_rsi(
                    rsi
                ),
                "pnl_pct": result["pnl_pct"],
            }
        )

    return results


def print_analysis(results):
    print()
    print("=" * 100)
    print("DECLINE × RSI ANALYSIS")
    print("=" * 100)

    categories = [
        ("Mild", "RSI 25-30"),
        ("Mild", "RSI 20-25"),
        ("Mild", "RSI <20"),
        ("Moderate", "RSI 25-30"),
        ("Moderate", "RSI 20-25"),
        ("Moderate", "RSI <20"),
        ("Strong", "RSI 25-30"),
        ("Strong", "RSI 20-25"),
        ("Strong", "RSI <20"),
    ]

    for decline_category, rsi_category in categories:

        rows = [
            row
            for row in results
            if row["decline_category"]
            == decline_category
            and row["rsi_category"]
            == rsi_category
        ]

        if not rows:
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

        win_rate = (
            winners / signals * 100
        )

        print()
        print(
            f"{decline_category:9} | "
            f"{rsi_category:10} | "
            f"Signals={signals:3} | "
            f"Winners={winners:3} | "
            f"Losers={losers:3} | "
            f"WinRate={win_rate:6.2f}% | "
            f"Avg={average_return:8.4f}%"
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
                f"ERROR processing {symbol}: "
                f"{exc}"
            )

    print_analysis(all_results)


if __name__ == "__main__":
    main()