# Stage 10 — Final OOS Robustness Analysis

**Verdict: NOT ROBUST — RETURN TO RESEARCH**

The frozen candidate does not show enough evidence of robust positive expectancy to proceed to Stage 11 paper trading as a validation candidate. This is a conclusion about this candidate and this small OOS sample, not a conclusion that the broader project has failed.

## Integrity checks

All checks passed before analysis:
- Stage 9 trades: 19; Stage 10 analysis trades: 19.
- Frozen diagnostic events: 37; accepted diagnostic matches: 19/19.
- No duplicate normalized keys in trades, diagnostics, or veto records; no missing or extra accepted-trade keys.
- Stage 9 net P&L: INR -3,362.29; metrics and Stage 10A accepted outcomes reconcile within 0.01.
- Trade order is chronological by signal timestamp; every entry follows its signal and every exit is at or after entry.
- Context attribution uses the existing signal-time down-count and unchanged 3-of-4 veto; no entry-time context or post-entry information was used for filtering.
- No strategy parameters, source trades, market data, risk rules, or OOS inputs were changed.

## Overall robustness

There were 19 trades: 9 wins and 10 losses (47.37% win rate). Gross P&L was INR 5.18, versus INR 3,367.47 of costs, leaving net P&L of INR -3,362.29. Gross winners totaled INR 3,917.30 and gross losers INR -3,912.12; gross expectancy was only INR 0.27 per trade, effectively flat in this sample, while net expectancy was INR -176.96 per trade.

| metric | value |
| --- | --- |
| trade_count | 19 |
| wins | 9 |
| losses | 10 |
| win_rate_pct | 47.37 |
| gross_pnl | 5.18 |
| total_costs | 3,367.47 |
| net_pnl | -3,362.29 |
| net_return_pct | -3.36 |
| average_trade | -176.96 |
| median_trade | -70.87 |
| profit_factor_net | 0.39 |
| profit_factor_gross | 1.00 |
| average_winner | 239.65 |
| average_loser | -551.91 |
| median_winner | 158.11 |
| median_loser | -556.22 |
| average_winner_to_average_loser_abs_ratio | 0.43 |
| maximum_drawdown_inr | 4,035.13 |
| maximum_drawdown_pct | -4.04 |
| maximum_consecutive_losses | 3 |
| best_trade_net_pnl | 472.59 |
| best_trade_symbol | TITAN |
| worst_trade_net_pnl | -1,172.75 |
| worst_trade_symbol | ITC |

Net average winner / absolute average loser is 0.434; average winners were INR 239.65 versus average losses of INR -551.91. Median winner/loser were INR 158.11 / INR -556.22. Average costs were INR 177.24 per trade. The gross profit factor is only marginally above 1, while the net profit factor is below 1.

## Time stability

Trades were split into equal chronological halves (10 then 9 trades) because monthly samples are too sparse for stable monthly comparisons. Results were mixed; this split is descriptive and not selected for favorable performance. The first half lost INR 3,486.88, while the second was near flat at INR 124.59; the aggregate loss is therefore concentrated in the first half, but the small counts cannot establish persistent time dependence.

| period | start_signal | end_signal | trades | wins | losses | win_rate_pct | gross_pnl | costs | net_pnl | average_trade |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| First chronological half | 2025-12-02T09:20:00+05:30 | 2026-03-30T09:20:00+05:30 | 10 | 4 | 6 | 40.00 | -1,702.62 | 1,784.26 | -3,486.88 | -348.69 |
| Second chronological half | 2026-04-13T09:20:00+05:30 | 2026-06-30T09:35:00+05:30 | 9 | 5 | 4 | 55.56 | 1,707.80 | 1,583.21 | 124.59 | 13.84 |

The two periods should not be interpreted as independent validation sets; 19 observations remain a small sample.

## Symbol concentration

| symbol | trades | wins | losses | win_rate_pct | gross_pnl | costs | net_pnl | average_trade |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TITAN | 3.00 | 3.00 | 0.00 | 100.00 | 1,464.30 | 529.28 | 935.02 | 311.67 |
| TCS | 5.00 | 3.00 | 2.00 | 60.00 | 1,493.00 | 885.78 | 607.22 | 121.44 |
| SBIN | 1.00 | 1.00 | 0.00 | 100.00 | 336.00 | 177.89 | 158.11 | 158.11 |
| AXISBANK | 1.00 | 1.00 | 0.00 | 100.00 | 316.80 | 177.04 | 139.76 | 139.76 |
| HDFCBANK | 1.00 | 0.00 | 1.00 | 0.00 | -150.00 | 181.65 | -331.65 | -331.65 |
| BHARTIARTL | 1.00 | 0.00 | 1.00 | 0.00 | -431.20 | 179.98 | -611.18 | -611.18 |
| MARUTI | 1.00 | 0.00 | 1.00 | 0.00 | -483.00 | 172.94 | -655.94 | -655.94 |
| LT | 3.00 | 1.00 | 2.00 | 33.33 | -420.00 | 525.61 | -945.61 | -315.20 |
| ITC | 1.00 | 0.00 | 1.00 | 0.00 | -991.62 | 181.13 | -1,172.75 | -1,172.75 |
| ICICIBANK | 2.00 | 0.00 | 2.00 | 0.00 | -1,129.10 | 356.19 | -1,485.29 | -742.64 |

Total portfolio net P&L was INR -3,362.29. Best five cumulative net P&L was INR 1,595.79 (-47.46% of total net P&L; 20.79% of absolute trade P&L). Worst five was INR -3,925.16 (116.74% of total net P&L; 51.14% of absolute trade P&L). Removing the worst single trade would still leave INR -2,189.54; removing the worst two would still leave INR -1,263.46. Thus, losses are concentrated in the five worst trades, but not explained by only one or two trades.

| scenario | excluded_symbols | net_pnl |
| --- | --- | --- |
| Exclude best symbol | TITAN | -4,297.32 |
| Exclude worst symbol | ICICIBANK | -1,877.01 |
| Exclude top 2 winning symbols | TITAN, TCS | -4,904.54 |
| Exclude top 2 losing symbols | ITC, ICICIBANK | -704.25 |

These exclusion calculations are counterfactual concentration diagnostics only; no symbols are removed or recommended for removal.

## Trade concentration

| group | rank | symbol | signal_timestamp | gross_pnl | total_cost | net_pnl | cumulative_group_net_pnl | share_of_portfolio_net_pnl_pct | share_of_absolute_trade_pnl_pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Best 5 | 1 | TITAN | 2026-06-11T09:20:00+05:30 | 650.40 | 177.81 | 472.59 | 472.59 | -14.06 | 6.16 |
| Best 5 | 2 | TITAN | 2026-04-23T09:20:00+05:30 | 501.90 | 173.44 | 328.46 | 801.05 | -9.77 | 4.28 |
| Best 5 | 3 | TCS | 2026-03-02T09:20:00+05:30 | 499.50 | 178.34 | 321.16 | 1,122.21 | -9.55 | 4.18 |
| Best 5 | 4 | TCS | 2026-06-30T09:35:00+05:30 | 493.50 | 178.03 | 315.47 | 1,437.68 | -9.38 | 4.11 |
| Best 5 | 5 | SBIN | 2026-03-30T09:20:00+05:30 | 336.00 | 177.89 | 158.11 | 1,595.79 | -4.70 | 2.06 |
| Worst 5 | 1 | ITC | 2026-02-01T12:40:00+05:30 | -991.62 | 181.13 | -1,172.75 | -1,172.75 | 34.88 | 15.28 |
| Worst 5 | 2 | ICICIBANK | 2026-03-19T09:20:00+05:30 | -746.90 | 179.18 | -926.08 | -2,098.83 | 27.54 | 12.06 |
| Worst 5 | 3 | MARUTI | 2026-03-12T09:20:00+05:30 | -483.00 | 172.94 | -655.94 | -2,754.77 | 19.51 | 8.55 |
| Worst 5 | 4 | BHARTIARTL | 2026-01-16T09:25:00+05:30 | -431.20 | 179.98 | -611.18 | -3,365.96 | 18.18 | 7.96 |
| Worst 5 | 5 | ICICIBANK | 2026-03-23T09:25:00+05:30 | -382.20 | 177.00 | -559.20 | -3,925.16 | 16.63 | 7.29 |

Contribution as a percentage of total net P&L is an arithmetic ratio against a negative portfolio result, so winning trades can show negative percentages and losing trades positive percentages. The absolute-P&L share is available in trade_concentration.csv for a non-signed concentration measure.

## Cost sensitivity

| scenario | net_pnl | assumption |
| --- | --- | --- |
| Actual net P&L | -3,362.29 | Observed Stage 9 economics |
| Gross P&L before costs | 5.18 | Sum of realized gross trade P&L |
| Net P&L excluding slippage | -1,543.69 | Actual net plus all recorded slippage |
| Net P&L at 50% slippage | -2,452.99 | All non-slippage costs unchanged |
| Net P&L at 2x slippage | -5,180.90 | All non-slippage costs unchanged |

The strategy is approximately flat before costs (gross INR 5.18) and becomes negative after costs. Recorded slippage was INR 1,818.61; other costs were INR 1,548.87. Removing all recorded slippage still leaves INR -1,543.69 net, so slippage alone does not explain the loss; other estimated costs also matter. This sensitivity changes no production assumptions.

## Winner and loser quality

| group | trades | gross_pnl | costs | net_pnl | average_net_pnl | median_net_pnl | average_mfe_pct | median_mfe_pct | average_mae_pct | median_mae_pct | average_r_multiple | median_r_multiple | average_holding_minutes | median_holding_minutes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| LOSS | 10 | -3,746.92 | 1,772.18 | -5,519.10 | -551.91 | -556.22 | 0.22 | 0.22 | -0.61 | -0.58 | -0.57 | -0.59 | 21.00 | 25.00 |
| WIN | 9 | 3,752.10 | 1,595.29 | 2,156.81 | 239.65 | 158.11 | 0.61 | 0.59 | -0.25 | -0.29 | 0.25 | 0.16 | 25.00 | 25.00 |

| exit_reason | trades | wins | losses | gross_pnl | costs | net_pnl | average_net_pnl | average_holding_minutes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| horizon_exit | 17 | 9 | 8 | 891.80 | 3,010.47 | -2,118.67 | -124.63 | 25.00 |
| session_exit | 1 | 0 | 1 | 105.00 | 175.87 | -70.87 | -70.87 | 10.00 |
| stop_exit | 1 | 0 | 1 | -991.62 | 181.13 | -1,172.75 | -1,172.75 | 0.00 |

Average MFE was 0.607% for winners and 0.223% for losers; average MAE was -0.251% and -0.609%, respectively. The winner/loser size imbalance is unfavorable. MFE/MAE and exit fields describe realized trade paths and were used only for post-trade analysis, never as entry filters.

## Market-context robustness

| indices_down | signals | vetoed_signals | context_passed_signals | accepted_trades | risk_rejected_signals | wins | losses | net_pnl |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 6 | 0 | 6 | 3 | 3 | 0 | 3 | -2,096.47 |
| 1 | 16 | 0 | 16 | 14 | 2 | 7 | 7 | -1,896.53 |
| 2 | 4 | 0 | 4 | 2 | 2 | 2 | 0 | 630.71 |
| 3 | 5 | 5 | 0 | 0 | 0 | 0 | 0 | 0.00 |
| 4 | 6 | 6 | 0 | 0 | 0 | 0 | 0 | 0.00 |

There are no accepted trades at 3 or 4 indices down because the frozen veto rejects those signals; their vetoed counts remain visible above. Outcomes for those vetoed events cannot establish what hypothetical trades would have done, and no alternative veto threshold is inferred.

Baseline A had 29 accepted trades, net P&L INR -4,233.36, 44.83% win rate, and PF 0.4421. Stage 9 had 10 fewer trades, reduced the absolute loss by INR 871.07, and improved win rate by 2.54 percentage points, but its PF was lower by 0.0513 (0.3908 versus 0.4421). Stage 9 remains unprofitable; the comparison is not evidence of profitability.

## Bootstrap diagnostic

| trade_count | iterations | random_seed | probability_positive_pct | median_cumulative_pnl | percentile_5_cumulative_pnl | percentile_95_cumulative_pnl | mean_cumulative_pnl |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 19 | 100000 | 20261007 | 4.65 | -3,314.40 | -6,748.00 | -63.10 | -3,353.19 |

This resamples the 19 realized net trade outcomes with replacement, using a fixed seed. It assumes the observed trade outcomes are representative and ignores temporal dependence, market-regime shifts, and strategy selection. With only 19 trades, it is descriptive and must not be treated as statistical significance or a forecast.

## Drawdown and equity curve

Starting capital was INR 100,000.00; ending equity was INR 96,637.71. Maximum peak-to-trough drawdown was INR 4,035.13 (-4.035%), from STARTING_CAPITAL to 2026-06-08T09:20:00+05:30. Recovery status: Not recovered by final trade. The longest losing sequence was 3 trades.

Equity is marked after each chronological accepted trade, including initial capital in the running high-water mark. No open or rejected signal is simulated.

## Interpretation and limitations

Observed evidence is weak: negative net expectancy, a near-zero gross P&L, costs exceeding gross gains, a net profit factor of about 0.39, and average losses substantially larger than average wins. Both symbol and period results vary, but small group counts prevent reliable attribution to one symbol or time period. The negative result is not explained by one or two trades or one symbol; the five largest losses dominate the total, and the worst two-symbol exclusion remains negative. The full sample has 10 losing trades and total gross loss slightly exceeds gross profit before costs.

The evidence points to a combination of a near-zero gross edge, winners materially smaller than losers, and high estimated trading costs. MFE/MAE are consistent with weaker favorable excursion and larger adverse excursion among losing outcomes, but realized trades alone cannot isolate entry quality as the cause. The 19 accepted trades are too few to establish stability, estimate tails reliably, or prove any single failure mechanism. Results do not justify parameter changes in this stage.

**Decision: NOT ROBUST — RETURN TO RESEARCH. Do not proceed to Stage 11 paper trading as a candidate-validation step; keep this frozen candidate research-only pending a separately specified research decision.**
