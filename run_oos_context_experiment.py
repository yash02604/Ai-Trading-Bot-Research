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

CONTEXT_DIR = Path(
    "data/research/market_context"
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
    "backtests/oos_market_context"
)

CONTEXT_FILES = {
    "nifty50": CONTEXT_DIR / "nifty50_5min.csv",
    "nifty_bank": CONTEXT_DIR / "nifty_bank_5min.csv",
    "nifty_it": CONTEXT_DIR / "nifty_it_5min.csv",
    "sensex": CONTEXT_DIR / "sensex_5min.csv",
}


def load_context(path):
    df = pd.read_csv(path)

    df["timestamp"] = (
        pd.to_datetime(
            df["timestamp"],
            utc=True,
        )
        .dt.tz_convert("Asia/Kolkata")
    )

    return (
        df[
            [
                "timestamp",
                "open",
                "high",
                "low",
                "close",
            ]
        ]
        .drop_duplicates("timestamp")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )


def build_context_lookup(context):
    """
    For every signal timestamp, find the latest completed
    index candle at or before that timestamp.

    This deliberately uses signal_timestamp rather than
    entry_timestamp.
    """

    rows = []

    for name, frame in context.items():

        temp = frame[
            [
                "timestamp",
                "open",
                "close",
            ]
        ].copy()

        temp["direction"] = temp.apply(
            lambda row:
                "up"
                if row["close"] > row["open"]
                else (
                    "down"
                    if row["close"] < row["open"]
                    else "flat"
                ),
            axis=1,
        )

        temp = temp[
            [
                "timestamp",
                "direction",
            ]
        ].rename(
            columns={
                "direction": f"{name}_direction"
            }
        )

        rows.append(temp)

    return rows


def classify_events(events, context):
    """
    Attach completed signal-candle index context.

    A candidate is vetoed when >=3 of the 4 equity
    indices are down on the latest completed candle
    at or before the signal timestamp.
    """

    result = []

    lookup_frames = build_context_lookup(context)

    events_df = pd.DataFrame(events)

    if events_df.empty:
        return events, []

    events_df["signal_timestamp"] = pd.to_datetime(
        events_df["signal_timestamp"],
        utc=True,
    ).dt.tz_convert("Asia/Kolkata")

    for lookup in lookup_frames:

        events_df = pd.merge_asof(
            events_df.sort_values(
                "signal_timestamp"
            ),
            lookup.sort_values(
                "timestamp"
            ),
            left_on="signal_timestamp",
            right_on="timestamp",
            direction="backward",
            allow_exact_matches=True,
        )

        events_df = events_df.drop(
            columns=["timestamp"]
        )

    direction_columns = [
        "nifty50_direction",
        "nifty_bank_direction",
        "nifty_it_direction",
        "sensex_direction",
    ]

    events_df["index_down_count"] = (
        events_df[direction_columns] == "down"
    ).sum(axis=1)

    events_df["market_context_veto"] = (
        events_df["index_down_count"] >= 3
    )

    events_df["index_agreement"] = "mixed"

    events_df.loc[
        events_df["index_down_count"] >= 3,
        "index_agreement",
    ] = "bearish_alignment"

    events_df.loc[
        (
            (
                events_df[
                    [
                        "nifty50_direction",
                        "nifty_bank_direction",
                        "nifty_it_direction",
                        "sensex_direction",
                    ]
                ]
                == "up"
            )
            .sum(axis=1)
            >= 3
        ),
        "index_agreement",
    ] = "bullish_alignment"

    for _, row in events_df.iterrows():

        event = {
            column: row[column]
            for column in events_df.columns
            if column not in direction_columns
        }

        for column in direction_columns:
            event[column] = row[column]

        event["index_down_count"] = int(
            row["index_down_count"]
        )

        event["market_context_veto"] = bool(
            row["market_context_veto"]
        )

        result.append(event)

    return result, direction_columns


def simulate_context_book(
    frames,
    events,
    initial_capital=100_000.0,
    stop_pct=1.0,
    horizon=6,
    costs=None,
):
    costs = costs or CostAssumptions()

    symbol_counts = {
        symbol: Counter()
        for symbol in frames
    }

    for event in events:
        symbol_counts[
            event["symbol"]
        ]["frozen_signals"] += 1

    accepted_events = []

    vetoed_events = []

    for event in events:

        symbol = event["symbol"]

        if event["market_context_veto"]:

            vetoed_events.append(event)

            symbol_counts[
                symbol
            ]["market_context_veto"] += 1

            continue

        accepted_events.append(event)

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

    for event in accepted_events:

        symbol = event["symbol"]

        entry_time = event["entry_timestamp"]

        if (
            last_exit is not None
            and entry_time <= last_exit
        ):
            reject_reasons[
                "maximum_open_positions"
            ] += 1

            symbol_counts[
                symbol
            ]["risk_rejected"] += 1

            continue

        day = entry_time.date()

        if capital <= 0:

            reject_reasons[
                "insufficient_capital"
            ] += 1

            symbol_counts[
                symbol
            ]["risk_rejected"] += 1

            continue

        active_config = RiskConfig(
            capital=capital,
            max_risk_per_trade=(
                risk_config.max_risk_per_trade
            ),
            max_daily_loss=(
                risk_config.max_daily_loss
            ),
            max_positions=(
                risk_config.max_positions
            ),
            max_trades_per_day=(
                risk_config.max_trades_per_day
            ),
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

            symbol_counts[
                symbol
            ]["risk_rejected"] += 1

            continue

        frame = frames[symbol]

        entry = float(
            frame.at[
                event["entry_index"],
                "open",
            ]
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

            symbol_counts[
                symbol
            ]["risk_rejected"] += 1

            continue

        trade = _simulate_exit(
            frame,
            event,
            stop_pct,
            horizon,
        )

        trade["quantity"] = size.quantity
        trade["planned_risk"] = size.planned_risk
        trade["capital_exposure"] = (
            size.capital_required
        )

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
            trade["net_pnl"]
            / size.planned_risk
            if size.planned_risk
            else 0.0
        )

        trade["book"] = (
            "oos_market_context"
        )

        trade["ai_replay"] = "unavailable"

        trade["index_down_count"] = (
            event["index_down_count"]
        )

        trade["index_agreement"] = (
            event["index_agreement"]
        )

        trade["market_context_veto"] = False

        book_trades.append(trade)

        symbol_counts[
            symbol
        ]["risk_accepted"] += 1

        symbol_counts[
            symbol
        ]["quantitative_buy_entries"] += 1

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

    trading_days = len(
        set(
            event["entry_timestamp"].date()
            for event in accepted_events
        )
    )

    counts = {
        "frozen_oos_signals": len(events),
        "market_context_vetoes": len(
            vetoed_events
        ),
        "signals_after_context_filter": len(
            accepted_events
        ),
        "risk_accepted_entries": len(
            book_trades
        ),
        "risk_rejected_signals": int(
            sum(reject_reasons.values())
        ),
        "risk_rejection_reasons": dict(
            reject_reasons
        ),
        "ai_replay": "unavailable",
    }

    metrics = calculate_metrics(
        book_trades,
        initial_capital,
        trading_days,
        counts,
        "oos_market_context",
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
        vetoed_events,
        metrics,
        counts,
    )


def main():

    print(
        "=== OOS MARKET CONTEXT EXPERIMENT B ==="
    )

    print(
        f"OOS period: "
        f"{OOS_START} -> {OOS_END}"
    )

    print(
        "Veto rule: >=3/4 equity indices "
        "DOWN on latest completed SIGNAL candle"
    )

    print(
        "Indices: NIFTY 50, NIFTY Bank, "
        "NIFTY IT, SENSEX"
    )

    print()

    data, audit = load_historical_data(
        SOURCE
    )

    print(
        f"Loaded rows: {len(data)}"
    )

    frames = prepare_symbol_frames(
        data
    )

    all_events, raw_signals, data_rejects = (
        _make_events(frames)
    )

    oos_events = [
        event
        for event in all_events
        if (
            OOS_START
            <= event["signal_timestamp"]
            <= OOS_END
        )
    ]

    print(
        f"Frozen OOS events: "
        f"{len(oos_events)}"
    )

    context = {}

    for name, path in CONTEXT_FILES.items():

        if not path.exists():
            raise FileNotFoundError(path)

        context[name] = load_context(
            path
        )

        print(
            f"Loaded {name}: "
            f"{len(context[name]):,} candles"
        )

    enriched_events, direction_columns = (
        classify_events(
            oos_events,
            context,
        )
    )

    missing_context = 0

    for event in enriched_events:

        if any(
            pd.isna(event[column])
            for column in direction_columns
        ):
            missing_context += 1

    if missing_context:
        raise RuntimeError(
            f"{missing_context} OOS events "
            "are missing one or more index "
            "context candles."
        )

    trades, vetoed, metrics, counts = (
        simulate_context_book(
            frames,
            enriched_events,
            initial_capital=100_000.0,
            stop_pct=1.0,
            horizon=6,
            costs=CostAssumptions(
                slippage_bps_per_side=5.0
            ),
        )
    )

    OUTPUT.mkdir(
        parents=True,
        exist_ok=True,
    )

    pd.DataFrame(trades).to_csv(
        OUTPUT / "trades.csv",
        index=False,
    )

    pd.DataFrame(vetoed).to_csv(
        OUTPUT / "vetoed_signals.csv",
        index=False,
    )

    pd.DataFrame(
        [metrics]
    ).to_csv(
        OUTPUT / "metrics.csv",
        index=False,
    )

    print()
    print("=== RESULTS ===")

    print(
        f"Frozen OOS signals: "
        f"{counts['frozen_oos_signals']}"
    )

    print(
        f"Market-context vetoes: "
        f"{counts['market_context_vetoes']}"
    )

    print(
        f"Signals after context filter: "
        f"{counts['signals_after_context_filter']}"
    )

    print(
        f"Accepted trades: "
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
        f"Estimated costs: "
        f"₹{metrics['total_estimated_costs']:.2f}"
    )

    print()
    print(
        f"Trades: "
        f"{OUTPUT / 'trades.csv'}"
    )

    print(
        f"Vetoed signals: "
        f"{OUTPUT / 'vetoed_signals.csv'}"
    )


if __name__ == "__main__":
    main()
