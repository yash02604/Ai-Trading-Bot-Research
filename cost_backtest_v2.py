import pandas as pd

from stop_loss_backtest import (
    STOCKS,
    collect_historical_candles,
    simulate_stop_loss,
)

from app.reversal_detector import detect_reversal_candidates
from app.market_analysis import calculate_indicators


# ---------------------------------------------------------
# Configuration
# ---------------------------------------------------------

NOTIONALS = [
    1000,
    5000,
    10000,
    25000,
    50000,
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

SLIPPAGES = [
    0.00,
    0.05,
    0.10,
]


# ---------------------------------------------------------
# Charges
# ---------------------------------------------------------

def calculate_charges(buy_value, sell_value):

    turnover = buy_value + sell_value

    # Brokerage: ₹20/order or 0.1%, whichever is lower
    brokerage_buy = min(
        20.0,
        buy_value * 0.001,
    )

    brokerage_sell = min(
        20.0,
        sell_value * 0.001,
    )

    brokerage = (
        brokerage_buy
        + brokerage_sell
    )

    # STT: 0.025% on sell side for equity intraday
    stt = sell_value * 0.00025

    # NSE transaction charges from 1 March 2026
    transaction_charges = (
        turnover * 0.0000307
    )

    # Stamp duty: 0.003% on buy side
    stamp_duty = (
        buy_value * 0.00003
    )

    # SEBI turnover fee: ₹10/crore
    sebi = (
        turnover * 0.000001
    )

    # IPFT: ₹0.01/crore
    ipft = (
        turnover * 0.000000001
    )

    # GST: 18% on brokerage + transaction + IPFT
    gst = 0.18 * (
        brokerage
        + transaction_charges
        + ipft
    )

    total = (
        brokerage
        + stt
        + transaction_charges
        + stamp_duty
        + sebi
        + ipft
        + gst
    )

    return total


# ---------------------------------------------------------
# Load historical signals
# ---------------------------------------------------------

def build_dataset():

    dataset = []

    for symbol, instrument_key in STOCKS.items():

        print(
            f"\nLoading {symbol}..."
        )

        candles = collect_historical_candles(
            instrument_key
        )

        if not candles:
            print(
                f"No candles found for {symbol}"
            )
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

        # Convert numeric columns.
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
                "open",
                "high",
                "low",
                "close",
            ]
        )

        # Make sure the candles are chronological.
        df = df.sort_values(
            "timestamp"
        ).reset_index(
            drop=True
        )

 # -------------------------------------------------
        # Calculate RSI 14
        # -------------------------------------------------

        df = calculate_indicators(df)

        # -------------------------------------------------
        # Detect reversal candidates
        # -------------------------------------------------

        result = detect_reversal_candidates(
            df
        )

        signal_indices = result.index[
            result["reversal_candidate"]
        ].tolist()

        print(
            f"{symbol}: "
            f"{len(signal_indices)} candidates"
        )

        for signal_index in signal_indices:

            dataset.append(
                {
                    "symbol": symbol,
                    "df": result,
                    "signal_index": signal_index,
                }
            )

    print(
        f"\nTotal signals: "
        f"{len(dataset)}"
    )

    return dataset


# ---------------------------------------------------------
# Run experiment
# ---------------------------------------------------------

def run_experiment(
    dataset,
    notional,
    stop_pct,
    slippage_pct,
):

    trades = []

    for item in dataset:

        symbol = item["symbol"]

        df = item["df"]

        signal_index = item[
            "signal_index"
        ]

        result = simulate_stop_loss(
            df,
            signal_index,
            stop_pct,
        )

        if result is None:
            continue

        entry_price = result[
            "entry_price"
        ]

        exit_price = result[
            "exit_price"
        ]

        if entry_price <= 0:
            continue

        # -------------------------------------------------
        # Fractional quantity
        # -------------------------------------------------

        quantity = (
            notional
            / entry_price
        )

        # -------------------------------------------------
        # Slippage
        # -------------------------------------------------

        actual_entry = (
            entry_price
            * (1 + slippage_pct / 100)
        )

        actual_exit = (
            exit_price
            * (1 - slippage_pct / 100)
        )

        buy_value = (
            actual_entry
            * quantity
        )

        sell_value = (
            actual_exit
            * quantity
        )

        gross_pnl = (
            actual_exit
            - actual_entry
        ) * quantity

        charges = calculate_charges(
            buy_value,
            sell_value,
        )

        net_pnl = (
            gross_pnl
            - charges
        )

        gross_return_pct = (
            gross_pnl
            / buy_value
        ) * 100

        net_return_pct = (
            net_pnl
            / buy_value
        ) * 100

        trades.append(
            {
                "symbol": symbol,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "quantity": quantity,
                "gross_pnl": gross_pnl,
                "charges": charges,
                "net_pnl": net_pnl,
                "gross_return_pct": gross_return_pct,
                "net_return_pct": net_return_pct,
                "exit_reason": result[
                    "exit_reason"
                ],
            }
        )

    return trades


# ---------------------------------------------------------
# Summary
# ---------------------------------------------------------

def summarize(
    trades,
    notional,
    stop_pct,
    slippage_pct,
):

    if not trades:
        return None

    df = pd.DataFrame(
        trades
    )

    winners = (
        df["net_pnl"] > 0
    ).sum()

    losers = (
        df["net_pnl"] <= 0
    ).sum()

    total_gross = (
        df["gross_pnl"].sum()
    )

    total_charges = (
        df["charges"].sum()
    )

    total_net = (
        df["net_pnl"].sum()
    )

    avg_gross_return = (
        df["gross_return_pct"].mean()
    )

    avg_net_return = (
        df["net_return_pct"].mean()
    )

    win_rate = (
        winners
        / len(df)
    ) * 100

    return {
        "notional": notional,
        "stop_pct": stop_pct,
        "slippage_pct": slippage_pct,
        "trades": len(df),
        "winners": winners,
        "losers": losers,
        "win_rate": win_rate,
        "gross_pnl": total_gross,
        "charges": total_charges,
        "net_pnl": total_net,
        "avg_gross_return_pct":
            avg_gross_return,
        "avg_net_return_pct":
            avg_net_return,
    }


# ---------------------------------------------------------
# Main
# ---------------------------------------------------------

def main():

    print("=" * 80)

    print(
        "COST BACKTEST V2"
    )

    print(
        "Notional-based cost analysis"
    )

    print("=" * 80)

    dataset = build_dataset()

    if not dataset:

        print(
            "No reversal signals found."
        )

        return

    results = []

    for notional in NOTIONALS:

        print(
            f"\n{'#' * 80}"
        )

        print(
            f"NOTIONAL: ₹{notional:,.0f}"
        )

        print(
            f"{'#' * 80}"
        )

        for stop_pct in STOP_LEVELS:

            for slippage_pct in SLIPPAGES:

                trades = run_experiment(
                    dataset,
                    notional,
                    stop_pct,
                    slippage_pct,
                )

                summary = summarize(
                    trades,
                    notional,
                    stop_pct,
                    slippage_pct,
                )

                if summary:

                    results.append(
                        summary
                    )

                    print(
                        f"Stop {stop_pct:>4.2f}% | "
                        f"Slip {slippage_pct:>4.2f}% | "
                        f"Trades {summary['trades']:>3} | "
                        f"Win {summary['win_rate']:>6.2f}% | "
                        f"Gross ₹{summary['gross_pnl']:>9.2f} | "
                        f"Charges ₹{summary['charges']:>8.2f} | "
                        f"Net ₹{summary['net_pnl']:>9.2f} | "
                        f"Avg Net "
                        f"{summary['avg_net_return_pct']:>8.4f}%"
                    )

    # -----------------------------------------------------
    # Save results
    # -----------------------------------------------------

    results_df = pd.DataFrame(
        results
    )

    output_file = (
        "cost_backtest_v2_results.csv"
    )

    results_df.to_csv(
        output_file,
        index=False,
    )

    print(
        f"\nResults saved to: "
        f"{output_file}"
    )

    print(
        "\nExperiment complete."
    )


if __name__ == "__main__":
    main()