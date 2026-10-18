# Stage 11 Research Report

## Research boundary

All new Stage 11 files are isolated in `backtests/stage11_research/`. Stage 9 trades, metrics, counts, veto records, and Stage 10 reports were not modified.

The available 5-minute development candles end on 2025-12-01. The separate 2025-12-02 to 2026-10-01 OOS data and its Stage 9/10 outcomes have already been evaluated and reported. They are not a fresh holdout for this search, so no Stage 11 variant was scored on them. A genuinely untouched holdout requires future or independently sourced data collected after the Stage 11 candidates are frozen.

Development used 2023-12-01 through 2024-12-31. Walk-forward validation used four fixed 2025 windows (Q1, Q2, Q3, and Q4 through Dec 1). These historical periods were used by prior project-wide validation and are not pristine; they are used here for exploratory development and sequential robustness checks, not as a new independent final test.

## Break-even baseline

This section reads the existing Stage 9 accepted trades descriptively. It is not a variant search or a re-simulation.

| metric | value | unit |
| --- | --- | --- |
| stage9_trade_count | 19.00 | trades |
| average_cost_per_trade | 177.24 | INR/trade |
| average_gross_win | 356.12 | INR/winning gross trade |
| average_gross_loss_abs | 489.02 | INR/losing gross trade |
| gross_win_rate | 57.89 | percent of trades |
| gross_pnl_per_trade | 0.27 | INR/trade |
| net_pnl_per_trade | -176.96 | INR/trade |
| break_even_win_rate_including_average_cost | 78.83 | percent, using average gross win/loss |
| gross_pnl_per_trade_needed_to_cover_costs | 177.24 | INR/trade |
| average_gross_move_needed_to_cover_costs | 177.24 | INR/trade, currency P&L proxy |
| average_capital_exposure | 95,716.01 | INR/trade |
| average_cost_as_pct_of_exposure | 0.19 | percent of average deployed capital |
| observed_gross_pnl | 5.18 | INR |
| observed_costs | 3,367.47 | INR |
| observed_net_pnl | -3,362.29 | INR |

Break-even win rate is computed from average gross wins, average absolute gross losses, and average cost. Required gross currency P&L per trade equals average cost; a price-move percentage cannot be derived without treating each trade's distinct exposure as identical.

Stage 9 baseline cost sensitivity, scaling the recorded estimated costs only:

| cost_multiplier | gross_pnl | costs | net_pnl |
| --- | --- | --- | --- |
| 1.00 | 5.18 | 3,367.47 | -3,362.29 |
| 1.50 | 5.18 | 5,051.21 | -5,046.03 |
| 2.00 | 5.18 | 6,734.94 | -6,729.76 |

This is arithmetic on the frozen existing trades and does not alter the production cost model. Brokerage and slippage are estimates; exact account charges, spreads, liquidity impact, and fill quality are unverified.

## Experiment protocol

Distinct variants defined: 15. Development evaluations logged: 15; finalist walk-forward fold evaluations: 20. The filter study defined 6 variants, including baseline, three standalone filters, one predeclared combined filter, and the second ATR threshold. No holdout trials were run.

Every variant was tested first on development only. For exit and filter families, the unchanged baseline plus at most one development-selected alternative proceeded to walk-forward; for the strategy-family comparison, at most one of the two alternatives proceeded. Selection required at least 10 development trades and used development net P&L only. No walk-forward result was used to modify a variant or select a different candidate.

Exit variants use the same frozen 5-minute reversal events, signal-time context veto, next-candle-open entry, risk rules, and cost model. Tested exits are 1% stop with no target at 6/12/24 bars, targets at 1.5R/2R/3R with six-bar horizon, and a one-R-activated 1% trailing stop with a 24-bar maximum.

Filters were predeclared as ATR(14)/price >=0.5%, ATR(14)/price >=1.0%, signals before 10:45, trailing 20-bar average turnover >=₹25 lakh, and the conjunction of the first ATR, time, and turnover tests. Rationales are movement relative to costs, opening-session liquidity, and basic capacity; thresholds are hypotheses, not optimized settings.

Timeframe/family variants are a 15-minute aggregation of the existing 5-minute bars using the frozen reversal definition, and a 5-minute opening-range breakout (first three bars define the range; first close above it signals; entry is next bar open). Both are intraday and use the existing intraday cost model. A daily delivery strategy was not prototyped because the repository has no verified delivery-fee schedule or corporate-action-adjusted data contract.

Walk-forward candidates had to have positive combined net P&L, at least three of four positive quarterly folds, and at least 30 accepted validation trades before candidate-specific robustness checks. The holdout is not evaluated even if a candidate clears that gate.

## Variant results

| category | variant | development_trades | development_net_pnl | walk_forward_finalist | walk_forward_folds_tested | walk_forward_trades | walk_forward_net_pnl | positive_walk_forward_folds | net_pnl_per_trade |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| exit | target_1_5R_h6 | 13 | -4,608.70 | True | 4 | 18 | -4,347.19 | 1 | -241.51 |
| exit | baseline_stop_1pct_h6 | 13 | -5,328.92 | True | 4 | 18 | -4,347.19 | 1 | -241.51 |
| exit | target_2R_h6 | 13 | -5,328.92 | False | 0 | 0 | 0.00 | 0 |  |
| exit | target_3R_h6 | 13 | -5,328.92 | False | 0 | 0 | 0.00 | 0 |  |
| exit | trailing_1R_activate_h24 | 13 | -6,744.18 | False | 0 | 0 | 0.00 | 0 |  |
| exit | baseline_stop_1pct_h12 | 13 | -7,596.75 | False | 0 | 0 | 0.00 | 0 |  |
| exit | baseline_stop_1pct_h24 | 13 | -8,402.13 | False | 0 | 0 | 0.00 | 0 |  |
| filter | first_90_minutes | 10 | -4,682.49 | True | 4 | 18 | -4,347.19 | 1 | -241.51 |
| filter | no_filter | 13 | -5,328.92 | True | 4 | 18 | -4,347.19 | 1 | -241.51 |
| filter | combined_predeclared_filters | 1 | -536.49 | False | 0 | 0 | 0.00 | 0 |  |
| filter | atr_at_least_1pct | 1 | -1,174.23 | False | 0 | 0 | 0.00 | 0 |  |
| filter | atr_at_least_0_5pct | 3 | -1,191.36 | False | 0 | 0 | 0.00 | 0 |  |
| filter | 20bar_turnover_at_least_25lakh | 13 | -5,328.92 | False | 0 | 0 | 0.00 | 0 |  |
| strategy | 15m_frozen_reversal | 12 | -4,152.74 | True | 4 | 12 | -6,105.93 | 0 | -508.83 |
| strategy | 5m_opening_range_breakout | 663 | -75,627.96 | False | 0 | 0 | 0.00 | 0 |  |

`exit_experiments.csv`, `filter_experiments.csv`, and `strategy_comparison.csv` contain the per-window metrics, including wins/losses, gross P&L, costs, net P&L, drawdown, and rejection counts. `trial_log.csv` shows each variant/window evaluation and marks holdout as not run.

## Robustness checks

No development-selected candidate met the preregistered walk-forward gate. Candidate-specific cost sensitivity, concentration, and bootstrap confidence intervals were therefore not run; doing them on a non-qualifying candidate would not establish robustness. Stage 9 baseline 1x/1.5x/2x cost arithmetic is included above.

## Limitations and assumptions

- The 2023–2025 source is not a new untouched dataset; prior validation and research artifacts exist for it. The search log makes this reuse visible.
- There is no unexposed final holdout in the repository. The previously reported Stage 9/10 OOS is embargoed from Stage 11 variant selection and scoring.
- Trade counts are small relative to the number of variants. Development selection is exploratory; walk-forward estimates remain noisy and multiple-comparison effects remain possible.
- Missing or irregular bars are handled by the existing historical loader and event builder. Indicators use only current and earlier bars; signal context is joined backward at signal time; no future bars are used to select entries.
- OHLC bars cannot identify intrabar stop/target order. When both stop and target are touched in one bar, the engine assumes the stop happened first. Trailing stops use the previous high-water mark, so they cannot move favorably using the current bar before its low is tested.
- The filters and opening-range breakout are predeclared research hypotheses, not production recommendations. No parameter changes were made to Stage 9/10.
- Daily delivery strategies and delivery charges were not modeled. Tested alternative timeframe/family strategies remain intraday so existing cost estimates are comparable, though those estimates still need broker-contract-note and execution validation.
- A future holdout should be collected after candidate definitions and execution assumptions are frozen, ideally prospectively, with no tuning after its results are opened.

## Run commands

From the `ai-tarding-assistant` directory:

```powershell
python -m pytest .\backtests\stage11_research\tests -q
python .\backtests\stage11_research\run_stage11_breakeven.py
python .\backtests\stage11_research\run_stage11_exit_experiments.py
python .\backtests\stage11_research\run_stage11_filter_experiments.py
python .\backtests\stage11_research\run_stage11_strategy_comparison.py
python .\backtests\stage11_research\run_stage11_robustness.py
python .\backtests\stage11_research\run_stage11_report.py
```

## Final conclusion

No candidate has been validated on an untouched holdout. The current Stage 9 candidate has a negative known OOS result after costs, and this research cannot establish that a replacement has a real cost-adjusted edge. Do not proceed to live trading; freeze any finalist and collect new untouched forward data.

**INCONCLUSIVE**
