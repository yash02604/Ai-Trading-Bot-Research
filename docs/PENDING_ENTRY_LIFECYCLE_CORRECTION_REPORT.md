# Root Cause

A reviewed AI proposal creates a PENDING intent for the next five-minute opening timestamp. Actual-entry validation waits for a later snapshot containing the completed entry candle. The verified calendar's new-entry acquisition gate can close before that observation arrives. Settlement-only acquisition requires an accepted OPEN position, so neither acquisition mode could resolve a stranded PENDING intent.

The regression was added and executed before changing production code. On the November 2, 2026 boundary, the proposed entry was 15:10 IST; recovery at 15:15:01 returned NO_PENDING_RECOVERY instead of a terminal rejection. Result: one expected failing test, in 9.14 seconds.

# PENDING Resolution Design

Added local-only `resolve_expired_entry` to the existing recovery module. Under the experiment lock and current immutable-configuration checks, it validates the account, original decision, proposed entry timestamp, original verified calendar interval, saved batch/response identity and response evidence seal. It never acquires market data or calls a provider.

The validation cutoff is the end of the original verified continuous-session interval minus five minutes, matching the existing calendar's latest new-entry acquisition boundary. Once that cutoff is reached, an unaccepted PENDING intent receives one existing-convention ENTRY_REJECTED event with reason_code ENTRY_WINDOW_EXPIRED and taken false. The event preserves original decision and proposed entry timestamps, cutoff and resolution time. No entry price, fill, execution costs, realized loss or P&L is invented.

The same atomic account commit appends the rejection, clears position state and advances the account observation clock. Cash and balance are unchanged. Duplicate event validation remains in the original state loader. Repeated resolution returns the saved terminal event without another commit. Commit interruption before or after atomic publication is recoverable without duplicate events.

Normal reviewed resume, explicit local `--recover-only`, and settlement-only orchestration invoke this rejection before attempting new acquisition. Local recovery requires no API credentials or network flags. Normal and settlement entry points retain their existing opt-ins and settings checks. OPEN positions return immediately from the new helper and retain their original settlement path. An unexpired PENDING intent remains untouched. No observation-only network mode was added.

Resolution occurs on an authorized invocation/resume; no background scheduler was added. Missing, malformed or unverifiable evidence still fails closed instead of receiving a fabricated terminal record. Original decisions, batches, requests, responses and consumed budgets are never rewritten.

# Files Changed

- `app/research/ai_led_recovery.py`: verified local terminal rejection and idempotent result.
- `app/research/ai_led_reviewed.py`: invokes rejection from the three reviewed lifecycle entry points.
- `tests/test_pending_entry_resolution.py`: new boundary, integrity, restart, capital and quota regressions.
- `tests/test_prospective_lifecycle_fixes.py`: the existing OPEN-only settlement rejection case now uses an unexpired PENDING clock, so its original safety assertion remains applicable.
- `docs/PENDING_ENTRY_LIFECYCLE_CORRECTION_REPORT.md`: this new report.

Pre-existing Git modifications are preserved. No changes were made to the Upstox client, v1/v2 attestations, CLI, core account engine, calendar, RiskEngine, cost model, frozen quantitative strategy or existing reports.

# Entry Timing and Execution Semantics

The unchanged core model uses five distinct times:

1. Decision time: the current observation/AI evaluation time, after completed candles.
2. Proposed entry: floor(decision time to five minutes) plus five minutes.
3. Entry candle opening timestamp: the first future candle must match that proposed entry exactly.
4. Observable completion: that candle is only available as a completed candle five minutes after its opening.
5. Actual-entry validation: a subsequent observation validates the historical open, risk, affordability and modeled costs before recording ENTRY_ACCEPTED.

For example, a 14:45:01 decision proposes 14:50 entry. Its 14:50 open is not known at decision time; after the candle completes at 14:55, the model can validate and record a simulated 14:50 fill. The existing model therefore uses a historical candle open retrospectively for offline-style paper execution. It does not establish that an order could actually have filled there when validation occurred. Quotes used for candidate evaluation do not constitute an execution receipt for that future fill.

This correction never turns expired intents into such fills and leaves established accepted-trade modeling unchanged. Genuine real-time execution requires a separately reviewed fill policy with contemporaneous executable observations/order receipts, latency and slippage handling. No such redesign or strategy change is included here. Paper results must be labeled as prospective decisions with modeled candle-open fills, not authenticated live execution performance.

# Calendar Boundary Handling

No universal 15:15 cutoff was introduced. The original session's verified interval determines expiry:

- November 2, 2026 verified close 15:15: unaccepted PENDING expiry at 15:10.
- December 7, 2026 verified close 15:30: expiry at 15:25.
- An intent just before its cutoff is preserved; at the cutoff it is rejected locally.
- Restarts after close, on weekends/holidays, during unsupported special sessions, or outside current calendar date coverage can reject an old intent using its original verified interval. They receive no current acquisition authorization.
- New entries and OPEN-position acquisition retain all existing date, holiday, special-session, completion and freshness checks.

The final candle is unnecessary for rejecting an unaccepted intent: no actual entry or outcome is inferred from missing observations. Accepted OPEN positions continue using the separately verified settlement acquisition window and contiguous candle validation.

# Account and Budget Integrity

Rejection retains the original decision ID and uses ENTRY_REJECTED, which existing summaries already separate from accepted and completed trades. Rejected entries do not enter the daily accepted-trade counter or daily realized-loss calculation. Clearing PENDING releases its virtual reservation, with no debit or credit to cash. There are no simulated execution charges for this rejection.

Tests preserve request, response, batch, input and budget bytes across resolution. The boundary case remains at four consumed market-data reservations and one consumed provider reservation; resolution adds zero. Repeated summary/resume cannot add a second event. An intervening NO_TRADE decision does not prevent duplicate-safe terminal replay, and a later valid session can process another batch subject to the original provider and market-data caps.

Capital remains configurable. Whole-share, cost-eligible fixtures verify unchanged capital at 1000, 2500, 10000 and 100000. The one-percent trade-risk and two-percent daily-loss limits, RiskEngine quantity ceiling, fees and affordability rules are unchanged.

# Tests Added

Nineteen new cases cover the documented boundary; normal, local and settlement resume; no late fill, fee or P&L; preserved evidence/budgets; no provider/market sends; quota and cash; exact cutoff; missing observations; after-close/weekend/special-session/holiday/out-of-coverage restarts; unexpired intents; four capital amounts; the later verified close; interruption before and after commit; corrupt evidence; intervening NO_TRADE; and later-batch progress. Existing OPEN-position settlement and attestation regressions are retained in the full suite.

# Verification Results

Commands run from the repository root use `-B`, blocked sockets, dummy credentials and external temporary artifacts. No real network requests are permitted by the test fixtures.

Initial red regression:

```powershell
.venv\Scripts\python.exe -B -m pytest tests/test_pending_entry_resolution.py -q -p no:cacheprovider --basetemp C:\Users\Priyanka\.codex\visualizations\2026\10\30\01a18b43-af58-74b1-ad68-35a667601200\.pytest-pending-before
```

Result: 1 failed as expected, demonstrating the original defect. An intermediate focused run had 15 passes and three capital-fixture failures: using unchanged 600-price data at higher capital did not produce the intended one-share eligible setup. Fixtures were scaled to each account's capital without weakening production sizing or cost checks. The corrected focused run passed 18 tests in 55.07 seconds. One additional intervening-decision/later-batch case was then added to the final suite.

Final full regression command:

```powershell
.venv\Scripts\python.exe -B -m pytest tests/test_pending_entry_resolution.py tests/test_upstox_deadlines.py tests/test_prospective_lifecycle_fixes.py tests/test_ai_led_reviewed.py tests/test_ai_led_guarded.py tests/test_ai_led_offline.py tests/test_ai_led_replay.py tests/test_prospective_preflight.py tests/test_readiness_evidence.py tests/test_offline_market_data_v1.py tests/test_deepseek_prospective.py tests/test_deepseek_prospective_summary.py tests/test_cost_model_execution_integrity.py tests/test_core.py tests/test_deepseek_eval_v1.py tests/test_ai_v2.py tests/test_ai_v2_evaluation.py tests/test_provenance_audit.py tests/test_execution_observations.py tests/test_execution_integrity.py tests/test_paper_session.py -q -p no:cacheprovider --basetemp C:\Users\Priyanka\.codex\visualizations\2026\10\30\01a18b43-af58-74b1-ad68-35a667601200\.pytest-pending-regression-final
```

Result: **599 passed in 295.44 seconds, with no exclusions or remaining test failures.** This includes all 580 prior cases and 19 new cases; counts from overlapping focused runs are not additive.

All four changed/new Python files passed in-memory compilation using `.venv\Scripts\python.exe -B -` and `compile(path.read_bytes(), str(path), 'exec')`, without bytecode writes. All five changed/new files passed targeted whitespace validation using `git -c core.autocrlf=false -c core.whitespace=blank-at-eol,blank-at-eof,cr-at-eol diff --no-index --check <task-start-copy-or-empty> <current-file>`. Task-start comparisons include untracked files, which ordinary Git diff omits. The final task diff was inspected for unintended risk, strategy, cost, acquisition, retry, budget, calendar and broker changes.

SHA256 verification confirms all **359 protected historical evidence files unchanged**, including Sessions 02-05, their budgets, historical provenance and original attestations. The 493-file task-start inventory identifies only the three existing files listed above as changed; the only additions are the new test and this report. Client attestation v2 continues to verify against the unchanged Upstox source. No Session 06 or AI-led real-data trial directory was created. No external API calls, captures, provider token usage or broker orders occurred.

# Remaining Risks

Historical candle-open fills remain modeling assumptions. Stop gaps can exceed planned risk. Mocked proposals show pipeline behavior, not DeepSeek predictive skill or profitability. Source-hash-bound experiments from earlier implementations are not silently migrated to current code; their evidence and budgets remain unchanged. Corrupt evidence requires investigation, not forced rejection or reset. Resolution requires a runner/recovery invocation and a trustworthy local clock.

# Real-Data Paper Trial Readiness

This change addresses the terminal-state blocker only. A real-data research trial must explicitly acknowledge modeled historical-open fills and undergo final read-only review and operator authorization. It is not ready to claim contemporaneous executable fills or autonomous trading readiness. No real-data trial command was run.

# Broker Execution Readiness

NO-GO. Broker orders remain disabled. No broker balance fetching, execution policy, order transport or broker sandbox approval was added.

# Recommended Next Step

Review the terminal-state diff and regression results read-only. Decide explicitly whether a future bounded experiment is research using modeled candle-open fills. If genuine real-time paper execution is intended, specify and review its fill policy separately before claiming execution readiness. Do not start Session 06 or a real-data trial on the strength of offline tests alone.
