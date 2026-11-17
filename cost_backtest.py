from app.upstox_client import get_historical_candles
from app.candle_integrity import merge_raw_candle_batches
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

STOP_LEVELS = [
    0.25,
    0.50,
    0.75,
    1.00,
    1.50,
    2.00,
    3.00,
]

SLIPPAGE_LEVELS = [
    0.00,
    0.05,
    0.10,
]

HORIZON = 6


def collect_historical_candles(instrument_key):
    all_candles = []

    for date in ANCHOR_DATES:
        response = get_historical_candles(
            instrument_key,
            date,
            5,
        )

        candles = response.get("data", {}).get("candles", [])
        all_candles.extend(candles)

    if not all_candles:
        return []

    merged, _ = merge_raw_candle_batches([("historical", all_candles)], instrument_key)
    return merged


def simulate_trade(df, signal_index, stop_pct):
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

            if candle_open <= stop_price:
                exit_price = candle_open
                exit_reason = "gap_exit"
            else:
                exit_price = stop_price
                exit_reason = "stop_exit"

            return {
                "entry_price": entry_price,
                "exit_price": exit_price,
                "exit_timestamp": candle["timestamp"],
                "exit_reason": exit_reason,
                "stop_pct": stop_pct,
            }

    exit_candle = future.iloc[-1]

    return {
        "entry_price": entry_price,
        "exit_price": float(exit_candle["close"]),
        "exit_timestamp": exit_candle["timestamp"],
        "exit_reason": "horizon_exit",
        "stop_pct": stop_pct,
    }


def collect_trades():

    all_trades = []

    for symbol, instrument_key in STOCKS.items():

        print(f"Loading {symbol}...")

        candles = collect_historical_candles(
            instrument_key
        )

        if not candles:
            continue

        backtester = Backtester(candles)
        df = backtester.add_indicators()

        detected = detect_reversal_candidates(df)

        candidates = detected[
            detected["reversal_candidate"]
        ]

        print(
            f"{symbol}: "
            f"{len(candidates)} signals"
        )

        for signal_index in candidates.index:

            for stop_pct in STOP_LEVELS:

                result = simulate_trade(
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

                all_trades.append(result)

    return all_trades


def calculate_charges(
    buy_value,
    sell_value,
):
    """
    Approximate NSE equity intraday charges
    using the current Upstox published structure.

    Brokerage:
        min(₹20, 0.1% of order value)
        for each executed order.

    STT:
        0.025% on sell value.

    NSE transaction charge:
        0.00307% on buy + sell.

    Stamp duty:
        0.003% on buy value.

    SEBI charge:
        ₹10 per crore.

    GST:
        18% on brokerage + transaction charges
        + SEBI charge.
    """

    buy_brokerage = min(
        20.0,
        buy_value * 0.001,
    )

    sell_brokerage = min(
        20.0,
        sell_value * 0.001,
    )

    brokerage = (
        buy_brokerage
        + sell_brokerage
    )

    stt = sell_value * 0.00025

    transaction_charge = (
        buy_value + sell_value
    ) * 0.0000307

    stamp_duty = buy_value * 0.00003

    sebi_charge = (
        buy_value + sell_value
    ) * (10 / 10_000_000)

    gst_base = (
        brokerage
        + transaction_charge
        + sebi_charge
    )

    gst = gst_base * 0.18

    total = (
        brokerage
        + stt
        + transaction_charge
        + stamp_duty
        + sebi_charge
        + gst
    )

    return {
        "brokerage": brokerage,
        "stt": stt,
        "transaction_charge": transaction_charge,
        "stamp_duty": stamp_duty,
        "sebi_charge": sebi_charge,
        "gst": gst,
        "total": total,
    }


def apply_costs(
    trade,
    slippage_pct,
):
    entry = trade["entry_price"]
    exit_price = trade["exit_price"]

    # Buy execution becomes more expensive.
    actual_entry = entry * (
        1 + slippage_pct / 100
    )

    # Sell execution becomes cheaper.
    actual_exit = exit_price * (
        1 - slippage_pct / 100
    )

    gross_pnl = actual_exit - actual_entry

    buy_value = actual_entry
    sell_value = actual_exit

    charges = calculate_charges(
        buy_value,
        sell_value,
    )

    net_pnl = (
        gross_pnl
        - charges["total"]
    )

    gross_return = (
        gross_pnl
        / actual_entry
    ) * 100

    net_return = (
        net_pnl
        / actual_entry
    ) * 100

    result = trade.copy()

    result.update(
        {
            "actual_entry": actual_entry,
            "actual_exit": actual_exit,
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "gross_return": gross_return,
            "net_return": net_return,
            "charges": charges["total"],
            "slippage_pct": slippage_pct,
        }
    )

    return result


def summarize(trades):

    print()
    print("=" * 100)
    print("COST + SLIPPAGE BACKTEST")
    print("=" * 100)

    for stop_pct in STOP_LEVELS:

        print()
        print(
            f"STOP {stop_pct:.2f}%"
        )
        print("-" * 80)

        stop_trades = [
            trade
            for trade in trades
            if trade["stop_pct"] == stop_pct
        ]

        for slippage_pct in SLIPPAGE_LEVELS:

            results = [
                apply_costs(
                    trade,
                    slippage_pct,
                )
                for trade in stop_trades
            ]

            if not results:
                continue

            winners = sum(
                result["net_pnl"] > 0
                for result in results
            )

            losers = sum(
                result["net_pnl"] < 0
                for result in results
            )

            total_gross = sum(
                result["gross_pnl"]
                for result in results
            )

            total_cost = sum(
                result["charges"]
                for result in results
            )

            total_net = sum(
                result["net_pnl"]
                for result in results
            )

            avg_net_return = (
                sum(
                    result["net_return"]
                    for result in results
                )
                / len(results)
            )

            win_rate = (
                winners
                / len(results)
            ) * 100

            print(
                f"Slippage={slippage_pct:.2f}% | "
                f"Trades={len(results)} | "
                f"Winners={winners} | "
                f"Losers={losers} | "
                f"WinRate={win_rate:.2f}% | "
                f"Gross=₹{total_gross:.4f} | "
                f"Costs=₹{total_cost:.4f} | "
                f"Net=₹{total_net:.4f} | "
                f"AvgNet={avg_net_return:.4f}%"
            )


def main():

    trades = collect_trades()

    print()
    print(
        f"Total simulated signals: "
        f"{len(trades) // len(STOP_LEVELS)}"
    )

    summarize(trades)


if __name__ == "__main__":
    main()
