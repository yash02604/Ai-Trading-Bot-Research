from pathlib import Path
from collections import Counter

import pandas as pd

from app.cost_model import CostAssumptions, estimate_round_trip_costs
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

OUTPUT = Path("backtests/oos_baseline")


def simulate_oos_book(
    frames,
    initial_capital=100_000.0,
    stop_pct=1.0,
    horizon=6,
    costs=None,
):
    costs = costs or CostAssumptions()

    # Generate events from the FULL warm-up dataset.
    all_events, raw_signals, data_rejects = _make_events(frames)

    # OOS filter is based on SIGNAL timestamp, not entry timestamp.
    events = [
        event
        for event in all_events
        if OOS_START <= event["signal_timestamp"] <= OOS_END
    ]

    symbol_counts = {
        symbol: Counter(frozen_signals=raw_signals.get(symbol, 0))
        for symbol in frames
    }

    # The OOS signal count should represent only the OOS period.
    for symbol in symbol_counts:
        symbol_counts[symbol]["frozen_signals"] = sum(
            1
            for event in events
            if event["symbol"] == symbol
        )

    book_trades = []
    reject_reasons = Counter()

    risk_config = RiskConfig(capital=initial_capital)
    capital = initial_capital
    last_exit = None

    daily_trades = Counter()
    daily_losses = Counter()

    max_exposure = 0.0

    for event in events:
        symbol = event["symbol"]

        entry_time = event["entry_timestamp"]

        if last_exit is not None and entry_time <= last_exit:
            reject_reasons["maximum_open_positions"] += 1
            symbol_counts[symbol]["risk_rejected"] += 1
            continue

        day = entry_time.date()

        if capital <= 0:
            reject_reasons["insufficient_capital"] += 1
            symbol_counts[symbol]["risk_rejected"] += 1
            continue

        active_config = RiskConfig(
            capital=capital,
            max_risk_per_trade=risk_config.max_risk_per_trade,
            max_daily_loss=risk_config.max_daily_loss,
            max_positions=risk_config.max_positions,
            max_trades_per_day=risk_config.max_trades_per_day,
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
            symbol_counts[symbol]["risk_rejected"] += 1
            continue

        frame = frames[symbol]

        entry = float(
            frame.at[event["entry_index"], "open"]
        )

        stop = entry * (
            1.0 - stop_pct / 100.0
        )

        size = calculate_position_size(
            active_config,
            entry,
            stop,
        )

        if size.quantity <= 0:
            reject_reasons[
                "insufficient_capital_for_one_share"
            ] += 1
            symbol_counts[symbol]["risk_rejected"] += 1
            continue

        trade = _simulate_exit(
            frame,
            event,
            stop_pct,
            horizon,
        )

        trade["quantity"] = size.quantity
        trade["planned_risk"] = size.planned_risk
        trade["capital_exposure"] = size.capital_required

        trade["gross_pnl"] = (
            trade["exit_price"]
            - trade["entry_price"]
        ) * size.quantity

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
            trade["net_pnl"] / size.planned_risk
            if size.planned_risk
            else 0.0
        )

        trade["book"] = "oos_baseline"
        trade["ai_replay"] = "unavailable"

        book_trades.append(trade)

        symbol_counts[symbol]["risk_accepted"] += 1
        symbol_counts[symbol]["quantitative_buy_entries"] += 1

        max_exposure = max(
            max_exposure,
            size.capital_required,
        )

        capital += trade["net_pnl"]

        if trade["net_pnl"] < 0:
            daily_losses[day] += abs(
                trade["net_pnl"]
            )

        daily_trades[day] += 1

        last_exit = trade["exit_timestamp"]

    for symbol in frames:
        symbol_counts[symbol]["data_rejected"] = sum(
            count
            for (s, _), count in data_rejects.items()
            if s == symbol
        )

        symbol_counts[symbol]["sell_signals"] = 0

    trading_days = len(
        set(
            event["entry_timestamp"].date()
            for event in events
        )
    )

    counts = {
        "frozen_reversal_signals": len(events),
        "risk_accepted_entries": len(book_trades),
        "risk_rejected_signals": int(
            sum(reject_reasons.values())
        ),
        "data_rejected_signals": int(
            sum(
                count
                for (symbol, reason), count in data_rejects.items()
                if any(
                    event["symbol"] == symbol
                    for event in events
                )
            )
        ),
        "ai_confirmed_buy_signals": None,
        "ai_rejected_signals": None,
        "sell_signals": 0,
        "hold_signals": None,
        "ai_replay": "unavailable",
        "risk_rejection_reasons": dict(
            reject_reasons
        ),
        "data_rejection_reasons": {
            f"{symbol}:{reason}": int(value)
            for (symbol, reason), value
            in data_rejects.items()
        },
        "per_symbol": {
            symbol: dict(count)
            for symbol, count
            in symbol_counts.items()
        },
    }

    metrics = calculate_metrics(
        book_trades,
        initial_capital,
        trading_days,
        counts,
        "oos_baseline",
    )

    metrics["maximum_capital_exposure"] = max_exposure

    metrics["maximum_capital_exposure_pct"] = (
        max_exposure / initial_capital * 100
        if initial_capital
        else 0.0
    )

    return book_trades, metrics, counts


def main():
    print("=== OOS BASELINE A ===")
    print(f"Warm-up data: {SOURCE}")
    print(
        f"OOS period: {OOS_START} -> {OOS_END}"
    )
    print("Strategy: FROZEN")
    print("Stop: 1.0%")
    print("Horizon: 6 candles")
    print("Risk/trade: 1%")
    print("Daily loss limit: 2%")
    print("Max open positions: 1")
    print("Max trades/day: 3")
    print("AI replay: unavailable")
    print()

    data, audit = load_historical_data(SOURCE)

    print(f"Loaded rows: {len(data)}")
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
    print()

    missing = sorted(
        set(STOCKS) - set(data.stock.unique())
    )

    if missing:
        raise RuntimeError(
            f"Missing expected symbols: {missing}"
        )

    # IMPORTANT:
    # Keep the complete dataset here so indicators
    # receive November warm-up candles.
    frames = prepare_symbol_frames(data)

    missing_frames = sorted(
        set(STOCKS) - set(frames)
    )

    if missing_frames:
        raise RuntimeError(
            f"Missing prepared frames: "
            f"{missing_frames}"
        )

    costs = CostAssumptions(
        slippage_bps_per_side=5.0
    )

    trades, metrics, counts = simulate_oos_book(
        frames,
        initial_capital=100_000.0,
        stop_pct=1.0,
        horizon=6,
        costs=costs,
    )

    OUTPUT.mkdir(
        parents=True,
        exist_ok=True,
    )

    trades_path = OUTPUT / "trades.csv"
    metrics_path = OUTPUT / "metrics.csv"

    pd.DataFrame(trades).to_csv(
        trades_path,
        index=False,
    )

    pd.DataFrame(
        [metrics]
    ).to_csv(
        metrics_path,
        index=False,
    )

    print()
    print("=== RESULTS ===")
    print(
        f"Frozen OOS signals: "
        f"{counts['frozen_reversal_signals']}"
    )
    print(
        f"Accepted entries: "
        f"{counts['risk_accepted_entries']}"
    )
    print(
        f"Risk rejected: "
        f"{counts['risk_rejected_signals']}"
    )
    print(
        f"Net P&L: "
        f"₹{metrics['net_pnl']:.2f}"
    )
    print(
        f"Net return: "
        f"{metrics['net_return_pct']:.2f}%"
    )
    print(
        f"Win rate: "
        f"{metrics['win_rate'] * 100:.2f}%"
    )
    print(
        f"Profit factor: "
        f"{metrics['profit_factor']}"
    )
    print(
        f"Total estimated costs: "
        f"₹{metrics['total_estimated_costs']:.2f}"
    )
    print()
    print(f"Trades: {trades_path}")
    print(f"Metrics: {metrics_path}")


if __name__ == "__main__":
    main()