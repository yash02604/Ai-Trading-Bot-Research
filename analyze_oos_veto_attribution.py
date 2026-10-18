import pandas as pd

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

OOS_START = pd.Timestamp("2025-12-02 00:00:00", tz="Asia/Kolkata")
OOS_END = pd.Timestamp("2026-10-01 23:59:59", tz="Asia/Kolkata")

VETO_FILE = "backtests/oos_market_context/vetoed_signals.csv"

STOP_PCT = 1.0
HORIZON = 6

INITIAL_CAPITAL = 100000.0

RISK_CONFIG = RiskConfig(
    capital=INITIAL_CAPITAL,
    max_risk_per_trade=0.01,
    max_daily_loss=0.02,
    max_positions=1,
    max_trades_per_day=3,
)

COST_ASSUMPTIONS = CostAssumptions(
    slippage_bps_per_side=5.0,
)


# =========================================================
# Load frozen OOS events
# =========================================================

print("=== OOS VETO ATTRIBUTION ===")

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


selected_events = [
    event
    for event in oos_events
    if (
        event["symbol"],
        event["signal_timestamp"],
    ) in veto_keys
]

print(f"Vetoed signals: {len(selected_events)}")


# =========================================================
# Counterfactual trade simulation
# =========================================================

trades = []

for event in selected_events:

    symbol = event["symbol"]
    frame = frames[symbol]

    result = _simulate_exit(
        frame,
        event,
        STOP_PCT,
        HORIZON,
    )

    if result is None:
        continue

    entry_price = float(result["entry_price"])
    exit_price = float(result["exit_price"])

    stop_loss_price = entry_price * (1.0 - STOP_PCT / 100.0)

    position_size = calculate_position_size(
        RISK_CONFIG,
        entry_price,
        stop_loss_price,
    )

    quantity = position_size.quantity

    if quantity <= 0:
        continue

    gross_pnl = (
        exit_price - entry_price
    ) * quantity

    costs = estimate_round_trip_costs(
    entry_price,
    exit_price,
    quantity,
    result["entry_timestamp"],
    assumptions=COST_ASSUMPTIONS,
)

    net_pnl = gross_pnl - costs["total_cost"]

    result = dict(result)

    result.update(
        {
            "quantity": quantity,
            "gross_pnl": gross_pnl,
            **costs,
            "net_pnl": net_pnl,
        }
    )

    trades.append(result)


# =========================================================
# Results
# =========================================================

df = pd.DataFrame(trades)

if df.empty:
    print("\nNo counterfactual trades produced.")
    raise SystemExit(0)


print("\n=== COUNTERFACTUAL RESULTS ===")

print(f"Signals vetoed: {len(selected_events)}")
print(f"Counterfactual trades: {len(df)}")

print(
    f"Gross P&L: ₹{df['gross_pnl'].sum():,.2f}"
)

print(
    f"Estimated costs: ₹{df['total_cost'].sum():,.2f}"
)

print(
    f"Net P&L: ₹{df['net_pnl'].sum():,.2f}"
)

print(
    f"Winners: {(df['net_pnl'] > 0).sum()}"
)

print(
    f"Losers: {(df['net_pnl'] <= 0).sum()}"
)

print(
    f"Average MFE: {df['mfe_pct'].mean():.4f}%"
)

print(
    f"Average MAE: {df['mae_pct'].mean():.4f}%"
)

print("\n=== EXIT REASONS ===")
print(df["exit_reason"].value_counts().to_string())


print("\n=== VETOED TRADE DETAIL ===")

columns = [
    "symbol",
    "signal_timestamp",
    "entry_timestamp",
    "entry_price",
    "exit_price",
    "exit_reason",
    "quantity",
    "gross_pnl",
    "total_cost",
    "net_pnl",
    "mfe_pct",
    "mae_pct",
]

print(
    df[columns]
    .sort_values("signal_timestamp")
    .to_string(index=False)
)


# =========================================================
# Save
# =========================================================

output = "backtests/oos_market_context/veto_counterfactual_trades.csv"

df.to_csv(
    output,
    index=False,
)

print(f"\nSaved: {output}")