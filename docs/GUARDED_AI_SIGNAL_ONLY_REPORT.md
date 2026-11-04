# Components Reused

Inspected the observation replay, strict fill validator, existing guarded/reviewed/offline/recovery flows, Upstox client, DeepSeek provider, calendar, attestations, request budgets, quantitative baseline, configuration and tests. Preserved the pre-existing Git diff and task-start hashes for 501 existing source/evidence files.

Reused the exact `_ReviewedRun` transport binding, bounded Upstox GETs, no-retry DeepSeek SDK adapter, current-session/attestation preflight, durable send/receipt ledgers, persistent reservations, immutable file helpers, complete-batch candle validators, frozen normalized inputs, strict proposal/rationale parsing and independent response seals. Quote observations use the existing immutable observation normalizer and prefix replay. The quantitative comparison calls the existing frozen reversal predicate without changing its thresholds.

No second network client, live WebSocket connection, scheduler or background polling loop was added. Existing paper execution and settlement continue through their original authorization checks and shared transport implementation.

# Signal-Only Architecture

Added mode `AI_SIGNAL_ONLY` to the existing `scripts/run_ai_led_prospective_paper.py` CLI. Collection requires `--signal-only --enable-signal-only --allow-market-data-requests --allow-provider-requests`, plus the existing explicit universe, capital, output and caps. These flags do not enable execution. Combining signal mode with paper-entry or settlement authorization is rejected. Missing authorization blocks before output initialization or requests.

The isolated `signal_experiment.json` manifest freezes mode, configured capital/universe/model, source identities, transport limits and evaluation policy before requests. It cannot adopt a pre-existing experiment directory, change capital/caps/policy silently or call the paper account engine. A complete configured candle batch must validate before provider reservation. Optional `--include-quotes` adds the existing fresh-response/depth-shape checks and UNKNOWN-freshness observation evidence; genuine book freshness is not asserted or required for a prediction.

The AI independently proposes BUY opportunities or NO_TRADE over the whole configured universe. The frozen quantitative predicate is recorded only as a comparison. It never filters the AI universe or creates a paper position. Forecasts may be recorded even when the research capital could not buy a whole share, because these are predictions rather than trades. All records have `taken: false`, `execution_enabled: false` and `trade_placement_enabled: false`. No PENDING/OPEN state, fill, execution cost, P&L, quota consumption or balance mutation is generated.

`--signal-only --recover-only --enable-signal-only` performs local evidence recovery without request flags or API credentials. It reuses saved valid responses and original receipt/decision-ready timestamps, never a new provider evaluation. Missing response evidence after a spent/interrupted request fails closed rather than reconstructing an AI decision. Incomplete inputs, seals or source evidence also fail closed. Repeated completed recovery verifies and returns the identical immutable signal.

# Files Changed

Existing files modified:

- `app/research/ai_led_reviewed.py`: adds signal/recovery purposes, delegates signal-specific authorization, shares the existing SDK send boundary, optionally records quote envelopes, and keeps paper-entry authorization separate.
- `scripts/run_ai_led_prospective_paper.py`: adds mode-specific signal opt-in and local recovery routing; existing runner retained.

New files:

- `app/research/ai_signal_only.py`: isolated manifest, acquisition orchestration, immutable prediction evidence and recovery.
- `app/research/signal_evaluation.py`: pure saved-data movement evaluation and descriptive summaries.
- `tests/test_ai_signal_only.py`: mocked collection, recovery, safety and evaluation tests.
- `docs/GUARDED_AI_SIGNAL_ONLY_REPORT.md`: this report.

The strict fill validator, market-observation replay, Upstox transport, provider adapter, risk/cost modules, frozen strategy, historical artifacts, budgets and all source attestations were not edited. Existing source-hash-bound experiments are never rewritten/migrated to the new implementation. Reusing a manifest pinned to an older source version remains fail-closed.

# Prediction Evidence Schema

`ai-signal-only-v1` records include:

- Mode, decision ID and configured instrument priority.
- Common candidate timestamp and original market/batch decision timestamp.
- Provider request ID, request hash, raw response hash where available, requested/validated model identity.
- Response-receipt time and validation-complete `decision_ready_at` for valid predictions.
- Validated action, bounded confidence score, entry/stop/target/holding proposal and allowlisted rationale codes.
- Frozen evaluation policy, input hashes, source label, observation-prefix hash, reference closes and quantitative baseline flags.
- UNKNOWN depth freshness and unestablished tick-size verification, plus disabled execution flags.

Request-send time is linked through the immutable provider request ID to `transport_sent.jsonl`; response times and hashes are cross-checked with `transport_receipts.jsonl`. Valid timing requires original batch time <= send <= receipt <= decision-ready, before the existing next-five-minute acquisition/decision deadline. Recovery preserves those original times rather than setting a new historical decision time.

Original normalized Upstox received snapshots and frozen input copies have separate hashes linked through `capture_sources`. Source identity, instrument, observation time and numeric candles must agree during recovery. Quote envelopes represent canonical decoded REST JSON, explicitly not original HTTP wire bytes. Their `RECORDED_UNAUTHENTICATED` label and UNKNOWN freshness remain unchanged through replay. With quotes disabled, the observation prefix is explicitly empty; candle input hashes and receipts still bind the complete decision batch.

Only validated numeric proposals, known model identity and bounded rationale labels are persisted from provider content. Raw unrestricted response text and arbitrary exceptions are not stored; a response hash is not provider-origin authentication. Transport receipts establish locally asserted prospective evidence, not independent timestamp signatures. Mock transports in tests demonstrate correctness only and do not become genuine historical AI performance.

# Provider Budget and Preflight

Default CLI behavior remains network-disabled. Signal authorization is distinct from paper-entry authorization. All actual sends still pass current settings/model checks, client attestation, verified session/calendar, immutable configuration, persistent reservation and single-use durable send marker. Direct `fetch`/`complete` calls cannot skip those guards. There is no alternate SDK or arbitrary callback/URL input.

Collection performs one bounded complete batch per invocation, with no polling. For N stocks, candle-only acquisition normally reserves N logical market-data requests; including quotes normally reserves 2N. Each logical Upstox request retains the existing maximum three physical attempts, deadline and session-aware retry behavior. DeepSeek retains zero SDK retries. Attempts/failures retain their existing reservations; there are no refunds or cap resets. A provider cap of zero produces a distinct budget-exhaustion record rather than a fabricated NO_TRADE prediction. Failed and invalid provider attempts remain distinct from genuine NO_TRADE.

Capital is immutable experiment metadata and remains any valid positive configured amount. Tests cover 1000, 5000 and 10000, including forecasts priced above the smallest account. No account balance is fetched, initialized or changed. Existing risk/cost metadata may appear in the reused configuration structure; it is not an executable account or a bypass of risk checks.

Published circuit bounds are checked against proposals when optional quote evidence supplies them. Missing tick-size/security-class metadata is not invented, and the record does not claim executable prices. The existing mapped NSE cash universe is configurable; arbitrary unmapped identifiers are rejected. Verified continuous sessions, holidays, warm-up, liquidity, completed-candle freshness and unsupported special-session restrictions are retained. No additional metadata API call was introduced.

# Offline Prediction Evaluation

`evaluate_signal(decision, future_snapshots, *, as_of, calendar)` accepts an integrity-checked saved prediction and numeric future candle bodies. It has no provider, broker, account or file-writing operation. `summarize_predictions(decisions, evaluations)` rejects duplicate IDs and policy/decision-hash mismatches.

Policy `signal-movement-evaluation-v1` is frozen in the manifest before collection:

- Observe six complete five-minute bars beginning at the first full five-minute boundary strictly after decision readiness.
- The observation window is 30 minutes, ending approximately 30-35 minutes after readiness. Reported percentage movement compares the original decision-batch latest completed close with that window's final close; it is not a fill-based return or exact 30-minute holding return.
- Require every expected bar, correct identity, valid OHLCV, a subsequent observation time no later than explicit `as_of`, and a horizon within the original verified session. Missing, invalid, not-yet-observed or session-crossing data remains incomplete, never overnight-filled.
- Record UP/DOWN/FLAT movement, AI and quantitative selection flags on the same candidate/universe, and later observed stop/target relationships.
- Both levels touched in the first relevant same candle produce AMBIGUOUS; the evaluator never applies execution-engine stop precedence or infers an executable exit.
- NO_TRADE frequency excludes failed/invalid provider attempts; candidate coverage is proposals divided by valid prediction-universe opportunities.
- Confidence bins appear only after 30 selected observed examples. They are descriptive score/up-frequency diagnostics, not proof that model scores are calibrated probabilities or that the sample is statistically sufficient. Missing outcomes and differing AI/baseline selection coverage must remain visible.

Every evaluation binds the exact prediction hash and policy hash. Outcomes are returned in memory, not appended to original signal evidence. `execution_pnl` is always null. No share sizing, fees, fills, paired trading performance or profitability is calculated. Future observations are used only for subsequent labels, never prompts, source-batch selection or proposal generation. Do not tune selection on these outcomes and present the same sample as validation.

# Tests and Verification

Added 32 mocked tests for complete multi-stock prediction batches; independent AI BUY despite a false quantitative predicate; NO_TRADE/failure separation; strict model/symbol/score/rationale/schema handling; point-in-time inputs; opt-ins; bounded budgets; current calendar/attestation failures; no account/fill calls; immutable capital; source tampering; publication interruption/local recovery without keys or requests; spent-request non-reexecution; CLI mode separation; published price bands; frozen evaluation policy; missing/ambiguous future observations; duplicate and identity rejection; and descriptive confidence diagnostics.

Focused command:

```powershell
.venv\Scripts\python.exe -B -m pytest tests/test_ai_signal_only.py -q -p no:cacheprovider --basetemp C:\Users\Priyanka\.codex\visualizations\2026\10\30\01a18b43-af58-74b1-ad68-35a667601200\.pytest-ai-signal-focus-final
```

Result: **32 passed in 63.92 seconds**. The earlier subset passed 27 cases; these counts overlap. An additional exact-decision/policy aggregation binding assertion is included in the final full regression run.

Full relevant regression command:

```powershell
.venv\Scripts\python.exe -B -m pytest tests/test_ai_signal_only.py tests/test_market_observation_replay.py tests/test_prospective_paper_fills.py tests/test_pending_entry_resolution.py tests/test_upstox_deadlines.py tests/test_prospective_lifecycle_fixes.py tests/test_ai_led_reviewed.py tests/test_ai_led_guarded.py tests/test_ai_led_offline.py tests/test_ai_led_replay.py tests/test_prospective_preflight.py tests/test_readiness_evidence.py tests/test_offline_market_data_v1.py tests/test_deepseek_prospective.py tests/test_deepseek_prospective_summary.py tests/test_cost_model_execution_integrity.py tests/test_core.py tests/test_deepseek_eval_v1.py tests/test_ai_v2.py tests/test_ai_v2_evaluation.py tests/test_provenance_audit.py tests/test_execution_observations.py tests/test_execution_integrity.py tests/test_paper_session.py -q -p no:cacheprovider --basetemp C:\Users\Priyanka\.codex\visualizations\2026\10\30\01a18b43-af58-74b1-ad68-35a667601200\.pytest-ai-signal-regression-final
```

Result: **754 passed in 363.07 seconds, without exclusions or failures**: all 722 previously relevant cases plus 32 new cases. This is the full relevant offline selection listed above, not a claim that every unrelated repository test was run.

All five changed/new Python files passed in-memory compilation with `.venv\Scripts\python.exe -B -` and `compile(path.read_bytes(), str(path), 'exec')`. All six changed/new files passed targeted whitespace checks using `git -c core.autocrlf=false -c core.whitespace=blank-at-eol,blank-at-eof,cr-at-eol diff --no-index --check <task-start-copy-or-empty> <current-file>`. The final diff was inspected, including untracked implementation files omitted by ordinary Git diff.

The 501-file task-start inventory identifies exactly the two existing integration files listed above as modified; all other existing inventoried files remain unchanged. All **359 protected historical evidence files** match their prior SHA256 inventory, including Sessions 02-05, budgets, historical reports and original attestations. The strict fill policy, market-observation module and Upstox source bytes are unchanged. Pre-existing tracked Git modifications were preserved. No real API calls, tokens, captures, broker orders, Session 06 or real-data experiment occurred; all mocked experiment artifacts are under external temporary test directories.

# Remaining Limitations

No actual Upstox/DeepSeek collection was performed. Mocked SDK/HTTP behavior does not verify current provider availability, credential validity, live performance or profitability. Genuine depth freshness, tick-size metadata and executable fills remain unestablished. Observation/response hashes establish local integrity, not independent provider authorship. Interrupted requests with no saved response intentionally block instead of being regenerated. Multiple-stock quote collection can exceed the existing five-second response freshness window and must fail closed. Provider-budget exhaustion does not authorize another evaluation or an automatic new experiment.

The offline evaluator requires separately supplied future observations; it does not autonomously acquire outcomes. A short or incomplete session may produce insufficient future data. Confidence scores are not established probabilities. Baseline/AI comparisons are descriptive prediction comparisons, not trading-strategy performance. Source-hash-bound previous experiments require their original source versions for identity-compatible resume; no historical manifest is migrated here.

# Readiness for First Genuine AI Signal Trial

The implemented path supports bounded, operator-authorized **signal-only research**, subject to final read-only review, current verified calendar coverage, deliberate fresh-directory policy/caps and separate credential confirmation. It creates predictions only. Genuine trial readiness is not evidence of predictive skill, real-time simulated execution readiness or permission to place orders.

No real-data experiment was initialized in this task. A prospective fill or observed-feed settlement trial remains NO-GO under the unresolved source-freshness and causal-execution contracts. The existing strict fill validator is not weakened, called to manufacture eligibility, or relabeled as accepting real Upstox depth.

# Broker Execution Status

Disabled. Signal-only records and transports have no broker-order or live-balance capability. No sandbox or real execution approval is implied.

# Recommended Next Step

Perform a read-only safety review of mode isolation, preflight/budget guards, provider evidence, recovery and the frozen evaluation policy. Then explicitly authorize a small signal-only trial if desired, with market-data/provider caps and a fresh directory. Do not start Session 06, execute any real provider command or infer profitability from the offline test results.
