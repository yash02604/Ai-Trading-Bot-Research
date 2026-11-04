# Signal monitor reliability remediation — 2026-11-04

Scope: the standalone offline signal monitor. Strategy thresholds, production indicator implementations, broker execution, prospective runners, durable request accounting, and historical evidence are unchanged. This work does not authorize an experiment.

## Confirmed defects and changes

| Defect / root cause | Affected function | Implemented change and failure action | Regression coverage |
| --- | --- | --- | --- |
| A history exception installed an empty cache with today's success date, then continued without context. Subsequent polls never retried history. | `SignalMonitor._load_historical_context`, `_scan_stock` in [signal_monitor.py](../signal_monitor.py) | Validate response shape, OHLCV, timestamp awareness/alignment, duplicate consistency, calendar eligibility, minimum lookback and within-day continuity before committing cache/date. Transport or validation failure skips the symbol without advancing its checkpoint; next poll retries. No fabricated or failed history fallback. | `test_history_failure_then_recovery_with_real_indicators` (transport, invalid OHLC, malformed response, empty history, conflicting history, missing bar, invalid session, insufficient history). |
| Latest returned candle was accepted solely because it had completed, regardless of how old it was. | `_scan_stock`, `scan_once` | Derive exact eligible completed start labels from the verified offline NSE calendar and five-minute duration. Require the latest received completed candle to equal the latest expected label, plus complete current-session coverage through that label. Stale data produces structured `STALE_MARKET_DATA` with symbol in the log, received/expected timestamps and delay in seconds; it never reaches history acquisition, indicators or detector. In-progress candles cannot substitute for completed bars. Earlier backlog rows update event grouping but cannot emit an outdated alert. | Stale, boundary, in-progress, missing-bar, timezone and backlog tests listed below. |
| Dictionary assignment silently implemented last-record-wins for duplicate timestamps. | `_deduplicate_candles` | Delegate to existing `merge_raw_candle_batches` / `merge_candle_sources`. Identical duplicates collapse; authoritative OHLCV disagreements raise `CandleDataError`. Log `CONFLICTING_CANDLES` with symbol and validated conflict timestamps; skip symbol and do not advance checkpoints. Reversed order has the same rejection. | Conflict field/order and real-indicator duplicate tests. |
| Acquisition used the same hard-coded close boundary as candle eligibility. A final candle arriving just after close could never be processed. | `scan_once`, `run`, calendar eligibility helpers | Separate acquisition from eligibility. Permit a default 120-second post-close drain (configurable positive value, maximum 300 seconds), while labels remain restricted to calendar intervals. Final processed symbols are not fetched again after close. `run` finishes when every symbol evaluated the final eligible bar or its bounded acquisition window expires. | Final-candle, real-indicator close, run completion/expiry and deadline tests. |

Production session rules come from the existing, hash-verified v3 manifest through `OfflineSessionCalendar`. No calendar source or policy was edited. In particular, the existing conservative 15:15 cutoff remains in force on relevant dates; the 15:25–15:30 scenario is tested on 2026-12-08, when the manifest permits a 15:30 close. Unsupported Muhurat execution, holidays and unresolved calendar dates remain blocked. Calendar verification is local; this remediation performed no online verification.

The monitor now defaults to a disabled transport rather than automatically wiring Upstox GET functions. Tests explicitly inject offline fetchers. No network-enable CLI option, broker path, request-ledger adapter or live experiment capability was added. The pre-existing standalone monitor had no persistent request ledger; this change does not retrofit one or bypass the separate guarded research workflows. Existing physical-attempt, reservation, request-cap, response recovery, complete-seal and torn-seal controls remain untouched. History acquisition retains its maximum of 14 attempted supported-session lookbacks per symbol/poll and stops immediately on a rejected response.

### Failure decisions

- Failed acquisition / invalid response / invalid candle / incomplete history or current-session sequence: log `SCAN_FAILED` and exception type; skip symbol, keep checkpoint unchanged, retry normally next poll. Generic exception text is not printed because it can contain credentials.
- Conflicting candles: log `CONFLICTING_CANDLES` and affected timestamps; skip symbol before indicator calculation and retry next poll.
- Stale or missing latest completed candle: structured/logged `STALE_MARKET_DATA`; skip symbol before history or indicator calculation and retry within the acquisition window.
- Before the first bar completes: no acquisition or evaluation.
- Holiday or blocked session: no acquisition or evaluation. Missing/unverified calendar: `SESSION_UNVERIFIED`, fail closed; `run` terminates.
- Final candle never arrives: acquisition ends at the configured drain deadline. No final evaluation is fabricated.
- An unexpected per-symbol exception is contained by the existing outer scan boundary, explicitly logged as failure, and never counted as a successful evaluation. A stock receives `EVALUATED` only after successful calculation and checkpoint advancement.

## Tests added

All tests are in [test_signal_monitor.py](../tests/test_signal_monitor.py). The pre-existing threshold and event-gap tests remain.

| Test | Behavior proved |
| --- | --- |
| `test_history_failure_then_recovery_with_real_indicators` | Eight independent failure modes do not cache or reach indicators. Next poll retries, caches validated history, and reaches a deterministic no-signal decision through actual production indicators/detector. |
| `test_stale_market_data_suppresses_indicators` | Four stale ages including midday and close suppress both calculations and history fetches; timestamps and delay are diagnostic. |
| `test_real_indicator_freshness_boundaries` | Open, first completed bar, subsequent bars and near-close boundaries use real indicators and correct checkpoints; incomplete provider bars are excluded. |
| `test_in_progress_candle_cannot_replace_missing_completed_candle` | An incomplete latest bar does not make an older completed bar fresh. |
| `test_conflicting_intraday_duplicates_rejected_before_indicators` | Open/high/low/close/volume conflicts are rejected in either order before indicators. |
| `test_real_indicators_identical_duplicates_order_and_repeat_are_deterministic` | Real history + current-session indicator integration yields the same no-signal/checkpoint result in either order, with identical duplicates retained once and no repeated evaluation output. |
| `test_malformed_intraday_is_rejected` | Empty/short rows and naive timestamps fail closed. |
| `test_calendar_holiday_weekend_blocked_muhurat_and_unknown_fail_closed` | Actual calendar blocks acquisition on a holiday, weekend, restricted Muhurat date and unknown date. |
| `test_final_candle_arrives_after_calendar_close_and_is_evaluated_once` | Missing final bar at close becomes available afterward, alerts once, ignores post-session candles and preserves both calendar close schedules. |
| `test_real_indicator_final_candle_after_close` | Actual indicators evaluate the final session bar in the drain, yielding deterministic no-signal completion. |
| `test_run_post_close_drain_terminates_on_completion_or_deadline` | Simulated clock proves clean exit on successful drain or missing-data timeout; acquisition counts are bounded. |
| `test_default_monitor_has_no_network_transport` | Default monitor cannot call broker-data transports or advance a checkpoint. |
| `test_missing_intraday_bar_rejects_even_when_latest_is_fresh` | Latest-label freshness cannot hide an earlier missing current-session candle. |
| `test_post_close_deadline_prevents_further_acquisition` | Both single-poll and loop interfaces reject acquisition after drain expiry. |
| `test_freshness_is_timezone_normalized` | UTC and IST refer to the same eligible completed bar. |
| `test_recovered_backlog_does_not_emit_stale_signal` | Catch-up polling preserves event state without emitting an older backlog signal. |

Real-indicator tests replace only external fetches/output. The indicator and detector functions are not mocked in those integration scenarios. Separate unit tests use forbidden-call sentinels to prove rejection occurs before production calculations. Socket connections are blocked by the existing offline `tests/conftest.py` fixtures, and the Upstox worker boundary is mocked there. No provider, broker, real-time paper, or causal-settlement experiment was run.

## Test commands and results

Run from the repository root in PowerShell, using the requested virtual environment. All commands below completed.

1. Focused regressions:

   ```powershell
   .\.venv\Scripts\python.exe -m pytest tests/test_signal_monitor.py -q -k 'history_failure or stale_market or conflicting_intraday or final_candle or post_close'
   ```

   Successful focused run: **27 passed, 0 failed, 0 skipped, 33 deselected**. An earlier run had 25 passed / 2 failed / 33 deselected because a test's supposed conflicting high equaled the original high; changing the fixture high from 102 to 103 corrected that test input. Three additional tests were subsequently added and covered by the complete module run.

2. Complete monitor module:

   ```powershell
   .\.venv\Scripts\python.exe -m pytest tests/test_signal_monitor.py -q
   ```

   **63 passed, 0 failed, 0 skipped, 0 deselected.**

3. Related regression suite:

   ```powershell
   .\.venv\Scripts\python.exe -m pytest tests/test_candle_integrity.py tests/test_nse_session_audit.py tests/test_core.py tests/test_prospective_preflight.py tests/test_upstox_deadlines.py tests/test_signal_request_remediation.py tests/test_signal_complete_seal_integrity.py tests/test_signal_torn_seal_recovery.py -q --basetemp C:/Users/Priyanka/AppData/Local/Temp/vscode-agent-codex-sandbox-pcnyBU/signal-monitor-related-tests
   ```

   **226 passed, 0 failed, 0 skipped, 0 deselected** (186.18 seconds).

4. Full offline test directory:

   ```powershell
   .\.venv\Scripts\python.exe -m pytest tests -q --basetemp C:/Users/Priyanka/AppData/Local/Temp/vscode-agent-codex-sandbox-pcnyBU/signal-monitor-full-tests
   ```

   **1,157 passed, 0 failed, 0 skipped, 0 deselected** (711.07 seconds).

5. Static/editor checks: no diagnostics for the changed Python files; `git diff --check -- signal_monitor.py tests/test_signal_monitor.py` passed.

## Integrity and remaining limits

Before editing, SHA-256 hashes and paths were captured for all **322 existing files under `backtests/` and `data/`**, including protected Sessions 02–05, retained source documents, calendars and associated evidence. After the full suite, all 322 files were present with unchanged SHA-256 hashes; no files were added, deleted or modified in those trees. Protected Session 02–05 files and existing historical evidence were unchanged.

Integrity verification used `Get-ChildItem -LiteralPath backtests,data -Recurse -File` and `Get-FileHash -LiteralPath <each-file> -Algorithm SHA256` before editing and after the final tests, comparing paths and digests. The temporary baseline inventory was kept outside the repository, in the sandbox temporary directory. The existing protected manifest/evidence bytes were never rewritten.

Only the monitor, its test module and this new report are intended changes from this task. The workspace already contained unrelated modified/untracked research work; it has not been reverted or treated as part of this remediation.

- External API/network requests during implementation/tests: **none**. Test command escalation only grants access to the local virtual environment; it is not an authorization to acquire market data.
- Broker/live execution enabled: **no**. This monitor only produces research alerts; default data transport is disabled.
- Prospective, real-time paper or causal-settlement experiment run: **no**.
- Strategy thresholds, profitability logic and indicator implementations changed: **no**.
- Calendar dates outside the verified manifest remain blocked; there is no schedule extrapolation.
- Exact latest-completed-bar semantics intentionally reject delayed provider data until it catches up. After the drain expires, missing final data remains unevaluated.
- History lookback still requires at least 35 validated candles; full previous sessions are not invented. Partial returned history is accepted only when sufficient and contiguous within each returned date. Indicator decisions are based on the supplied validated context, not independent verification of provider truth.
- Monitor deduplication/checkpoints remain in memory; durable crash recovery belongs to the separate guarded workflows and was not added here.
- No new adapter binds this standalone monitor to persistent request reservations. Any future transport integration needs independent authorization and review of those controls.
- The drain bounds polling eligibility, not execution time of an arbitrary injected Python callable. Future authorized transport adapters must enforce their own acquisition deadlines and request-window checks; the monitor does not preempt a hanging fetcher.
- Tests prove offline behavior, not profitability, provider latency, experiment readiness or execution readiness.

## Readiness verdict

**READY FOR INDEPENDENT READ-ONLY AUDIT ONLY. No experiment authorized.**

This verdict covers the scoped monitor remediation and the evidence recorded here. It does not authorize real-time paper trading, causal settlement experiments, broker/live execution, or connecting a transport to the standalone monitor. No profitability claim is made.
