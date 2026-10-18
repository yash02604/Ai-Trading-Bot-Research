# Execution Cost Sensitivity Review

Source trade file: `C:\Users\Priyanka\Desktop\ai-trading-bot - Copy\ai-tarding-assistant\backtests\validation\trades.csv`.
Portfolio trades: 46; entry period: 2023-12-07T09:30:00+05:30 to 2025-11-07T09:30:00+05:30.
Observed quote sample: 0. Historical trades contain no bid/ask snapshots, so no spread was measured.
All spread scenarios below are explicit assumptions, not observed costs or executable-fill estimates.
The historical entry/exit references are OHLC values and the existing model does not include spread; therefore each assumed full spread is charged as half-spread on entry and half-spread on exit. No spread is added to a price already adjusted by an observed quote.

Existing modeled costs separate saved brokerage/statutory charges from the saved 5 bps/side slippage. Adverse scenarios change only slippage and/or add the disclosed spread allowance. The base cost model and historical outputs are unchanged.

Existing modeled gross P&L: -₹2,514.17; costs: ₹7,994.62; net P&L: -₹10,508.79; net profit factor: 0.3750523673243914.

| Scenario | Slippage / side | Full spread | Brokerage | STT | Exchange | IPFT | SEBI | Stamp | GST | Slippage | Spread | Total costs | Net P&L | Net win rate | PF |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| existing_model | 5 bps | 0 bps | ₹1,840.00 | ₹1,071.36 | ₹276.06 | ₹8.57 | ₹0.86 | ₹128.64 | ₹382.43 | ₹4,286.69 | ₹0.00 | ₹7,994.62 | -₹10,508.79 | 30.43% | 0.3750523673243914 |
| higher_slippage | 10 bps | 0 bps | ₹1,840.00 | ₹1,071.36 | ₹276.06 | ₹8.57 | ₹0.86 | ₹128.64 | ₹382.43 | ₹8,573.38 | ₹0.00 | ₹12,281.31 | -₹14,795.48 | 26.09% | 0.2582497495066107 |
| assumed_10bps_spread | 5 bps | 10 bps | ₹1,840.00 | ₹1,071.36 | ₹276.06 | ₹8.57 | ₹0.86 | ₹128.64 | ₹382.43 | ₹4,286.69 | ₹4,286.69 | ₹12,281.31 | -₹14,795.48 | 26.09% | 0.2582497495066108 |
| assumed_20bps_spread | 5 bps | 20 bps | ₹1,840.00 | ₹1,071.36 | ₹276.06 | ₹8.57 | ₹0.86 | ₹128.64 | ₹382.43 | ₹4,286.69 | ₹8,573.38 | ₹16,568.00 | -₹19,082.17 | 21.74% | 0.17723665401460842 |
| combined_adverse | 10 bps | 10 bps | ₹1,840.00 | ₹1,071.36 | ₹276.06 | ₹8.57 | ₹0.86 | ₹128.64 | ₹382.43 | ₹8,573.38 | ₹4,286.69 | ₹16,568.00 | -₹19,082.17 | 21.74% | 0.17723665401460842 |

## Interpretation

These are sensitivity calculations on the existing 46-trade portfolio. They do not establish actual spread, quote freshness, fill probability, market impact, or profitability. Live quote collection is required for those measurements; the paper runner was not started for this report.

Detailed per-trade scenario values are in `trade_sensitivity.csv`; complete scenario values are in `scenario_summary.csv`.
