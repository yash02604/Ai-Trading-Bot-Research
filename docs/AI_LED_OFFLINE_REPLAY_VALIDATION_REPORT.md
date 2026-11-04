# Existing Replay Capabilities

The foundation already accepted a complete, synchronized instrument batch, ranked independent AI proposals deterministically, applied RiskEngine and the cost-aware gate, validated the actual entry, monitored stops/targets/horizons, and atomically persisted a shared paper account. It supported individual-batch resume and idempotent decisions. Its 245 existing tests were retained.

This work reuses that account implementation, `app/risk_engine.py`, `app/cost_model.py`, prospective snapshot/candle validation, the existing directory lock, and the offline NSE session-manifest validator. It does not implement another position-sizing engine or replace the quantitative strategy.

# Gaps Found

- No chronological replay-plan runner existed for multiple batches or sessions.
- The foundation's weekday/clock checks did not establish exchange holidays or special-session intervals.
- Saved AI responses had no explicit distinction between mock decisions and asserted historical recordings.
- There was no replay-specific immutable input binding or performance report.
- The checked-in NSE calendar ends on **2026-10-01**, so it cannot validate current dates in late October 2026.
- An existing provenance regression asserts an older hash of `app/upstox_client.py`; that client was already modified before this task.

# Implementation Changes

Added an offline replay orchestrator that reads a declared chronological plan, advances the existing shared account, and returns a pure performance summary. Steps must be strictly increasing; no implicit sorting repairs a bad plan. Instrument submission order is irrelevant, while the predeclared instrument priority remains the tie-breaker after AI score.

The plan freezes capital, model, instrument priority, calendar identity, START candle-label convention, evidence classification, and hashes of all snapshots and saved responses. The account checks the bytes actually loaded against these frozen hashes before processing. Changing a saved response, input file, calendar, capital, priority, or relevant implementation makes resume fail closed. Complete plans can be replayed or resumed without creating duplicate decisions or outcomes.

An optional calendar argument was added to the existing account engine. The replay CLI always supplies an explicit calendar; legacy single-batch callers retain their prior clock behavior. Calendar-aware entry and settlement use the declared interval end, including special sessions, instead of a hard-coded regular close. Existing source-hash freezing means older experiment files are not migrated or rewritten: updated implementation requires a fresh output directory.

Three evidence categories are kept distinct:

1. `SYNTHETIC_MOCK`: artificial or saved data with mocked AI proposals. This is execution validation only.
2. `RECORDED_HISTORICAL_ASSERTED`: supplied original recording artifacts must bind the exact request hash, response hash, model, and historical decision timestamp. A response recorded today cannot pass as a decision at an older replay clock. This checks lineage, **not independent timestamp authenticity**; reports retain that warning. No genuine historical AI performance dataset was supplied or evaluated in this task.
3. Future prospective AI decisions are outside this runner. It accepts only the existing exact saved-response provider class; there is no network provider adapter or future-prospective evidence mode.

Unknown snapshot metadata is still excluded from AI inputs. Requests contain only the current step's validated market information, with every START-labeled candle completed by the simulation decision time. Later candles are used when later steps advance the position. The plan's future snapshots and responses are not included in the AI request.

# Replay Test Scenarios

The final replay suite has **26 passing test cases**, including parameterized capital and invalid-data cases. It covers:

- Multiple eligible stocks at the same timestamp, fixed configured priority, reversed submission order, and identical repeated-run account bytes.
- Complete-batch identity, missing instruments, duplicate instruments, stale data, future bars, out-of-order steps, and missing candles.
- Point-in-time request contents and immutable saved input/response binding.
- No-trade and invalid AI output, unaffordable proposals, and distinct proposal-versus-actual-entry rejection counts.
- Stop exits, modeled costs and slippage, successive losing/winning trades, cash after losses, and two intraday sessions without an account reset.
- Open-position blocking, partial restart, full-plan resume, and rejection of duplicate settlement records.
- Existing position preservation on missing continuation or overnight attempts, with no invented exit.
- Explicit regular closes, holidays, special sessions, split-session gaps, and absent calendar coverage.
- Request-budget isolation, existing-directory refusal, CLI operation, and saved historical evidence binding.

The retained foundation and regression suites additionally exercise gap-through-stop fills, actual-entry rejection clearing, zero-share sizing, malformed fees, per-trade and daily risk controls, quota exhaustion, atomic write failure, and secret-safe response rejection. Network socket connections are prohibited in the new replay tests and in the synthetic artifact-generation run.

# Capital and Risk Verification

Separate accounts were tested and replayed at **INR 1,000, 2,500, 10,000, and 100,000**. Each account shares its funding across the instrument universe; capital is not allocated independently to each stock.

The existing risk percentages remain 1% per trade and 2% daily loss. RiskEngine determines whole-share maximum quantity using the smaller of usable cash and initial capital. The cost gate can reject that quantity and never increases it. Both reference and actual entry must satisfy:

```text
entry notional + estimated buy-side charges <= usable funding
estimated stop loss + modeled round-trip costs <= initial capital * 1%
```

Pending/open positions still block an additional position. Only accepted entries count toward the daily trade quota. Rejected entries consume no accepted-trade slot. Settled net P&L changes the account balance; usable funding remains capped at the experiment's original capital, preserving the foundation's funding rule. Profit retained above the funding cap remains visible in account equity. Neither a stop nor a daily loss check guarantees maximum realized loss through a gap.

# Calendar and Data-Integrity Verification

Replay uses `data/audits/nse_cm_session_manifest_v2.json` through the existing schema validator. The new adapter checks date verification labels, source references and effective dates, and explicit normal-market intervals. It reads local evidence only and does not claim new online verification.

- The recorded **2024-11-20 holiday** rejects candles and trading.
- The recorded **2024-11-01 Muhurat session** recognizes 18:00-19:00; regular daytime hours are not substituted.
- The recorded **2024-03-02 split session** rejects the interval between its two open periods.
- A clearly marked **synthetic Saturday evening calendar fixture** proves that an explicitly configured special session can be replayed outside regular hours.
- The real one-hour Muhurat session cannot meet the unchanged 35-bar same-session requirement; it is rejected rather than reducing that requirement.
- Missing calendar dates, out-of-session candles, missing continuation bars, and accepted positions crossing an unobserved overnight boundary fail closed.
- An observed final session candle can settle a position at its close; a missing final candle never generates a fabricated close.

Saved market data remains unauthenticated unless independently established elsewhere. The synthetic fixtures explicitly use START labels; a saved-data replay must explicitly declare START semantics, and that declaration alone does not authenticate a provider's historical timestamp convention. No historical datasets, provenance gates, or existing audit reports were modified.

# Performance Report

These numbers come from a **predeclared synthetic execution fixture**, with artificial prices scaled to exercise the same one-share lifecycle at each capital level. They are not real market prices, genuine historical AI decisions, or evidence of profitability. The four accounts replay the same scenario; they are not four independent predictive samples.

Each account accepted and settled **3 trades: 2 net wins and 1 net loss**, giving a mechanically constructed 66.67% win rate. There were zero rejected actual entries, zero rejected proposals, and no unresolved final position in this particular fixture. Rejection behavior is covered by separate tests. All monetary amounts below are INR, rounded for display; saved JSON retains full precision.

| Initial capital | Gross P&L | Transaction costs | Net P&L | Ending available cash | Ending equity |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1,000 | 18.00 | 6.72 | 11.28 | 1,000.00 | 1,011.28 |
| 2,500 | 45.00 | 16.80 | 28.20 | 2,500.00 | 2,528.20 |
| 10,000 | 180.00 | 67.21 | 112.79 | 10,000.00 | 10,112.79 |
| 100,000 | 1,800.00 | 386.82 | 1,413.18 | 100,000.00 | 101,413.18 |

| Initial capital | Average net expectancy/trade | Net profit factor | Sampled maximum drawdown |
| ---: | ---: | ---: | ---: |
| 1,000 | 3.76 | 2.3726 | 8.22 |
| 2,500 | 9.40 | 2.3726 | 20.54 |
| 10,000 | 37.60 | 2.3726 | 82.17 |
| 100,000 | 471.06 | 2.9412 | 727.99 |

The larger account has different relative costs because the existing model caps brokerage per order. Profit factor uses completed net winning P&L divided by absolute completed net losing P&L. It is unavailable when there are no losing trades; no-trade win rate and expectancy are likewise unavailable. Drawdown samples decision-clock equity, including estimated liquidation costs for an open position. It does not measure intrabar worst-case drawdown.

The bundle is `reports/ai_led_replay_validation_20261030/`. It contains explicitly named synthetic snapshots, mock responses, four frozen plans, four account states, four JSON summaries, and an `artifacts_manifest.json` listing paths and SHA-256 hashes. There are **65 files**, including four disposable directory-lock files. Each account was replayed twice; the second run produced identical metrics and unchanged state bytes.

# Tests Passed and Failed

The expanded regression command was:

```powershell
.venv\Scripts\python.exe -B -m pytest tests/test_ai_led_replay.py tests/test_ai_led_offline.py tests/test_deepseek_prospective.py tests/test_deepseek_prospective_summary.py tests/test_cost_model_execution_integrity.py tests/test_core.py tests/test_deepseek_eval_v1.py tests/test_ai_v2.py tests/test_ai_v2_evaluation.py tests/test_offline_market_data_v1.py -q -p no:cacheprovider --basetemp C:\Users\Priyanka\.codex\visualizations\2026\10\30\01a18b43-af58-74b1-ad68-35a667601200\.pytest-ai-replay-regression
```

Result: **290 passed, 1 failed in 59.23 seconds**. The failure was `tests/test_offline_market_data_v1.py::test_manifest_builder_preserves_v1_and_prior_artifacts`, at its initial preservation assertion (line 96). Its recorded audit expects `app/upstox_client.py` hash `59cfb331c2e1c8bece64ff0c256a7f374698b6e1692b3ea5609f7735883e5ac3`; the current already-modified client has hash `27ccb06e8777296edfbb2ebc56153dac291e4111d311fa424cc41813e03d9ccb`. This task did not change that client or its audit. The failure was not fixed, hidden, or converted into a passing assertion.

The same command with `-k "not test_manifest_builder_preserves_v1_and_prior_artifacts"` and scratch suffix `.pytest-ai-replay-final` returned **290 passed, 1 deselected in 53.47 seconds**.

After adding the final two-session replay test, the final focused command was:

```powershell
.venv\Scripts\python.exe -B -m pytest tests/test_ai_led_replay.py -q -p no:cacheprovider --basetemp C:\Users\Priyanka\.codex\visualizations\2026\10\30\01a18b43-af58-74b1-ad68-35a667601200\.pytest-ai-replay-focused-final
```

Result: **26 passed in 45.33 seconds**. There are no known new test failures. The broad regression result and the later focused result overlap; they must not be added together as independent test counts.

All five affected Python files passed in-memory Python compilation. Targeted Git whitespace checks and full task diffs were inspected. These source files were already untracked or newly added in this working tree, so `git diff --no-index` against the saved pre-task source or an empty file was used to review their actual contents. Its exit 1 denotes differences; whitespace diagnostics were required to be empty. Unrelated repository changes were preserved.

# Files Changed

Modified:

- `app/research/ai_led_offline.py`: optional calendar/replay context, input and historical-evidence binding, interval-aware closes, and sampled equity recording. Position sizing, costs, strategy predicates, cash reconciliation, actual-entry checks, locking, and event deduplication were reused.

Added:

- `app/research/ai_led_replay.py`
- `app/research/offline_session_calendar.py`
- `scripts/replay_ai_led_offline.py`
- `tests/test_ai_led_replay.py`
- `docs/AI_LED_OFFLINE_REPLAY_VALIDATION_REPORT.md`
- `reports/ai_led_replay_validation_20261030/` (exact generated file list in `artifacts_manifest.json`).

The original foundation CLI, foundation tests, and foundation report were not edited. SHA-256 checks against pre-task baselines verify all **22 Session 02-05 files**, including budgets, unchanged. No Session 06, provider request, market-data capture, broker order, or historical-live evaluation was started.

# Remaining Risks

- The historical Upstox-client provenance hash mismatch remains unresolved. The original audit must remain intact; a reviewed, separately versioned provenance audit is needed before relying on that historical gate.
- Calendar evidence is local and only covers dates through 2026-10-01. Current dates require reviewed additional coverage.
- Saved historical AI record metadata is not independently signed or timestamp-authenticated. Correct hashes establish matching artifacts, not proof that a person did not fabricate an old recording.
- OHLCV turnover remains a coarse liquidity filter; bid/ask spread, depth, executable volume, and broker fills are unknown.
- Small synthetic samples cannot establish expectancy, profitability, or model skill. No tuning or historical AI inference was performed for these results.
- Full universe batches and the 35-bar contiguous same-session window are still required. Short special sessions remain ineligible. Missing data can leave a position unresolved and halt replay until valid continuation is available.
- Atomic state files and hashes protect cooperative operation and accidental corruption, not malicious rewriting by an external process. Large replay histories still need a reviewed archival design.

# DeepSeek Flash Prospective Readiness

**NO-GO for real provider requests in this task.** The saved-response replay and execution mechanics pass their offline tests. Actual DeepSeek response timing, authenticated original recordings, current calendar coverage, prospective data availability semantics, and current-market liquidity have not been validated. Mock choices are not evidence that DeepSeek can select profitable trades.

# Broker Sandbox Readiness

**NO-GO.** There is no broker executor in this pipeline. Broker funds and position reconciliation, exchange calendar maintenance, idempotent orders, partial fills, execution acknowledgements, and independent operational controls remain future work. Broker orders remain disabled.

# Exact Next Step

Continue offline with an independently sourced, untouched multi-session dataset and a reviewed calendar covering its dates. Declare timestamp semantics and data provenance, freeze the replay plan before evaluation, and use independently preserved historical decision records only when their lineage is available. Run it in a new isolated account directory with:

```powershell
.venv\Scripts\python.exe -B scripts/replay_ai_led_offline.py --plan <reviewed-plan.json> --output <new-offline-account-directory>
```

A plan declares `schema_version`, `capital`, ordered `instrument_priority`, `model_id`, local `calendar`, `calendar_evidence_kind`, `ai_evidence_kind`, `market_data_kind`, `candle_timestamp_convention: "START"`, and chronological `steps`. Each step declares `as_of`, the complete `snapshots` list, a saved `response`, and `record_artifact` (null for mocks). Capital cannot be overridden on the saved-plan CLI.

Current-market screening, genuine prospective DeepSeek capture, and sandbox work require separately authorized follow-up tasks after those blockers are addressed. This task establishes deterministic offline execution validation only.
