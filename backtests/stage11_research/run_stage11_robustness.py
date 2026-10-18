from pathlib import Path

import numpy as np
import pandas as pd

from research_core import (
    EXIT_VARIANTS,
    FAMILY_VARIANTS,
    FILTER_VARIANTS,
    OUTPUT_DIR,
    SPLITS,
    STAGE9_TRADES,
    calculate_metrics,
    load_inputs,
    prepare_frames,
    simulate_window,
)


EXPERIMENT_FILES = {
    "exit": OUTPUT_DIR / "exit_experiments.csv",
    "filter": OUTPUT_DIR / "filter_experiments.csv",
    "strategy": OUTPUT_DIR / "strategy_comparison.csv",
}
MIN_WALK_FORWARD_TRADES = 30
MIN_POSITIVE_WINDOWS = 3


def _candidate_specs():
    registries = {
        "exit": {item["variant"]: item for item in EXIT_VARIANTS},
        "filter": {item["variant"]: item for item in FILTER_VARIANTS},
        "strategy": {item["variant"]: item for item in FAMILY_VARIANTS},
    }
    rows = []
    for category, path in EXPERIMENT_FILES.items():
        if not path.exists():
            continue
        results = pd.read_csv(path)
        finals = results[
            (results["role"] == "walk_forward")
            & results["is_development_finalist"].astype(bool)
        ]
        for variant, group in finals.groupby("variant", sort=False):
            positive_windows = int((group["net_pnl"] > 0).sum())
            total_trades = int(group["trades"].sum())
            total_net = float(group["net_pnl"].sum())
            if (
                total_net > 0
                and positive_windows >= MIN_POSITIVE_WINDOWS
                and total_trades >= MIN_WALK_FORWARD_TRADES
            ):
                rows.append(
                    {
                        "category": category,
                        "variant": variant,
                        "positive_windows": positive_windows,
                        "walk_forward_trades": total_trades,
                        "walk_forward_net_pnl": total_net,
                        "spec": registries[category][variant],
                    }
                )
    return rows


def _run_candidate(candidate, data, context):
    category = candidate["category"]
    spec = candidate["spec"]
    timeframe = spec.get("timeframe_minutes", 5)
    family = spec.get("family", "reversal")
    filter_name = spec.get("filter", "none")
    frames = prepare_frames(data, timeframe_minutes=timeframe)
    exit_spec = spec if category == "exit" else {"variant": spec["variant"], "horizon": 6}
    rows = []
    base_trades = []
    for multiplier in (1.0, 1.5, 2.0):
        group_trades = []
        period_results = []
        for split in SPLITS[1:]:
            row, trades = simulate_window(
                frames,
                context,
                split,
                exit_spec,
                family=family,
                timeframe_minutes=timeframe,
                filter_name=filter_name,
                cost_multiplier=multiplier,
                keep_trades=True,
            )
            row["cost_multiplier"] = multiplier
            period_results.append(row)
            group_trades.extend(trades)
        if multiplier == 1.0:
            base_trades = group_trades
        total_net = float(sum(trade["net_pnl"] for trade in group_trades))
        total_gross = float(sum(trade["gross_pnl"] for trade in group_trades))
        total_costs = float(sum(trade["total_cost"] for trade in group_trades))
        rows.append(
            {
                "analysis_type": "walk_forward_cost_sensitivity",
                "category": category,
                "variant": candidate["variant"],
                "cost_multiplier": multiplier,
                "trades": len(group_trades),
                "gross_pnl": total_gross,
                "costs": total_costs,
                "net_pnl": total_net,
                "positive_walk_forward_windows": int(
                    sum(period["net_pnl"] > 0 for period in period_results)
                ),
                "time_period_or_symbol": "",
                "win_rate_pct": (
                    float(np.mean([trade["net_pnl"] > 0 for trade in group_trades]) * 100)
                    if group_trades
                    else np.nan
                ),
                "ci_2_5_pct": np.nan,
                "ci_97_5_pct": np.nan,
                "probability_positive_pct": np.nan,
                "status": "candidate passed predeclared walk-forward gate; no holdout evaluated",
            }
        )
        if multiplier == 1.0:
            for period in period_results:
                rows.append(
                    {
                        "analysis_type": "walk_forward_period",
                        "category": category,
                        "variant": candidate["variant"],
                        "cost_multiplier": 1.0,
                        "trades": period["trades"],
                        "gross_pnl": period["gross_pnl"],
                        "costs": period["costs"],
                        "net_pnl": period["net_pnl"],
                        "positive_walk_forward_windows": np.nan,
                        "time_period_or_symbol": period["split"],
                        "win_rate_pct": period["win_rate_pct"],
                        "ci_2_5_pct": np.nan,
                        "ci_97_5_pct": np.nan,
                        "probability_positive_pct": np.nan,
                        "status": "walk-forward fold",
                    }
                )

    if base_trades:
        trade_frame = pd.DataFrame(base_trades)
        for symbol, group in trade_frame.groupby("symbol", sort=True):
            rows.append(
                {
                    "analysis_type": "symbol_concentration",
                    "category": category,
                    "variant": candidate["variant"],
                    "cost_multiplier": 1.0,
                    "trades": len(group),
                    "gross_pnl": group["gross_pnl"].sum(),
                    "costs": group["total_cost"].sum(),
                    "net_pnl": group["net_pnl"].sum(),
                    "positive_walk_forward_windows": np.nan,
                    "time_period_or_symbol": symbol,
                    "win_rate_pct": (group["net_pnl"] > 0).mean() * 100,
                    "ci_2_5_pct": np.nan,
                    "ci_97_5_pct": np.nan,
                    "probability_positive_pct": np.nan,
                    "status": "descriptive only; not a symbol filter",
                }
            )

        seed = np.random.default_rng(20261007)
        outcomes = trade_frame["net_pnl"].to_numpy(dtype=float)
        samples = seed.choice(outcomes, size=(50_000, len(outcomes)), replace=True).sum(axis=1)
        rows.append(
            {
                "analysis_type": "trade_bootstrap_95pct_interval",
                "category": category,
                "variant": candidate["variant"],
                "cost_multiplier": 1.0,
                "trades": len(outcomes),
                "gross_pnl": trade_frame["gross_pnl"].sum(),
                "costs": trade_frame["total_cost"].sum(),
                "net_pnl": trade_frame["net_pnl"].sum(),
                "positive_walk_forward_windows": candidate["positive_windows"],
                "time_period_or_symbol": "seed=20261007, resample=50000",
                "win_rate_pct": (trade_frame["net_pnl"] > 0).mean() * 100,
                "ci_2_5_pct": float(np.percentile(samples, 2.5)),
                "ci_97_5_pct": float(np.percentile(samples, 97.5)),
                "probability_positive_pct": float((samples > 0).mean() * 100),
                "status": "diagnostic trade-level bootstrap; ignores dependence",
            }
        )
    return rows


def main():
    rows = []
    candidates = _candidate_specs()
    if candidates:
        data, context, _ = load_inputs()
        for candidate in candidates:
            rows.extend(_run_candidate(candidate, data, context))
    else:
        rows.append(
            {
                "analysis_type": "eligibility",
                "category": "all",
                "variant": "none",
                "cost_multiplier": np.nan,
                "trades": 0,
                "gross_pnl": 0.0,
                "costs": 0.0,
                "net_pnl": 0.0,
                "positive_walk_forward_windows": 0,
                "time_period_or_symbol": "",
                "win_rate_pct": np.nan,
                "ci_2_5_pct": np.nan,
                "ci_97_5_pct": np.nan,
                "probability_positive_pct": np.nan,
                "status": "no candidate passed the preregistered gate: net positive, >=3/4 positive folds, >=30 walk-forward trades",
            }
        )

    stage9 = pd.read_csv(STAGE9_TRADES)
    gross = pd.to_numeric(stage9["gross_pnl"], errors="coerce")
    costs = pd.to_numeric(stage9["total_cost"], errors="coerce")
    for multiplier in (1.0, 1.5, 2.0):
        rows.append(
            {
                "analysis_type": "existing_stage9_oos_cost_sensitivity",
                "category": "frozen_stage9_baseline_only",
                "variant": "stage9_reported_trades",
                "cost_multiplier": multiplier,
                "trades": len(stage9),
                "gross_pnl": float(gross.sum()),
                "costs": float(costs.sum() * multiplier),
                "net_pnl": float(gross.sum() - costs.sum() * multiplier),
                "positive_walk_forward_windows": np.nan,
                "time_period_or_symbol": "existing OOS artifact; arithmetic sensitivity only",
                "win_rate_pct": np.nan,
                "ci_2_5_pct": np.nan,
                "ci_97_5_pct": np.nan,
                "probability_positive_pct": np.nan,
                "status": "not a variant test and not used for candidate selection",
            }
        )

    pd.DataFrame(rows).to_csv(OUTPUT_DIR / "robustness_checks.csv", index=False)
    print(f"Walk-forward candidates passing gate: {len(candidates)}")
    print(pd.DataFrame(rows).to_string(index=False))


if __name__ == "__main__":
    main()