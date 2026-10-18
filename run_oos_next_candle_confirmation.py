from pathlib import Path
from collections import Counter

import pandas as pd

from app.cost_model import (
    CostAssumptions,
    estimate_round_trip_costs,
)

from app.historical_validation import (
    load_historical_data,
    prepare_symbol_frames,
    _make_events,
    _simulate_exit,
    calculate_metrics,
)

from app.risk_engine import (
    RiskConfig,
    RiskState,
    check_trade_allowed,
    calculate_position_size,
)

from strategy_backtest import STOCKS


# ============================================================
# STAGE 7A
# OOS NEXT-CANDLE REVERSAL CONFIRMATION
# ============================================================

SOURCE = Path(
    "scripts/data/research/"
    "extended_historical_robustness_oos_candles.csv"
)

OOS_START = pd.Timestamp(
    "2025-12-02 00:00:00",
    tz="Asia/Kolkata",
)

OOS_END = pd.Timestamp(
    "2026-10-01 23:59:59",
    tz="Asia/Kolkata",
)

OUTPUT = Path(
    "backtests/oos_next_candle_confirmation"
)


# ============================================================
# CONFIRMATION RULE
#
# Frozen signal candle
#        ↓
# Next 5-minute candle
#        ↓
# close > open
#        ↓
# Enter following candle OPEN
#
# Everything else remains frozen.
# ============================================================


def build_confirmation_events(frames):

    all_events, raw_signals, data_rejects = _make_events(
        frames
    )

    # IMPORTANT:
    # OOS selection is based on the ORIGINAL
    # frozen signal timestamp.
    events = [
        event
        for event in all_events
        if OOS_START
        <= event["signal_timestamp"]
        <= OOS_END
    ]

    confirmed_events = []
    rejected_signals = []

    for event in events:

        symbol = event["symbol"]

        frame = frames[symbol]

        signal_index = event["signal_index"]

        confirmation_index = signal_index + 1
        confirmed_entry_index = signal_index + 2

        # Need:
        # signal candle
        # confirmation candle
        # entry candle
        if confirmed_entry_index >= len(frame):

            rejected_signals.append({
                "symbol": symbol,
                "signal_timestamp": event[
                    "signal_timestamp"
                ],
                "reason": "insufficient_future_candles",
            })

            continue

        confirmation_candle = frame.iloc[
            confirmation_index
        ]

        confirmation_open = float(
            confirmation_candle["open"]
        )

        confirmation_close = float(
            confirmation_candle["close"]
        )

        confirmation_timestamp = (
            confirmation_candle["timestamp"]
        )

        confirmation_return_pct = (
            (
                confirmation_close
                / confirmation_open
            ) - 1.0
        ) * 100.0

        # ----------------------------------------------------
        # CONFIRMATION CONDITION
        # ----------------------------------------------------

        if confirmation_close <= confirmation_open:

            rejected_signals.append({
                "symbol": symbol,
                "signal_timestamp": event[
                    "signal_timestamp"
                ],
                "confirmation_timestamp":
                    confirmation_timestamp,
                "confirmation_open":
                    confirmation_open,
                "confirmation_close":
                    confirmation_close,
                "confirmation_return_pct":
                    confirmation_return_pct,
                "reason": "confirmation_failed",
            })

            continue

        # ----------------------------------------------------
        # CREATE MODIFIED EVENT
        #
        # Signal remains exactly the same.
        # Only entry is moved one candle later.
        # ----------------------------------------------------

        confirmed_event = dict(event)

        confirmed_event[
            "original_entry_index"
        ] = event["entry_index"]

        confirmed_event[
            "original_entry_timestamp"
        ] = event["entry_timestamp"]

        confirmed_event[
            "confirmation_index"
        ] = confirmation_index

        confirmed_event[
            "confirmation_timestamp"
        ] = confirmation_timestamp

        confirmed_event[
            "confirmation_open"
        ] = confirmation_open

        confirmed_event[
            "confirmation_close"
        ] = confirmation_close

        confirmed_event[
            "confirmation_return_pct"
        ] = confirmation_return_pct

        # New entry = candle AFTER confirmation.
        confirmed_event[
            "entry_index"
        ] = confirmed_entry_index

        confirmed_event[
            "entry_timestamp"
        ] = frame.iloc[
            confirmed_entry_index
        ]["timestamp"]

        confirmed_events.append(
            confirmed_event
        )

    confirmed_events.sort(
        key=lambda x: (
            x["signal_timestamp"],
            x.get("priority", 0),
        )
    )

    return (
        confirmed_events,
        rejected_signals,
        raw_signals,
        data_rejects,
    )


# ============================================================
# OOS PORTFOLIO SIMULATION
# ============================================================


def simulate_confirmation_book(
    frames,
    initial_capital=100_000.0,
    stop_pct=1.0,
    horizon=6,
    costs=None,
):

    costs = costs or CostAssumptions(
        slippage_bps_per_side=5.0
    )

    (
        events,
        confirmation_rejections,
        raw_signals,
        data_rejects,
    ) = build_confirmation_events(frames)

    symbol_counts = {
        symbol: Counter(
            frozen_signals=0
        )
        for symbol in frames
    }

    for event in events:

        symbol = event["symbol"]

        symbol_counts[symbol][
            "confirmation_accepted"
        ] += 1

    for symbol in symbol_counts:

        symbol_counts[symbol][
            "frozen_signals"
        ] = sum(
            1
            for event in events
            if event["symbol"] == symbol
        )

    book_trades = []

    reject_reasons = Counter()

    risk_config = RiskConfig(
        capital=initial_capital
    )

    capital = initial_capital

    last_exit = None

    daily_trades = Counter()
    daily_losses = Counter()

    max_exposure = 0.0

    # --------------------------------------------------------
    # PROCESS CONFIRMED EVENTS
    # --------------------------------------------------------

    for event in events:

        symbol = event["symbol"]

        entry_time = event[
            "entry_timestamp"
        ]

        # ----------------------------------------------------
        # EXACT BASELINE POSITION RULE
        # ----------------------------------------------------

        if (
            last_exit is not None
            and entry_time <= last_exit
        ):

            reject_reasons[
                "maximum_open_positions"
            ] += 1

            symbol_counts[symbol][
                "risk_rejected"
            ] += 1

            continue

        day = entry_time.date()

        # ----------------------------------------------------
        # CAPITAL CHECK
        # ----------------------------------------------------

        if capital <= 0:

            reject_reasons[
                "insufficient_capital"
            ] += 1

            symbol_counts[symbol][
                "risk_rejected"
            ] += 1

            continue

        # ----------------------------------------------------
        # EXACT BASELINE RISK CONFIG
        # ----------------------------------------------------

        active_config = RiskConfig(
            capital=capital,
            max_risk_per_trade=
                risk_config.max_risk_per_trade,
            max_daily_loss=
                risk_config.max_daily_loss,
            max_positions=
                risk_config.max_positions,
            max_trades_per_day=
                risk_config.max_trades_per_day,
        )

        state = RiskState(
            daily_loss=daily_losses[day],
            trades_today=daily_trades[day],
        )

        allowed, reason = check_trade_allowed(
            active_config,
            state,
        )

        if not allowed:

            reject_reasons[reason] += 1

            symbol_counts[symbol][
                "risk_rejected"
            ] += 1

            continue

        frame = frames[symbol]

        # ----------------------------------------------------
        # ENTRY PRICE
        #
        # This is now the candle AFTER the
        # confirmation candle.
        # ----------------------------------------------------

        entry = float(
            frame.at[
                event["entry_index"],
                "open",
            ]
        )

        stop = entry * (
            1.0 - stop_pct / 100.0
        )

        # ----------------------------------------------------
        # EXACT BASELINE POSITION SIZING
        # ----------------------------------------------------

        size = calculate_position_size(
            active_config,
            entry,
            stop,
        )

        if size.quantity <= 0:

            reject_reasons[
                "insufficient_capital_for_one_share"
            ] += 1

            symbol_counts[symbol][
                "risk_rejected"
            ] += 1

            continue

        # ----------------------------------------------------
        # EXACT BASELINE EXIT SIMULATION
        #
        # Same 1% stop.
        # Same 6-candle horizon.
        #
        # The only difference is the modified
        # entry_index in the event.
        # ----------------------------------------------------

        trade = _simulate_exit(
            frame,
            event,
            stop_pct,
            horizon,
        )

        # ----------------------------------------------------
        # POSITION INFORMATION
        # ----------------------------------------------------

        trade["quantity"] = size.quantity

        trade["planned_risk"] = (
            size.planned_risk
        )

        trade["capital_exposure"] = (
            size.capital_required
        )

        # ----------------------------------------------------
        # P&L
        # ----------------------------------------------------

        trade["gross_pnl"] = (
            trade["exit_price"]
            - trade["entry_price"]
        ) * size.quantity

        # ----------------------------------------------------
        # EXACT BASELINE COST MODEL
        # ----------------------------------------------------

        cost_values = estimate_round_trip_costs(
            trade["entry_price"],
            trade["exit_price"],
            size.quantity,
            trade["entry_timestamp"],
            assumptions=costs,
        )

        trade.update(cost_values)

        trade["pnl"] = trade["gross_pnl"]

        trade["net_pnl"] = (
            trade["gross_pnl"]
            - trade["total_cost"]
        )

        trade["r_multiple"] = (
            trade["net_pnl"]
            / size.planned_risk
            if size.planned_risk
            else 0.0
        )

        # ----------------------------------------------------
        # STAGE 7A METADATA
        # ----------------------------------------------------

        trade["book"] = (
            "oos_next_candle_confirmation"
        )

        trade["ai_replay"] = "unavailable"

        trade["original_signal_timestamp"] = (
            event["signal_timestamp"]
        )

        trade["original_entry_timestamp"] = (
            event["original_entry_timestamp"]
        )

        trade["confirmation_timestamp"] = (
            event["confirmation_timestamp"]
        )

        trade["confirmation_open"] = (
            event["confirmation_open"]
        )

        trade["confirmation_close"] = (
            event["confirmation_close"]
        )

        trade["confirmation_return_pct"] = (
            event["confirmation_return_pct"]
        )

        # ----------------------------------------------------
        # STORE TRADE
        # ----------------------------------------------------

        book_trades.append(trade)

        symbol_counts[symbol][
            "risk_accepted"
        ] += 1

        symbol_counts[symbol][
            "quantitative_buy_entries"
        ] += 1

        max_exposure = max(
            max_exposure,
            size.capital_required,
        )

        # ----------------------------------------------------
        # UPDATE CAPITAL
        # ----------------------------------------------------

        capital += trade["net_pnl"]

        if trade["net_pnl"] < 0:

            daily_losses[day] += abs(
                trade["net_pnl"]
            )

        daily_trades[day] += 1

        last_exit = trade[
            "exit_timestamp"
        ]

    # --------------------------------------------------------
    # SYMBOL DATA REJECTIONS
    # --------------------------------------------------------

    for symbol in frames:

        symbol_counts[symbol][
            "data_rejected"
        ] = sum(
            count
            for (s, _), count
            in data_rejects.items()
            if s == symbol
        )

        symbol_counts[symbol][
            "sell_signals"
        ] = 0

    # --------------------------------------------------------
    # TRADING DAYS
    # --------------------------------------------------------

    trading_days = len(
        set(
            event["entry_timestamp"].date()
            for event in events
        )
    )

    # --------------------------------------------------------
    # METRIC COUNTS
    # --------------------------------------------------------

    counts = {

        "frozen_reversal_signals":
            len(events),

        "confirmation_passed":
            len(events),

        "confirmation_rejected":
            len(confirmation_rejections),

        "risk_accepted_entries":
            len(book_trades),

        "risk_rejected_signals":
            int(sum(reject_reasons.values())),

        "data_rejected_signals":
            int(sum(data_rejects.values())),

        "ai_confirmed_buy_signals":
            None,

        "ai_rejected_signals":
            None,

        "sell_signals":
            0,

        "hold_signals":
            None,

        "ai_replay":
            "unavailable",

        "risk_rejection_reasons":
            dict(reject_reasons),

        "data_rejection_reasons":
            {
                f"{symbol}:{reason}":
                    int(value)

                for (symbol, reason), value
                in data_rejects.items()
            },

        "per_symbol":
            {
                symbol: dict(count)
                for symbol, count
                in symbol_counts.items()
            },
    }

    # --------------------------------------------------------
    # EXACT PROJECT METRICS CALCULATION
    # --------------------------------------------------------

    metrics = calculate_metrics(
        book_trades,
        initial_capital,
        trading_days,
        counts,
        "oos_next_candle_confirmation",
    )

    metrics[
        "maximum_capital_exposure"
    ] = max_exposure

    metrics[
        "maximum_capital_exposure_pct"
    ] = (
        max_exposure
        / initial_capital
        * 100
        if initial_capital
        else 0.0
    )

    return (
        book_trades,
        metrics,
        counts,
        confirmation_rejections,
    )


# ============================================================
# MAIN
# ============================================================


def main():

    print("=" * 72)
    print(
        "STAGE 7A — OOS NEXT-CANDLE "
        "REVERSAL CONFIRMATION"
    )
    print("=" * 72)

    print(
        f"\nWarm-up data:\n{SOURCE}"
    )

    print(
        f"\nOOS period:\n"
        f"{OOS_START} -> {OOS_END}"
    )

    print("\nStrategy: FROZEN")

    print(
        "Confirmation: "
        "next candle must close bullish"
    )

    print(
        "Entry: "
        "following candle OPEN"
    )

    print("Stop: 1.0%")
    print("Horizon: 6 candles")
    print("Risk/trade: 1%")
    print("Daily loss limit: 2%")
    print("Max open positions: 1")
    print("Max trades/day: 3")
    print("Slippage: 5 bps/side")
    print("AI replay: unavailable")

    print("\nLoading historical data...")

    data, audit = load_historical_data(
        SOURCE
    )

    print(
        f"Loaded rows: {len(data):,}"
    )

    print(
        f"Loaded symbols: "
        f"{len(data.stock.unique())}"
    )

    print(
        f"Loaded first: "
        f"{data.timestamp.min()}"
    )

    print(
        f"Loaded last: "
        f"{data.timestamp.max()}"
    )

    # --------------------------------------------------------
    # VERIFY EXPECTED STOCKS
    # --------------------------------------------------------

    missing = sorted(
        set(STOCKS)
        - set(data.stock.unique())
    )

    if missing:

        raise RuntimeError(
            f"Missing expected symbols: "
            f"{missing}"
        )

    # --------------------------------------------------------
    # PREPARE FULL DATASET
    #
    # IMPORTANT:
    # Keep November warm-up candles so that
    # indicators are calculated correctly.
    # --------------------------------------------------------

    frames = prepare_symbol_frames(
        data
    )

    missing_frames = sorted(
        set(STOCKS)
        - set(frames)
    )

    if missing_frames:

        raise RuntimeError(
            f"Missing prepared frames: "
            f"{missing_frames}"
        )

    print(
        f"Prepared frames: "
        f"{len(frames)}"
    )

    # --------------------------------------------------------
    # RUN OOS SIMULATION
    # --------------------------------------------------------

    costs = CostAssumptions(
        slippage_bps_per_side=5.0
    )

    (
        trades,
        metrics,
        counts,
        confirmation_rejections,
    ) = simulate_confirmation_book(
        frames,
        initial_capital=100_000.0,
        stop_pct=1.0,
        horizon=6,
        costs=costs,
    )

    # --------------------------------------------------------
    # OUTPUT DIRECTORY
    # --------------------------------------------------------

    OUTPUT.mkdir(
        parents=True,
        exist_ok=True,
    )

    trades_df = pd.DataFrame(trades)

    trades_df.to_csv(
        OUTPUT / "trades.csv",
        index=False,
    )

    pd.DataFrame(
        confirmation_rejections
    ).to_csv(
        OUTPUT
        / "confirmation_rejected_signals.csv",
        index=False,
    )

    pd.DataFrame(
        [metrics]
    ).to_csv(
        OUTPUT / "metrics.csv",
        index=False,
    )

    # --------------------------------------------------------
    # PRINT RESULTS
    # --------------------------------------------------------

    print("\n")
    print("=" * 72)
    print("STAGE 7A — OOS RESULTS")
    print("=" * 72)

    all_oos_events, _, _, _ = build_confirmation_events(frames)

    all_oos_events = [
        event
        for event in all_oos_events
        if OOS_START
        <= event["signal_timestamp"]
        <= OOS_END
    ]

    print(
        f"\nFrozen OOS signals: "
        f"{len(all_oos_events)}"
    )

    print(
        f"Confirmation passed: "
        f"{len(all_oos_events) - len(confirmation_rejections)}"
    )

    print(
        f"Confirmation rejected: "
        f"{len(confirmation_rejections)}"
    )

    print(
        f"\nAccepted trades: "
        f"{len(trades)}"
    )

    print(
        f"Risk rejected: "
        f"{counts['risk_rejected_signals']}"
    )

    if trades:

        winners = [
            t for t in trades
            if t["net_pnl"] > 0
        ]

        losers = [
            t for t in trades
            if t["net_pnl"] <= 0
        ]

        print(
            f"\nWinners: {len(winners)}"
        )

        print(
            f"Losers: {len(losers)}"
        )

    print(
        "\nMetrics:"
    )

    for key in [
        "win_rate_pct",
        "gross_pnl",
        "total_cost",
        "net_pnl",
        "net_return_pct",
        "profit_factor",
        "ending_capital",
    ]:

        if key in metrics:

            value = metrics[key]

            if "pnl" in key or key == "ending_capital":

                print(
                    f"  {key}: "
                    f"₹{float(value):,.2f}"
                )

            elif "pct" in key:

                print(
                    f"  {key}: "
                    f"{float(value):.2f}%"
                )

            else:

                print(
                    f"  {key}: "
                    f"{float(value):.4f}"
                )

    # --------------------------------------------------------
    # SAVE SUMMARY
    # --------------------------------------------------------

    summary = {
        "stage": "7A",
        "experiment":
            "next_candle_confirmation",

        "oos_start":
            str(OOS_START),

        "oos_end":
            str(OOS_END),

        "confirmation_rule":
            "next candle close > open",

        "entry_rule":
            "following candle open",

        "stop_pct":
            1.0,

        "horizon_candles":
            6,

        "risk_per_trade_pct":
            1.0,

        "daily_loss_limit_pct":
            2.0,

        "max_open_positions":
            1,

        "max_trades_per_day":
            3,

        "slippage_bps_per_side":
            5.0,

        "frozen_signals":
            counts[
                "frozen_reversal_signals"
            ],

        "confirmation_rejected":
            len(confirmation_rejections),

        "accepted_trades":
            len(trades),

        "metrics":
            metrics,
    }

    import json

    with open(
        OUTPUT / "summary.json",
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            summary,
            f,
            indent=2,
            default=str,
        )

    print("\nSaved:")

    print(
        f"  {OUTPUT / 'trades.csv'}"
    )

    print(
        f"  {OUTPUT / 'metrics.csv'}"
    )

    print(
        "  "
        f"{OUTPUT / 'confirmation_rejected_signals.csv'}"
    )

    print(
        f"  {OUTPUT / 'summary.json'}"
    )

    print("\n" + "=" * 72)
    print(
        "IMPORTANT: PRE-REGISTERED OOS TEST"
    )
    print(
        "NO PARAMETER TUNING"
    )
    print("=" * 72)


if __name__ == "__main__":
    main()