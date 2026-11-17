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

HORIZON = 6

def calculate_charges(buy_value, sell_value):
    """
    Estimate Indian equity intraday charges for backtesting.

    Includes:
        - Brokerage: min(?20, 0.1% of order value) per side
        - STT: 0.025% on sell value
        - NSE transaction charges: 0.00307% on turnover
        - Stamp duty: 0.003% on buy value
        - SEBI charges: ?10 per crore
        - IPFT: ?0.01 per crore
        - GST: 18% on brokerage + transaction charges + IPFT
    """

    brokerage_buy = min(20.0, buy_value * 0.001)
    brokerage_sell = min(20.0, sell_value * 0.001)
    brokerage = brokerage_buy + brokerage_sell

    stt = sell_value * 0.00025

    transaction_charges = (
        buy_value + sell_value
    ) * 0.0000307

    stamp_duty = buy_value * 0.00003

    sebi_charges = (
        buy_value + sell_value
    ) * (10.0 / 10_000_000)

    ipft = (
        buy_value + sell_value
    ) * (0.01 / 10_000_000)

    gst = (
        brokerage
        + transaction_charges
        + ipft
    ) * 0.18

    total_charges = (
        brokerage
        + stt
        + transaction_charges
        + stamp_duty
        + sebi_charges
        + ipft
        + gst
    )

    return {
        "brokerage": brokerage,
        "stt": stt,
        "transaction_charges": transaction_charges,
        "stamp_duty": stamp_duty,
        "sebi_charges": sebi_charges,
        "ipft": ipft,
        "gst": gst,
        "total": total_charges,
    }



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

    merged, _ = merge_raw_candle_batches([("historical", all_candles)], instrument_key)
    return merged


def simulate_stop_loss(df, signal_index, stop_pct):
    entry_price = float(df.loc[signal_index, "close"])
    stop_price = entry_price * (1 - stop_pct / 100)

    future = df.loc[
        signal_index + 1:signal_index + HORIZON
    ]

    if future.empty:
        return None

    for future_index, candle in future.iterrows():

        candle_low = float(candle["low"])
        candle_open = float(candle["open"])

        if candle_low <= stop_price:

            # Conservative execution assumption:
            # if the candle opens below the stop, assume
            # the exit occurs at the opening price.
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
                "stopped": True,
            }

    # Stop was never touched.
    exit_candle = future.iloc[-1]
    exit_price = float(exit_candle["close"])

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
        "stopped": False,
    }


def analyze_stock(symbol, instrument_key):
    print()
    print("=" * 70)
    print(symbol)
    print("=" * 70)

    candles = collect_historical_candles(instrument_key)

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

    print("Reversal candidates:", len(candidates))

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
            result["signal_timestamp"] = df.loc[
                signal_index,
                "timestamp",
            ]

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


def simulate_portfolio(all_results, stop_pct):
    """
    Simulate one shared portfolio across all stocks.

    Portfolio starts at ₹1,000 and evolves after every completed trade.

    Risk:
        - 1% of current portfolio per trade
        - 2% daily loss limit
        - maximum 3 trades per day
        - maximum 1 open position
        - no leverage
    """

    INITIAL_CAPITAL = 1000.0
    RISK_PER_TRADE = 0.01
    DAILY_LOSS_LIMIT = 0.02
    MAX_TRADES_PER_DAY = 3

    portfolio = INITIAL_CAPITAL
    peak_portfolio = INITIAL_CAPITAL
    max_drawdown = 0.0

    trades = []

    # Only use this stop-loss level.
    rows = [
        row
        for row in all_results
        if row["stop_pct"] == stop_pct
    ]

    # Process signals chronologically.
    rows.sort(
        key=lambda row: row["signal_timestamp"]
    )

    current_position_end = None

    current_day = None
    daily_start_portfolio = portfolio
    daily_loss = 0.0
    trades_today = 0

    for row in rows:

        signal_time = row["signal_timestamp"]
        signal_day = signal_time.date()

        # Reset daily state on a new trading day.
        if current_day != signal_day:
            current_day = signal_day
            daily_start_portfolio = portfolio
            daily_loss = 0.0
            trades_today = 0

        # Maximum one open position.
        if (
            current_position_end is not None
            and signal_time <= current_position_end
        ):
            continue

        # Maximum 3 trades per day.
        if trades_today >= MAX_TRADES_PER_DAY:
            continue

        # Daily loss limit.
        daily_loss_limit = (
            daily_start_portfolio
            * DAILY_LOSS_LIMIT
        )

        if daily_loss >= daily_loss_limit:
            continue

        entry_price = row["entry_price"]
        stop_price = row["stop_price"]

        # Risk budget is based on CURRENT portfolio.
        max_trade_risk = (
            portfolio
            * RISK_PER_TRADE
        )

        risk_per_share = (
            entry_price - stop_price
        )

        if risk_per_share <= 0:
            continue

        # Quantity limited by risk.
        risk_quantity = int(
            max_trade_risk
            // risk_per_share
        )

        # Quantity limited by available capital.
        capital_quantity = int(
            portfolio
            // entry_price
        )

        quantity = min(
            risk_quantity,
            capital_quantity,
        )

        # Cannot execute fractional/zero shares.
        if quantity <= 0:
            
            continue

        # Actual capital deployed.
        capital_required = (
            quantity * entry_price
        )

        # Actual trade values.
        buy_value = (
            entry_price * quantity
        )

        sell_value = (
            row["exit_price"] * quantity
        )

        # Gross P&L before charges.
        gross_pnl = (
            row["exit_price"]
            - entry_price
        ) * quantity

        # Estimated brokerage, taxes, and exchange/regulatory charges.
        charges = calculate_charges(
            buy_value,
            sell_value,
        )

        total_charges = charges["total"]

        # Net P&L after charges.
        pnl = gross_pnl - total_charges

        portfolio_before = portfolio

        portfolio += pnl

        # Position remains open until its simulated exit.
        current_position_end = row[
            "exit_timestamp"
        ]

        peak_portfolio = max(
            peak_portfolio,
            portfolio,
        )

        drawdown = (
            peak_portfolio - portfolio
        )

        if peak_portfolio > 0:
            drawdown_pct = (
                drawdown
                / peak_portfolio
            ) * 100
        else:
            drawdown_pct = 0.0

        max_drawdown = max(
            max_drawdown,
            drawdown_pct,
        )

        trades.append(
            {
                "symbol": row["symbol"],
                "entry_time": signal_time,
                "exit_time": row["exit_timestamp"],
                "entry_price": entry_price,
                "exit_price": row["exit_price"],
                "stop_pct": stop_pct,
                "quantity": quantity,
                "capital_required": capital_required,
                "risk_budget": max_trade_risk,
                "planned_risk": (
                    risk_per_share
                    * quantity
                ),
                "gross_pnl": gross_pnl,
                "charges": total_charges,
                "pnl": pnl,
                "portfolio_before": portfolio_before,
                "portfolio_after": portfolio,
                "exit_reason": row["exit_reason"],
            }
        )
        print(
                    f"TRADE | "
                    f"{signal_time} | "
                    f"{row['symbol']} | "
                    f"ENTRY={entry_price:.2f} | "
                    f"EXIT={row['exit_price']:.2f} | "
                    f"QTY={quantity} | "
                    f"STOP={stop_pct:.2f}% | "
                    f"REASON={row['exit_reason']} | "
                    f"P&L=₹{pnl:.2f} | "
                    f"PORTFOLIO=₹{portfolio:.2f}"
                )

    return {
        "stop_pct": stop_pct,
        "starting_capital": INITIAL_CAPITAL,
        "ending_capital": portfolio,
        "net_pnl": (
            portfolio
            - INITIAL_CAPITAL
        ),
        
                
        "return_pct": (
            (
                portfolio
                - INITIAL_CAPITAL
            )
            / INITIAL_CAPITAL
        ) * 100,
        "trades": trades,
        "trade_count": len(trades),
        "winning_trades": sum(
            trade["pnl"] > 0
            for trade in trades
        ),
        "losing_trades": sum(
            trade["pnl"] < 0
            for trade in trades
        ),
        "max_drawdown_pct": max_drawdown,
    }


def print_portfolio_summary(results):

    print()
    print("=" * 100)
    print("DYNAMIC ₹1,000 PORTFOLIO BACKTEST")
    print("=" * 100)

    for result in results:

        trade_count = result["trade_count"]

        if trade_count > 0:
            win_rate = (
                result["winning_trades"]
                / trade_count
            ) * 100
        else:
            win_rate = 0.0

        print()
        print(
            f"Stop loss: "
            f"{result['stop_pct']:.2f}%"
        )

        print(
            "Starting capital: ₹"
            f"{result['starting_capital']:.2f}"
        )

        print(
            "Ending capital: ₹"
            f"{result['ending_capital']:.2f}"
        )

        print(
            "Net P&L: ₹"
            f"{result['net_pnl']:.2f}"
        )

        print(
            "Portfolio return:",
            round(
                result["return_pct"],
                4,
            ),
            "%",
        )

        print(
            "Trades executed:",
            trade_count,
        )

        print(
            "Winning trades:",
            result["winning_trades"],
        )

        print(
            "Losing trades:",
            result["losing_trades"],
        )

        print(
            "Win rate:",
            round(win_rate, 2),
            "%",
        )

        print(
            "Maximum drawdown:",
            round(
                result["max_drawdown_pct"],
                4,
            ),
            "%",
        )

def print_trade_cost_report(results):
    print()
    print("=" * 100)
    print("TRADE-LEVEL COST REPORT")
    print("=" * 100)

    for result in results:
        trades = result["trades"]

        if not trades:
            continue

        gross_pnl = sum(
            trade["gross_pnl"]
            for trade in trades
        )

        total_charges = sum(
            trade["charges"]
            for trade in trades
        )

        net_pnl = sum(
            trade["pnl"]
            for trade in trades
        )

        total_buy_value = sum(
            trade["capital_required"]
            for trade in trades
        )

        total_sell_value = sum(
            trade["exit_price"] * trade["quantity"]
            for trade in trades
        )

        total_turnover = (
            total_buy_value
            + total_sell_value
        )

        charges_pct_turnover = (
            total_charges / total_turnover * 100
            if total_turnover > 0
            else 0.0
        )

        gross_winners = sum(
            trade["gross_pnl"] > 0
            for trade in trades
        )

        gross_losers = sum(
            trade["gross_pnl"] < 0
            for trade in trades
        )

        net_winners = sum(
            trade["pnl"] > 0
            for trade in trades
        )

        net_losers = sum(
            trade["pnl"] < 0
            for trade in trades
        )

        gross_winners_became_net_losers = sum(
            trade["gross_pnl"] > 0
            and trade["pnl"] <= 0
            for trade in trades
        )

        gross_losers = sum(
            trade["gross_pnl"] < 0
            for trade in trades
        )

        print()
        print(
            f"Stop loss: "
            f"{result['stop_pct']:.2f}%"
        )

        print(
            f"Trades: {len(trades)}"
        )

        print(
            f"Gross P&L: ₹{gross_pnl:.2f}"
        )

        print(
            f"Total charges: ₹{total_charges:.2f}"
        )

        print(
            f"Net P&L: ₹{net_pnl:.2f}"
        )

        print(
            f"Charges / turnover: "
            f"{charges_pct_turnover:.4f}%"
        )

        print(
            f"Gross winners: {gross_winners}"
        )

        print(
            f"Gross losers: {gross_losers}"
        )

        print(
            f"Net winners: {net_winners}"
        )

        print(
            f"Net losers: {net_losers}"
        )

        print(
            "Gross winners turned into "
            f"net losers: "
            f"{gross_winners_became_net_losers}"
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

    portfolio_results = []

    for stop_pct in STOP_LEVELS:

        result = simulate_portfolio(
            all_results,
            stop_pct,
        )

        portfolio_results.append(result)

    print_portfolio_summary(
        portfolio_results
    )

    print_trade_cost_report(
    portfolio_results
)


if __name__ == "__main__":
    main()
