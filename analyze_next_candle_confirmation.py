"""
Stage 7A — Next-Candle Reversal Confirmation

Hypothesis:
    Frozen reversal signal
        ->
    next 5-minute candle must close bullish (close > open)
        ->
    enter at the following candle OPEN

This is a descriptive historical experiment only.
It does NOT modify the frozen strategy and does NOT run OOS.

Rules:
- Frozen signal remains unchanged.
- Confirmation candle = baseline entry candle.
- Confirmation condition = confirmation close > confirmation open.
- Entry = following candle open.
- Stop = 1%.
- Holding horizon = 6 candles AFTER the new entry.
- Long-only.
- Whole-share position sizing.
- Uses the project's existing cost model.
"""

from pathlib import Path

import pandas as pd

from app.cost_model import CostAssumptions, estimate_round_trip_costs
from app.historical_validation import (
    load_historical_data,
    prepare_symbol_frames,
    _make_events,
)
from app.risk_engine import RiskConfig, calculate_position_size
from strategy_backtest import STOCKS


# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

DATA_PATH = Path("data/research/extended_historical_robustness_candles.csv")
TRADES_PATH = Path("backtests/validation/trades.csv")
OUTPUT_DIR = Path("backtests/validation")

INITIAL_CAPITAL = 100_000.0
STOP_PCT = 1.0
HORIZON = 6
SLIPPAGE_BPS_PER_SIDE = 5.0


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

def pct_return(entry, price):
    if entry == 0:
        return 0.0
    return (price / entry - 1.0) * 100.0


def simulate_confirmed_trade(
    frame,
    entry_index,
    entry_price,
    stop_pct=1.0,
    horizon=6,
):
    """
    Simulate the trade beginning at the confirmed entry candle.

    Entry:
        following candle OPEN

    Stop:
        1% below entry

    Exit:
        first candle whose LOW reaches stop,
        otherwise close of the horizon candle.

    Important:
        This intentionally mirrors the project's OHLC-style
        historical simulation convention.
    """

    stop_price = entry_price * (1.0 - stop_pct / 100.0)

    last_index = min(entry_index + horizon - 1, len(frame) - 1)

    mfe_pct = float("-inf")
    mae_pct = float("inf")

    exit_index = None
    exit_price = None
    exit_reason = None

    for i in range(entry_index, last_index + 1):
        row = frame.iloc[i]

        high = float(row["high"])
        low = float(row["low"])

        mfe_pct = max(
            mfe_pct,
            pct_return(entry_price, high),
        )

        mae_pct = min(
            mae_pct,
            pct_return(entry_price, low),
        )

        # Stop-loss check.
        if low <= stop_price:
            exit_index = i
            exit_price = stop_price
            exit_reason = "stop_exit"
            break

    # Horizon exit.
    if exit_index is None:
        exit_index = last_index
        exit_price = float(frame.iloc[last_index]["close"])
        exit_reason = "horizon_exit"

    return {
        "exit_index": exit_index,
        "exit_timestamp": frame.iloc[exit_index]["timestamp"],
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "mfe_pct": mfe_pct if mfe_pct != float("-inf") else 0.0,
        "mae_pct": mae_pct if mae_pct != float("inf") else 0.0,
    }


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():

    print("=" * 72)
    print("STAGE 7A — NEXT-CANDLE REVERSAL CONFIRMATION")
    print("=" * 72)

    print("\nLoading historical candles...")

    data, audit = load_historical_data(DATA_PATH)

    print(f"Loaded candles: {len(data):,}")

    frames = prepare_symbol_frames(data)

    missing = sorted(set(STOCKS) - set(frames))

    if missing:
        raise ValueError(
            f"Missing configured symbols: {missing}"
        )

    print(f"Prepared symbols: {len(frames)}")

    # -----------------------------------------------------------------
    # Load original portfolio trades.
    # -----------------------------------------------------------------

    trades = pd.read_csv(TRADES_PATH)

    trades = trades[
        trades["book"].astype(str) == "portfolio"
    ].copy()

    print(f"Historical portfolio trades: {len(trades)}")

    if len(trades) != 46:
        print(
            f"WARNING: expected 46 portfolio trades, "
            f"found {len(trades)}."
        )

    trades["signal_timestamp"] = pd.to_datetime(
        trades["signal_timestamp"],
        utc=True,
    )

    trades["entry_timestamp"] = pd.to_datetime(
        trades["entry_timestamp"],
        utc=True,
    )

    # Convert to IST so they match the historical candle timestamps.
    trades["signal_timestamp"] = (
        trades["signal_timestamp"]
        .dt.tz_convert("Asia/Kolkata")
    )

    trades["entry_timestamp"] = (
        trades["entry_timestamp"]
        .dt.tz_convert("Asia/Kolkata")
    )

    # -----------------------------------------------------------------
    # Build frozen strategy events directly from the current code.
    # -----------------------------------------------------------------

    print("\nBuilding frozen reversal events...")

    events, raw_signals, data_rejects = _make_events(frames)

    event_lookup = {}

    for event in events:
        key = (
            event["symbol"],
            pd.Timestamp(event["signal_timestamp"]),
        )
        event_lookup[key] = event

    # -----------------------------------------------------------------
    # Evaluate confirmation.
    # -----------------------------------------------------------------

    results = []

    for _, original_trade in trades.iterrows():

        symbol = original_trade["symbol"]

        signal_time = pd.Timestamp(
            original_trade["signal_timestamp"]
        )

        key = (symbol, signal_time)

        if key not in event_lookup:
            results.append({
                "symbol": symbol,
                "signal_timestamp": signal_time,
                "status": "event_not_found",
            })
            continue

        event = event_lookup[key]

        frame = frames[symbol]

        signal_index = int(event["signal_index"])

        confirmation_index = signal_index + 1
        confirmed_entry_index = signal_index + 2

        # Need:
        #
        # signal candle
        # confirmation candle
        # following entry candle
        #
        if confirmed_entry_index >= len(frame):
            results.append({
                "symbol": symbol,
                "signal_timestamp": signal_time,
                "status": "insufficient_future_data",
            })
            continue

        signal_row = frame.iloc[signal_index]
        confirmation_row = frame.iloc[confirmation_index]
        entry_row = frame.iloc[confirmed_entry_index]

        confirmation_open = float(
            confirmation_row["open"]
        )

        confirmation_close = float(
            confirmation_row["close"]
        )

        confirmation_high = float(
            confirmation_row["high"]
        )

        confirmation_low = float(
            confirmation_row["low"]
        )

        confirmation_bullish = (
            confirmation_close > confirmation_open
        )

        confirmation_return_pct = pct_return(
            confirmation_open,
            confirmation_close,
        )

        entry_price = float(entry_row["open"])

        entry_timestamp = entry_row["timestamp"]

        base = {
            "symbol": symbol,
            "signal_timestamp": signal_time,
            "signal_close": float(signal_row["close"]),
            "confirmation_timestamp": confirmation_row["timestamp"],
            "confirmation_open": confirmation_open,
            "confirmation_high": confirmation_high,
            "confirmation_low": confirmation_low,
            "confirmation_close": confirmation_close,
            "confirmation_return_pct": confirmation_return_pct,
            "confirmation_bullish": confirmation_bullish,
            "confirmed_entry_timestamp": entry_timestamp,
            "confirmed_entry_price": entry_price,
            "original_entry_timestamp": original_trade[
                "entry_timestamp"
            ],
            "original_entry_price": original_trade[
                "entry_price"
            ],
            "original_net_pnl": original_trade[
                "net_pnl"
            ],
        }

        # -------------------------------------------------------------
        # Rejected by confirmation.
        # -------------------------------------------------------------

        if not confirmation_bullish:

            base.update({
                "status": "rejected_confirmation",
                "net_pnl": 0.0,
                "gross_pnl": 0.0,
                "total_cost": 0.0,
                "mfe_pct": 0.0,
                "mae_pct": 0.0,
                "exit_reason": "",
            })

            results.append(base)
            continue

        # -------------------------------------------------------------
        # Confirmed -> simulate the later entry.
        # -------------------------------------------------------------

        simulation = simulate_confirmed_trade(
            frame=frame,
            entry_index=confirmed_entry_index,
            entry_price=entry_price,
            stop_pct=STOP_PCT,
            horizon=HORIZON,
        )

        risk_config = RiskConfig(
            capital=INITIAL_CAPITAL
        )

        stop_price = entry_price * (
            1.0 - STOP_PCT / 100.0
        )

        size = calculate_position_size(
            risk_config,
            entry_price,
            stop_price,
        )

        quantity = int(size.quantity)

        if quantity <= 0:

            base.update({
                "status": "insufficient_position_size",
                "quantity": 0,
                "net_pnl": 0.0,
                "gross_pnl": 0.0,
                "total_cost": 0.0,
                "mfe_pct": simulation["mfe_pct"],
                "mae_pct": simulation["mae_pct"],
                "exit_reason": simulation["exit_reason"],
            })

            results.append(base)
            continue

        gross_pnl = (
            simulation["exit_price"] - entry_price
        ) * quantity

        costs = estimate_round_trip_costs(
            entry_price,
            simulation["exit_price"],
            quantity,
            entry_timestamp,
            assumptions=CostAssumptions(
                slippage_bps_per_side=SLIPPAGE_BPS_PER_SIDE
            ),
        )

        total_cost = float(
            costs["total_cost"]
        )

        net_pnl = gross_pnl - total_cost

        base.update({
            "status": "confirmed_trade",
            "quantity": quantity,
            "stop_price": stop_price,
            "exit_timestamp": simulation[
                "exit_timestamp"
            ],
            "exit_price": simulation[
                "exit_price"
            ],
            "exit_reason": simulation[
                "exit_reason"
            ],
            "gross_pnl": gross_pnl,
            "total_cost": total_cost,
            "net_pnl": net_pnl,
            "mfe_pct": simulation["mfe_pct"],
            "mae_pct": simulation["mae_pct"],
        })

        results.append(base)

    # -----------------------------------------------------------------
    # Save results.
    # -----------------------------------------------------------------

    result_df = pd.DataFrame(results)

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    output_file = (
        OUTPUT_DIR
        / "next_candle_confirmation_analysis.csv"
    )

    result_df.to_csv(
        output_file,
        index=False,
    )

    # -----------------------------------------------------------------
    # Summary.
    # -----------------------------------------------------------------

    total = len(result_df)

    confirmed = result_df[
        result_df["status"] == "confirmed_trade"
    ]

    rejected = result_df[
        result_df["status"] == "rejected_confirmation"
    ]

    confirmed_count = len(confirmed)
    rejected_count = len(rejected)

    winners = confirmed[
        confirmed["net_pnl"] > 0
    ]

    losers = confirmed[
        confirmed["net_pnl"] <= 0
    ]

    gross_pnl = (
        confirmed["gross_pnl"].sum()
        if confirmed_count
        else 0.0
    )

    total_cost = (
        confirmed["total_cost"].sum()
        if confirmed_count
        else 0.0
    )

    net_pnl = (
        confirmed["net_pnl"].sum()
        if confirmed_count
        else 0.0
    )

    win_rate = (
        len(winners) / confirmed_count * 100.0
        if confirmed_count
        else 0.0
    )

    avg_pnl = (
        confirmed["net_pnl"].mean()
        if confirmed_count
        else 0.0
    )

    avg_mfe = (
        confirmed["mfe_pct"].mean()
        if confirmed_count
        else 0.0
    )

    avg_mae = (
        confirmed["mae_pct"].mean()
        if confirmed_count
        else 0.0
    )

    print("\n" + "=" * 72)
    print("STAGE 7A RESULTS")
    print("=" * 72)

    print(f"\nOriginal portfolio signals: {total}")

    print(
        f"Confirmation passed:       "
        f"{confirmed_count}"
    )

    print(
        f"Confirmation rejected:     "
        f"{rejected_count}"
    )

    if total:
        print(
            f"Confirmation pass rate:    "
            f"{confirmed_count / total * 100:.2f}%"
        )

    print("\nConfirmed trades:")
    print(
        f"  Winners:                  {len(winners)}"
    )
    print(
        f"  Losers:                   {len(losers)}"
    )
    print(
        f"  Win rate:                 {win_rate:.2f}%"
    )
    print(
        f"  Gross P&L:               ₹{gross_pnl:,.2f}"
    )
    print(
        f"  Costs:                   ₹{total_cost:,.2f}"
    )
    print(
        f"  Net P&L:                 ₹{net_pnl:,.2f}"
    )
    print(
        f"  Average net/trade:       ₹{avg_pnl:,.2f}"
    )
    print(
        f"  Average MFE:              {avg_mfe:.4f}%"
    )
    print(
        f"  Average MAE:              {avg_mae:.4f}%"
    )

    print("\nConfirmation candle return:")

    if confirmed_count:
        print(
            f"  Mean:                    "
            f"{confirmed['confirmation_return_pct'].mean():.4f}%"
        )

        print(
            f"  Median:                  "
            f"{confirmed['confirmation_return_pct'].median():.4f}%"
        )

    print("\nComparison with original 46-trade baseline:")

    original_net = trades["net_pnl"].sum()
    original_winners = (
        trades["net_pnl"] > 0
    ).sum()

    original_win_rate = (
        original_winners / len(trades) * 100.0
    )

    print(
        f"  Original net P&L:        ₹{original_net:,.2f}"
    )

    print(
        f"  Original win rate:        "
        f"{original_win_rate:.2f}%"
    )

    print(
        f"  Confirmation net P&L:    "
        f"₹{net_pnl:,.2f}"
    )

    print(
        f"  Confirmation win rate:    "
        f"{win_rate:.2f}%"
    )

    print(
        f"\nSaved:"
        f"\n  {output_file}"
    )

    print("\n" + "=" * 72)
    print("IMPORTANT: DESCRIPTIVE ONLY — NO OOS TEST")
    print("=" * 72)


if __name__ == "__main__":
    main()