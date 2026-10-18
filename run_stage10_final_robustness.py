from pathlib import Path
import math

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parent
BACKTESTS = ROOT / "backtests"
STAGE9_DIR = BACKTESTS / "stage9_candidate"
STAGE10A_DIAGNOSTICS = (
    BACKTESTS
    / "stage10a_entry_regime"
    / "signal_diagnostics.csv"
)
OUTPUT_DIR = BACKTESTS / "stage10_final_robustness"
EXPECTED_TRADES = 19
EXPECTED_SIGNALS = 37
EXPECTED_NET_PNL = -3362.29
BOOTSTRAP_SEED = 20261007
BOOTSTRAP_ITERATIONS = 100_000


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def parse_timestamps(frame, column):
    timestamps = pd.to_datetime(
        frame[column],
        errors="coerce",
        utc=True,
    )
    require(
        timestamps.notna().all(),
        f"Invalid or missing timestamps in {column}.",
    )
    return timestamps.dt.tz_convert("Asia/Kolkata")


def normalized_keys(frame, timestamp_column):
    symbols = (
        frame["symbol"]
        .astype(str)
        .str.strip()
        .str.upper()
    )
    timestamps = parse_timestamps(frame, timestamp_column)
    require(
        symbols.ne("").all(),
        "Blank symbols found while creating normalized keys.",
    )
    keys = symbols + "|" + timestamps.map(
        lambda value: value.isoformat()
    )
    return symbols, timestamps, keys


def count_value(counts, name):
    return int(float(counts.loc[name]))


def pnl_stats(frame):
    net = frame["net_pnl"].astype(float)
    gross = frame["gross_pnl"].astype(float)
    costs = frame["total_cost"].astype(float)
    winners = net[net > 0]
    losers = net[net < 0]
    positive_gross = gross[gross > 0].sum()
    negative_gross = abs(gross[gross < 0].sum())
    positive_net = winners.sum()
    negative_net = abs(losers.sum())

    return {
        "trades": int(len(frame)),
        "wins": int((net > 0).sum()),
        "losses": int((net < 0).sum()),
        "win_rate_pct": float((net > 0).mean() * 100),
        "gross_pnl": float(gross.sum()),
        "costs": float(costs.sum()),
        "net_pnl": float(net.sum()),
        "average_trade": float(net.mean()),
        "median_trade": float(net.median()),
        "profit_factor": (
            float(positive_net / negative_net)
            if negative_net
            else math.inf
        ),
        "gross_profit_factor": (
            float(positive_gross / negative_gross)
            if negative_gross
            else math.inf
        ),
        "average_winner": float(winners.mean()) if len(winners) else np.nan,
        "average_loser": float(losers.mean()) if len(losers) else np.nan,
        "median_winner": float(winners.median()) if len(winners) else np.nan,
        "median_loser": float(losers.median()) if len(losers) else np.nan,
    }


def maximum_loss_streak(values):
    longest = 0
    current = 0
    for value in values:
        if value < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def validate_inputs():
    trades = pd.read_csv(STAGE9_DIR / "trades.csv")
    metrics_frame = pd.read_csv(STAGE9_DIR / "metrics.csv")
    counts_frame = pd.read_csv(STAGE9_DIR / "counts.csv")
    vetoed = pd.read_csv(STAGE9_DIR / "vetoed_signals.csv")
    diagnostics = pd.read_csv(STAGE10A_DIAGNOSTICS)

    require(len(metrics_frame) == 1, "Expected exactly one Stage 9 metrics row.")
    metrics = metrics_frame.iloc[0]
    counts = counts_frame.set_index("metric")["value"]
    counts.index = counts.index.astype(str)

    required_trade_columns = {
        "symbol",
        "signal_timestamp",
        "entry_timestamp",
        "exit_timestamp",
        "gross_pnl",
        "net_pnl",
        "total_cost",
        "slippage",
        "exit_reason",
        "mfe_pct",
        "mae_pct",
        "r_multiple",
        "holding_minutes",
        "index_down_count",
    }
    missing = sorted(required_trade_columns - set(trades.columns))
    require(not missing, f"Stage 9 trades are missing columns: {missing}")

    trade_symbols, trade_times, trade_keys = normalized_keys(
        trades,
        "signal_timestamp",
    )
    diagnostic_symbols, diagnostic_times, diagnostic_keys = normalized_keys(
        diagnostics,
        "signal_timestamp",
    )
    veto_symbols, _, veto_keys = normalized_keys(
        vetoed,
        "signal_timestamp",
    )

    require(len(trades) == EXPECTED_TRADES, f"Expected 19 Stage 9 trades; found {len(trades)}.")
    require(len(diagnostics) == EXPECTED_SIGNALS, f"Expected 37 diagnostic signals; found {len(diagnostics)}.")
    require(int(metrics["total_trades"]) == EXPECTED_TRADES, "Stage 9 metrics do not report 19 trades.")
    require(count_value(counts, "frozen_reversal_signals") == EXPECTED_SIGNALS, "Stage 9 counts do not report 37 frozen signals.")
    require(count_value(counts, "context_vetoed_signals") == len(vetoed) == 11, "Stage 9 veto count is inconsistent.")
    require(count_value(counts, "context_passed_signals") == 26, "Stage 9 context-passed count is inconsistent.")
    require(count_value(counts, "risk_accepted_entries") == EXPECTED_TRADES, "Stage 9 accepted count is inconsistent.")
    require(count_value(counts, "risk_rejected_signals") == 7, "Stage 9 risk-rejected count is inconsistent.")

    require(not trade_keys.duplicated().any(), "Duplicate normalized Stage 9 trade keys found.")
    require(not diagnostic_keys.duplicated().any(), "Duplicate normalized diagnostic keys found.")
    require(not veto_keys.duplicated().any(), "Duplicate normalized veto-signal keys found.")

    accepted = diagnostics["accepted_trade"].astype(bool)
    context_veto = diagnostics["context_veto"].astype(bool)
    down_count = pd.to_numeric(diagnostics["down_count"], errors="coerce")
    require(down_count.notna().all(), "Missing down-count context values.")
    require(down_count.between(0, 4).all(), "Context down-count must be between 0 and 4.")
    require(int(accepted.sum()) == EXPECTED_TRADES, "Stage 10A diagnostics do not mark 19 accepted trades.")
    require(int(context_veto.sum()) == 11, "Stage 10A context veto count is not 11.")
    require(
        (context_veto == (down_count >= 3)).all(),
        "Stage 10A veto flags do not match the frozen >=3-of-4 context rule.",
    )
    require(
        set(veto_keys) == set(diagnostic_keys[context_veto]),
        "Stage 9 vetoed-signal keys do not match Stage 10A veto events.",
    )
    require(
        set(trade_keys) == set(diagnostic_keys[accepted]),
        "Stage 9 trades do not match all and only accepted diagnostic keys.",
    )

    require(
        trade_times.is_monotonic_increasing,
        "Stage 9 trades are not ordered chronologically by signal timestamp.",
    )
    require(
        diagnostic_times.is_monotonic_increasing,
        "Stage 10A diagnostic events are not chronologically ordered.",
    )
    entry_times = parse_timestamps(trades, "entry_timestamp")
    exit_times = parse_timestamps(trades, "exit_timestamp")
    require((entry_times > trade_times).all(), "A trade entry is not after its signal timestamp.")
    require((exit_times >= entry_times).all(), "A trade exit precedes its entry timestamp.")

    trades = trades.copy()
    trades["symbol"] = trade_symbols
    trades["signal_timestamp"] = trade_times
    trades["entry_timestamp"] = entry_times
    trades["exit_timestamp"] = exit_times
    trades["trade_key"] = trade_keys
    trades["index_down_count"] = pd.to_numeric(
        trades["index_down_count"],
        errors="coerce",
    )
    trades = trades.sort_values(
        "signal_timestamp",
        kind="stable",
    ).reset_index(drop=True)

    diagnostics = diagnostics.copy()
    diagnostics["symbol"] = diagnostic_symbols
    diagnostics["signal_timestamp"] = diagnostic_times
    diagnostics["trade_key"] = diagnostic_keys
    diagnostics["accepted_trade"] = accepted
    diagnostics["context_veto"] = context_veto
    diagnostics["down_count"] = down_count.astype(int)

    accepted_diagnostics = diagnostics.loc[accepted].set_index("trade_key")
    trade_context = trades["trade_key"].map(accepted_diagnostics["down_count"])
    require(
        trade_context.notna().all()
        and (trade_context.to_numpy() == trades["index_down_count"].to_numpy()).all(),
        "Trade context counts do not match signal-time diagnostic context.",
    )

    diagnostic_outcomes = accepted_diagnostics["net_pnl"].astype(float).sort_index()
    trade_outcomes = trades.set_index("trade_key")["net_pnl"].astype(float).sort_index()
    require(
        np.allclose(diagnostic_outcomes, trade_outcomes, rtol=0, atol=0.01),
        "Stage 10A accepted-trade P&L does not match Stage 9 trades.",
    )

    total_net = float(trades["net_pnl"].sum())
    total_gross = float(trades["gross_pnl"].sum())
    total_costs = float(trades["total_cost"].sum())
    require(
        math.isclose(total_net, EXPECTED_NET_PNL, abs_tol=0.01),
        f"Stage 9 net P&L is {total_net:.2f}, not -3362.29.",
    )
    require(
        math.isclose(total_net, float(metrics["net_pnl"]), abs_tol=0.01),
        "Stage 9 trade P&L does not match Stage 9 metrics.",
    )
    require(
        math.isclose(total_gross, float(metrics["gross_pnl"]), abs_tol=0.01),
        "Stage 9 gross P&L does not match Stage 9 metrics.",
    )
    require(
        math.isclose(total_costs, float(metrics["total_estimated_costs"]), abs_tol=0.01),
        "Stage 9 trade costs do not match Stage 9 metrics.",
    )
    require(
        np.allclose(
            trades["gross_pnl"].astype(float) - trades["total_cost"].astype(float),
            trades["net_pnl"].astype(float),
            rtol=0,
            atol=0.01,
        ),
        "Trade-level gross P&L less costs does not reconcile to net P&L.",
    )

    diagnostics = diagnostics.sort_values(
        "signal_timestamp",
        kind="stable",
    ).reset_index(drop=True)
    return trades, diagnostics, metrics, counts


def build_equity_curve(trades, initial_capital):
    curve = trades.copy()
    curve["trade_number"] = np.arange(1, len(curve) + 1)
    curve["cumulative_gross_pnl"] = curve["gross_pnl"].cumsum()
    curve["cumulative_costs"] = curve["total_cost"].cumsum()
    curve["cumulative_net_pnl"] = curve["net_pnl"].cumsum()
    curve["equity"] = initial_capital + curve["cumulative_net_pnl"]
    curve["running_peak"] = np.maximum.accumulate(
        np.r_[initial_capital, curve["equity"].to_numpy()]
    )[1:]
    curve["drawdown"] = curve["equity"] - curve["running_peak"]
    curve["drawdown_pct"] = curve["drawdown"] / curve["running_peak"] * 100
    curve["consecutive_losses"] = [
        maximum_loss_streak(curve["net_pnl"].iloc[: index + 1])
        for index in range(len(curve))
    ]
    curve["recovered_prior_peak"] = curve["equity"] >= curve["running_peak"]
    return curve


def make_period_analysis(trades):
    ordered = trades.sort_values("signal_timestamp", kind="stable").reset_index(drop=True)
    split = (len(ordered) + 1) // 2
    periods = [
        ("First chronological half", ordered.iloc[:split]),
        ("Second chronological half", ordered.iloc[split:]),
    ]
    rows = []
    for label, period in periods:
        stats = pnl_stats(period)
        rows.append(
            {
                "period": label,
                "start_signal": period["signal_timestamp"].min().isoformat(),
                "end_signal": period["signal_timestamp"].max().isoformat(),
                "trades": stats["trades"],
                "wins": stats["wins"],
                "losses": stats["losses"],
                "win_rate_pct": stats["win_rate_pct"],
                "gross_pnl": stats["gross_pnl"],
                "costs": stats["costs"],
                "net_pnl": stats["net_pnl"],
                "average_trade": stats["average_trade"],
            }
        )
    return pd.DataFrame(rows)


def make_symbol_analysis(trades):
    rows = []
    for symbol, group in trades.groupby("symbol", sort=True):
        stats = pnl_stats(group)
        rows.append(
            {
                "analysis_type": "symbol",
                "symbol": symbol,
                "scenario": "",
                "excluded_symbols": "",
                "trades": stats["trades"],
                "wins": stats["wins"],
                "losses": stats["losses"],
                "win_rate_pct": stats["win_rate_pct"],
                "gross_pnl": stats["gross_pnl"],
                "costs": stats["costs"],
                "net_pnl": stats["net_pnl"],
                "average_trade": stats["average_trade"],
            }
        )

    by_symbol = pd.DataFrame(rows).sort_values(
        "net_pnl",
        ascending=False,
        kind="stable",
    )
    positive = by_symbol[by_symbol["net_pnl"] > 0]
    negative = by_symbol[by_symbol["net_pnl"] < 0]
    best = by_symbol.iloc[0]
    worst = by_symbol.iloc[-1]
    top_two_positive = positive.head(2)
    top_two_negative = negative.tail(2)
    scenarios = [
        ("Exclude best symbol", [best["symbol"]]),
        ("Exclude worst symbol", [worst["symbol"]]),
        ("Exclude top 2 winning symbols", top_two_positive["symbol"].tolist()),
        ("Exclude top 2 losing symbols", top_two_negative["symbol"].tolist()),
    ]
    total_net = float(trades["net_pnl"].sum())
    scenario_rows = []
    for name, excluded in scenarios:
        excluded_net = float(
            by_symbol.loc[by_symbol["symbol"].isin(excluded), "net_pnl"].sum()
        )
        scenario_rows.append(
            {
                "analysis_type": "exclusion_scenario",
                "symbol": "",
                "scenario": name,
                "excluded_symbols": ", ".join(excluded),
                "trades": np.nan,
                "wins": np.nan,
                "losses": np.nan,
                "win_rate_pct": np.nan,
                "gross_pnl": np.nan,
                "costs": np.nan,
                "net_pnl": total_net - excluded_net,
                "average_trade": np.nan,
            }
        )
    portfolio_row = {
        "analysis_type": "portfolio_total",
        "symbol": "",
        "scenario": "Total portfolio",
        "excluded_symbols": "",
        "trades": len(trades),
        "wins": int((trades["net_pnl"] > 0).sum()),
        "losses": int((trades["net_pnl"] < 0).sum()),
        "win_rate_pct": float((trades["net_pnl"] > 0).mean() * 100),
        "gross_pnl": float(trades["gross_pnl"].sum()),
        "costs": float(trades["total_cost"].sum()),
        "net_pnl": total_net,
        "average_trade": float(trades["net_pnl"].mean()),
    }
    return pd.concat(
        [
            pd.DataFrame([portfolio_row]),
            by_symbol,
            pd.DataFrame(scenario_rows),
        ],
        ignore_index=True,
    )


def make_trade_concentration(trades):
    parts = []
    absolute_total = float(trades["net_pnl"].abs().sum())
    portfolio_net = float(trades["net_pnl"].sum())
    for group_name, ascending in [("Best 5", False), ("Worst 5", True)]:
        group = trades.sort_values(
            "net_pnl",
            ascending=ascending,
            kind="stable",
        ).head(5).copy()
        group["group"] = group_name
        group["rank"] = np.arange(1, len(group) + 1)
        group["cumulative_group_net_pnl"] = group["net_pnl"].cumsum()
        group["share_of_portfolio_net_pnl_pct"] = (
            group["net_pnl"] / portfolio_net * 100
        )
        group["share_of_absolute_trade_pnl_pct"] = (
            group["net_pnl"].abs() / absolute_total * 100
        )
        parts.append(group)
    result = pd.concat(parts, ignore_index=True)
    result["signal_timestamp"] = result["signal_timestamp"].map(
        lambda value: value.isoformat()
    )
    result["entry_timestamp"] = result["entry_timestamp"].map(
        lambda value: value.isoformat()
    )
    result["exit_timestamp"] = result["exit_timestamp"].map(
        lambda value: value.isoformat()
    )
    return result[
        [
            "group",
            "rank",
            "symbol",
            "signal_timestamp",
            "net_pnl",
            "gross_pnl",
            "total_cost",
            "cumulative_group_net_pnl",
            "share_of_portfolio_net_pnl_pct",
            "share_of_absolute_trade_pnl_pct",
        ]
    ]


def make_cost_sensitivity(trades):
    gross = float(trades["gross_pnl"].sum())
    costs = float(trades["total_cost"].sum())
    slippage = float(trades["slippage"].sum())
    actual_net = float(trades["net_pnl"].sum())
    rows = [
        ("Actual net P&L", actual_net, "Observed Stage 9 economics"),
        ("Gross P&L before costs", gross, "Sum of realized gross trade P&L"),
        (
            "Net P&L excluding slippage",
            actual_net + slippage,
            "Actual net plus all recorded slippage",
        ),
        (
            "Net P&L at 50% slippage",
            actual_net + slippage * 0.5,
            "All non-slippage costs unchanged",
        ),
        (
            "Net P&L at 2x slippage",
            actual_net - slippage,
            "All non-slippage costs unchanged",
        ),
    ]
    return pd.DataFrame(
        [
            {
                "scenario": name,
                "net_pnl": value,
                "gross_pnl": gross,
                "total_costs": costs,
                "total_slippage": slippage,
                "assumption": assumption,
            }
            for name, value, assumption in rows
        ]
    )


def make_winner_loser_analysis(trades):
    rows = []
    outcomes = trades["net_pnl"].gt(0).map({True: "WIN", False: "LOSS"})
    trades = trades.assign(outcome=outcomes)
    for outcome, group in trades.groupby("outcome", sort=True):
        rows.append(
            {
                "analysis_type": "outcome",
                "group": outcome,
                "exit_reason": "",
                "trades": len(group),
                "wins": int((group["net_pnl"] > 0).sum()),
                "losses": int((group["net_pnl"] < 0).sum()),
                "gross_pnl": float(group["gross_pnl"].sum()),
                "costs": float(group["total_cost"].sum()),
                "net_pnl": float(group["net_pnl"].sum()),
                "average_net_pnl": float(group["net_pnl"].mean()),
                "median_net_pnl": float(group["net_pnl"].median()),
                "average_mfe_pct": float(group["mfe_pct"].mean()),
                "median_mfe_pct": float(group["mfe_pct"].median()),
                "average_mae_pct": float(group["mae_pct"].mean()),
                "median_mae_pct": float(group["mae_pct"].median()),
                "average_r_multiple": float(group["r_multiple"].mean()),
                "median_r_multiple": float(group["r_multiple"].median()),
                "average_holding_minutes": float(group["holding_minutes"].mean()),
                "median_holding_minutes": float(group["holding_minutes"].median()),
            }
        )
    for exit_reason, group in trades.groupby("exit_reason", dropna=False, sort=True):
        rows.append(
            {
                "analysis_type": "exit_reason",
                "group": "",
                "exit_reason": str(exit_reason),
                "trades": len(group),
                "wins": int((group["net_pnl"] > 0).sum()),
                "losses": int((group["net_pnl"] < 0).sum()),
                "gross_pnl": float(group["gross_pnl"].sum()),
                "costs": float(group["total_cost"].sum()),
                "net_pnl": float(group["net_pnl"].sum()),
                "average_net_pnl": float(group["net_pnl"].mean()),
                "median_net_pnl": float(group["net_pnl"].median()),
                "average_mfe_pct": float(group["mfe_pct"].mean()),
                "median_mfe_pct": float(group["mfe_pct"].median()),
                "average_mae_pct": float(group["mae_pct"].mean()),
                "median_mae_pct": float(group["mae_pct"].median()),
                "average_r_multiple": float(group["r_multiple"].mean()),
                "median_r_multiple": float(group["r_multiple"].median()),
                "average_holding_minutes": float(group["holding_minutes"].mean()),
                "median_holding_minutes": float(group["holding_minutes"].median()),
            }
        )
    return pd.DataFrame(rows)


def make_context_analysis(trades, diagnostics):
    trade_columns = [
        "trade_key",
        "net_pnl",
        "gross_pnl",
        "total_cost",
    ]
    events = diagnostics.merge(
        trades[trade_columns],
        on="trade_key",
        how="left",
        validate="many_to_one",
        suffixes=("_diagnostic", "_trade"),
    )
    rows = []
    for down_count in range(5):
        group = events[events["down_count"] == down_count]
        accepted = group[group["accepted_trade"]]
        net = accepted["net_pnl_trade"].astype(float)
        gross = accepted["gross_pnl_trade"].astype(float)
        costs = accepted["total_cost_trade"].astype(float)
        rows.append(
            {
                "indices_down": down_count,
                "signals": len(group),
                "vetoed_signals": int(group["context_veto"].sum()),
                "context_passed_signals": int((~group["context_veto"]).sum()),
                "accepted_trades": len(accepted),
                "risk_rejected_signals": int((~group["context_veto"] & ~group["accepted_trade"]).sum()),
                "wins": int((net > 0).sum()),
                "losses": int((net < 0).sum()),
                "win_rate_pct": float((net > 0).mean() * 100) if len(net) else np.nan,
                "gross_pnl": float(gross.sum()),
                "costs": float(costs.sum()),
                "net_pnl": float(net.sum()),
                "average_trade": float(net.mean()) if len(net) else np.nan,
            }
        )
    return pd.DataFrame(rows)


def make_bootstrap(trades):
    net = trades["net_pnl"].to_numpy(dtype=float)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    samples = rng.choice(
        net,
        size=(BOOTSTRAP_ITERATIONS, len(net)),
        replace=True,
    ).sum(axis=1)
    return pd.DataFrame(
        [
            {
                "trade_count": len(net),
                "iterations": BOOTSTRAP_ITERATIONS,
                "random_seed": BOOTSTRAP_SEED,
                "probability_positive_pct": float((samples > 0).mean() * 100),
                "median_cumulative_pnl": float(np.median(samples)),
                "percentile_5_cumulative_pnl": float(np.percentile(samples, 5)),
                "percentile_95_cumulative_pnl": float(np.percentile(samples, 95)),
                "mean_cumulative_pnl": float(samples.mean()),
            }
        ]
    )


def make_summary(trades, metrics, curve, bootstrap):
    stats = pnl_stats(trades)
    net_values = trades["net_pnl"].astype(float).to_numpy()
    best = trades.loc[trades["net_pnl"].idxmax()]
    worst = trades.loc[trades["net_pnl"].idxmin()]
    trough_index = int(curve["drawdown"].to_numpy().argmin())
    trough = curve.iloc[trough_index]
    peak_level = float(trough["running_peak"])
    prior_curve = curve.iloc[: trough_index + 1]
    peak_matches = prior_curve.index[
        np.isclose(prior_curve["equity"], peak_level)
    ]
    peak_timestamp = (
        curve.loc[peak_matches[-1], "signal_timestamp"].isoformat()
        if len(peak_matches)
        else "STARTING_CAPITAL"
    )
    recovery = curve.iloc[trough_index:]
    recovery = recovery[recovery["equity"] >= peak_level]
    recovery_timestamp = (
        recovery.iloc[0]["signal_timestamp"].isoformat()
        if len(recovery)
        else "Not recovered by final trade"
    )
    initial_capital = float(metrics["initial_capital"])
    maximum_drawdown = float(curve["drawdown"].min())
    max_drawdown_pct = float(curve["drawdown_pct"].min())
    max_loss_streak = maximum_loss_streak(net_values)
    total_net = stats["net_pnl"]
    slippage = float(trades["slippage"].sum())
    total_costs = stats["costs"]
    best_five = trades.nlargest(5, "net_pnl")["net_pnl"].sum()
    worst_five = trades.nsmallest(5, "net_pnl")["net_pnl"].sum()
    best_two = trades.nlargest(2, "net_pnl")["net_pnl"].sum()
    worst_two = trades.nsmallest(2, "net_pnl")["net_pnl"].sum()

    values = [
        ("trade_count", stats["trades"]),
        ("wins", stats["wins"]),
        ("losses", stats["losses"]),
        ("win_rate_pct", stats["win_rate_pct"]),
        ("gross_pnl", stats["gross_pnl"]),
        ("total_costs", total_costs),
        ("net_pnl", total_net),
        ("net_return_pct", total_net / initial_capital * 100),
        ("average_trade", stats["average_trade"]),
        ("median_trade", stats["median_trade"]),
        ("profit_factor_net", stats["profit_factor"]),
        ("profit_factor_gross", stats["gross_profit_factor"]),
        ("average_winner", stats["average_winner"]),
        ("average_loser", stats["average_loser"]),
        ("median_winner", stats["median_winner"]),
        ("median_loser", stats["median_loser"]),
        (
            "average_winner_to_average_loser_abs_ratio",
            stats["average_winner"] / abs(stats["average_loser"]),
        ),
        ("maximum_drawdown_inr", abs(maximum_drawdown)),
        ("maximum_drawdown_pct", max_drawdown_pct),
        ("drawdown_peak_signal", peak_timestamp),
        ("drawdown_trough_signal", trough["signal_timestamp"].isoformat()),
        ("drawdown_recovery", recovery_timestamp),
        ("maximum_consecutive_losses", max_loss_streak),
        ("best_trade_net_pnl", float(best["net_pnl"])),
        ("best_trade_symbol", best["symbol"]),
        ("best_trade_signal", best["signal_timestamp"].isoformat()),
        ("worst_trade_net_pnl", float(worst["net_pnl"])),
        ("worst_trade_symbol", worst["symbol"]),
        ("worst_trade_signal", worst["signal_timestamp"].isoformat()),
        ("average_total_cost_per_trade", total_costs / len(trades)),
        ("total_slippage", slippage),
        ("non_slippage_costs", total_costs - slippage),
        ("best_5_trades_net_pnl", float(best_five)),
        ("best_5_share_of_total_net_pnl_pct", float(best_five / total_net * 100)),
        ("worst_5_trades_net_pnl", float(worst_five)),
        ("worst_5_share_of_total_net_pnl_pct", float(worst_five / total_net * 100)),
        ("best_2_trades_net_pnl", float(best_two)),
        ("worst_2_trades_net_pnl", float(worst_two)),
        ("bootstrap_probability_positive_pct", float(bootstrap.iloc[0]["probability_positive_pct"])),
        ("bootstrap_median_cumulative_pnl", float(bootstrap.iloc[0]["median_cumulative_pnl"])),
        ("bootstrap_5th_percentile_pnl", float(bootstrap.iloc[0]["percentile_5_cumulative_pnl"])),
        ("bootstrap_95th_percentile_pnl", float(bootstrap.iloc[0]["percentile_95_cumulative_pnl"])),
        ("initial_capital", initial_capital),
        ("ending_capital", initial_capital + total_net),
        ("source_metrics_maximum_drawdown_pct", float(metrics["maximum_drawdown_pct"])),
    ]
    return pd.DataFrame(values, columns=["metric", "value"])


def markdown_table(frame, columns, digits=2):
    def cell(value):
        if pd.isna(value):
            return ""
        if isinstance(value, (float, np.floating)):
            if math.isinf(float(value)):
                return "inf"
            return f"{float(value):,.{digits}f}"
        return str(value).replace("|", "\\|")

    header = "| " + " | ".join(columns) + " |"
    divider = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(cell(value) for value in row) + " |"
        for row in frame[columns].itertuples(index=False, name=None)
    ]
    return "\n".join([header, divider, *body])


def build_report(
    trades,
    metrics,
    counts,
    summary,
    periods,
    symbols,
    concentration,
    costs,
    winner_loser,
    context,
    equity,
    bootstrap,
):
    values = summary.set_index("metric")["value"]
    period_net = periods["net_pnl"].to_numpy()
    period_label = (
        "consistently negative"
        if (period_net < 0).all()
        else "consistently positive"
        if (period_net > 0).all()
        else "mixed"
    )
    best_five_sum = float(concentration.loc[concentration["group"] == "Best 5", "net_pnl"].sum())
    worst_five_sum = float(concentration.loc[concentration["group"] == "Worst 5", "net_pnl"].sum())
    best_five_abs_share = float(
        concentration.loc[
            concentration["group"] == "Best 5",
            "share_of_absolute_trade_pnl_pct",
        ].sum()
    )
    worst_five_abs_share = float(
        concentration.loc[
            concentration["group"] == "Worst 5",
            "share_of_absolute_trade_pnl_pct",
        ].sum()
    )
    symbol_rows = symbols[symbols["analysis_type"] == "symbol"]
    scenario_rows = symbols[symbols["analysis_type"] == "exclusion_scenario"]
    initial_capital = float(metrics["initial_capital"])
    gross = float(trades["gross_pnl"].sum())
    net = float(trades["net_pnl"].sum())
    net_excluding_worst_trade = net - float(trades["net_pnl"].min())
    net_excluding_worst_two_trades = net - float(
        trades.nsmallest(2, "net_pnl")["net_pnl"].sum()
    )
    costs_total = float(trades["total_cost"].sum())
    gross_positive = float(trades.loc[trades["gross_pnl"] > 0, "gross_pnl"].sum())
    gross_negative = float(trades.loc[trades["gross_pnl"] < 0, "gross_pnl"].sum())
    cost_rows = costs.set_index("scenario")
    sensitivity_no_slippage = float(cost_rows.loc["Net P&L excluding slippage", "net_pnl"])
    best_mfe = winner_loser[winner_loser["group"] == "WIN"].iloc[0]
    loss_mfe = winner_loser[winner_loser["group"] == "LOSS"].iloc[0]
    exit_rows = winner_loser[winner_loser["analysis_type"] == "exit_reason"]
    context_summary = context[
        [
            "indices_down",
            "signals",
            "vetoed_signals",
            "context_passed_signals",
            "accepted_trades",
            "risk_rejected_signals",
            "wins",
            "losses",
            "net_pnl",
        ]
    ]
    exclusion_text = markdown_table(
        scenario_rows,
        ["scenario", "excluded_symbols", "net_pnl"],
    )
    period_text = markdown_table(
        periods,
        [
            "period",
            "start_signal",
            "end_signal",
            "trades",
            "wins",
            "losses",
            "win_rate_pct",
            "gross_pnl",
            "costs",
            "net_pnl",
            "average_trade",
        ],
    )
    symbol_text = markdown_table(
        symbol_rows.sort_values("net_pnl", ascending=False),
        ["symbol", "trades", "wins", "losses", "win_rate_pct", "gross_pnl", "costs", "net_pnl", "average_trade"],
    )
    concentration_text = markdown_table(
        concentration,
        ["group", "rank", "symbol", "signal_timestamp", "gross_pnl", "total_cost", "net_pnl", "cumulative_group_net_pnl", "share_of_portfolio_net_pnl_pct", "share_of_absolute_trade_pnl_pct"],
    )
    cost_text = markdown_table(costs, ["scenario", "net_pnl", "assumption"])
    winner_text = markdown_table(
        winner_loser[winner_loser["analysis_type"] == "outcome"],
        ["group", "trades", "gross_pnl", "costs", "net_pnl", "average_net_pnl", "median_net_pnl", "average_mfe_pct", "median_mfe_pct", "average_mae_pct", "median_mae_pct", "average_r_multiple", "median_r_multiple", "average_holding_minutes", "median_holding_minutes"],
    )
    exit_text = markdown_table(
        exit_rows,
        ["exit_reason", "trades", "wins", "losses", "gross_pnl", "costs", "net_pnl", "average_net_pnl", "average_holding_minutes"],
    )
    context_text = markdown_table(context_summary, list(context_summary.columns))
    bootstrap_text = markdown_table(
        bootstrap,
        ["trade_count", "iterations", "random_seed", "probability_positive_pct", "median_cumulative_pnl", "percentile_5_cumulative_pnl", "percentile_95_cumulative_pnl", "mean_cumulative_pnl"],
    )

    verdict = "NOT ROBUST — RETURN TO RESEARCH"
    lines = [
        "# Stage 10 — Final OOS Robustness Analysis",
        "",
        f"**Verdict: {verdict}**",
        "",
        "The frozen candidate does not show enough evidence of robust positive expectancy to proceed to Stage 11 paper trading as a validation candidate. This is a conclusion about this candidate and this small OOS sample, not a conclusion that the broader project has failed.",
        "",
        "## Integrity checks",
        "",
        "All checks passed before analysis:",
        "- Stage 9 trades: 19; Stage 10 analysis trades: 19.",
        "- Frozen diagnostic events: 37; accepted diagnostic matches: 19/19.",
        "- No duplicate normalized keys in trades, diagnostics, or veto records; no missing or extra accepted-trade keys.",
        f"- Stage 9 net P&L: INR {net:,.2f}; metrics and Stage 10A accepted outcomes reconcile within 0.01.",
        "- Trade order is chronological by signal timestamp; every entry follows its signal and every exit is at or after entry.",
        "- Context attribution uses the existing signal-time down-count and unchanged 3-of-4 veto; no entry-time context or post-entry information was used for filtering.",
        "- No strategy parameters, source trades, market data, risk rules, or OOS inputs were changed.",
        "",
        "## Overall robustness",
        "",
        f"There were {int(values['trade_count'])} trades: {int(values['wins'])} wins and {int(values['losses'])} losses ({values['win_rate_pct']:.2f}% win rate). Gross P&L was INR {gross:,.2f}, versus INR {costs_total:,.2f} of costs, leaving net P&L of INR {net:,.2f}. Gross winners totaled INR {gross_positive:,.2f} and gross losers INR {gross_negative:,.2f}; gross expectancy was only INR {gross / len(trades):,.2f} per trade, effectively flat in this sample, while net expectancy was INR {net / len(trades):,.2f} per trade.",
        "",
        markdown_table(
            summary[summary["metric"].isin([
                "trade_count", "wins", "losses", "win_rate_pct", "gross_pnl", "total_costs", "net_pnl", "net_return_pct", "average_trade", "median_trade", "profit_factor_net", "profit_factor_gross", "average_winner", "average_loser", "median_winner", "median_loser", "average_winner_to_average_loser_abs_ratio", "maximum_drawdown_inr", "maximum_drawdown_pct", "maximum_consecutive_losses", "best_trade_net_pnl", "best_trade_symbol", "worst_trade_net_pnl", "worst_trade_symbol"
            ])],
            ["metric", "value"],
        ),
        "",
        "Net average winner / absolute average loser is "
        f"{values['average_winner_to_average_loser_abs_ratio']:.3f}; average winners were INR {values['average_winner']:,.2f} versus average losses of INR {values['average_loser']:,.2f}. Median winner/loser were INR {values['median_winner']:,.2f} / INR {values['median_loser']:,.2f}. Average costs were INR {costs_total / len(trades):,.2f} per trade. The gross profit factor is only marginally above 1, while the net profit factor is below 1.",
        "",
        "## Time stability",
        "",
        f"Trades were split into equal chronological halves (10 then 9 trades) because monthly samples are too sparse for stable monthly comparisons. Results were {period_label}; this split is descriptive and not selected for favorable performance. The first half lost INR {abs(float(periods.iloc[0]['net_pnl'])):,.2f}, while the second was near flat at INR {float(periods.iloc[1]['net_pnl']):,.2f}; the aggregate loss is therefore concentrated in the first half, but the small counts cannot establish persistent time dependence.",
        "",
        period_text,
        "",
        "The two periods should not be interpreted as independent validation sets; 19 observations remain a small sample.",
        "",
        "## Symbol concentration",
        "",
        symbol_text,
        "",
        f"Total portfolio net P&L was INR {net:,.2f}. Best five cumulative net P&L was INR {best_five_sum:,.2f} ({values['best_5_share_of_total_net_pnl_pct']:.2f}% of total net P&L; {best_five_abs_share:.2f}% of absolute trade P&L). Worst five was INR {worst_five_sum:,.2f} ({values['worst_5_share_of_total_net_pnl_pct']:.2f}% of total net P&L; {worst_five_abs_share:.2f}% of absolute trade P&L). Removing the worst single trade would still leave INR {net_excluding_worst_trade:,.2f}; removing the worst two would still leave INR {net_excluding_worst_two_trades:,.2f}. Thus, losses are concentrated in the five worst trades, but not explained by only one or two trades.",
        "",
        exclusion_text,
        "",
        "These exclusion calculations are counterfactual concentration diagnostics only; no symbols are removed or recommended for removal.",
        "",
        "## Trade concentration",
        "",
        concentration_text,
        "",
        "Contribution as a percentage of total net P&L is an arithmetic ratio against a negative portfolio result, so winning trades can show negative percentages and losing trades positive percentages. The absolute-P&L share is available in trade_concentration.csv for a non-signed concentration measure.",
        "",
        "## Cost sensitivity",
        "",
        cost_text,
        "",
        f"The strategy is approximately flat before costs (gross INR {gross:,.2f}) and becomes negative after costs. Recorded slippage was INR {float(trades['slippage'].sum()):,.2f}; other costs were INR {costs_total - float(trades['slippage'].sum()):,.2f}. Removing all recorded slippage still leaves INR {sensitivity_no_slippage:,.2f} net, so slippage alone does not explain the loss; other estimated costs also matter. This sensitivity changes no production assumptions.",
        "",
        "## Winner and loser quality",
        "",
        winner_text,
        "",
        exit_text,
        "",
        f"Average MFE was {best_mfe['average_mfe_pct']:.3f}% for winners and {loss_mfe['average_mfe_pct']:.3f}% for losers; average MAE was {best_mfe['average_mae_pct']:.3f}% and {loss_mfe['average_mae_pct']:.3f}%, respectively. The winner/loser size imbalance is unfavorable. MFE/MAE and exit fields describe realized trade paths and were used only for post-trade analysis, never as entry filters.",
        "",
        "## Market-context robustness",
        "",
        context_text,
        "",
        "There are no accepted trades at 3 or 4 indices down because the frozen veto rejects those signals; their vetoed counts remain visible above. Outcomes for those vetoed events cannot establish what hypothetical trades would have done, and no alternative veto threshold is inferred.",
        "",
        "Baseline A had 29 accepted trades, net P&L INR -4,233.36, 44.83% win rate, and PF 0.4421. Stage 9 had 10 fewer trades, reduced the absolute loss by INR 871.07, and improved win rate by 2.54 percentage points, but its PF was lower by 0.0513 (0.3908 versus 0.4421). Stage 9 remains unprofitable; the comparison is not evidence of profitability.",
        "",
        "## Bootstrap diagnostic",
        "",
        bootstrap_text,
        "",
        "This resamples the 19 realized net trade outcomes with replacement, using a fixed seed. It assumes the observed trade outcomes are representative and ignores temporal dependence, market-regime shifts, and strategy selection. With only 19 trades, it is descriptive and must not be treated as statistical significance or a forecast.",
        "",
        "## Drawdown and equity curve",
        "",
        f"Starting capital was INR {initial_capital:,.2f}; ending equity was INR {values['ending_capital']:,.2f}. Maximum peak-to-trough drawdown was INR {values['maximum_drawdown_inr']:,.2f} ({values['maximum_drawdown_pct']:.3f}%), from {values['drawdown_peak_signal']} to {values['drawdown_trough_signal']}. Recovery status: {values['drawdown_recovery']}. The longest losing sequence was {int(values['maximum_consecutive_losses'])} trades.",
        "",
        "Equity is marked after each chronological accepted trade, including initial capital in the running high-water mark. No open or rejected signal is simulated.",
        "",
        "## Interpretation and limitations",
        "",
        "Observed evidence is weak: negative net expectancy, a near-zero gross P&L, costs exceeding gross gains, a net profit factor of about 0.39, and average losses substantially larger than average wins. Both symbol and period results vary, but small group counts prevent reliable attribution to one symbol or time period. The negative result is not explained by one or two trades or one symbol; the five largest losses dominate the total, and the worst two-symbol exclusion remains negative. The full sample has 10 losing trades and total gross loss slightly exceeds gross profit before costs.",
        "",
        "The evidence points to a combination of a near-zero gross edge, winners materially smaller than losers, and high estimated trading costs. MFE/MAE are consistent with weaker favorable excursion and larger adverse excursion among losing outcomes, but realized trades alone cannot isolate entry quality as the cause. The 19 accepted trades are too few to establish stability, estimate tails reliably, or prove any single failure mechanism. Results do not justify parameter changes in this stage.",
        "",
        f"**Decision: {verdict}. Do not proceed to Stage 11 paper trading as a candidate-validation step; keep this frozen candidate research-only pending a separately specified research decision.**",
        "",
    ]
    return "\n".join(lines)


def main():
    trades, diagnostics, metrics, counts = validate_inputs()
    initial_capital = float(metrics["initial_capital"])
    equity = build_equity_curve(trades, initial_capital)
    periods = make_period_analysis(trades)
    symbols = make_symbol_analysis(trades)
    concentration = make_trade_concentration(trades)
    costs = make_cost_sensitivity(trades)
    winner_loser = make_winner_loser_analysis(trades)
    context = make_context_analysis(trades, diagnostics)
    bootstrap = make_bootstrap(trades)
    summary = make_summary(trades, metrics, equity, bootstrap)

    summary.loc[len(summary)] = ["stage9_accepted_trades", len(trades)]
    summary.loc[len(summary)] = ["stage10_analysis_trades", len(trades)]
    summary.loc[len(summary)] = ["diagnostic_signals", len(diagnostics)]
    summary.loc[len(summary)] = ["matched_accepted_trades", len(trades)]
    summary.loc[len(summary)] = ["duplicate_trade_keys", 0]
    summary.loc[len(summary)] = ["missing_accepted_trades", 0]
    summary.loc[len(summary)] = ["strategy_parameters_modified", False]

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    summary.to_csv(OUTPUT_DIR / "summary.csv", index=False)
    periods.to_csv(OUTPUT_DIR / "period_analysis.csv", index=False)
    symbols.to_csv(OUTPUT_DIR / "symbol_analysis.csv", index=False)
    concentration.to_csv(OUTPUT_DIR / "trade_concentration.csv", index=False)
    costs.to_csv(OUTPUT_DIR / "cost_sensitivity.csv", index=False)
    winner_loser.to_csv(OUTPUT_DIR / "winner_loser_analysis.csv", index=False)
    context.to_csv(OUTPUT_DIR / "context_analysis.csv", index=False)
    equity.to_csv(OUTPUT_DIR / "equity_curve.csv", index=False)
    bootstrap.to_csv(OUTPUT_DIR / "bootstrap_results.csv", index=False)

    report = build_report(
        trades,
        metrics,
        counts,
        summary,
        periods,
        symbols,
        concentration,
        costs,
        winner_loser,
        context,
        equity,
        bootstrap,
    )
    (OUTPUT_DIR / "FINAL_ROBUSTNESS_REPORT.md").write_text(
        report,
        encoding="utf-8",
    )

    print("Stage 10 integrity checks: PASS")
    print(f"Stage 9 accepted trades: {len(trades)}")
    print(f"Stage 10 analysis trades: {len(trades)}")
    print(f"Diagnostic signals: {len(diagnostics)}")
    print(f"Matched accepted trades: {len(trades)}")
    print(f"Net P&L: INR {trades['net_pnl'].sum():,.2f}")
    print(f"Outputs: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()