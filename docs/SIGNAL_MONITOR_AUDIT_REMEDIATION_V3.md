# Signal Monitor Audit Remediation V3

## 1. Files changed

| File | Type | Purpose |
|---|---|---|
| [signal_monitor.py](C:/Users/Priyanka/Desktop/ai-trading-bot-research/signal_monitor.py) | Modified | Canonical history, frozen intraday evidence, drain schedule, outbox and state validation. |
| [cost_backtest.py](C:/Users/Priyanka/Desktop/ai-trading-bot-research/cost_backtest.py) | Modified | Strict conflict-aware historical ingestion. |
| [historical_robustness_test.py](C:/Users/Priyanka/Desktop/ai-trading-bot-research/historical_robustness_test.py) | Modified | Strict conflict-aware early historical ingestion. |
| [multi_stock_research.py](C:/Users/Priyanka/Desktop/ai-trading-bot-research/multi_stock_research.py) | Modified | Strict conflict-aware historical ingestion. |
| [stop_loss_backtest.py](C:/Users/Priyanka/Desktop/ai-trading-bot-research/stop_loss_backtest.py) | Modified | Strict conflict-aware historical ingestion. |
| [tests/test_signal_monitor.py](C:/Users/Priyanka/Desktop/ai-trading-bot-research/tests/test_signal_monitor.py) | Modified | Two-session fixtures and recovery acquisition counts; existing behavior checks retained. |
| [tests/test_signal_monitor_v2.py](C:/Users/Priyanka/Desktop/ai-trading-bot-research/tests/test_signal_monitor_v2.py) | Modified pre-existing V2 file | Two-session fixture and request-count assertion; numerical oracles retained. |
| [tests/test_signal_monitor_v3.py](C:/Users/Priyanka/Desktop/ai-trading-bot-research/tests/test_signal_monitor_v3.py) | New | Six findings, production indicators, revision and drain/transport regressions. |
| [tests/test_research_collectors_v3.py](C:/Users/Priyanka/Desktop/ai-trading-bot-research/tests/test_research_collectors_v3.py) | New | Four collectors, every OHLCV conflict, identical duplicates and both response orders. |
| [docs/SIGNAL_MONITOR_AUDIT_REMEDIATION_V3.md](C:/Users/Priyanka/Desktop/ai-trading-bot-research/docs/SIGNAL_MONITOR_AUDIT_REMEDIATION_V3.md) | New | This report; no previous report overwritten. |

Starting HEAD: `a0789cdb5db50deece446005c40064ca5de989a4`. No commit created. Starting workspace already contained V2 changes; they were preserved. In particular `app/monitor_transport.py`, `expanded_event_oos_validation.py`, `tests/conftest.py` and the V2 report are byte-identical to this task's baseline. Git's HEAD diff includes those earlier changes; the V3-only diff is [v3-final-source.diff](C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/signal-monitor-v3-089d56dbe9164fe595d8c37c0b7f04b5/v3-final-source.diff) and its stat is [v3-final-diff-stat.json](C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/signal-monitor-v3-089d56dbe9164fe595d8c37c0b7f04b5/v3-final-diff-stat.json). Starting status: [git-status-start.txt](C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/signal-monitor-v3-089d56dbe9164fe595d8c37c0b7f04b5/git-status-start.txt).

## 2. V3 findings remediated

| Finding | Severity | Root cause | Fix | Evidence |
|---|---|---|---|---|
| Recursive seed determinism | HIGH | Sufficient tail rows did not fix recursive seeds. | Exact two complete preceding eligible sessions; older rows excluded. | `_required_history`, `_select_history`; production comparison and prefix/restart tests. |
| Intraday revision detection | HIGH | Prior current-session OHLCV was reacquired without comparison. | Persist canonical OHLCV and hashes; reject revisions and omissions. | `_scan_stock`; all OHLCV fields and real create/remove-signal negative controls. |
| Final drain opportunity | HIGH | Poll at expiry was already ineligible. | One explicit final pre-expiry poll, exclusive send/return deadline. | `run`; 40-case availability matrix and real transport path with mocked final HTTP. |
| Pending reconciliation | MEDIUM | Durable pending alert could be hidden by later progress. | Recovery notification, persistent pending summaries, explicit confirmation API. | `_surface_pending`, `_pending_summary`, `acknowledge_pending`; crash/restart/new-event tests. |
| Checkpoint logical consistency | MEDIUM | Checksum alone did not link counts, progress and outbox. | Validate journal, event references, clocks and canonical candle evidence. | `_validate_entry`, `_load_checkpoint`; 13 valid-checksum corruption cases. |
| Research duplicate safety | MEDIUM | Four collectors chose the last timestamp occurrence. | Existing strict merge helper replaces dictionary selection. | 48 mocked collector cases in both response orders. |

Primary locations: [signal_monitor.py](C:/Users/Priyanka/Desktop/ai-trading-bot-research/signal_monitor.py:229) for entry invariants; [signal_monitor.py](C:/Users/Priyanka/Desktop/ai-trading-bot-research/signal_monitor.py:314) for checkpoint loading; [signal_monitor.py](C:/Users/Priyanka/Desktop/ai-trading-bot-research/signal_monitor.py:375) for durable publication; [signal_monitor.py](C:/Users/Priyanka/Desktop/ai-trading-bot-research/signal_monitor.py:482) for canonical history; [signal_monitor.py](C:/Users/Priyanka/Desktop/ai-trading-bot-research/signal_monitor.py:595) for acquisition/comparison/evaluation; [signal_monitor.py](C:/Users/Priyanka/Desktop/ai-trading-bot-research/signal_monitor.py:792) for scheduling. Production indicator and detector implementations and frozen thresholds were not changed.

## 3. Canonical historical context

The selected context is **two complete preceding eligible sessions**, derived backward from the pinned local calendar. Whole sessions are added only if two unusually short sessions contain fewer than the structural minimum of 36 candles. The search is bounded to 14 calendar days. Verified holidays and unsupported sessions contribute no labels; unresolved calendar data fails closed. Normal 75-bar sessions yield 150 historical rows; the September test sessions yield 144.

Every required label must exist after strict duplicate validation. Conflicting copies, invalid identities/session labels and missing required labels fail closed. Valid older rows are validated but excluded from calculation. Historical acquisition requests each required date until the complete canonical set is present; a broad response can satisfy both dates in one call. Normal two-session coverage can need one or two historical calls, in addition to current-session acquisition. Exceptional additional sessions required by the 36-row minimum can need further date-specific calls. Every actual call is charged; no budget is increased or refunded.

Canonical serialization uses sorted timezone-aware ISO IST timestamps and float OHLCV. The unused OI field is normalized to zero and is not an authoritative signal input. The selected context is persisted and hashed. Same-day restart reuses that frozen context without another historical call. An independent fresh selection from a larger response is also tested to match it exactly.

The context SHA-256 includes stock, instrument key, session date, interval, pinned calendar identity, canonical history and the completed intraday prefix actually used. Separate history/intraday hashes are retained. Source hashes bind the monitor, production indicators and detector; identity also binds acquisition timeout and drain. Different source/configuration identity fails closed.

Two sessions are a deterministic practical warm-up policy, **not a proof of infinite-history convergence**. Section 9 shows both raw-window sensitivity and exact invariance after canonical selection.

## 4. Processed intraday integrity

The checkpoint stores every completed session candle from session open through processed progress, using timestamp/open/high/low/close/volume. A later fetch compares all stored rows before indicator calculation. Identical copies, including identical duplicates, are accepted. Changed OHLCV yields `PROCESSED_CANDLE_REVISION` with symbol, canonical timestamp and differing field names. Omission yields `MISSING_PROCESSED_CANDLE`.

The chosen omission policy is strict rejection; the implementation does not silently substitute the stored copy. Either failure leaves durable progress/events/candles unchanged and emits no new signal. Tests independently demonstrate a revision that would create a real production signal and its reverse that would remove one; both are blocked before indicators. Same-session comparisons do not leak across rollover. Prior session entries are archived so their events retain verifiable evidence. Successfully processed latest candles remain process-once across restart without unnecessary acquisition.

## 5. Drain scheduling

`final_opportunity = close + drain - min(1 second, drain/2)`. Ordinary polls continue until this time, plus exactly one final opportunity if the last candle remains missing. For a run beginning at close:

| Configuration | Acquisition opportunities when still missing |
|---|---|
| 120s drain / 60s poll | +0, +60, **+119 seconds** |
| 30s drain / 10s poll | +0, +10, +20, **+29 seconds** |

Both acquisition authorization and accepted response must occur **strictly before** the effective deadline, the earlier of per-acquisition timeout and close-plus-drain. Exactly-at-deadline begins/returns and later results are ineligible. A result returning at +120 or +120.01 is rejected before indicators. No acquisition starts at +120; post-session candle labels never become eligible. The final opportunity is a separately charged logical/physical attempt, never a free retry. If the final candle was processed earlier, the run exits without that extra fetch.

The 120/60 matrix captures availability at +70, +90 and +119 through the +119 poll. The matrix also covers +1, +50, +60, +61, exactly expiry, after expiry and never, with both 15:15 and 15:30 calendar closes and the shorter drain. A mocked HTTP test follows the unchanged reviewed budget/send-marker path: three intraday calls and one broad history call spend four distinct reservations/attempts before evaluation. All results must still return within the remaining deadline.

## 6. Pending delivery reconciliation

Publication atomically persists the evaluation and stable context-bound event ID as `PENDING` before console output. Output callback return followed by durable acknowledgement produces `ACKNOWLEDGED/OUTPUT_RETURNED`. Failure or interruption leaves uncertainty explicit. The output-acknowledgement update uses a copied state, preventing an unsuccessful write from acknowledging in-memory evidence.

At startup/before ordinary processing, unnotified pending IDs produce `PENDING_ALERT_REQUIRES_RECONCILIATION`, with original timestamps, IDs and count. This is a recovery notification, not a new current signal or automatic alert redelivery. Every symbol status also exposes `pending_count`, `pending_event_ids` and `oldest_pending_timestamp`, including advancing `EVALUATED` polls. Notifications are attempted once per event per process; pending summaries remain visible if console output fails.

Local reconciliation uses `monitor.acknowledge_pending(event_id, confirmed_delivered=True)`, under the checkpoint lock and validation. It never calls market/provider transports. The caller must separately verify delivery before explicitly confirming; it durably records `OPERATOR_CONFIRMED`. Omitted confirmation is rejected. Repeating a confirmation is idempotent. A new signal can coexist with an older pending alert without hiding or duplicating its identity.

## 7. Checkpoint logical validation

The V3 schema fails closed as `CHECKPOINT_STATE_INVALID` rather than resetting or repairing inconsistent state. Load validates:

- Paired identity marker, checksum, unique JSON fields, exact schema and pinned source/calendar/configuration identity.
- Canonical historical label set, ordered complete current-session prefix through progress, OHLCV validity and all hashes.
- Session/date identity and progress completion before authoritative current time; no unexplained future/orphan candle.
- Ordered evaluation journal, exact previous/through transitions, initial-latest-only rule and evaluated-row counts.
- Acquisition/decision/deadline clocks, including exact bounded deadline and completed-candle timing.
- Per-evaluation context hash over the stored candle prefix; unique ordered candidate timestamps within the evaluated range.
- Fifteen-minute suppression transitions, final suppression timestamp and journal/latest evaluation agreement.
- Signal counts equal referenced durable event counts; each reference exists exactly once; no orphan event.
- Stable event ID, stock/session/timestamp/close/context agreement, frozen-condition eligibility and finite alert values.
- Mutually consistent pending/acknowledged delivery state and acknowledgement kind; no duplicate event identity.
- Archive session uniqueness and chronology; archived events remain linked to their original evidence.

The 13 valid-checksum corruption variants include count=200, missing/duplicate/orphan event, contradictory delivery state, wrong symbol/date, future timestamp, missing/orphan candle evidence, impossible transition, history-session mismatch and bad context identity. Each preserves the corrupt file unchanged and performs no acquisition.

## 8. Research collector safety

Modified entrypoints: [cost_backtest.py](C:/Users/Priyanka/Desktop/ai-trading-bot-research/cost_backtest.py:44), [historical_robustness_test.py](C:/Users/Priyanka/Desktop/ai-trading-bot-research/historical_robustness_test.py:89), [multi_stock_research.py](C:/Users/Priyanka/Desktop/ai-trading-bot-research/multi_stock_research.py:27), [stop_loss_backtest.py](C:/Users/Priyanka/Desktop/ai-trading-bot-research/stop_loss_backtest.py:100). All invoke existing `merge_raw_candle_batches`; identical OHLCV overlaps deduplicate and conflicting open/high/low/close/volume raises `CandleDataError` regardless of response order. No historical research output was regenerated.

Repository search also assessed remaining matches:

- `app/candle_integrity.py` keeps a first row only after conflict validation: defended.
- `app/paper_session.py:completed_frame` calls `candles_to_dataframe` first, which rejects duplicate timestamps: defended before its legacy keep-last operation.
- `extended_historical_robustness_test.py:upsert_research_csv` strictly merges OHLCV; its remaining keep-last branch handles derived non-candle results.
- `event_oos_validation.py` selects the first signal per grouped event ID; this is event grouping, not raw candle ingestion.
- `strategy_backtest.py`, `expanded_event_oos_validation.py` and both downloader merge paths already use strict merging; preserved.
- **Remaining unsafe legacy CSV context readers:** `run_oos_context_experiment.py:load_context`, `run_stage9_candidate.py:load_context`, `run_stage10a_entry_regime_diagnostic.py:load_context`, and `backtests/stage11_research/research_core.py:load_inputs` select timestamp duplicates without local conflict validation. Their usual downloader validates ingestion, but the readers independently accept arbitrary CSVs without a mandatory binding/duplicate check. These were reported rather than blanket-edited; they are outside monitor acquisition and the four targeted collectors.
- Protected baseline/corrected copies beneath `backtests/boundary_correction_review` retain historical implementations and were left unchanged.

## 9. Indicator determinism evidence

All values below use real production indicators/detector on the same synthetic target candle, 2026-10-01 09:20 IST. An older 40-point loss makes variable recursive seeding visible.

| Context | Historical rows used | RSI14 | MACD | MACD signal | Frozen signal |
|---|---:|---:|---:|---:|---|
| 36 | 36 | 21.826280623608 | -0.289482563539 | -0.103488869913 | true |
| 50 | 50 | 21.826280623608 | -0.289482563539 | -0.103488869913 | true |
| one_session | 72 | 18.341072190088 | -0.626890355893 | -0.596973547860 | false |
| two_sessions_canonical | 144 | 18.341072190088 | -0.626890355893 | -0.596973198509 | false |
| larger_reference | 644 | 18.360555020957 | -0.626500705459 | -0.596400184820 | false |
| canonical_plus_100 | 144 | 18.341072190088 | -0.626890355893 | -0.596973198509 | false |
| canonical_plus_500 | 144 | 18.341072190088 | -0.626890355893 | -0.596973198509 | false |
| restart_frozen | 144 | 18.341072190088 | -0.626890355893 | -0.596973198509 | false |

The 36/50/one-session/larger rows are comparison calculations, not alternate contexts accepted by the monitor. For canonical, +100, +500 and frozen restart, exact float equality is asserted, not approximate tolerance. Separate positive-signal fixture cases verify unchanged RSI 21.826280623608 and a signal across extra-prefix/reordering cases. Existing production RSI formula and detector-mutation negative controls remain intact. Ten additional offline hash-binding probes independently change every OHLCV field in historical and intraday context; all change the hash while original inputs retain their identity. These supplementary probes are retained externally in [test_context_identity.py](C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/signal-monitor-v3-089d56dbe9164fe595d8c37c0b7f04b5/test_context_identity.py). Raw measured evidence: [indicator-stability.json](C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/signal-monitor-v3-089d56dbe9164fe595d8c37c0b7f04b5/v3-final2-tmp-61bdff3b35dc421f8a4b7ea51d00730c/test_real_recursive_indicator_0/indicator-stability.json).

## 10. Test results

| Suite | Passed | Failed | Skipped | Deselected |
|---|---:|---:|---:|---:|
| V3 focused | 86 | 0 | 0 | 0 |
| V2 regressions | 62 | 0 | 0 | 0 |
| Complete monitor | 211 | 0 | 0 | 0 |
| Related safety | 333 | 0 | 0 | 0 |
| Research collectors | 48 | 0 | 0 | 0 |
| Full offline | 1353 | 0 | 0 | 0 |

Every final run exited zero with zero pytest errors. These rows overlap; they must not be summed as unique tests. All acquired market/provider responses are synthetic or mocked; the full suite also reads existing local historical/evidence fixtures. Local calendars and production computations are real. No genuine observation/prediction was created.

Exact command prefix, run from `C:/Users/Priyanka/Desktop/ai-trading-bot-research`:

```powershell
.venv/Scripts/python.exe -B C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/signal-monitor-v3-089d56dbe9164fe595d8c37c0b7f04b5/run_offline.py MODE
```

Final passing modes, in required suite order: `v3-final2`, `v2-final2`, `monitor-final1`, `safety-final1`, `collectors-final2`, `full-final1`. Replace `MODE` with each listed literal. Each linked result includes the exact expanded pytest argv and external basetemp:

- `v3-final2`: exit 0; pytest errors 0; wall time 46.2s; [v3-final2-result.json](C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/signal-monitor-v3-089d56dbe9164fe595d8c37c0b7f04b5/v3-final2-result.json).
- `v2-final2`: exit 0; pytest errors 0; wall time 29.87s; [v2-final2-result.json](C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/signal-monitor-v3-089d56dbe9164fe595d8c37c0b7f04b5/v2-final2-result.json).
- `monitor-final1`: exit 0; pytest errors 0; wall time 95.28s; [monitor-final1-result.json](C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/signal-monitor-v3-089d56dbe9164fe595d8c37c0b7f04b5/monitor-final1-result.json).
- `safety-final1`: exit 0; pytest errors 0; wall time 215.35s; [safety-final1-result.json](C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/signal-monitor-v3-089d56dbe9164fe595d8c37c0b7f04b5/safety-final1-result.json).
- `collectors-final2`: exit 0; pytest errors 0; wall time 3.38s; [collectors-final2-result.json](C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/signal-monitor-v3-089d56dbe9164fe595d8c37c0b7f04b5/collectors-final2-result.json).
- `full-final1`: exit 0; pytest errors 0; wall time 894.75s; [full-final1-result.json](C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/signal-monitor-v3-089d56dbe9164fe595d8c37c0b7f04b5/full-final1-result.json).

Supplementary context-identity verification: `identity-final1`, 10 passed, 0 failed/errors/skipped/deselected; [identity-final1-result.json](C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/signal-monitor-v3-089d56dbe9164fe595d8c37c0b7f04b5/identity-final1-result.json). This external probe run is additional to the six project-suite rows and is not included in the full `tests` collection.

Related safety selection explicitly includes candle integrity, NSE session audit, core production indicators, preflight, Upstox deadlines, persistent request remediation, torn/complete seals, readiness evidence, V2 checkpoint/transport tests and signal-evaluation accounting. Full mode runs `tests`.

Before tests, fixtures/configuration and relevant imports were inspected. No pytest project configuration file was present; plugin autoload and cached addopts were disabled. Dotenv loading is disabled, real provider credential environment variables are removed, `.env` file reads are rejected, sockets/DNS and subprocesses are blocked. Python audit hooks reject writes outside the external root (including SQLite paths for full-suite execution). Autouse fixtures isolate logs and default monitor state. Provenance fixtures and full-suite logging are relocated externally. Python uses `-B` and pytest cacheprovider is disabled. Test state, budgets and mocked experiment-shaped fixtures are all temporary; protected experiments are only hashed/read.

Compilation: 12 Python files compiled in memory, exit zero, no bytecode written. Scoped whitespace checks passed against the seven starting copies plus both new tests; Git diff/stat captured. Details: [static-checks.json](C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/signal-monitor-v3-089d56dbe9164fe595d8c37c0b7f04b5/static-checks.json).

Development attempts are retained rather than hidden: system Python initially lacked pytest (no tests executed), then two test-helper timestamp-format errors and one timestamp-string assertion were corrected. A V2 request-count assertion was updated for the two-session policy, and recovery fixtures now hold failure consistent across both date requests. The first collector run failed because its assertion expected raw strings instead of the strict helper's normalized timestamps; only that test was corrected before rerunning collectors and the full suite. Production code did not change between the six passing suites. No thresholds or production indicator oracles were weakened. Final runs above supersede those failed development attempts.

## 11. Integrity verification

| Check | Result |
|---|---|
| External network requests | **NO** |
| Broker execution enabled | **NO** |
| Prospective experiment run | **NO** |
| Protected evidence changed | **NO** |
| Real decision log changed | **NO** |
| Operational monitor state changed by final tests | **NO** |

The initial inventory contains 617 files, including 426 existing protected files. All 426 protected hashes match before/after, including Sessions 02–05, `_02`, prior reports, data/calendar evidence, historical budgets and attestations. No file was removed. Operational logs, paper-state SQLite database, reports and diagnostic evidence match their baseline hashes; no real default monitor checkpoint existed at the starting inventory.

Real `logs/decisions.jsonl` SHA-256 remains `cc8c5b30954ee3aee4050209f7c6ecc8e6149f53c2128d54348e801b0c45ecf2`. Project hashes before/after the full final suite match exactly, including all edited source/tests. HEAD remains `a0789cdb5db50deece446005c40064ca5de989a4`. Only the seven intended existing files changed from the V3 starting snapshot, plus two new tests and this new report. Source data/strategy/capital/risk/transport settings and genuine artifacts were not regenerated or tuned. This report is published after final tests; it is the only planned project addition after the matched test snapshots.

Machine-readable evidence: [baseline.json](C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/signal-monitor-v3-089d56dbe9164fe595d8c37c0b7f04b5/baseline.json), [pre-full-integrity.json](C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/signal-monitor-v3-089d56dbe9164fe595d8c37c0b7f04b5/pre-full-integrity.json), [post-tests-integrity.json](C:/Users/Priyanka/.codex/visualizations/2026/11/03/01a1a0fd-3308-7ce2-8029-33d126edc703/signal-monitor-v3-089d56dbe9164fe595d8c37c0b7f04b5/post-tests-integrity.json). Earlier pre-test snapshots are also retained. The external harness/results are separate from operational evidence.

## 12. Remaining risks

- Finite canonical context guarantees repeatability, not equivalence to infinite history; the larger raw reference differs numerically as shown. The selected policy requires independent audit acceptance.
- The final scheduled acquisition is +119s (or +29s). Availability after that opportunity, scheduler delay, insufficient remaining network latency or exhausted request budget can still yield a missing candle. Late data is rejected; no replacement/refund is promised.
- Console visibility cannot prove human receipt. `OUTPUT_RETURNED` means callback return; uncertain deliveries need explicit operator confirmation and pending summaries must be monitored.
- V2 checkpoints are intentionally incompatible; no silent migration/reset is supplied. Source/configuration changes also invalidate identity. Session archives/journals grow and require future retention/performance review before prolonged operation.
- The four legacy CSV context-reader paths listed in section 8 still need separately scoped conflict validation; no new research conclusions should rely on ambiguous duplicate inputs there.
- Tests use synthetic/mocked acquisitions. Real transport latency/data provenance and actual experiment artifact chains were not exercised. This is an implementation report, not an independent audit; a separate read-only audit is still required.

## 13. Final readiness verdict

**READY FOR INDEPENDENT READ-ONLY AUDIT ONLY**

Offline remediation/regression work is complete. Prospective real-time paper trading, causal settlement and broker/live execution remain NO-GO. No trial or live acquisition is authorized by this report.

**No experiment authorized. Broker execution remains disabled.**
