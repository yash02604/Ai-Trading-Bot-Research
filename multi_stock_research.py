from app.upstox_client import get_historical_candles
from backtester import Backtester
from app.reversal_detector import detect_reversal_candidates


STOCKS = {
    "RELIANCE": "NSE_EQ|INE002A01018",
    "HDFCBANK": "NSE_EQ|INE040A01034",
    "ICICIBANK": "NSE_EQ|INE090A01021",
    "INFY": "NSE_EQ|INE009A01021",
    "TCS": "NSE_EQ|INE467B01029",
    "SBIN": "NSE_EQ|INE062A01020",
}


ANCHOR_DATES = [
    "2026-04-01",
    "2026-05-01",
    "2026-06-01",
    "2026-07-01",
    "2026-08-01",
    "2026-09-01",
]


def collect_historical_candles(instrument_key):
    all_candles = []

    for date in ANCHOR_DATES:
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

    # Remove duplicate timestamps.
    unique_candles = {
        candle[0]: candle
        for candle in all_candles
    }

    # Sort oldest -> newest.
    candles = [
        unique_candles[timestamp]
        for timestamp in sorted(unique_candles)
    ]

    return candles


def classify_decline(decline_pct):
    decline = abs(decline_pct)

    if decline < 1.0:
        return "Mild (0.5%-<1.0%)"

    if decline < 2.0:
        return "Moderate (1.0%-<2.0%)"

    return "Strong (>=2.0%)"

def classify_rsi(rsi):
    if rsi >= 25:
        return "RSI 25-30"

    if rsi >= 20:
        return "RSI 20-<25"

    return "RSI <20"


def analyze_decline_strength(forward):
    forward["decline_group"] = (
        forward["decline_pct"].apply(classify_decline)
    )

    print()
    print("-" * 100)
    print("DECLINE STRENGTH ANALYSIS")
    print("-" * 100)

    groups = [
        "Mild (0.5%-<1.0%)",
        "Moderate (1.0%-<2.0%)",
        "Strong (>=2.0%)",
    ]

    for group in groups:

        group_data = forward[
            forward["decline_group"] == group
        ]

        if group_data.empty:
            print()
            print(group)
            print("Signals: 0")
            continue

        signals = len(group_data)

        positive = int(
            (group_data["return_pct"] > 0).sum()
        )

        positive_rate = (
            positive / signals
        ) * 100

        average_return = (
            group_data["return_pct"].mean()
        )

        average_favorable = (
            group_data["max_favorable_pct"].mean()
        )

        average_adverse = (
            group_data["max_adverse_pct"].mean()
        )

        print()
        print(group)
        print("Signals:", signals)
        print("Positive:", positive)
        print(
            "Positive rate:",
            round(positive_rate, 2),
            "%",
        )
        print(
            "Average 6-candle return:",
            round(average_return, 4),
            "%",
        )
        print(
            "Average max favorable:",
            round(average_favorable, 4),
            "%",
        )
        print(
            "Average max adverse:",
            round(average_adverse, 4),
            "%",
        )

    

    # ---------------------------------------------------------
    # RSI-strength analysis
    # ---------------------------------------------------------

    forward["rsi_group"] = (
        forward["rsi_14"].apply(classify_rsi)
    )

    print()
    print("-" * 100)
    print("RSI STRENGTH ANALYSIS")
    print("-" * 100)

    rsi_groups = [
        "RSI 25-30",
        "RSI 20-<25",
        "RSI <20",
    ]

    for group in rsi_groups:

        group_data = forward[
            forward["rsi_group"] == group
        ]

        if group_data.empty:
            print()
            print(group)
            print("Signals: 0")
            continue

        signals = len(group_data)

        positive = int(
            (group_data["return_pct"] > 0).sum()
        )

        positive_rate = (
            positive / signals
        ) * 100

        average_return = (
            group_data["return_pct"].mean()
        )

        average_favorable = (
            group_data["max_favorable_pct"].mean()
        )

        average_adverse = (
            group_data["max_adverse_pct"].mean()
        )

        print()
        print(group)
        print("Signals:", signals)
        print("Positive:", positive)
        print(
            "Positive rate:",
            round(positive_rate, 2),
            "%",
        )
        print(
            "Average 6-candle return:",
            round(average_return, 4),
            "%",
        )
        print(
            "Average max favorable:",
            round(average_favorable, 4),
            "%",
        )
        print(
            "Average max adverse:",
            round(average_adverse, 4),
            "%",
        )

    return forward




def analyze_decline_rsi_combination(forward):
    if forward.empty:
        return

    print()
    print("-" * 100)
    print("DECLINE STRENGTH × RSI STRENGTH ANALYSIS")
    print("-" * 100)

    decline_groups = [
        "Mild (0.5%-<1.0%)",
        "Moderate (1.0%-<2.0%)",
        "Strong (>=2.0%)",
    ]

    rsi_groups = [
        "RSI 25-30",
        "RSI 20-<25",
        "RSI <20",
    ]

    for decline_group in decline_groups:
        for rsi_group in rsi_groups:

            group_data = forward[
                (forward["decline_group"] == decline_group)
                & (forward["rsi_group"] == rsi_group)
            ]

            if group_data.empty:
                print()
                print(decline_group, "|", rsi_group)
                print("Signals: 0")
                continue

            signals = len(group_data)

            positive = int(
                (group_data["return_pct"] > 0).sum()
            )

            positive_rate = (
                positive / signals
            ) * 100

            average_return = (
                group_data["return_pct"].mean()
            )

            average_favorable = (
                group_data["max_favorable_pct"].mean()
            )

            average_adverse = (
                group_data["max_adverse_pct"].mean()
            )

            print()
            print(decline_group, "|", rsi_group)
            print("Signals:", signals)
            print("Positive:", positive)
            print(
                "Positive rate:",
                round(positive_rate, 2),
                "%",
            )
            print(
                "Average 6-candle return:",
                round(average_return, 4),
                "%",
            )
            print(
                "Average max favorable:",
                round(average_favorable, 4),
                "%",
            )
            print(
                "Average max adverse:",
                round(average_adverse, 4),
                "%",
            )   
    
def analyze_stock(symbol, instrument_key):
    print()
    print("=" * 60)
    print(symbol)
    print("=" * 60)

    candles = collect_historical_candles(instrument_key)

    if not candles:
        print("No candles returned")
        return None

    backtester = Backtester(candles)

    validation = backtester.validate()

    print()
    print("Combined dataset:")
    print("Candles:", validation["candles"])
    print("Trading days:", validation["trading_days"])
    print("Start:", validation["start"])
    print("End:", validation["end"])
    print(
        "Duplicate timestamps:",
        validation["duplicate_timestamps"],
    )
    print(
        "Invalid OHLC rows:",
        validation["invalid_ohlc_rows"],
    )

    df = backtester.add_indicators()

    detected = detect_reversal_candidates(df)

    candidates = detected[
        detected["reversal_candidate"]
    ]

    forward = backtester.analyze_forward_movement(
        candidates,
        horizon=6,
    )

    print()
    print("Reversal candidates:", len(candidates))

    if forward.empty:
        print("Forward results: none")

        return {
            "symbol": symbol,
            "candles": validation["candles"],
            "trading_days": validation["trading_days"],
            "candidates": len(candidates),
            "forward_results": 0,
            "positive_results": 0,
            "average_return_pct": None,
        }

    # Add signal information.
    forward = forward.copy()

    forward["decline_pct"] = [
        candidates.loc[index, "decline_pct"]
        for index in candidates.index
    ]

    forward["rsi_14"] = [
        candidates.loc[index, "rsi_14"]
        for index in candidates.index
    ]

    print()
    print("-" * 100)
    print("INDIVIDUAL REVERSAL SIGNALS")
    print("-" * 100)

    for _, row in forward.iterrows():
        print(
            f"{row['timestamp']} | "
            f"Entry={row['entry_price']:.2f} | "
            f"Decline={row['decline_pct']:.2f}% | "
            f"RSI={row['rsi_14']:.2f} | "
            f"6C Return={row['return_pct']:.3f}% | "
            f"MaxFav={row['max_favorable_pct']:.3f}% | "
            f"MaxAdv={row['max_adverse_pct']:.3f}%"
        )

     # Analyze decline strength.
    forward = analyze_decline_strength(forward)

    # Add RSI strength classification.
    forward["rsi_group"] = (
        forward["rsi_14"].apply(classify_rsi)
    )

    # Analyze decline strength × RSI strength.
    analyze_decline_rsi_combination(forward)

    positive_results = int(
        (forward["return_pct"] > 0).sum()
    )

    average_return = forward["return_pct"].mean()

    print()
    print("Positive forward returns:", positive_results)

    print(
        "Average 6-candle return:",
        round(average_return, 4),
        "%",
    )
    

    return {
        "symbol": symbol,
        "candles": validation["candles"],
        "trading_days": validation["trading_days"],
        "candidates": len(candidates),
        "forward_results": len(forward),
        "positive_results": positive_results,
        "average_return_pct": average_return,
    }
    

def main():
    results = []

    for symbol, instrument_key in STOCKS.items():

        try:
            result = analyze_stock(
                symbol,
                instrument_key,
            )

            if result is not None:
                results.append(result)

        except Exception as exc:
            print("ERROR:", exc)

    print()
    print("=" * 60)
    print("MULTI-STOCK SUMMARY")
    print("=" * 60)

    for result in results:
        print(result)


if __name__ == "__main__":
    main()