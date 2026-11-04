# Execution Cost Sensitivity Review

Source trade file: `C:\Users\Priyanka\Desktop\ai-trading-bot-research\.pytest-deepseek-full-final3\test_cost_sensitivity_separate0\trades.csv`.
Portfolio trades: 1; entry period: 2026-10-12T09:25:00+05:30 to 2026-10-12T09:25:00+05:30.
Observed quote sample: 0. Historical trades contain no bid/ask snapshots, so no spread was measured.
All spread scenarios below are explicit assumptions, not observed costs or executable-fill estimates.
The historical entry/exit references are OHLC values and the existing model does not include spread; therefore each assumed full spread is charged as half-spread on entry and half-spread on exit. No spread is added to a price already adjusted by an observed quote.

Existing modeled costs separate saved brokerage/statutory charges from the saved 5 bps/side slippage. Adverse scenarios change only slippage and/or add the disclosed spread allowance. The base cost model and historical outputs are unchanged.

Existing modeled gross P&L: ₹10.00; costs: ₹3.73; net P&L: ₹6.27; net profit factor: Infinity.

| Scenario | Slippage / side | Full spread | Brokerage | STT | Exchange | IPFT | SEBI | Stamp | GST | Slippage | Spread | Total costs | Net P&L | Net win rate | PF |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| existing_model | 5 bps | 0 bps | ₹2.01 | ₹0.25 | ₹0.06 | ₹0.00 | ₹0.00 | ₹0.03 | ₹0.37 | ₹1.00 | ₹0.00 | ₹3.73 | ₹6.27 | 100.00% | Infinity |
| higher_slippage | 10 bps | 0 bps | ₹2.01 | ₹0.25 | ₹0.06 | ₹0.00 | ₹0.00 | ₹0.03 | ₹0.37 | ₹2.01 | ₹0.00 | ₹4.74 | ₹5.26 | 100.00% | Infinity |
| assumed_10bps_spread | 5 bps | 10 bps | ₹2.01 | ₹0.25 | ₹0.06 | ₹0.00 | ₹0.00 | ₹0.03 | ₹0.37 | ₹1.00 | ₹1.00 | ₹4.74 | ₹5.26 | 100.00% | Infinity |
| assumed_20bps_spread | 5 bps | 20 bps | ₹2.01 | ₹0.25 | ₹0.06 | ₹0.00 | ₹0.00 | ₹0.03 | ₹0.37 | ₹1.00 | ₹2.01 | ₹5.74 | ₹4.26 | 100.00% | Infinity |
| combined_adverse | 10 bps | 10 bps | ₹2.01 | ₹0.25 | ₹0.06 | ₹0.00 | ₹0.00 | ₹0.03 | ₹0.37 | ₹2.01 | ₹1.00 | ₹5.74 | ₹4.26 | 100.00% | Infinity |

## Interpretation

These are sensitivity calculations on the existing 46-trade portfolio. They do not establish actual spread, quote freshness, fill probability, market impact, or profitability. Live quote collection is required for those measurements; the paper runner was not started for this report.

Detailed per-trade scenario values are in `trade_sensitivity.csv`; complete scenario values are in `scenario_summary.csv`.
