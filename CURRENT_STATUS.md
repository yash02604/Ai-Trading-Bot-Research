# Current Project Status

Audit date: 2026-10-02

## Completed

- Retained the existing Upstox read-only data, OpenRouter context, quantitative signal, risk gate, and paper trading flow. Real order placement remains disabled; no orders were placed.
- Added a reproducible historical validation runner with structured JSON/CSV results, a detailed report, data-quality audit, per-symbol standalone results, calendar periods, and lagged market-regime summaries.
- Kept the existing sharp-reversal rule and risk settings frozen for this analysis. No strategy redesign, fitting, or parameter optimization was performed.
- Added explicit brokerage, statutory charges, and two-sided 5 bps slippage assumptions. The slippage assumption is illustrative, not measured.
- Improved missing-bar handling: same-session gaps invalidate affected signal windows and entry bars, while valid overnight rolling windows remain allowed. Entry uses the next consecutive five-minute candle.
- Added tests for causal indicators, fee calculations, net metrics, missing-bar behavior, and lagged regimes.

## Validated

- Local data: 14 configured Indian equities, 518,570 raw five-minute rows from 2023-12-01 through 2025-12-01; 518,567 valid rows retained. Three malformed OHLC rows excluded, four zero-volume rows retained, no duplicate symbol/timestamps, and 28 same-session gaps over five minutes.
- Frozen candidates: 58. The shared ₹100,000 simulated account accepted 46 entries and rejected 12 due to the one-position limit; no further data-gap exclusions occurred after continuity handling.
- Portfolio result before AI: 46 trades, 30.43% net win rate, gross P&L -₹2,514.17, estimated costs ₹7,994.62, net P&L -₹10,508.79 (-10.51%), net profit factor 0.375, and closed-trade drawdown -10.51%.
- Annual portfolio net P&L: 2023 -₹539.38 (2 trades), 2024 -₹5,246.69 (19), 2025 -₹4,722.71 (25).
- Lagged trend regimes: bearish 2 trades / -₹1,436.32; bullish 15 / -₹4,426.69; sideways 27 / -₹4,106.39; warmup 2 / -₹539.38. Volatility regimes: low 44 / -₹9,969.40; high 0 trades; warmup 2 / -₹539.38.
- Per-symbol standalone results are in the report and `metrics.csv`; these are separate counterfactual accounts and cannot be summed into the shared portfolio. Counts per stock are small.
- Historical AI decisions cannot be replayed without point-in-time prompts/responses; current-model replay would introduce lookahead. Quantitative results therefore do not represent AI-approved entries.
- Automated suite: 19 passed. `compileall` passed. Deterministic simulator completed its expected stop-loss scenario. Read-only Upstox historical endpoint returned 1,500 candles in a smoke check; current off-hours intraday request returned zero. OpenRouter synthetic-context smoke check returned HOLD. No credentials were printed.

## Not yet validated

- The 46 shared-account trades over roughly two years are insufficient to support a reliable performance conclusion. Several individual stocks have only one to ten standalone trades.
- Historical AI confirmation, full walk-forward/out-of-sample validation, longer paper trading, live fill quality, actual spreads, impact, and liquidity constraints remain unvalidated.
- Data corporate-action adjustments, survivorship bias, official market-index regimes, intratrade mark-to-market drawdown, and exact contract-note charges were not independently verified/modelled.
- OHLC bars do not reveal intrabar event order; stop and session-exit fills are simplified. The 5 bps-per-side slippage assumption is not calibrated.

## Blocked

- No unresolved code or test blocker was found for the local historical validation. Real trading remains unavailable and inappropriate to enable based on this evidence; the measured net result is negative and the sample is too small.
- Sustained provider availability and AI historical replay depend on external services/data that are not available as point-in-time records.

## Next step

- Treat the current outcome as **insufficient data for a performance conclusion**. Continue with independently sourced, longer-period, point-in-time data and forward paper tracking while keeping parameters frozen; only reconsider after costs, execution, and risk behavior are validated on an adequately sized untouched sample.

## Reproduction

From the outer workspace root in PowerShell:

```powershell
& .\.venv\Scripts\Activate.ps1
Set-Location .\ai-tarding-assistant
python run_historical_validation.py
python -m pytest -q
```

Full findings and limitations: [`VALIDATION_REPORT.md`](VALIDATION_REPORT.md). Machine-readable outputs are under `backtests/validation/`.

## FINAL STATUS

**Insufficient data for conclusion.** The validation is reproducible and the observed cost-adjusted result is negative; there is no evidence here supporting a profitability claim or live deployment.
