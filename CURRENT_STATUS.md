# Current Project Status

## Provider-only smoke-mode update — 2026-10-28

- The session_02 DeepSeek call was correctly skipped because the final frozen candidate check returned `no frozen quantitative candidate`. At its 15:10 IST signal candle, the three-candle decline was +0.1813% and RSI(14) was 38.3604; the close-above-previous condition passed, but decline and RSI thresholds did not. RiskEngine itself allowed a trade; the frozen signal criteria were the blocker. The valid 72-candle RELIANCE snapshot provider request budget remains unused, while its one-call Upstox capture reservation is consumed. Session_02 ledgers and budgets were read only in this follow-up.
- Added an isolated `provider-smoke-test` mode for a user-supplied current snapshot. It sends a fixed connectivity/JSON acknowledgement request, accepts only `{"status":"ok"}`, writes to separate smoke budget/event artifacts, and never produces a trading decision, prospective observation, trade simulation, or performance evidence. It requires `--enable-provider-smoke-test` and `--allow-provider-requests`, checks `PAPER_TRADING`, and uses no Upstox fetch path.
- Prospective observations now distinguish provider configuration, request attempted, skip reason, and redacted response. An ineligible baseline records `provider_skip_reason=quantitative_baseline_ineligible` and does not reserve a provider request.
- Focused tests after these changes: **57 passed** using `.\.venv\Scripts\python.exe -m pytest tests\test_deepseek_prospective.py tests\test_deepseek_prospective_summary.py tests\test_deepseek_eval_v1.py tests\test_synthetic_provider_check.py -q --basetemp .pytest-deepseek-smoke-final`. `compileall` passed. The offline CLI completed with zero API requests; report: `backtests/deepseek_provider_smoke_offline_final_20261028/offline_synthetic_check_20261028T100352436512Z.json`. No live API call or order was made.
- Manually initiated smoke command and validation steps are in `docs/DEEPSEEK_PROSPECTIVE_RESEARCH_PLAN.md`. Historical evaluation remains blocked by the ten unresolved provenance findings.

## Prospective paired research update — 2026-10-28

- Added a read-only, opt-in Upstox V3 snapshot capture path with its own persistent capture-reservation cap and sanitized event/failure ledgers. The shared Upstox client retains bounded retries, so transient failures can create more HTTP attempts than reserved calls. It requires `--fetch-upstox --allow-market-data-requests`; this task did not fetch market data.
- Prospective eligible quantitative candidates now receive a paired future outcome even if the AI decision vetoes the candidate. The summary reports direction-label accuracy separately from simulated trade metrics for both strategies on matched timestamps, under six explicit cost assumption scenarios. Costs are estimates, not observed fills.
- Added direct summary tooling and documentation. The exact model request remains point-in-time only; future bars/outcomes are appended separately. Provider errors and invalid output remain fail-closed to HOLD. Order placement remains disabled.
- Latest synthetic offline report: `backtests/deepseek_prospective_preflight_20261028/offline_synthetic_check_20261028T094539030444Z.json`; status `COMPLETED`, all five cases `SYNTHETIC`, zero API requests, and broker orders disabled. No prospective observation or historical evaluation was created.
- Verification on 2026-10-28: focused prospective/evaluator/provider suite **45 passed**; full offline suite **364 passed**; compilation passed. Commands: `.\.venv\Scripts\python.exe -m pytest tests\test_deepseek_prospective.py tests\test_deepseek_prospective_summary.py tests\test_deepseek_eval_v1.py tests\test_synthetic_provider_check.py -q --basetemp .pytest-deepseek-focus-final3`, `.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-deepseek-full-final3`, `.\.venv\Scripts\python.exe -m compileall -q app scripts tests backtester.py`, and `.\.venv\Scripts\python.exe scripts\run_deepseek_prospective_research.py --mode offline-test --output backtests\deepseek_prospective_research_v1_offline_final`.
- Historical evaluation remains blocked: 10 provenance findings are unresolved. No profitability evidence is established; directional accuracy and small prospective samples cannot support a profitability claim.
- Final preflight on 2026-10-28: prospective/evaluator/provider tests **49 passed**; offline synthetic CLI completed with five synthetic cases and zero API requests; `compileall` passed. The full suite was not rerun in this preflight; its last result (**364 passed**) predates these preflight safeguards. The already-used `backtests/deepseek_prospective_session_01` has one of one market-data capture reservations used; the documented manual smoke-test command targets the currently unused `session_02` directory.
- Controlled prospective command (runs only when explicitly executed, during an authorized market session, with configured credentials; it reserves one Upstox capture call (the Upstox HTTP client may retry transient failures) and up to one DeepSeek request if baseline-eligible): `.\.venv\Scripts\python.exe scripts\run_deepseek_prospective_research.py --mode prospective-paper --enable-prospective --allow-provider-requests --fetch-upstox --symbol RELIANCE --allow-market-data-requests --output backtests\deepseek_prospective_session_02 --max-market-data-requests 1 --max-total-requests 1`. Keep the output directory and its persistent budget/cache ledgers intact. Summarize later with `.\.venv\Scripts\python.exe scripts\summarize_deepseek_prospective.py --output backtests\deepseek_prospective_session_02`.

Historical status snapshot date: 2026-10-02. See the current verification update below.

## Current verification update — 2026-10-28

- The complete offline suite most recently passed **359 tests** after replay-freshness and cost-model audit changes; do not use the older 19-test count below as current. The reviewed virtual environment is Python 3.13.2 with pip 24.3.1. `requirements.txt` remains unpinned, and a clean-environment dependency install was not verified.
- The provenance audit summary currently records 18 findings: 5 `VERIFIED_VALID`, 3 `VERIFIED_INVALID`, and 10 `UNRESOLVED`. The exact historical main-candle file binding is `VERIFIED` for byte identity only; overall dataset status remains `UNRESOLVED` and `historical_live_permitted` is false.
- The historical metrics below are the report's 2026-10-02 results, not independently reproduced by this audit. No historical-live DeepSeek evaluation was run.
- The prospective offline synthetic matrix is labeled `SYNTHETIC` and reports zero provider requests. No prospective API observation was created in this audit. A current Upstox snapshot was unavailable at the time of review, so prospective snapshot and quantitative-baseline readiness remain unverified.
- `scripts/run_acquisition_pilot_v1.py` exists and is fail-closed; it is not yet authorized to run. Source-use, raw-retention, request-limit, and dated official instrument-master evidence remain unresolved as described in the acquisition gate documents.
- The offline recorded-decision evaluator now allows up to 600 seconds from a START label (five minutes for bar formation plus a bounded five-minute delay). The shared/live `RiskLimits` default remains 300 seconds after an automatic review rejected widening that global safety limit; the AI research service also remains strict by default, so delayed live decisions can fail closed to HOLD.
- Real broker-order placement remains disabled and no orders were placed. No strategy profitability claim is supported.
- Cost-model review found `sebi_rate` was `0.0000001` despite the code comment specifying ₹10/crore. It is now `0.000001` (₹10/crore), consistent with the current Upstox charge schedule. Historical artifacts were not recalculated; their cost estimates predate this correction and remain subject to the unresolved provenance gate. The GST base was left unchanged because broker and exchange documentation differ in presentation and no date-specific contract note was established for this modeled dataset.
- Cost-model audit follow-up: transaction and IPFT schedules are now date-dependent where source evidence supports them. The calculator fails closed for NSE cash intraday transaction dates before 2024-04-01 because this account's historical Upstox client rate is not established. The saved 46-trade portfolio was repriced only as an explicit 18-scenario cost sensitivity in `backtests/cost_model_audit_v2/`; it is not a new validation run. The illustrative center scenario gives ₹7,985.88 costs / -₹10,500.05 net versus saved ₹7,994.62 / -₹10,508.79; this is not a verified corrected baseline. Historical brokerage tariff, GST-on-SEBI treatment, and fill slippage remain unresolved. Input hashes are in that directory's `input_hashes.json`.
- NSE rate findings: the Upstox schedule supports 0.00322% per side for 2024-04-01–2024-09-30 and 0.00297% from 2024-10-01; the 2026-03-01 NSE schedule is split into ₹306.99/crore transaction charge plus ₹0.01/crore IPFT to avoid double-counting the ₹307/crore total. NSE IPFT from 2023-04-01 through 2026-02-28 is ₹10/crore. Pre-April-2024 client-rate scenarios are labeled proxies only. No historical validation or DeepSeek evaluation was run, and the 10 provenance findings remain unresolved.
- Cost audit verification on 2026-10-28: focused cost/historical/V2-evaluation tests **51 passed**; full offline suite **359 passed**; `compileall` passed. Commands: `.\.venv\Scripts\python.exe -m pytest tests\test_cost_model_execution_integrity.py tests\test_historical_validation.py tests\test_cost_model_audit.py tests\test_ai_v2_evaluation.py -q --basetemp .pytest-cost-focus-final`, `.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-cost-full-final`, and `.\.venv\Scripts\python.exe -m compileall -q app scripts tests backtester.py`. The cost audit is reproducible with `.\.venv\Scripts\python.exe -m scripts.run_cost_model_audit --trades backtests\validation\trades.csv --output backtests\cost_model_audit_v2`.

### Current audit commands

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_ai_v2_evaluation.py tests\test_ai_led_research.py tests\test_synthetic_provider_check.py -q --basetemp .pytest-p1fix
.\.venv\Scripts\python.exe -m pytest -q --basetemp .pytest-final2
.\.venv\Scripts\python.exe -m compileall -q app scripts tests backtester.py
```

Results: focused related AI/replay tests **70 passed**; full suite **348 passed**; Python compilation **passed**.
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

From the repository root in PowerShell, the safe reproduction commands are:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m scripts.run_cost_model_audit --trades backtests\validation\trades.csv --output backtests\cost_model_audit_v2
.\.venv\Scripts\python.exe -m compileall -q app scripts tests backtester.py
```

Full findings and limitations: [`VALIDATION_REPORT.md`](VALIDATION_REPORT.md). Machine-readable outputs are under `backtests/validation/`.
Do not rerun the historical evaluator until the provenance gate and historical cost schedule gaps are resolved.

## FINAL STATUS

**Insufficient data for conclusion.** The prior validation artifact has not been reproduced with the date-aware cost model. Its source data remains unresolved, and no profitability claim or live deployment is supported.
