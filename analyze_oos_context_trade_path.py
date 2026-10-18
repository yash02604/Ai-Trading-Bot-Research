import pandas as pd


BASELINE_FILE = "backtests/oos_baseline/trades.csv"
CONTEXT_FILE = "backtests/oos_market_context/trades.csv"
VETO_FILE = "backtests/oos_market_context/vetoed_signals.csv"


KEY_COLUMNS = [
    "symbol",
    "signal_timestamp",
]


def load_trades(path):
    df = pd.read_csv(path)

    df["signal_timestamp"] = pd.to_datetime(
        df["signal_timestamp"],
        utc=True,
    ).dt.tz_convert("Asia/Kolkata")

    return df


def make_key(df):
    return list(
        zip(
            df["symbol"],
            df["signal_timestamp"],
        )
    )


print("=== OOS CONTEXT A → B TRADE-PATH RECONCILIATION ===")

# ---------------------------------------------------------
# Load
# ---------------------------------------------------------

baseline = load_trades(BASELINE_FILE)
context = load_trades(CONTEXT_FILE)
vetoed = load_trades(VETO_FILE)

print(f"Baseline A trades: {len(baseline)}")
print(f"Experiment B trades: {len(context)}")
print(f"Vetoed signals: {len(vetoed)}")


# ---------------------------------------------------------
# Keys
# ---------------------------------------------------------

baseline["trade_key"] = make_key(baseline)
context["trade_key"] = make_key(context)
vetoed["trade_key"] = make_key(vetoed)

baseline_keys = set(baseline["trade_key"])
context_keys = set(context["trade_key"])
veto_keys = set(vetoed["trade_key"])


# ---------------------------------------------------------
# 1. Baseline trades removed by Experiment B
# ---------------------------------------------------------

removed = baseline[
    ~baseline["trade_key"].isin(context_keys)
].copy()

removed["was_context_veto"] = (
    removed["trade_key"].isin(veto_keys)
)


# ---------------------------------------------------------
# 2. Experiment B trades added relative to Baseline A
# ---------------------------------------------------------

added = context[
    ~context["trade_key"].isin(baseline_keys)
].copy()


# ---------------------------------------------------------
# 3. Trades accepted by both
# ---------------------------------------------------------

common_keys = (
    baseline_keys
    .intersection(context_keys)
)

baseline_common = baseline[
    baseline["trade_key"].isin(common_keys)
].copy()

context_common = context[
    context["trade_key"].isin(common_keys)
].copy()


common_compare = baseline_common[
    [
        "trade_key",
        "symbol",
        "signal_timestamp",
        "net_pnl",
    ]
].rename(
    columns={
        "net_pnl": "baseline_net_pnl",
    }
)

context_common = context_common[
    [
        "trade_key",
        "net_pnl",
    ]
].rename(
    columns={
        "net_pnl": "context_net_pnl",
    }
)

common_compare = common_compare.merge(
    context_common,
    on="trade_key",
    how="left",
)

common_compare["net_pnl_delta"] = (
    common_compare["context_net_pnl"]
    - common_compare["baseline_net_pnl"]
)


# ---------------------------------------------------------
# 4. Vetoed trades actually removed
# ---------------------------------------------------------

removed_vetoed = removed[
    removed["was_context_veto"]
].copy()

removed_non_veto = removed[
    ~removed["was_context_veto"]
].copy()


# ---------------------------------------------------------
# Print removed trades
# ---------------------------------------------------------

print("\n=== TRADES REMOVED FROM A → B ===")

if removed.empty:
    print("None")
else:
    print(
        removed[
            [
                "symbol",
                "signal_timestamp",
                "net_pnl",
                "was_context_veto",
            ]
        ]
        .sort_values("signal_timestamp")
        .to_string(index=False)
    )


# ---------------------------------------------------------
# Print added trades
# ---------------------------------------------------------

print("\n=== TRADES ADDED IN B ===")

if added.empty:
    print("None")
else:
    print(
        added[
            [
                "symbol",
                "signal_timestamp",
                "net_pnl",
            ]
        ]
        .sort_values("signal_timestamp")
        .to_string(index=False)
    )


# ---------------------------------------------------------
# Print common trade changes
# ---------------------------------------------------------

print("\n=== COMMON TRADES: CAPITAL-PATH EFFECT ===")

if common_compare.empty:
    print("None")
else:
    changed = common_compare[
        common_compare["net_pnl_delta"].abs() > 0.000001
    ].copy()

    print(
        f"Common trades: {len(common_compare)}"
    )

    print(
        f"Common trades with different P&L: "
        f"{len(changed)}"
    )

    print(
        f"Common-trade P&L delta: "
        f"₹{common_compare['net_pnl_delta'].sum():,.2f}"
    )

    if not changed.empty:
        print(
            changed[
                [
                    "symbol",
                    "signal_timestamp",
                    "baseline_net_pnl",
                    "context_net_pnl",
                    "net_pnl_delta",
                ]
            ]
            .sort_values("signal_timestamp")
            .to_string(index=False)
        )


# ---------------------------------------------------------
# Exact reconciliation
# ---------------------------------------------------------

baseline_total = baseline["net_pnl"].sum()
context_total = context["net_pnl"].sum()

removed_total = removed["net_pnl"].sum()
added_total = added["net_pnl"].sum()
common_delta = common_compare["net_pnl_delta"].sum()


# The exact relationship is:
#
# B - A
# = added trades
# - removed trades
# + common-trade P&L changes


calculated_delta = (
    added_total
    - removed_total
    + common_delta
)

actual_delta = context_total - baseline_total


# ---------------------------------------------------------
# Detailed veto effect
# ---------------------------------------------------------

veto_removed_pnl = removed_vetoed["net_pnl"].sum()

non_veto_removed_pnl = removed_non_veto["net_pnl"].sum()


print("\n=== VETO EFFECT ===")

print(
    f"Vetoed trades removed from A: "
    f"{len(removed_vetoed)}"
)

print(
    f"Net P&L of those vetoed trades: "
    f"₹{veto_removed_pnl:,.2f}"
)

print(
    f"Non-veto trades removed from A: "
    f"{len(removed_non_veto)}"
)

print(
    f"Net P&L of non-veto removed trades: "
    f"₹{non_veto_removed_pnl:,.2f}"
)


# ---------------------------------------------------------
# Final reconciliation
# ---------------------------------------------------------

print("\n=== EXACT A → B RECONCILIATION ===")

print(
    f"Baseline A net: "
    f"₹{baseline_total:,.2f}"
)

print(
    f"Experiment B net: "
    f"₹{context_total:,.2f}"
)

print(
    f"Actual B - A: "
    f"₹{actual_delta:,.2f}"
)

print(
    f"Added-trade contribution: "
    f"₹{added_total:,.2f}"
)

print(
    f"Removed-trade contribution: "
    f"₹{-removed_total:,.2f}"
)

print(
    f"Common-trade capital-path contribution: "
    f"₹{common_delta:,.2f}"
)

print(
    f"Calculated B - A: "
    f"₹{calculated_delta:,.2f}"
)

difference = calculated_delta - actual_delta

print(
    f"Reconciliation difference: "
    f"₹{difference:,.6f}"
)


if abs(difference) < 0.01:
    print(
        "\nRESULT: RECONCILIATION PASSED"
    )
else:
    print(
        "\nRESULT: RECONCILIATION FAILED"
    )


# ---------------------------------------------------------
# Save files
# ---------------------------------------------------------

removed.to_csv(
    "backtests/oos_market_context/"
    "a_to_b_removed_trades.csv",
    index=False,
)

added.to_csv(
    "backtests/oos_market_context/"
    "a_to_b_added_trades.csv",
    index=False,
)

common_compare.to_csv(
    "backtests/oos_market_context/"
    "a_to_b_common_trade_comparison.csv",
    index=False,
)

print(
    "\nSaved:"
)

print(
    "backtests/oos_market_context/"
    "a_to_b_removed_trades.csv"
)

print(
    "backtests/oos_market_context/"
    "a_to_b_added_trades.csv"
)

print(
    "backtests/oos_market_context/"
    "a_to_b_common_trade_comparison.csv"
)