# Phase 1 — Independent V2.2 read-only audit

## 1. Audit scope

Offline review of the current Windows V2.2 causal implementation, its inherited lifecycle, local durability/recovery, accounting, source binding, request reservations/retries and relevant security boundaries. No real acquisition, provider request, experiment, order, migration or production remediation is authorized by this report. The user explicitly selected **keep Phase 1 read-only; report the defect** after a material adjacent validator defect was reproduced.

Fresh-context reviewers separately reviewed correctness, architecture and security. A fresh correctness reviewer owns the complete repository rerun and authored additional independent synthetic invariant checks. The coordinating agent previously implemented V2.2; that continuity is disclosed. This is fresh-agent scrutiny within the same workspace, not certification by an unrelated organization or human auditor. The coordinator independently assessed findings and owns the verdict.

## 2. Repository state

Repository: C:/Users/Priyanka/Desktop/ai-trading-bot-research. HEAD: a0789cdb5db50deece446005c40064ca5de989a4. The starting working tree already contained tracked edits and untracked causal modules/tests/reports. Their status is captured externally; no clean-tree assumption was made.

Starting protected inventory: 634 files, canonical manifest SHA256 8f3a4d75e388387046734f124cac816a202cee24fa03a39b95ec6ec18811fb84. Authoritative V2.2 implementation SHA256: f77facd4d0fa3b844a66ebdb3cd34e9b564dc7dffe150ffc03d76f8e10e68b90. MODE remains CAUSAL_FORWARD_PAPER_V2; POLICY remains causal-forward-candle-v2. Configuration binds source files, calendar identity, instruments, capital, offline provenance and risk policy. Configuration was checked in code, not inferred from reports.

Final preservation comparison: all 634 pre-existing files match, including all 627 historical-baseline files, 22 Sessions 02–05 files and five real log/report/paper-state/WAL/SHM files. The same 634-path protected manifest hashes to 8f3a4d75e388387046734f124cac816a202cee24fa03a39b95ec6ec18811fb84 before and after. Historical retained manifest SHA256 is 14f4918bec9fe8bf2a9de92280c46c2492dea25400b42954520752b9baadce58. HEAD is unchanged; status adds only this report. Exact comparisons are retained in external preservation.json; this is historical proof against retained manifests, not a new baseline presented as old evidence.

## 3. Files reviewed

Correctness: app/research/causal_forward_paper_v2.py, causal_forward_paper.py, ai_led_offline.py, ai_led_guarded.py, ai_led_reviewed.py, ai_led_recovery.py, deepseek_prospective.py, signal_request_budget.py, signal_observations.py, offline_session_calendar.py; app/risk_engine.py, cost_model.py, upstox_client.py, offline_market_data_v1.py and session_manifest_v3.py. Supporting acquisition consumer: scripts/run_acquisition_pilot_v1.py:388. Supporting evaluation CLI: scripts/run_deepseek_research_eval_v1.py:102–120.

Relevant tests: test_causal_forward_paper.py; test_causal_forward_paper_v2.py; test_causal_forward_paper_v2_recovery.py; test_causal_forward_paper_v2_durability.py; test_ai_led_reviewed.py; test_signal_complete_seal_integrity.py; test_signal_torn_seal_recovery.py; test_offline_market_data_v1.py. Prior V2/V2.1/V2.2 remediation reports were background evidence, not accepted as proof.

Security baseline fully read 44 app files; the focused reviewer fully read the remaining 16. The canonical security coverage artifact records the union of all 60 app source files. Supporting scripts were reviewed at relevant consumer sites; no claim of a complete repository-wide security audit or installed-dependency advisory scan is made.

## 4. MCPs used and purpose

Superpowers supplied the primary structured planning and verification workflow: preserve baseline, separate hypotheses from findings, trace controls, reproduce defects, verify results before conclusions. Its workflow is available as local skills; no separate Superpowers MCP server was exposed.

Caveman's read-only explorer workflow supplied compact verified locations for reservations, retries, seals, ranking and source binding. The persona-changing Caveman Mode MCP was not used. Fresh architecture review was a separate source-only assignment.

Codex Security MCP completed scan 05091814-06aa-47e2-ad10-8f3a890f5528 against app. Capability preflight was ready with a nonblocking worker-capacity warning; no persistent configuration was changed. A baseline and focused reviewer supplied source evidence; the coordinator validated the reportable finding. Canonical report, threat model, findings and coverage were finalized by the plugin, not handwritten.

CodeRabbit was not used: production code did not change. No builder/refactor workflow or unrelated plugin was used.

Host-reported cumulative usage at security finalization was 17,496,999 tokens, including 17,094,144 cached input tokens, across three recorded threads. This is rollout/thread accounting, not a measured isolated marginal cost of this audit.

## 5. Commands executed

All commands ran from the repository root. Exact argv/environment metadata for the fresh complete rerun is retained in the external evidence directory below. The full command has no test path, -k or -m filter.

```powershell
$Python = 'C:\Users\Priyanka\Desktop\ai-trading-bot-research\.venv\Scripts\python.exe'
$TempRoot = 'C:\Users\Priyanka\.codex\visualizations\2026\11\05\01a1acdb-980d-7dd2-b495-2691796e9d33'
$Audit = "$TempRoot\phase1-v22-audit"
& $Python -B "$Audit\run_offline.py" -q -p no:cacheprovider --basetemp "$TempRoot\p1indfull" --junitxml "$Audit\independent-full.xml"
& $Python -B "$Audit\run_offline.py" "$Audit\test_independent_correctness.py" -q -p no:cacheprovider --basetemp "$TempRoot\p1extra" --junitxml "$Audit\independent-correctness.xml"
& $Python -B "$Audit\run_offline.py" "$Audit\test_independent_row_isolation.py" -q -p no:cacheprovider --basetemp "$TempRoot\p1rows" --junitxml "$Audit\row-isolation.xml"
git rev-parse HEAD
git status --short
```

The fresh reviewer confirmed the independent-check command used p1extra exactly as shown. The source-only search command was the installed native rg.exe; no bootstrap/download wrapper was used. Security preflight's exact command is retained in security-preflight.json. A coordinator-started full run was stopped before completion to transfer ownership to the fresh reviewer; it is excluded from completed verification counts.

## 6. Environment

Windows host; Python 3.13.2, 64-bit MSC build; project .venv; pytest 9.1.1. Asia/Kolkata NSE calendar semantics remain in source. No US-market assumptions or legal/regulatory readiness conclusions were applied.

The launcher disables dotenv before app import, clears environment keys containing API_KEY/TOKEN/SECRET/PASSWORD/CREDENTIAL, denies socket connect/connect_ex/sendto/create_connection and DNS lookup, disables bytecode and pytest cache. Existing conftest isolates default logs/state and requires mocked HTTP transports. Temporary ledgers, logs, XML and new audit probes are outside the repository. Child tests inherit cleared credentials and bytecode settings; existing abrupt-child tests include their own network denial. These are Python test safeguards, not an asserted OS-wide network sandbox. No credentials were used or printed; actual private .env values were not inspected.

## 7. Test results

The fresh reviewer completed the unfiltered repository suite and verified process exit plus JUnit; the coordinator independently read the final XML/log afterward. **The 1,641 passed result was reproduced.**

| Run | Passed | Failed | Errors | Skipped | Deselected | JUnit duration |
|---|---:|---:|---:|---:|---:|---:|
| Independent complete repository suite | 1641 | 0 | 0 | 0 | 0 | 1151.884s |
| Four fresh V2.2 invariant probes | 4 | 0 | 0 | 0 | 0 | 10.735s |
| Newly discovered row-isolation regressions | 0 | 2 | 0 | 0 | 0 | 0.660s |

Complete-suite console elapsed time: 1152.11s; fresh driver wall time: 1154.8858229s; exit 0. The external audit regressions are not part of the original repository discovery count and were not used to modify or dilute its tests. Their exit 1 and failures remain evidence of COR-1. No result from the interrupted coordinator run is counted. The table deliberately separates passing existing tests from newly failing audit probes.

Four independently authored V2.2 checks passed: repeated exactly-once 1,005 reconstruction; committed sequence 5→2 rejection without witness/database mutation; post-commit anchor-directory flush failure followed by exactly-once recovery; source-identity mismatch rejection on fresh restart. These checks are external and do not increase repository test discovery counts.

Two new external candle-row isolation regressions failed on the unchanged implementation, with zero errors/skips/deselections. Their expected classifications are correct; failures are the reproduced defect and have not been hidden, excluded or rewritten to pass. Repository-suite success cannot supersede these failures.

## 8. Lifecycle audit

**SUPPORTED BY CODE REVIEW:** upstream core parsing constrains known symbols, finite prices, holding horizon and score. ai_led_offline.py:410–424 deterministically sorts by score/instrument priority and independently checks size/cost eligibility. V2.2 itself accepts an upstream BUY proposal; it does not invoke ranking or an AI provider.

V1 propose:192–241 validates the proposal and computes the next slot. observe:243–312 checks expected slot, completion, timely receipt, symbol/instrument, OHLCV and acceptance clock. V2 inherits those mechanics. Entry rechecks current equity/cash and risk. _monitor:344–400 conservatively handles partial-entry timing, stop/target ambiguity, horizon and session closure, then applies costs once. Unavailable post-entry information becomes unresolved exposure, not an invented exit. Terminal retries return original outcomes; conflicting evidence quarantines the ledger.

The engine does not autonomously create missing observations if its caller stops invoking observe. External scheduling/completeness reconciliation remains an operational prerequisite, not proven by this library audit.

## 9. Recovery audit

| Starting state and fault | Expected / actual reviewed behavior | Evidence class |
|---|---|---|
| Valid committed ledger; clean restart | Reconstruct identical economic state | Independent synthetic check; existing restart tests |
| Pending intent, no SQLite commit; process interruption | Keep exact committed base; identical retry may complete once | V2.1 interrupted-publication tests |
| Settlement committed; anchor publication interrupted | Validate ancestry/intent and recover final once | Independent flush-failure check; existing boundary matrix |
| Owned writing prefix / complete ready | Verify ownership and bytes after ledger validation; quarantine and recover | Existing V2.1/V2.2 staging tests |
| Stale, corrupt, foreign, multiple or mixed staging | Reject without selecting an arbitrary candidate | Existing staging-rejection tests and source review |
| Torn final seal / incomplete seal | Preserve bytes, expose partial-publication status; never fabricate seal/outcome | test_signal_torn_seal_recovery; signal_observations.py:217–294 |
| Valid complete seal | Require scheduled identity, exact saved artifact hash and reconstructed record | test_signal_complete_seal_integrity |
| Duplicate field/row or inconsistent complete seal | Fail closed in strict signal-seal reader | Complete-seal tests; _unique_seal_fields |
| Corrupt event/checkpoint/settlement or mismatched identity | Full replay/byte checks reject | Existing V2 tamper tests; _verified:426–452 |
| Incompatible source identity on reopen | Reject before mutation | Independent source-binding check |
| Reserved request/send without saved response | Do not send again or refund request authority | test_interrupted_provider_is_not_retried_on_restart; _mark:240–256 |
| Saved sealed response; delayed recovery | Verify frozen request/receipts; expired entry cannot become a late fill | ai_led_recovery.py:95–246; related recovery tests |
| Pending entry reaches cutoff / final candle | Verified local rejection or entry cutoff; no invented completed-candle fill | resolve_expired_entry; causal cutoff tests |
| Prepared settlement after account commit | Accept only exact before/after identity; no second economic effect | finish_settlement:253 onward; related settlement tests |
| Unacknowledged empty genesis; second interruption after quarantine move | May remain permanently fail-closed with missing anchor/root stage | Source review only; nonblocking availability limitation |

The named existing test modules were included in the completed fresh unfiltered rerun. Their exercised cases are **PROVEN BY TEST** within those fixture/process assumptions; broader claims remain code-review-only or unproven. V2 anchor/event readers use ordinary json.loads, so strict duplicate-key rejection must not be generalized from signal seals to every JSON reader. Semantic replay still checks parsed V2 state; no independent financial/security bypass from alternate duplicate-key representations was established.

## 10. Durability audit

**SUPPORTED BY CODE REVIEW:** _atomic:109–127 writes/flushes/fsyncs writing bytes, renames to ready, synchronizes the parent directory, verifies exact ready bytes, replaces canonical, fsyncs canonical and synchronizes its directory. _apply:487–516 durably publishes intent before SQLite commit, then anchor, cleanup and return. _verified validates schema/config/replay/checkpoints/settlements/ancestry before staging action. _finish_publication:356–366 re-establishes flush boundaries before clearing pending metadata.

The independent directory-failure check proves no success return and exactly-once recovery for its exercised process/filesystem state. Existing tests cover before/after writing creation/fsync, ready rename/directory sync, canonical replacement/file/directory sync, anchor completion and lost acknowledgement. Their fixtures use deterministic exceptions/mocks; existing V2.1 tests also stop actual child processes. No host crash or physical power cut was attempted.

Windows writable directory handles use FlushFileBuffers; POSIX opens/fsyncs/closes a directory descriptor. Errors propagate. API success is an OS acknowledgement, not proof of device honesty or universal Windows/POSIX equivalence. The ledger directory must be provisioned reliably; its own entry in its parent is not separately flushed by V2.2. Native POSIX and genuine hardware/power-loss safety remain **NOT PROVEN**.

## 11. Ledger/accounting audit

**PROVEN BY INDEPENDENT TEST:** one settlement, deterministic 1,005 balance after 1,000 initial capital, unchanged settlement identity under repeated reopen/retry; committed 5→2 replacement intent rejected without database/anchor changes.

**SUPPORTED BY CODE REVIEW:** all proposed commands replay before intent publication; COMMAND/EXIT/SETTLEMENT/checkpoints commit in one SQLite transaction. Repeated command identity returns its original result; duplicate commands and economic closures reject. Checkpoints and settlements are derived, not authority. Missing post-entry evidence keeps reservation/open-position accounting.

Risk policy remains 1% current realized eligible equity for planned cost-inclusive trade risk, 2% initial-capital daily-loss reference. Existing risk tests cover 900→9, 1,000→10 and realized-profit 1,005→10.05, with above-threshold rejection and restart reconstruction. These are synthetic policy checks, not broker-balance reconciliation or physical loss guarantees; gaps/slippage can exceed planned loss. No permanent application-wide 1,000 ceiling is introduced.

## 12. Request-budget/retry audit

signal_request_budget.py:12–78 binds immutable purpose slots to request identities and records a physical attempt after verifying reservation/send evidence. Reviewed _mark:240–256 fsyncs reservation and one-use send checkpoints before I/O. Signal/future paths limit each slot to one physical attempt; retry authority is not silently replenished. Upstox _get:155–201 bounds attempts, deadlines, backoff and acquisition window, and fails immediately on authentication errors. Explicit mode flags separate entry, future observation, settlement and recovery; recovery cannot grant network authority.

Saved responses bind request, manifest, payload and evidence. Recovery checks exact before/after settlement identities and reuses sealed evidence. These source controls and their mocked offline tests do not establish real endpoint timing, market freshness or actual provider availability.

## 13. Source-binding audit

Configuration freezes source hashes and calendar identity at construction; _verified compares the stored configuration before recovery mutation. The fresh independent source-mismatch test rejects a new source identity using injected read-only bytes, without editing protected source files.

Missing migration is an **intentional operational limitation**, with recoverability implications for changing versions, rather than permission to normalize or rewrite old ledgers. No automatic migration exists or was attempted. Sources/imported runtime/calendar are assumed frozen for an object's lifetime; hot reload was not certified. Retained schema-v3 calendar loading additionally checks pinned evidence.

## 14. Rollback trust-boundary analysis

CASE A: with a newer retained trusted local witness, an older prefix or wrong base cannot satisfy exact ancestry/final-head checks. Independent 5→2 rejection and existing contradictory-metadata tests support this bounded property.

CASE B: matching older database and anchor restored together remain locally indistinguishable from that older valid state. The existing joint-rollback test intentionally demonstrates this limitation. More local hashes/files do not supply independently trusted freshness.

Future choices, **not implemented**: remote append-only completion log (separate retention/write authority); independently stored monotonic sequence (freshness but availability dependency); signed checkpoints (authenticity still needs an unrolled freshness witness); remote/object-store anchor (independent credentials/version retention); secure hardware monotonic storage (platform/provisioning constraints); independent trusted service (availability/operations boundary). Each requires a separately reviewed trust and recovery design.

## 15. Codex Security findings

**SEC-1 — LOW, CWE-1236, SUPPORTED BY CODE REVIEW:** legacy free-form provider explanation reaches spreadsheet-facing trade CSV without formula neutralization. Source: ai_engine.py:28–37 → trading_engine.py:24–28,77–83 → paper_trader.py:120–143 → trade_logger.py:44–57. Requires a permitted legacy paper BUY/closure and separate operator opening the CSV in a formula-evaluating viewer. CSV structural quoting does not ensure literal cell interpretation. No command execution, exfiltration, broker execution or V2.2 risk/settlement bypass was established. No payload was generated or executed. Mitigation: neutralize spreadsheet-sensitive text at export, retain exact original audit text separately where needed, and add benign viewer/export regressions. Do not rewrite historical exports.

All 60 app files were fully source-reviewed by the baseline/focused reviewers. No material V2.2 security crossing was identified. Dependency advisories, actual SDK/proxy/TLS behavior and OS ACLs were not verified. A caller-injected nonmock provider in the old offline-evaluation API is a mode-contract caveat; the shipped CLI forces the offline provider. No lower-trust crossing was established, so it is not reported as a security vulnerability.

Canonical security artifacts: [report](C:/Users/Priyanka/.codex/state/plugins/codex-security/scans/ai-trading-bot-research/a0789cdb5db50deece446005c40064ca5de989a4_20261107T071951Z_rhuxgk0d/report.md), [findings](C:/Users/Priyanka/.codex/state/plugins/codex-security/scans/ai-trading-bot-research/a0789cdb5db50deece446005c40064ca5de989a4_20261107T071951Z_rhuxgk0d/findings.json), [coverage](C:/Users/Priyanka/.codex/state/plugins/codex-security/scans/ai-trading-bot-research/a0789cdb5db50deece446005c40064ca5de989a4_20261107T071951Z_rhuxgk0d/coverage.json), [threat model](C:/Users/Priyanka/.codex/state/plugins/codex-security/scans/ai-trading-bot-research/a0789cdb5db50deece446005c40064ca5de989a4_20261107T071951Z_rhuxgk0d/threatmodel.md).

## 16. Newly discovered defects

**COR-1 — MEDIUM, material data-validation correctness defect; PROVEN BY TEST.**

- Affected function: app/offline_market_data_v1.py::validate_candle_records, lines 411–483; particularly parsed_rows append at 461 and classification loop at 466 onward.
- Violated invariant: each candle must retain its own validation reasons; classification must be independent of the order or validity of unrelated rows.
- Root cause: reasons is recreated during the first loop but not stored with each parsed row. The second loop reuses the final first-loop list, and extends that shared list with duplicate/external reasons across rows.
- Benign reproduction: two distinct-timestamp synthetic candles, one with low above its high and one valid. Invalid-first/valid-last yields two VERIFIED_VALID rows. Reverse order yields two VERIFIED_INVALID rows. Both independently authored expected-behavior regressions fail on unchanged production source.
- Impact: affected acquisition/historical classifications can accept invalid candles or reject good ones, compromising data-quality partitions and subsequent research conclusions. scripts/run_acquisition_pilot_v1.py:388 consumes the validator. This is not proof that V2.2's separate candle validator accepts invalid input, nor proof that every retained historical dataset is wrong.
- Recovery/financial relevance: no ledger or money was changed; using incorrectly classified future datasets could invalidate later trading evaluation. Existing evidence must be preserved pending a separately scoped impact assessment.
- Defensive remediation design: carry an independent copy of reasons per parsed row; merge only that row's external/duplicate reasons during classification; ensure output reason lists do not alias. Add mixed-validity, reversed-order, duplicate-isolation and schema/interval regressions. Assess old outputs without silent regeneration or overwrite.
- Status: documented, unfixed under the user's explicit read-only choice. This finding blocks a Phase 1 PASS even if the original repository suite passes.

The empty-genesis double-interruption condition is a separate availability limitation, not a material financial-integrity finding: initialization has not returned success, no settlement exists, and recovery fails closed rather than accepting bad state.

## 17. Known limitations

Profitability and real-data timing/readiness are not established. Synthetic accounting gains are not strategy returns. Physical power loss, filesystem/controller/device failure, native POSIX behavior and jointly consistent local rollback remain unproven/unsupported as described. Local privileged replacement/host compromise is not prevented by local hashes. Source-bound old ledgers require an explicitly designed migration; no migration was approved. Muhurat execution remains blocked. Broker/live/prospective execution remains unauthorized.

## 18. Evidence classification

| Claim | Classification |
|---|---|
| Four fresh V2.2 invariant checks | PROVEN BY TEST |
| Candle row classification depends on another row | PROVEN BY TEST — two failing regressions |
| Full 1,641 reproduction | PROVEN BY FRESH TEST EXECUTION — 1641 passed |
| Full semantic replay, publication ordering and mode-specific request authority | SUPPORTED BY CODE REVIEW; exercised subsets covered by tests |
| Legacy spreadsheet-facing CSV risk | SUPPORTED BY CODE REVIEW; viewer behavior NOT PROVEN |
| Physical power-loss/hardware durability | NOT PROVEN |
| Strategy profitability / real operational timing | NOT PROVEN |
| Live execution, regulatory readiness, broker equity and migration deployment | OUTSIDE CURRENT SCOPE |

## 19. Files changed, if any

Only this new repository report. Production implementation, repository tests, configuration, earlier reports and historical evidence remain unchanged. New test probes, logs, manifests and metadata are external at C:/Users/Priyanka/.codex/visualizations/2026/11/05/01a1acdb-980d-7dd2-b495-2691796e9d33/phase1-v22-audit. Plugin artifacts are in its authoritative scan directory. No CodeRabbit remediation review was triggered because no production-code diff exists.

## 20. Final verdict

**REMEDIATE — material correctness/safety defect found; fix and re-audit before proceeding.**

COR-1 is in adjacent data validation, not a newly demonstrated V2.2 settlement failure. It nonetheless prevents relying on the project's affected data-quality output for the next phase. All 1,641 existing tests passed in the completed independent rerun; that does not erase the two new failing regressions. No material V2.2 committed-state accounting or security defect was confirmed in the reviewed scope. No Phase 2 work is authorized by this result.

## 21. Exact next authorized step

Complete the authorized Phase 1 read-only report and STOP. The user explicitly chose reporting rather than applying a fix. No remediation or later phase begins automatically. Recommended next request: authorize focused COR-1 remediation and re-audit, preserving all historical evidence; address the separate legacy CSV export finding in a scoped follow-up. Real-data-readiness engineering, experiments and live execution remain blocked pending subsequent review and authorization.

**No experiment was run or authorized. Broker execution remains disabled. Real-time paper trading and causal settlement remain NO-GO.**
