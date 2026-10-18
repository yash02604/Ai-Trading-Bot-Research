from pathlib import Path
import math

import numpy as np
import pandas as pd

from research_core import OUTPUT_DIR, trial_log


EXPERIMENT_FILES = {
    "exit": OUTPUT_DIR / "exit_experiments.csv",
    "filter": OUTPUT_DIR / "filter_experiments.csv",
    "strategy": OUTPUT_DIR / "strategy_comparison.csv",
}


def _markdown_table(frame, columns):
    def render(value):
        if pd.isna(value):
            return ""
        if isinstance(value, (float, np.floating)):
            if math.isinf(float(value)):
                return "inf"
            return f"{float(value):,.2f}"
        return str(value).replace("|", "\\|")

    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in frame[columns].itertuples(index=False, name=None):
        lines.append("| " + " | ".join(render(value) for value in row) + " |")
    return "\n".join(lines)


def _aggregate_results(frames):
    all_results = pd.concat(frames.values(), ignore_index=True)
    rows = []
    for (category, variant), group in all_results.groupby(
        ["category", "variant"],
        sort=False,
    ):
        development = group[group["role"] == "development"]
        walk = group[group["role"] == "walk_forward"]
        rows.append(
            {
                "category": category,
                "variant": variant,
                "development_trades": int(development["trades"].sum()),
                "development_net_pnl": float(development["net_pnl"].sum()),
                "walk_forward_finalist": bool(group["is_development_finalist"].astype(bool).any()),
                "walk_forward_folds_tested": len(walk),
                "walk_forward_trades": int(walk["trades"].sum()),
                "walk_forward_net_pnl": float(walk["net_pnl"].sum()),
                "positive_walk_forward_folds": int((walk["net_pnl"] > 0).sum()),
                "net_pnl_per_trade": (
                    float(walk["net_pnl"].sum() / walk["trades"].sum())
                    if walk["trades"].sum()
                    else np.nan
                ),
            }
        )
    return pd.DataFrame(rows)


def main():
    frames = {
        category: pd.read_csv(path)
        for category, path in EXPERIMENT_FILES.items()
    }
    breakeven_path = OUTPUT_DIR / "breakeven.csv"
    robustness_path = OUTPUT_DIR / "robustness_checks.csv"
    breakeven = pd.read_csv(breakeven_path)
    robustness = pd.read_csv(robustness_path)
    aggregate = _aggregate_results(frames)
    aggregate.to_csv(OUTPUT_DIR / "variant_summary.csv", index=False)
    log = trial_log(frames)

    if not robustness.empty:
        sensitivity = robustness[
            robustness["analysis_type"].isin(
                ["walk_forward_cost_sensitivity", "existing_stage9_oos_cost_sensitivity"]
            )
        ]
        if not sensitivity.empty:
            offset = int(log["trial_id"].max()) if not log.empty else 0
            extra_rows = []
            for index, row in enumerate(sensitivity.itertuples(index=False), start=1):
                extra_rows.append(
                    {
                        "trial_id": offset + index,
                        "category": "cost_sensitivity",
                        "variant": row.variant,
                        "variant_count_in_category": sensitivity["variant"].nunique(),
                        "split": "walk_forward_aggregate" if row.analysis_type == "walk_forward_cost_sensitivity" else "existing_stage9_oos_baseline",
                        "role": "robustness_diagnostic",
                        "trades": row.trades,
                        "net_pnl": row.net_pnl,
                        "finalist_selected_on_development": row.analysis_type == "walk_forward_cost_sensitivity",
                        "holdout_status": "NOT RUN",
                    }
                )
            log = pd.concat([log, pd.DataFrame(extra_rows)], ignore_index=True)
    log.to_csv(OUTPUT_DIR / "trial_log.csv", index=False)

    development_tests = int((log["role"] == "development").sum()) if not log.empty else 0
    walk_tests = int((log["role"] == "walk_forward").sum()) if not log.empty else 0
    distinct_variants = sum(frame["variant"].nunique() for frame in frames.values())
    filter_variant_count = frames["filter"][frames["filter"]["role"] == "development"]["variant"].nunique()
    finalist_rows = aggregate[aggregate["walk_forward_finalist"]]
    robustness_candidates = robustness[
        robustness["analysis_type"] == "walk_forward_cost_sensitivity"
    ]["variant"].nunique()

    break_rows = breakeven.set_index("metric")["value"]
    stage9_results = robustness[
        robustness["analysis_type"] == "existing_stage9_oos_cost_sensitivity"
    ][["cost_multiplier", "gross_pnl", "costs", "net_pnl"]]

    lines = [
        "# Stage 11 Research Report",
        "",
        "## Research boundary",
        "",
        "All new Stage 11 files are isolated in `backtests/stage11_research/`. Stage 9 trades, metrics, counts, veto records, and Stage 10 reports were not modified.",
        "",
        "The available 5-minute development candles end on 2025-12-01. The separate 2025-12-02 to 2026-10-01 OOS data and its Stage 9/10 outcomes have already been evaluated and reported. They are not a fresh holdout for this search, so no Stage 11 variant was scored on them. A genuinely untouched holdout requires future or independently sourced data collected after the Stage 11 candidates are frozen.",
        "",
        "Development used 2023-12-01 through 2024-12-31. Walk-forward validation used four fixed 2025 windows (Q1, Q2, Q3, and Q4 through Dec 1). These historical periods were used by prior project-wide validation and are not pristine; they are used here for exploratory development and sequential robustness checks, not as a new independent final test.",
        "",
        "## Break-even baseline",
        "",
        "This section reads the existing Stage 9 accepted trades descriptively. It is not a variant search or a re-simulation.",
        "",
        _markdown_table(breakeven, ["metric", "value", "unit"]),
        "",
        "Break-even win rate is computed from average gross wins, average absolute gross losses, and average cost. Required gross currency P&L per trade equals average cost; a price-move percentage cannot be derived without treating each trade's distinct exposure as identical.",
        "",
        "Stage 9 baseline cost sensitivity, scaling the recorded estimated costs only:",
        "",
        _markdown_table(stage9_results, ["cost_multiplier", "gross_pnl", "costs", "net_pnl"]),
        "",
        "This is arithmetic on the frozen existing trades and does not alter the production cost model. Brokerage and slippage are estimates; exact account charges, spreads, liquidity impact, and fill quality are unverified.",
        "",
        "## Experiment protocol",
        "",
        f"Distinct variants defined: {distinct_variants}. Development evaluations logged: {development_tests}; finalist walk-forward fold evaluations: {walk_tests}. The filter study defined {filter_variant_count} variants, including baseline, three standalone filters, one predeclared combined filter, and the second ATR threshold. No holdout trials were run.",
        "",
        "Every variant was tested first on development only. For exit and filter families, the unchanged baseline plus at most one development-selected alternative proceeded to walk-forward; for the strategy-family comparison, at most one of the two alternatives proceeded. Selection required at least 10 development trades and used development net P&L only. No walk-forward result was used to modify a variant or select a different candidate.",
        "",
        "Exit variants use the same frozen 5-minute reversal events, signal-time context veto, next-candle-open entry, risk rules, and cost model. Tested exits are 1% stop with no target at 6/12/24 bars, targets at 1.5R/2R/3R with six-bar horizon, and a one-R-activated 1% trailing stop with a 24-bar maximum.",
        "",
        "Filters were predeclared as ATR(14)/price >=0.5%, ATR(14)/price >=1.0%, signals before 10:45, trailing 20-bar average turnover >=₹25 lakh, and the conjunction of the first ATR, time, and turnover tests. Rationales are movement relative to costs, opening-session liquidity, and basic capacity; thresholds are hypotheses, not optimized settings.",
        "",
        "Timeframe/family variants are a 15-minute aggregation of the existing 5-minute bars using the frozen reversal definition, and a 5-minute opening-range breakout (first three bars define the range; first close above it signals; entry is next bar open). Both are intraday and use the existing intraday cost model. A daily delivery strategy was not prototyped because the repository has no verified delivery-fee schedule or corporate-action-adjusted data contract.",
        "",
        "Walk-forward candidates had to have positive combined net P&L, at least three of four positive quarterly folds, and at least 30 accepted validation trades before candidate-specific robustness checks. The holdout is not evaluated even if a candidate clears that gate.",
        "",
        "## Variant results",
        "",
        _markdown_table(
            aggregate.sort_values(["category", "walk_forward_finalist", "development_net_pnl"], ascending=[True, False, False]),
            ["category", "variant", "development_trades", "development_net_pnl", "walk_forward_finalist", "walk_forward_folds_tested", "walk_forward_trades", "walk_forward_net_pnl", "positive_walk_forward_folds", "net_pnl_per_trade"],
        ),
        "",
        "`exit_experiments.csv`, `filter_experiments.csv`, and `strategy_comparison.csv` contain the per-window metrics, including wins/losses, gross P&L, costs, net P&L, drawdown, and rejection counts. `trial_log.csv` shows each variant/window evaluation and marks holdout as not run.",
        "",
        "## Robustness checks",
        "",
    ]

    if robustness_candidates:
        lines.extend(
            [
                f"{robustness_candidates} candidate(s) passed the predeclared development/walk-forward gate. Their walk-forward cost stress at 1x/1.5x/2x, symbol concentration, fold-level time concentration, and fixed-seed trade bootstrap are in `robustness_checks.csv`.",
                "",
                _markdown_table(
                    robustness[robustness["analysis_type"].isin(["walk_forward_cost_sensitivity", "trade_bootstrap_95pct_interval"])],
                    ["analysis_type", "category", "variant", "cost_multiplier", "trades", "gross_pnl", "costs", "net_pnl", "positive_walk_forward_windows", "ci_2_5_pct", "ci_97_5_pct", "probability_positive_pct", "status"],
                ),
                "",
                "Bootstrap results are diagnostic only and assume exchangeable trade outcomes; they do not correct temporal dependence or multiple testing.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "No development-selected candidate met the preregistered walk-forward gate. Candidate-specific cost sensitivity, concentration, and bootstrap confidence intervals were therefore not run; doing them on a non-qualifying candidate would not establish robustness. Stage 9 baseline 1x/1.5x/2x cost arithmetic is included above.",
                "",
            ]
        )

    lines.extend(
        [
            "## Limitations and assumptions",
            "",
            "- The 2023–2025 source is not a new untouched dataset; prior validation and research artifacts exist for it. The search log makes this reuse visible.",
            "- There is no unexposed final holdout in the repository. The previously reported Stage 9/10 OOS is embargoed from Stage 11 variant selection and scoring.",
            "- Trade counts are small relative to the number of variants. Development selection is exploratory; walk-forward estimates remain noisy and multiple-comparison effects remain possible.",
            "- Missing or irregular bars are handled by the existing historical loader and event builder. Indicators use only current and earlier bars; signal context is joined backward at signal time; no future bars are used to select entries.",
            "- OHLC bars cannot identify intrabar stop/target order. When both stop and target are touched in one bar, the engine assumes the stop happened first. Trailing stops use the previous high-water mark, so they cannot move favorably using the current bar before its low is tested.",
            "- The filters and opening-range breakout are predeclared research hypotheses, not production recommendations. No parameter changes were made to Stage 9/10.",
            "- Daily delivery strategies and delivery charges were not modeled. Tested alternative timeframe/family strategies remain intraday so existing cost estimates are comparable, though those estimates still need broker-contract-note and execution validation.",
            "- A future holdout should be collected after candidate definitions and execution assumptions are frozen, ideally prospectively, with no tuning after its results are opened.",
            "",
            "## Run commands",
            "",
            "From the `ai-tarding-assistant` directory:",
            "",
            "```powershell",
            "python -m pytest .\\backtests\\stage11_research\\tests -q",
            "python .\\backtests\\stage11_research\\run_stage11_breakeven.py",
            "python .\\backtests\\stage11_research\\run_stage11_exit_experiments.py",
            "python .\\backtests\\stage11_research\\run_stage11_filter_experiments.py",
            "python .\\backtests\\stage11_research\\run_stage11_strategy_comparison.py",
            "python .\\backtests\\stage11_research\\run_stage11_robustness.py",
            "python .\\backtests\\stage11_research\\run_stage11_report.py",
            "```",
            "",
            "## Final conclusion",
            "",
            "No candidate has been validated on an untouched holdout. The current Stage 9 candidate has a negative known OOS result after costs, and this research cannot establish that a replacement has a real cost-adjusted edge. Do not proceed to live trading; freeze any finalist and collect new untouched forward data.",
            "",
            "**INCONCLUSIVE**",
            "",
        ]
    )
    (OUTPUT_DIR / "STAGE11_RESEARCH_REPORT.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Distinct variants: {distinct_variants}")
    print(f"Trial log rows: {len(log)}")
    print(f"Development evaluations: {development_tests}")
    print(f"Walk-forward evaluations: {walk_tests}")
    print(f"Filter variants/combinations: {filter_variant_count}")
    print(f"Walk-forward robustness candidates: {robustness_candidates}")
    print(f"Report: {OUTPUT_DIR / 'STAGE11_RESEARCH_REPORT.md'}")


if __name__ == "__main__":
    main()