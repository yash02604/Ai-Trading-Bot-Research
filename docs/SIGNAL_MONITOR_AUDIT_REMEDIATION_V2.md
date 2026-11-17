# Signal Monitor Audit Remediation V2
Date: 2026-11-04. Offline implementation and testing only. This report does not authorize an experiment.

## 1. Files changed

| File | Type | Purpose |
|---|---|---|
| signal_monitor.py | Production | Calendar-complete history; refreshed clock; durable checkpoints/outbox; deadlines; bounded transport dispatch and drain scheduling |
| app/monitor_transport.py | Production, new | Reuse bounded Upstox GET and persistent logical/physical request accounting; disabled by default |
| expanded_event_oos_validation.py | Research production | Strict conflict-aware collector merge |
| tests/conftest.py | Test | Redirect decision/trade logs and monitor checkpoints to per-test temporary paths |
| tests/test_signal_monitor.py | Test | Calendar-derived complete historical fixtures and explicit offline injection |
| tests/test_signal_monitor_v2.py | Test, new | Focused reproductions, real numerical oracles, restart/crash and transport regressions |
| docs/SIGNAL_MONITOR_AUDIT_REMEDIATION_V2.md | Documentation, new | This report |

Paths in tables are relative to `C:/Users/Priyanka/Desktop/ai-trading-bot-research`. Production indicator calculations, detector thresholds, ranking, signal thresholds, broker execution and historical evidence were not modified.

## 2. Audit findings remediated

| Finding | Severity | Root cause / safety impact | Fix / affected function | Test evidence |
|---|---|---|---|---|
| Required history incomplete | High | Counts/internal gaps could pass despite missing prior-session tail; wrong signal | _required_history(), _validate_history(), _load_historical_context() require exact eligible predecessor labels before caching | required_history_rejected; calendar_tail_coverage; truncated_history_would_create_a_false_real_signal |
| Old freshness clock | High | Pre-fetch clock could accept stale candle after boundary | _scan_stock() refreshes clock after intraday and again after history, immediately before indicator calculation | authoritative_clock_refresh; history_fetch_also_refreshes; boundary_at_close |
| Volatile progress | High | Restart discarded evaluation and suppression state, duplicating alerts/changing context | _load_checkpoint(), _commit(), _save_state(), scan_once() persist atomic hash-checked state and frozen history under a lock | restart_preserves; bad_checkpoint; next_eligible_day; atomic_commit_failure; output_crash |
| Unguarded acquisition | High | Arbitrary fetch path lacked durable attempt cap/deadline; late data could evaluate | BoundedMonitorTransport.fetch() uses existing request_window/_bounded_get, persistent budgets and physical markers; _check_deadline() gates evaluation | reviewed_transport_persistent; monitor_integrates_real_request_window; integrated_deadline_rejection; transport_revalidates_calendar |
| Drain shorter than poll | Medium | Loop could sleep through final window; missed signal | Constructor rejects poll>=drain; run() caps sleep by remaining drain | unsafe_drain_poll; short_drain_polling; closing cases in original suite |
| Weak indicator oracle | Medium | Tests asserted execution rather than numerical correctness | Real RSI/decline and positive/negative/near-threshold signal assertions, plus intentional negative controls | real_indicator_numeric_signal_oracle; real_oracle_rejects_material_indicator_mutations |
| Legacy last-write-wins | Medium | Expanded collector resolved conflicts by response order | collect_historical_candles() now uses merge_raw_candle_batches() | expanded_legacy_collector, both response orders |
| Persistent test log writes | Medium | Real logger globals retained production paths; contaminated evidence | Autouse isolated_persistent_logs_and_monitor_state redirects logger paths | decision_logging_isolated_from_real_persistent_log; complete-suite write guard and before/after hash |

Test names above abbreviate the corresponding test_ functions in the changed test files. Exact locations can be inspected in the scoped patch.

## 3. Historical completeness

The required immediate historical context is **36 eligible five-minute candles**: max(REQUIRED_CANDLES-1, MACD warm-up + detector lag) = max(34, (26-1)+(9-1)+3). The production MACD signal becomes valid after 33 discarded input rows, and the unchanged reversal detector then needs shift(3) on enriched rows. No indicator window or threshold was changed.

_required_history() walks the existing verified calendar backwards, skipping explicit holidays/weekends/unsupported sessions. It never assumes consecutive calendar days are trading days. It selects the final 36 eligible labels immediately before the current session, across multiple sessions if necessary, within the existing 14-day search bound. Unknown calendar coverage fails closed.

The historical response must contain every required label, including the final eligible candle of the immediately preceding required session. A sufficient row count from the wrong session cannot pass. All returned history is also validated for eligible sessions and internal gaps. Current-session data must contain the full completed eligible sequence from session open through the expected latest candle; together these checks prove the historical/current boundary.

The monitor retains the full validated returned context rather than silently trimming it or changing indicator seeds. It caches only after completeness succeeds. Validated context is frozen into the checkpoint on successful evaluation, preventing restart from reacquiring a different same-day EMA/RSI seed. Incomplete history produces INCOMPLETE_HISTORY with missing count/sample, required count, expected tail and received tail; it never reaches indicators, advances progress, creates a successful cache entry or fabricates/forward-fills rows. A later eligible poll may make a new budgeted logical acquisition.

## 4. Freshness timing

Acquisition-start time and monotonic elapsed time are recorded separately from decision time. Intraday data is checked after fetching, and the authoritative clock is sampled again after historical acquisition, immediately before final freshness validation/indicators.

The clock is normalized to Asia/Kolkata. The latest completed calendar-eligible label is recalculated from that decision time. A 09:29:59→09:30:01 fetch with only a 09:20 candle is rejected; inclusion of 09:25 permits evaluation. Both 15:15 and 15:30 close boundaries are covered.

A constructor now_fn supplies deterministic authoritative time; run(now_fn=...) propagates it. An explicit scan_once(now=...) is a frozen decision clock only in explicitly declared offline fixture mode. The bounded real transport and monitor must share the same injected clock.

## 5. Restart-safe state

The default operational checkpoint location is logs/signal_monitor/checkpoint.json. Tests replace it with a temporary location.

The checksum envelope contains an exact schema and identity tied to stocks/instrument keys, calendar hash, five-minute interval, required context and source hashes. Per-symbol data includes last_processed, last_signal_at, historical-cache date/payload and evaluation metadata. It also contains durable event identities, alert values and PENDING/ACKNOWLEDGED delivery status. Final-candle completion is derived from last_processed equalling the calendar's final eligible label.

An existing project cross-process lock serializes load/evaluation/commit. State is written to a same-directory temporary file, flushed/fsynced and atomically replaced. A separate identity marker prevents a missing checkpoint from being silently reinitialized while its marker remains. Duplicate JSON keys, malformed/truncated JSON, checksum/schema/identity mismatch, invalid session labels and missing members fail closed as CHECKPOINT_STATE_INVALID. No corrupt-state reset or historical repair is performed.

A valid commit precedes console output. If output/acknowledgement fails, the durable event remains pending or explicitly uncertain; restart reports pending IDs without automatically emitting the same alert again. Console output is not an external exactly-once delivery protocol: the authoritative record is the durable event/outbox, and uncertain delivery needs reconciliation.

First startup/new day retains the prior latest-only alert policy. Same-day restart/catch-up processes unseen enriched rows to maintain event suppression, but only the currently fresh expected candle can emit an alert. Backlog alerts are never invented retroactively. Evaluation metadata records row count, initial-latest-only policy, acquisition/decision timestamps and deadline. A new eligible trading date reacquires/revalidates that day's predecessor context and does not inherit a stale-date checkpoint as current progress.

## 6. Transport/request accounting

Only the exact BoundedMonitorTransport type is accepted as the real acquisition interface. Legacy callable injection requires explicit offline=True fixture mode and cannot be mixed with the reviewed transport. The official Upstox network path is blocked inside that offline callback mode. Arbitrary offline callback bodies are trusted test code, not an approved live adapter.

BoundedMonitorTransport is **disabled by default**, and main() still supplies no live transport. There is no new network-enabled CLI or experiment authorization.

For a separately configured adapter, maximum_requests is an explicit finite lifetime cap, persisted immutably in its policy/config. A nonempty directory without a monitor policy is rejected before lock/file creation, so prior experiment evidence and budget namespaces cannot be adopted. A regression verifies its bytes and file set remain unchanged. The existing PersistentRequestBudget reserves one logical request under the existing experiment/file lock before transmission. Its ledger is flushed/fsynced before proceeding. A distinct physical-attempt record is appended and fsynced through request_window's before_send callback immediately before _bounded_get. max_attempts=1 disables automatic retries; redirects remain disabled by the unchanged existing HTTP worker.

The same request_window reuses the existing killable Upstox worker and total/phase deadlines. Session/calendar/client attestation/policy are checked before acquisition and before the physical attempt. Request/response/receipt records are saved separately in the adapter's monitor acquisition directory. Failed/late attempts remain spent. Restart retains both counters; exhaustion fails closed without a physical send. A later eligible poll is a new logical request within the remaining lifetime cap, never a refund or retry of the old authorization. No AI_SIGNAL_ONLY trial purpose slots or budgets were changed.

The monitor's overall per-stock acquisition/final-validation deadline is min(start+configured timeout<=20s, final calendar close+drain), across intraday and history together. Post-fetch deadline checks also prevent a late returned response from entering indicators or advancing progress. Live HTTP has the existing hard worker deadline; offline fixture callbacks are checked on return rather than being promoted to live transport.

## 7. Drain configuration

Unsafe constructor configurations with poll_interval>=post_close_drain are rejected. Thus 30s/60s is invalid. Allowed short drains use a shorter poll; tests retry final data at +10s and +29s within a 30s drain, and stop if it never arrives. run() additionally sleeps no longer than the remaining drain duration. Existing 120-second default behavior, both calendar close times, blocked Muhurat, final-candle processing and exclusion of post-session bars are retained.

## 8. Indicator-test strength

Normal integration cases use real production candle merge, calculate_indicators(), detect_reversal_candidates(), completeness and freshness functions. Only external I/O and time are supplied offline.

The fixture has flat prior closes at 100, a historical final close of 99, current closes 98 and 98+g. With q=13/14, the independent RSI oracle is 100*g/(g+q*q+q), and the detector's decline is g-2 percent.

| g | RSI oracle | Decline | Frozen signal |
|---|---|---|---|
| 0.5 | approximately 21.826280624 | -1.5% | Yes |
| 0.5969 | approximately 24.9988 | -1.4031% | Yes, near upper RSI boundary |
| 0.6 | approximately 25.096030730 | -1.4% | No |

Assertions use the full formula to 1e-9, not the rounded display values. Reversed responses must give identical results. A missing historical final candle changes the g=.6 case from a real no-signal to a false signal in an intentionally unguarded reference calculation; the monitor rejects that incomplete input before indicators.

Dedicated negative controls deliberately replace RSI with 100/0, omit enrichment, or bypass the detector and prove the same positive monitor oracle raises AssertionError. These are intentional mutation checks; ordinary integration fixtures do not mock production calculations.

## 9. Legacy duplicate path

The expanded-history collector is reachable from its standalone research build_dataframe()/script pipeline, not imported by the current monitor or reviewed prospective entrypoint. It was fixed, not deleted: identical overlaps collapse deterministically; conflicting OHLCV timestamps raise CandleDataError in either response order. No historical datasets were regenerated.

## 10. Test-log isolation

An autouse fixture redirects trade_logger.LOG_DIR, LOG_FILE and DECISION_LOG_FILE to each test's tmp_path and redirects the monitor checkpoint root likewise. Imported logger functions read those module globals at call time, so TradingEngine's captured log_decision reference is isolated too.

The real decision log already contained **316 TEST-symbol records** before implementation. It remains contaminated and is not trustworthy as clean experiment evidence. No entries were removed or rewritten. Before/after SHA-256:
cc8c5b30954ee3aee4050209f7c6ecc8e6149f53c2128d54348e801b0c45ecf2.

A focused regression performs logging and asserts the real file bytes remain unchanged. The external test runner independently rejects all writes outside its isolated output root, protecting the entire project during the full suite.

## 11. Test results

| Suite | Passed | Failed | Errors | Skipped | Deselected | Pytest seconds |
|---|---:|---:|---:|---:|---:|---:|
| New focused regressions | 62 | 0 | 0 | 0 | 0 | 24.99 |
| Complete monitor | 63 | 0 | 0 | 0 | 0 | 16.16 |
| Related safety | 246 | 0 | 0 | 0 | 0 | 183.96 |
| Full offline tests/ | 1219 | 0 | 0 | 0 | 0 | 790.95 |

All final exit codes are zero. Suite counts overlap; they must not be summed as distinct tests.

Final production-code runs were sequential in the requested A→B→C→D order. The complete offline unit suite is the entire canonical tests/ directory; standalone research runners are not experiments invoked by this test command.

Exact commands (project working directory):

```powershell
& ./.venv/Scripts/python.exe -B 'C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/signal-monitor-v2-b599958f49ac4c92b0617a4a2fd8ecec/run_offline.py' focus-final2
& ./.venv/Scripts/python.exe -B 'C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/signal-monitor-v2-b599958f49ac4c92b0617a4a2fd8ecec/run_offline.py' monitor-final2
& ./.venv/Scripts/python.exe -B 'C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/signal-monitor-v2-b599958f49ac4c92b0617a4a2fd8ecec/run_offline.py' safety-final2
& ./.venv/Scripts/python.exe -B 'C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/signal-monitor-v2-b599958f49ac4c92b0617a4a2fd8ecec/run_offline.py' full-final2
& ./.venv/Scripts/python.exe -B 'C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/signal-monitor-v2-b599958f49ac4c92b0617a4a2fd8ecec/run_offline.py' compile
```

The external runner disables dotenv before collection, removes inherited Upstox/DeepSeek/OpenAI credentials, blanks target keys, blocks .env reads, socket/DNS/datagram access and subprocess launching, disables external pytest plugin autoload/cache, uses fresh UUID temporary directories and guards project writes. The full suite keeps pytest's built-in logging/caplog plugin with file output forced into the external isolation root. SDK/network boundaries use mocked/synthetic responses. No actual Upstox/DeepSeek request is made.

Six relevant changed Python files compiled without bytecode writes. Scoped Git whitespace checks passed with CRLF respected: git -c core.autocrlf=false -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --no-index --check -- BEFORE AFTER. No scoped diagnostics; no-index exit 1 denotes expected differences.

Development run history, retained externally:

- focus: 50 passed, 0 failed/errors/skipped/deselected, before extra state-schema and integration cases.
- focus-final: 61 passed, 0 failed/errors/skipped/deselected.
- monitor: 12 passed, 1 failed, 0 errors/skipped/deselected, stopped at first failure.
- monitor-debug: 12 passed, 1 failed, 0 errors/skipped/deselected, same reproduction with diagnostic assertion.
- The monitor failures were fixture problems: the old fixed-start tail missed three calendar-required labels and included labels after the saved session close. Fixtures now derive exact tails from the real calendar; monitor-final passes.
- full: 19 passed, 0 failed, 1 setup error, 0 skipped/deselected. The external harness had disabled the logging plugin and removed caplog. The harness, not application code, was corrected.
- full-final: interrupted without a completion result; no pass/count claim is made. Its partial log is preserved.
- full-resumed: 820 passed, 1 failed, 0 errors/skipped/deselected. The external write guard blocked an existing provenance test's project-local temporary directory. Its TEST_TMP_ROOT is now redirected externally by the runner; no project test or application code changed for that correction.
- full-verified: final complete run reported above. The full-verified run was stopped to add a monitor output-namespace guard and its regression. The final focus-final2, monitor-final2, safety-final2 and full-final2 runs revalidate the updated code in A→B→C→D order.


### Complete recorded development-run counts

| Run | Passed | Failed | Errors | Skipped | Deselected | Wrapper seconds |
|---|---:|---:|---:|---:|---:|---:|
| focus-final | 61 | 0 | 0 | 0 | 0 | 22.78 |
| focus-final2 | 62 | 0 | 0 | 0 | 0 | 25.57 |
| focus | 50 | 0 | 0 | 0 | 0 | 19.82 |
| full-final2 | 1219 | 0 | 0 | 0 | 0 | 791.61 |
| full | 19 | 0 | 1 | 0 | 0 | 6.63 |
| full-resumed | 820 | 1 | 0 | 0 | 0 | 526.2 |
| monitor-debug | 12 | 1 | 0 | 0 | 0 | 3.07 |
| monitor-final | 63 | 0 | 0 | 0 | 0 | 16.77 |
| monitor-final2 | 63 | 0 | 0 | 0 | 0 | 16.73 |
| monitor | 12 | 1 | 0 | 0 | 0 | 2.79 |
| safety-final2 | 246 | 0 | 0 | 0 | 0 | 184.53 |
| safety | 246 | 0 | 0 | 0 | 0 | 190.04 |

full-final and full-verified were interrupted without result files; their exact completion counts are unavailable and neither is claimed as a pass.

## 12. Integrity verification

- External network requests: **NO**.
- Broker execution enabled: **NO**.
- Prospective experiment run: **NO**.
- Protected evidence changed: **NO** (425 protected files unchanged; no baseline file missing).
- Persistent decision log changed during final verification: **NO** (hash unchanged).

All 21 trial _02 files and all Session 02/03/04/05 baseline files (7/4/3/8 respectively) remain unchanged. All prior reports, historical data/backtests, budgets, attestations and calendar evidence remain unchanged. The only modified pre-existing files are the four scoped source/test files listed in section 1; the transport, focused tests and report are new additions.

Starting and ending HEAD: `a0789cdb5db50deece446005c40064ca5de989a4`. Complete status snapshots are git-status-start.txt and git-status-end.txt. No commit was made. final-integrity.json contains exact file hashes/counts and scoped Git numstat/whitespace results; task-diff.patch contains the actual task diff against the pre-change byte copies.

Starting and ending git status --short snapshots, original source copies, baseline hashes, final per-file verification and task-scoped Git patch/numstat are retained externally in:

`C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/signal-monitor-v2-b599958f49ac4c92b0617a4a2fd8ecec`

No commit was created. Unrelated existing tracked changes/untracked files were preserved. Paths excluded from inventory: .git, .venv, bytecode/caches, prior temporary audit/remediation directories and .env. The baseline was not regenerated.

## 13. Remaining risks

- Console output is best effort; uncertain delivery is retained in the durable pending-event state and needs explicit reconciliation, rather than automatic duplicate-prone redelivery.
- Atomic file/fsync and local locks depend on filesystem/OS durability; this is not an authenticated external WORM store. The local outbox grows with events.
- No live transport, real provider availability or prospective paper/causal settlement behavior was exercised. Those operations remain NO-GO.
- The pre-existing decision log remains synthetically contaminated and requires a separate evidence-remediation decision before being trusted.
- A future change to production indicator windows must review the derived minimum context; source-bound existing checkpoints fail closed after source changes.

## 14. Final verdict

**READY FOR INDEPENDENT READ-ONLY AUDIT ONLY**

**No experiment authorized. Broker execution remains disabled.**
