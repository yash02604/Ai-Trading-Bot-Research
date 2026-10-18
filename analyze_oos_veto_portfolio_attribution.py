import pandas as pd
from collections import Counter

from app.historical_validation import (
    load_historical_data,
    prepare_symbol_frames,
    _make_events,
    _simulate_exit,
)

from app.risk_engine import (
    RiskConfig,
    RiskState,
    check_trade_allowed,
    calculate_position_size,
)

from app.cost_model import (
    CostAssumptions,
    estimate_round_trip_costs,
)


# =========================================================
# Configuration
# =========================================================

SOURCE = "scripts/data/research/extended_historical_robustness_oos_candles.csv"

OOS_START = pd.Timestamp(
    "2025-12-02 00:00:00",
    tz="Asia/Kolkata",
)

OOS_END = pd.Timestamp(
    "2026-10-01 23:59:59",
    tz="Asia/Kolkata",
)

VETO_FILE = "backtests/oos_market_context/vetoed_signals.csv"

INITIAL_CAPITAL = 100000.0

STOP_PCT = 1.0
HORIZON = 6

COST_ASSUMPTIONS = CostAssumptions(
    slippage_bps_per_side=5.0,
)


# =========================================================
# Load OOS data and frozen events
# =========================================================

print("=== OOS VETO PORTFOLIO ATTRIBUTION ===")

data, metadata = load_historical_data(SOURCE)

print(f"Loaded rows: {len(data)}")

frames = prepare_symbol_frames(data)

events, signal_counts, data_rejects = _make_events(frames)

oos_events = [
    event
    for event in events
    if OOS_START <= event["signal_timestamp"] <= OOS_END
]

print(f"Frozen OOS events: {len(oos_events)}")


# =========================================================
# Load Experiment B veto list
# =========================================================

vetoed = pd.read_csv(VETO_FILE)

vetoed["signal_timestamp"] = pd.to_datetime(
    vetoed["signal_timestamp"],
    utc=True,
).dt.tz_convert("Asia/Kolkata")

veto_keys = {
    (
        row["symbol"],
        row["signal_timestamp"],
    )
    for _, row in vetoed.iterrows()
}


# =========================================================
# Exact Baseline-A-style portfolio replay
# =========================================================

risk_config = RiskConfig(
    capital=INITIAL_CAPITAL,
)

capital = INITIAL_CAPITAL

last_exit = None

daily_trades = Counter()
daily_losses = Counter()

accepted_trades = []
all_event_records = []

reject_reasons = Counter()


for event in oos_events:

    symbol = event["symbol"]
    entry_time = event["entry_timestamp"]
    day = entry_time.date()

    is_vetoed = (
        event["symbol"],
        event["signal_timestamp"],
    ) in veto_keys

    record = {
        "symbol": symbol,
        "signal_timestamp": event["signal_timestamp"],
        "entry_timestamp": entry_time,
        "context_veto": is_vetoed,
        "baseline_status": None,
        "baseline_rejection_reason": None,
        "baseline_net_pnl": None,
        "baseline_exit_reason": None,
    }

    # -----------------------------------------------------
    # Shared one-position constraint
    # -----------------------------------------------------

    if last_exit is not None and entry_time <= last_exit:

        record["baseline_status"] = "risk_rejected"
        record["baseline_rejection_reason"] = (
            "maximum_open_positions"
        )

        reject_reasons["maximum_open_positions"] += 1

        all_event_records.append(record)
        continue

    # -----------------------------------------------------
    # Capital check
    # -----------------------------------------------------

    if capital <= 0:

        record["baseline_status"] = "risk_rejected"
        record["baseline_rejection_reason"] = (
            "insufficient_capital"
        )

        reject_reasons["insufficient_capital"] += 1

        all_event_records.append(record)
        continue

    # -----------------------------------------------------
    # Dynamic risk configuration
    # -----------------------------------------------------

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

        record["baseline_status"] = "risk_rejected"
        record["baseline_rejection_reason"] = reason

        reject_reasons[reason] += 1

        all_event_records.append(record)
        continue

    # -----------------------------------------------------
    # Position sizing
    # -----------------------------------------------------

    frame = frames[symbol]

    entry_price = float(
        frame.at[event["entry_index"], "open"]
    )

    stop_loss_price = (
        entry_price
        * (1.0 - STOP_PCT / 100.0)
    )

    size = calculate_position_size(
        active_config,
        entry_price,
        stop_loss_price,
    )

    if size.quantity <= 0:

        record["baseline_status"] = "risk_rejected"
        record["baseline_rejection_reason"] = (
            "insufficient_capital_for_one_share"
        )

        reject_reasons[
            "insufficient_capital_for_one_share"
        ] += 1

        all_event_records.append(record)
        continue

    # -----------------------------------------------------
    # Simulate trade
    # -----------------------------------------------------

    trade = _simulate_exit(
        frame,
        event,
        STOP_PCT,
        HORIZON,
    )

    if trade is None:

        record["baseline_status"] = "data_rejected"
        record["baseline_rejection_reason"] = (
            "no_valid_exit"
        )

        all_event_records.append(record)
        continue

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
        assumptions=COST_ASSUMPTIONS,
    )

    trade.update(cost_values)

    trade["pnl"] = trade["gross_pnl"]

    trade["net_pnl"] = (
        trade["gross_pnl"]
        - trade["total_cost"]
    )

    trade["context_veto"] = is_vetoed

    # -----------------------------------------------------
    # Accepted baseline trade
    # -----------------------------------------------------

    record["baseline_status"] = "accepted"

    record["baseline_net_pnl"] = trade["net_pnl"]

    record["baseline_exit_reason"] = trade[
        "exit_reason"
    ]

    record["baseline_trade_index"] = (
        len(accepted_trades)
    )

    accepted_trades.append(trade)

    all_event_records.append(record)

    # -----------------------------------------------------
    # Portfolio state update
    # -----------------------------------------------------

    capital += trade["net_pnl"]

    if trade["net_pnl"] < 0:
        daily_losses[day] += abs(
            trade["net_pnl"]
        )

    daily_trades[day] += 1

    last_exit = trade["exit_timestamp"]


# =========================================================
# Convert results
# =========================================================

events_df = pd.DataFrame(all_event_records)

trades_df = pd.DataFrame(accepted_trades)


# =========================================================
# Veto attribution
# =========================================================

veto_records = events_df[
    events_df["context_veto"] == True
].copy()

print("\n=== VETOED SIGNAL ATTRIBUTION ===")

print(
    veto_records[
        [
            "symbol",
            "signal_timestamp",
            "entry_timestamp",
            "baseline_status",
            "baseline_rejection_reason",
            "baseline_net_pnl",
            "baseline_exit_reason",
        ]
    ]
    .sort_values("signal_timestamp")
    .to_string(index=False)
)


# =========================================================
# Summary
# =========================================================

accepted_vetoes = veto_records[
    veto_records["baseline_status"] == "accepted"
]

rejected_vetoes = veto_records[
    veto_records["baseline_status"] == "risk_rejected"
]

accepted_veto_pnl = (
    accepted_vetoes["baseline_net_pnl"]
    .sum()
    if not accepted_vetoes.empty
    else 0.0
)

print("\n=== ATTRIBUTION SUMMARY ===")

print(
    f"Total frozen OOS signals: "
    f"{len(oos_events)}"
)

print(
    f"Total vetoed signals: "
    f"{len(veto_records)}"
)

print(
    f"Vetoed signals Baseline A would accept: "
    f"{len(accepted_vetoes)}"
)

print(
    f"Vetoed signals already rejected by Baseline A: "
    f"{len(rejected_vetoes)}"
)

print(
    f"Net P&L of accepted vetoed trades: "
    f"₹{accepted_veto_pnl:,.2f}"
)


# =========================================================
# Baseline replay summary
# =========================================================

if not trades_df.empty:

    baseline_net = trades_df["net_pnl"].sum()

    baseline_gross = trades_df["gross_pnl"].sum()

    baseline_costs = trades_df[
        "total_cost"
    ].sum()

else:

    baseline_net = 0.0
    baseline_gross = 0.0
    baseline_costs = 0.0


print("\n=== PORTFOLIO REPLAY ===")

print(
    f"Accepted trades: "
    f"{len(trades_df)}"
)

print(
    f"Gross P&L: "
    f"₹{baseline_gross:,.2f}"
)

print(
    f"Estimated costs: "
    f"₹{baseline_costs:,.2f}"
)

print(
    f"Net P&L: "
    f"₹{baseline_net:,.2f}"
)

print(
    f"Ending capital: "
    f"₹{capital:,.2f}"
)


print("\n=== REJECTION REASONS ===")

if reject_reasons:
    print(
        pd.Series(reject_reasons)
        .sort_values(ascending=False)
        .to_string()
    )
else:
    print("None")


# =========================================================
# Save attribution
# =========================================================

events_output = (
    "backtests/oos_market_context/"
    "veto_portfolio_attribution_events.csv"
)

veto_output = (
    "backtests/oos_market_context/"
    "veto_portfolio_attribution.csv"
)

events_df.to_csv(
    events_output,
    index=False,
)

veto_records.to_csv(
    veto_output,
    index=False,
)

print(
    f"\nSaved events: {events_output}"
)

print(
    f"Saved veto attribution: {veto_output}"
)