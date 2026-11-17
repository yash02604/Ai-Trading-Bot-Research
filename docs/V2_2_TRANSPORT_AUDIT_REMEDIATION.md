# V2.2 transport audit remediation

## 1. Verdict

**REMEDIATION VERIFIED** within the reviewed offline scope. Scope is only the two confirmed MEDIUM transport-audit findings. The complete run and fresh review were performed on 2026-11-14; report finalization and current-source comparison were completed on 2026-11-15. Current functional/source/test/data files match the frozen tested copy; only this new report changed while results were documented. No network transport, schedule, broker action or experiment is authorized.

## 2. Finding 1 root cause

`integration_identity()` maintained a local list that omitted `prospective_paper_fills.py`. V1 imports its `_stamp`, and V2 inherits those time interpretation/observation checks. Existing V2 genesis separately pins that file; a new ledger could nevertheless accept stale transport review approval. The missing edge is an ordinary shared financial helper, not an existing-history rewrite.

## 3. Complete reviewed dependency set and pre-change map

This dependency review was recorded before changing production code. The existing V2 constructor already has an explicit financial `sources` set; reuse its coverage as a regression invariant without importing V2 into the pure contract (which would introduce a cycle). Promote the approval list into one explicit reviewed dependency constant and verify that it covers the engine's financial source set. Add the retained session manifest as active financial configuration. Hash exact reviewed bytes under stable repository-relative names; canonicalize the identity map, not source text/newlines. Absolute checkout/temp paths are not source identity.

| Reviewed files | Runtime reason |
|---|---|
| `app/research/v2_transport_contract.py`, `v2_transport_controller.py`, `v2_live_transport.py` | Quote age, receipt/availability, candidate expiry, per-send/deadline/SDK policy, batching, selection and admission orchestration. |
| `app/research/causal_forward_paper_v2.py`, `causal_forward_paper.py`, **`prospective_paper_fills.py`** | Authoritative financial validation/replay, inherited completed-candle/time ordering and shared `_stamp`; includes dynamically referenced/inherited helpers, not only direct controller imports. |
| `app/research/ai_led_guarded.py`, `ai_led_offline.py` | Strict proposals, immutable evidence encoding, completed warmup/liquidity and shared cost/risk admission. |
| `app/research/deepseek_prospective.py` | Snapshot availability, logical budgets, append-only records, ownership and cost-aware position helper actually reused. |
| `app/research/offline_session_calendar.py`, `prospective_preflight.py`; `app/session_manifest_v3.py`, `offline_market_data_v1.py` | Session attribution, candle completion/cutoff, dynamically selected retained-calendar loader and validation. |
| `data/audits/nse_cm_session_manifest_v3.json` | Active immutable session configuration consumed by financial calendar checks. |
| `app/execution_observations.py`, `market_analysis.py`, `candle_integrity.py` | Quote source/receipt parsing, bid/ask/depth, candle timestamps/normalization/structural integrity. |
| `app/upstox_client.py`, `client_attestation.py`; `app/v2/provider.py`; `app/config.py` | Actual read-only request boundary, deadline/retry/worker setup, attestation, SDK configuration and paper/model/timing settings. |
| `app/risk_engine.py`, `cost_model.py` | Current-equity sizing, financial acceptance, modeled charges/slippage/net reward. |
| `strategy_backtest.py` | Actual instrument registry used for request and proposal identity. No strategy algorithm changes. |
| `app/research/scheduled_forward_paper.py`, `paper_observer.py` | Approved start/control/attempt interpretation, timestamp parser and observer instrument registry used by the contract. |

The exact set is exposed as `REVIEWED_DEPENDENCIES`: 25 application source files plus the active manifest. Existing calendar loader and client attestation continue to verify their retained evidence/registries against pinned constants; underlying historical documents are not rehashed into a second source list or modified. Active config is separately canonicalized by `configuration_identity`; model, timing, budgets, market/provider/start flags and approval-record path remain bound. Credentials are never hashed/persisted. The existing configured approval-file path remains intentional configuration identity; source checkout paths do not.

Excluded: unrelated documentation/reports/tests, CSV logging, and separate unused modules such as reversal/backtesting/legacy reviewed-paper execution that are imported but whose helpers are not called by this integration's financial path. Where a required helper lives in a mixed-purpose module (for example `strategy_backtest.STOCKS` or `prospective_paper_fills._stamp`), the entire file is hashed. Standard library/installed third-party runtime packages are assumed to be the reviewed environment; this narrow source identity is not a supply-chain/environment attestation. No external witness or protection against deliberate coherent local replacement is claimed.

## 4. Approval-binding fix

Implemented: one explicit auditable tuple, exact per-file SHA256 byte hashes and a canonical relative-name/hash map. Missing/unreadable dependencies fail closed through existing readiness. Relevant source/config changes require renewed review; irrelevant unrelated file edits do not. Existing approvals and source-bound control/ledger identities are not silently migrated or rewritten.

## 5. Dependency-coverage rationale

The source map above follows runtime timing/validation calls, including inherited `_stamp`, dynamic calendar loading and existing V2 genesis dependencies. An AST regression asserts that every V2 financial source dependency is included, so the engine's source set cannot silently outgrow transport approval coverage. No broad repository import-closure hashing is introduced.

## 6. Finding 1 regressions

All 26 dependency mutation cases require identity change, readiness refusal, no acquisition and no new ledger/control directory, then regain readiness only after exact bytes are restored. Further regressions cover the V2 financial source-set inclusion invariant, irrelevant source/report edits, provider/market/budget/timing changes, copied approval at a changed configured path, identical bytes under relocated source root, enumeration-order canonicalization and byte-exact newline sensitivity. All use synthetic file-byte views or temporary copies; actual reviewed/protected source data is not changed.

## 7. Finding 2 root cause

`run_session()` placed final `transport.health()` inside the same exception block as authoritative replay/account projection. A health-only error erased valid reporting and changed a completed financial attempt into `FAILED_CLOSED / RECOVERY_REQUIRED`, blocking next-day progression. Request counts depended exclusively on optional health output.

## 8. Health/financial separation design

Implemented `_diagnostics()` outside financial finalization: preserve existing financial statuses (`COMPLETED`, `NO_OPPORTUNITY`, `FAILED_CLOSED`) and existing `observer_failures`/`warnings: [OBSERVER_UNAVAILABLE]`. Collect/schema-validate optional health under its own exception boundary at startup, loop and shutdown; no raw exception is persisted. Read logical request counts independently from reconciled durable budget records through `request_counts()`. Health failure cannot turn seven known reservations into zero. New optional summary `request_counts` and existing `external_requests`/observer activity counts receive the independent values; unreadable/inconsistent values are `None`, never fabricated zero.

Financial replay/projection failure remains in its original fail-closed boundary. A verified completed settlement plus health failure remains `COMPLETED` with an observability warning; no new status enum was introduced. Earlier startup/loop failures remain counted when shutdown health later succeeds. Existing observer publication failures still accumulate, independently of health projection. Optional health is omitted when request accounting cannot be trusted, so legacy raw-health defaults cannot substitute for missing verified counts. Genuine acquisition journal inconsistency is still rejected by unchanged request-time reconciliation before any send; count projection neither repairs/refunds journals nor authorizes requests.

No new financial status enum, settlement algorithm, recovery design or scheduler ownership policy is needed. A diagnostic count projection cannot repair/refund request journals or authorize an acquisition; the existing request-time reconciliation remains authoritative.

## 9. Finding 2 regressions

Regressions cover normal completion, final/startup/all health failures, malformed health, earlier error retained after later health success, real financial verification failure with/without health failure, next-day control progression, exact account/candidate/settlement totals, seven logical requests, repeat invocation/replay exactly once and unavailable counts. Missing, corrupt and empty/truncated budget fixtures explicitly produce unknown counts/warnings while preserving the verified settlement; a subsequent acquisition rejects `REQUEST_CONFLICT` without another mocked call.

## 10. Request-count preservation

The complete synthetic lifecycle has six market-data reservations and one provider reservation. Startup/final/all health failures retain **7** total requests and `{market_data_requests: 6, provider_requests: 1}`. Financial verification failure still reports the independently known seven requests while leaving unverified financial totals unknown and status fail-closed. If durable count evidence cannot be reconciled/read, counts are unknown with warnings; an absent file is not accepted as known zero when surviving sent/result records contradict it. Logical reservation counts remain separate from physical retries. No remote exactly-once claim.

## 11. Exactly-once evidence

Health-failure cases preserve one synthetic settlement, equity **1005.755590992**, cash, zero open reservation, P&L and modeled charges/slippage exactly as authoritative replay projects them. A subsequent same-day invocation after the market closes performs no acquisition, retains the same account and does not settle again. Next-day control validation accepts prior completed-with-warning attempts. Injected genuine financial verification failures remain `FAILED_CLOSED / RECOVERY_REQUIRED` and block next-day control progression. V2 settlement/recovery source files are byte-identical to baseline.

## 12. Focused results

All requests mocked; socket/DNS/credential loading denied; temporary state outside the project. Focused results:

| Run | Passed | Failed | Errors | Skips | Deselections | Console runtime |
|---|---:|---:|---:|---:|---:|---:|
| Initial red remediation regressions | 31 | 12 | 0 | 0 | 0 | 84.44s |
| Isolated red health-only completion | 0 | 1 | 0 | 0 | 0 | 12.28s |
| Initial combined remediation + all existing transport tests | 135 | 0 | 0 | 0 | 0 | 188.94s |
| Red missing/corrupt counter review edge | 1 | 1 | 0 | 0 | 0 | 23.53s |
| Final expanded focused regression | 513 | 0 | 0 | 0 | 0 | 584.28s |
| Additional empty/truncated-budget case | 1 | 0 | 0 | 0 | 0 | 13.46s |

Runs overlap and are not summed. The final expanded run collected 45 new cases before the last empty-budget parameter was added, plus all 92 existing transport cases, 271 causal/recovery/durability cases and 105 scheduler/observer/script cases. The new 46th case passed separately and is included in the full unfiltered run. No original regression was deleted, skipped, xfailed or weakened.

Exact command roots (PowerShell, repository cwd):

```powershell
$repo = 'C:\Users\Priyanka\Desktop\ai-trading-bot-research'
$evidence = 'C:\Users\Priyanka\.codex\visualizations\2026\11\05\01a1acdb-980d-7dd2-b495-2691796e9d33\transport-audit-remediation-20261114'
$guard = 'C:\Users\Priyanka\.codex\visualizations\2026\11\05\01a1acdb-980d-7dd2-b495-2691796e9d33\phase1-v22-audit\run_offline.py'
& "$repo\.venv\Scripts\python.exe" -B $guard tests/test_v22_transport_audit_remediation.py tests/test_v2_live_transport.py tests/test_v22_transport_engine.py tests/test_v22_transport_controller.py tests/test_scheduled_forward_paper.py tests/test_scheduled_remediation.py tests/test_paper_observer.py tests/test_forward_paper_scheduler_scripts.py tests/test_causal_forward_paper.py tests/test_causal_forward_paper_v2.py tests/test_causal_forward_paper_v2_recovery.py tests/test_causal_forward_paper_v2_durability.py -q -p no:cacheprovider --basetemp "$evidence\focused-final-tmp" --junitxml "$evidence\focused-final.xml" --tb=short
& "$repo\.venv\Scripts\python.exe" -B $guard 'tests/test_v22_transport_audit_remediation.py::test_counter_projection_cannot_report_false_zero_and_acquisition_still_fails_closed[empty]' -q -p no:cacheprovider --basetemp "$evidence\counter-empty-tmp" --junitxml "$evidence\counter-empty.xml" --tb=short
```

Initial combined command used the first four modules above and `focused1-tmp`/`focused1.xml`. Red commands used the new remediation module (`red-tmp`/`red.xml`), its `[all]` health-completion node (`health-red-tmp`/`health-red.xml`), and its counter-projection parameterized function (`counter-red-tmp`/`counter-red.xml`), with the same guard/cache/temp/XML flags. XML artifacts retain exact counts and durations; native-wrapper tests use the supported approved runner without security-policy changes or task registration.

## 13. Full-suite results

Fresh complete unfiltered offline run on **2026-11-14**: **2,112 passed, 0 failures, 0 errors, 0 skips, 0 deselections; process exit 0**. Console runtime **1,298.68 seconds (21 minutes 38 seconds)**; JUnit runtime **1,298.464 seconds**; outer process wall time **1,304.2241215 seconds**. The 2,066-test baseline plus 46 new repository regressions equals 2,112. No count mismatch. External fresh-review probes are separate and are not added to this total.

The run used a byte-identical external source copy and the unchanged offline environment snapshot from the independent audit. No package installation/update occurred. Source/test hashes before and after the run show **zero changes**. On 2026-11-15, the current 662-file inventory was compared to the tested-copy input inventory: only this new report differed; no functional source, test, configuration, data or protected evidence changed. Therefore the recorded test result applies to the current patch; no second full run is falsely claimed.

Exact executed child command, with the variables defined in section 12:

```powershell
Set-Location -LiteralPath "$evidence\repository-copy"
& "$evidence\repository-copy\.venv\Scripts\python.exe" -B $guard -q -p no:cacheprovider --basetemp "$evidence\full-tmp" --junitxml "$evidence\full.xml" --tb=short
```

External evidence driver `run_full.py` recorded the child command/cwd, timestamps, exit code, source/test hashes and runtimes. Artifacts: `full-command.json`, `full-result.json`, `full.log`, `full.xml`, `after-code.json`, `before-final.json`. All HTTP/SDK calls were mocked; dotenv/credential loading and socket/DNS access were denied. Native wrapper tests used the supported approved runner, with no security-policy change or task registration/activation.

## 14. Fresh review

Fresh non-implementing reviewer `/root/fresh_transport_remediation_review_20261114` reviewed only approval coverage and health/financial isolation. It found the new count projection could report a missing journal as zero despite surviving evidence. This was reproduced (1 failure/1 pass), fixed by reusing existing record reconciliation, and rechecked. Final outcome: **no remaining confirmed actionable issues in scope**.

Independent final external matrix: **13 passed, 0 failures/errors/skips/deselections; console 105.47s, JUnit 105.468s, exit 0**. Earlier 12 are a subset and are not added again. Covers health-only/publish-only errors, missing/corrupt/empty journals and subsequent acquisition refusal without new I/O, unreadable representative financial dependencies, and genuine financial verification failure with startup/final/all/malformed health.

External report, exact command and artifacts: `$evidence\review\reviewer-findings.md`, `postfix.xml`, `postfix.stdout.txt`, `test_fresh_review.py`. This scoped implementation review is not the separate independent read-only remediation verification required before any future authorization.

## 15. Files changed

Modified production: `app/research/v2_transport_contract.py` (explicit approval dependency set); `app/research/v2_transport_controller.py` (separate diagnostics/financial finalization); `app/research/v2_live_transport.py` (independent reconciled count projection); `app/research/scheduled_forward_paper.py` (optional independent reporting counts only).

Added: `tests/test_v22_transport_audit_remediation.py` (46 deterministic cases) and this report. No other project file changed. All inventories, temporary ledgers, review probes, copied environment and run artifacts are external.

## 16. Preservation

Before changes: 660 inventoried files; SHA256 `188c547b575d243f819ca0054c74189e453cd20c4eef664c238155f7f1dc02b3`. Final scope: **662 files, 4 intended existing files modified, 2 added, 0 removed, 656 original files unchanged**, with no unrelated changes. Per-file SHA256 evidence is retained in external `before.json`/`after-final.json`; `preservation.json` checks the exact allowed change/addition sets. The report's own final hash is recorded externally to avoid a circular self-hash.

Protected overlapping groups remain byte-identical: **22 Session 02–05 files, 393 historical/data/evidence files, 5 real log/state files and 47 prior documents**. V2 and V1 settlement/recovery source, strategy/risk/cost/calendar rules, candle validator, CSV logger, shared Upstox/DeepSeek clients and task scripts are unchanged. Scheduler ownership, control-root validation, immutable attempt sequencing, identity/preflight/start/prepare functions are AST-identical to the audited baseline; only snapshot/summary reporting changed. HEAD remains `a0789cdb5db50deece446005c40064ca5de989a4`; no commit, push or history rewrite occurred.

A supported read-only local task query found no registered `AITradingBot-ForwardPaper` task (`CmdletizationQuery_NotFound_TaskName,Get-ScheduledTask`); no task was registered, enabled or started. Evidence: external `task-inventory.json`.

Evidence root: `C:\Users\Priyanka\.codex\visualizations\2026\11\05\01a1acdb-980d-7dd2-b495-2691796e9d33\transport-audit-remediation-20261114`.

## 17. Remaining limitations

Profitability and real-market readiness remain unproven. Local approval is an operator assertion, not independently signed authority. Joint consistent database-and-anchor rollback remains undetectable without a separately trusted witness; no extra local hash/file solves it. Quote timestamps are response-generated, not authenticated depth-event times. SDK/request-side bounds and unknown remote outcomes do not prove provider-side exactly-once execution or cancellation of an already submitted remote request.

## 18. Authorization status

Remediation and offline verification are complete. No enabled production configuration or positive production approval was created. No real market/provider/NSE/broker request, task activation, broker execution or real-time paper/causal settlement experiment occurred. The default-off opt-ins and broker boundary remain intact. Fresh scoped implementation review does not replace the separate independent read-only remediation verification required before any future authorization.

**REMEDIATION VERIFIED. The V2.2 transport approval now binds all reviewed financial timing/validation dependencies, and health/observer failures are isolated from verified financial completion and recovery state. Existing causality, freshness, budgeting, replay, risk, cost, settlement, scheduler and broker-boundary controls remain intact. Ready for independent read-only transport-remediation verification only. Do not enable network transports, activate the schedule, or start a real-time forward-paper session. Broker execution remains disabled.**
