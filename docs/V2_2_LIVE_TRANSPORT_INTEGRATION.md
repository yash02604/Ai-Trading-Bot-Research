# V2.2 transport integration

## 1. Architecture and discovery (recorded before implementation)

The existing Upstox boundary is `app.upstox_client.request_window/get_quote/get_intraday_candles`. It uses fixed GET endpoints, no redirects/implicit retries, a killable worker and bounded retry/deadline controls. `before_attempt` rechecks acquisition policy and `before_send` can durably record physical attempts. Client bytes are pinned by `verify_client_attestation`; they must remain unchanged.

`app.v2.provider.DeepSeekProvider.complete` fixes the provider endpoint, disables SDK retries/redirects, bounds tokens/response bytes and rejects truncation. It does not validate application proposals or returned provider/model identity. Existing strict `ai_led_guarded._proposal` and `ai_led_offline._parse` supply those proposal rules. Ranking is descending score then configured priority, not input order. Scores are not calibrated probabilities.

Existing `PersistentRequestBudget` and `AppendOnlyJsonl` provide logical reservation/idempotent records; their owner must lock and fsync before sends. `ai_led_guarded._write/_read` provide immutable canonical evidence publication. The old `_ReviewedRun` cannot be redirected: its preflight/provider authorization invokes the older guarded paper account. That account and its candle-open modeled fills remain separate.

Quotes reuse `parse_upstox_full_quote`, with strict symbol/aware timestamp/bid/ask/depth checks. Its timestamp is response-generated; independent depth event time is unavailable. Candle normalization reuses existing schema/explicit timestamps, `candles_to_dataframe`, `validate_candles` and retained V3 calendar. The isolated-row validator remains unchanged; V3 is not mislabeled as a V2 manifest. Complete unique rows are sorted deterministically; forming bars are explicitly excluded and future labels rejected, never filled or shifted. Candidate warmup reuses existing complete 35-bar same-day/liquidity checks.

V2 currently admits only two offline provenance labels and caller quantity. Live data must not be relabeled synthetic/unverified to bypass that boundary. A V2-only source-bound validated-transport provenance and quantity-free selection API will persist transport evidence in authoritative commands. Engine-owned sizing occurs at the actual completed causal close and retains all existing risk/cost checks. Legacy offline commands retain their original behavior. No V1/offline provenance expansion or second financial ledger is planned.

The scheduler currently blocks all production via exact V1 config; the new versioned config adds explicit market/provider/start opt-ins (all default false) and a separate source/config-bound operator review/network-authorization record. No positive production authorization record is created by this task. Observer reads sanitized scheduler publications only; a versioned extension will expose transport health without imports or execution capability.

Implementation plan: write failing transport and engine/controller contract regressions; add pure contract/readiness module, observations-only adapter and request evidence store, then orchestration via unchanged V2 replay/publication; wire default-off scheduler/observer paths; fresh scoped review; focused and complete guarded offline tests; hash preservation. No dependency, network exercise, task registration/activation or experiment.

## 2. Transport interfaces

Implemented in `v2_transport_contract.py`, `v2_live_transport.py` and `v2_transport_controller.py`. The transport exposes `candles(symbol, slot, purpose)`, `quote(symbol, slot, purpose)`, `proposals(payload, frames, candidate_time)` and a sanitized `health()` projection. Immutable results contain logical request ID, kind, symbol, requested/received/validated/source times, response hash and validated normalized values. Provider proposals contain only existing strict numeric/action fields and allowlisted rationale codes; no free-form engine instructions.

V2 adds `select_transport(...)` and `observe_transport(...)`. Proposal evidence binds provider/model/request, candidate, market availability and actual request/response/validation clocks. Observation evidence binds candle/quote request IDs, response hashes and the fresh quote. Selection quantity is `None`; V2 computes quantity at the actual causal close. The existing explicit-quantity offline API and offline provenance labels are unchanged. Raw transport results cannot use the legacy propose/nonempty-observe APIs under the new provenance.

## 3. Market-data flow

Fixed Upstox read APIs only; reviewed code/approval, mode, purpose/session and durable budget/send markers precede every send. Quote and candle identities must match configured instruments. No arbitrary URL, order endpoint, profile/balance or broker capability.

## 4. AI-provider flow

Existing bounded DeepSeek client and strict proposal/rationale allowlists; wrong identity, malformed/truncated/failed responses produce NO_VALID_PROPOSAL. Preserve existing proposal bounds and deterministic ranking. Free-form provider text is never an engine instruction.

## 5. Freshness rules

Quotes must remain within five seconds at actual acceptance, not merely original receipt. Candles must be completed at receipt and available before their causal slot expires. A fresh quote is admission evidence only; it does not replace V2's candle-close reference or prove a real executable fill/depth age.

## 6. Retry rules

Upstox retains at most three bounded attempts per logical request and rechecks purpose/session/deadline each time. Provider retains zero SDK retries and redirects. Physical-attempt publication is followed by another policy, wall-clock and monotonic-deadline check immediately before submission. Provider construction and lazy SDK initialization finish before this final checkpoint. Receipt and post-normalization acceptance also enforce the monotonic deadline, so wall-clock adjustment cannot extend acceptance. An expired window prevents remaining sends; late responses are rejected. Existing HTTP/SDK timeout mechanisms remain in use, rather than claiming cancellation of an already submitted remote request or provider-side exactly-once execution.

## 7. Request budgets

Use existing persistent logical budgets independently for market/provider. Durable reservation and send marker precede I/O; physical attempt markers record retry number. Before accepting cached evidence or reserving a new request, reconcile immutable intents, budget policy and reservations, sent records, ordered physical attempts, failures and results. Missing/contradictory surviving evidence is rejected; missing counters are not reset to zero. Reservation without send can continue; sent without a durable result/failure fails closed instead of silently resending. A failed request is never silently retried under a new ID for the same configured slot/purpose. Provider-side exactly-once delivery is not promised.

## 8. Causality

Availability is actual receipt/validation, not candle label. Future/incomplete rows cannot supply proposals/settlements. Candidate data must finish at the exact latest completed five-minute slot; warmup is the existing 35 contiguous same-day bars with positive volume and minimum recent turnover. Candidate acceptance expires at candidate plus ten minutes and at the original decision epoch boundary; an old receipt cannot acquire a later decision epoch. Observation acquisition and acceptance occur after the expected candle completes and before its next five-minute deadline. Original request/evidence and command identities remain fixed across retry. Missing due slots retain unresolved exposure through existing engine policy.

## 9. V2.2 authority boundary

New transport selection is quantity-free; engine derives whole-share quantity with existing RiskEngine/current equity/cash and independently rejects costs/risk/net reward violations. The 1% trade risk, 2% daily-loss policy, initial daily-loss base, reservations, fees, exits, ancestry, sequence, quarantine and atomic settlement remain unchanged. Honest transport provenance plus local source/config binding is not independent authentication of exchange truth or an external rollback witness.

## 10. Scheduler integration

`scheduled-forward-paper-v2` config selects this new path. Three independent fields (`market_data_enabled`, `provider_enabled`, `production_start_enabled`) default to false; `review_approval` defaults to null. Legacy V1 scheduler config remains blocked. The owned scheduler delegates only after strict readiness checks; an existing control/engine directory is never adopted under a changed source/config identity. The controller verifies authoritative V2 state and refuses unresolved/quarantined prior exposure before acquisition. Per-send validation and evidence/budget reconciliation remain mandatory after preflight.

The separate review record requires exact schema `v22-live-transport-review-v1`, current `integration_identity`, current `configuration_identity`, and both `independent_review_passed: true` and `network_exercise_authorized: true`. These are operator assertions bound to local source/config, not signatures, a remote authorization service or a trusted rollback witness. Safety-critical dependencies, including quote/candle conversion, inherited lifecycle, request journals/locks and the instrument registry, are included in source binding. No positive production approval, enabled config, task registration or activation was created. Existing source-bound control/ledger directories must not be migrated or rewritten silently.

Configuration is validated by `validate_options`; `default_options()` provides the exact default-off transport section. Example construction remains inert:

```python
from app.research.v2_transport_contract import default_options
config = dict(schema_version='scheduled-forward-paper-v2', mode='FORWARD_PAPER',
              initial_capital=1000, instrument_priority=['RELIANCE'],
              provider_cap=3, market_data_cap=30, transport=default_options())
```

The bounded scheduler loop waits without acquisition before the session/warmup, honors configured polling/cycle limits and fails closed with exposure retained if it stops with pending/open/unresolved state. No schedule-management mutation was issued. An initial sandboxed task inventory query returned a CIM error. A supported approved **read-only** local query then returned `CmdletizationQuery_NotFound_TaskName,Get-ScheduledTask`: no registered `AITradingBot-ForwardPaper` task was found. Nothing was registered, enabled or started. Existing disabled installer and launch scripts are byte-identical to baseline.

## 11. Observer integration

Observer schema `scheduled-paper-observer-v2` extends the retained strict V1 schema with `transport`: the three enabled permissions, `last_quote_at`, `last_candle_at`, `last_provider_at`, logical `market_data_requests`/`provider_requests`, allowlisted `status` and `last_error`. Activity uses actual recorded receipt times/counts. No key/token, raw exception, provider text or invented confidence/rank/depth timestamp is exposed. Reader imports no transport/config/engine, acquires no financial lock and cannot start transport or recover state.

The user's explicit policy clarification overrides the attachment's generic observer-unavailable entry prohibition: **record observer failures without changing entries or exits**. Publication failures accumulate in the attempt summary and warnings; financial processing continues. The integration regression proves the same profitable synthetic settlement while all five observer publications fail. That synthetic gain is accounting evidence only.

## 12. Secret handling

Credentials come only from existing settings, never config/command/evidence. Persist allowlisted normalized values, fixed errors and hashes; never arbitrary HTTP bodies/provider exceptions or free-form rationale. Raw invalid response handling fails closed without logging content.

## 13. Replay

`v2_transport_controller.replay(config, control_root)` verifies the local source/config binding and reconstructs V2's authoritative recorded commands and settlements. It does not call readiness/settings or construct an acquisition transport. It uses the original receipt/acceptance clocks and cannot reacquire missing evidence. V2 may complete its own already-authorized local publication recovery while verifying the ledger; this function is an offline replay/recovery API, not the strictly read-only observer API. Consistent joint local rollback still needs an independently trusted witness.

## 14. Recovery

Acquisition recovery is separate from financial recovery: budgets/send/results prevent repeated uncertain requests; original V2 constructor/replay controls accounts. Candidate plans and selection/observation commands are immutable and retain original identities. Stale pre-entry evidence cannot authorize a delayed new fill; an already committed identical command remains idempotent. A new controller/engine instance is constructed on each cycle, so proposal, entry and monitoring recovery use the existing V2 verification path. Missing/corrupt data creates no fabricated settlement and does not release reserved exposure. Existing V2 recovery/durability regressions cover interrupted settlement publication. No old evidence/source-bound ledger migration occurs.

## 15. Tests

Final new integration suite: **92 passed, 0 failed, 0 errors, 0 skips, 0 deselections; JUnit duration 110.476 seconds** (`transport-final.xml`). Coverage includes mocked real Upstox request wrappers and DeepSeek SDK wire contract; quote identity/bid/ask/depth/freshness; wrong symbol/interval, duplicate/future/invalid/forming/final-session candles; strict proposal identity/schema/price/stop/target/score/duplicates/truncation/HTTP/timeout failure; final logical-budget slot/exhaustion, bounded retries, missing journal reconciliation, uncertain sends and cache idempotency; deadline-expired fsync/SDK initialization/monotonic response; future receipt/candidate expiry; stale uncommitted observation restart; current engine-owned quantity/causal close; proposal-entry-settlement/replay; three opt-ins/source/config approval; unknown calendar/closed market; static broker API exclusion; sanitized read-only observer and isolated publication failure.

Crash probes cover intent before request, reservation before send, persisted market/provider responses, provider request without result, committed proposal, entry and monitoring reconstruction. Existing V1/V2 recovery and durability suites exercise `.writing -> .ready -> canonical -> anchor`, interrupted settlement publication, ancestry/sequence monotonicity, torn records, corruption/quarantine, deterministic reconstruction and exactly-once settlement.

Exact command variables (PowerShell, repository working directory):

```powershell
$repoRoot = 'C:\Users\Priyanka\Desktop\ai-trading-bot-research'
$evidenceRoot = 'C:\Users\Priyanka\.codex\visualizations\2026\11\05\01a1acdb-980d-7dd2-b495-2691796e9d33\v22-transport-integration-20261113'
$guard = 'C:\Users\Priyanka\.codex\visualizations\2026\11\05\01a1acdb-980d-7dd2-b495-2691796e9d33\phase1-v22-audit\run_offline.py'
& "$repoRoot\.venv\Scripts\python.exe" -B $guard tests/test_v22_transport_engine.py tests/test_v2_live_transport.py tests/test_v22_transport_controller.py -q -p no:cacheprovider --basetemp "$evidenceRoot\transport-final-tmp" --junitxml "$evidenceRoot\transport-final.xml" --tb=short
```

The guarded driver disables dotenv loading, clears credential-like environment values and denies socket/DNS calls; repository test fixtures also deny unexpected sockets, require explicitly mocked HTTP workers and redirect persistent logs/state into temporary directories. No external call was exercised. Positive review records and credential markers are fake fixtures in external temporary test roots only.

A broader focused command selected the three integration modules plus `tests/test_scheduled_forward_paper.py`, `tests/test_scheduled_remediation.py`, `tests/test_paper_observer.py`, `tests/test_forward_paper_scheduler_scripts.py`, `tests/test_causal_forward_paper.py`, `tests/test_causal_forward_paper_v2.py`, `tests/test_causal_forward_paper_v2_recovery.py`, `tests/test_causal_forward_paper_v2_durability.py`, with the same flags and `focused3-tmp`/`focused3.xml` paths. It collected 466 at that point: **462 passed, 4 failed, 0 errors, 0 skips, 0 deselections; 461.844 seconds**. The failures were PowerShell `AuthorizationManager` sandbox denials, not failed wrapper assertions after execution. No execution-policy setting or security control was changed. All 271 V1/V2 engine/recovery/durability tests in that run passed; the later final transport run supersedes its 90-test integration subset.

The four denied tests were then rerun through the supported approved runner, using copied projects and no task registration:

```powershell
& "$repoRoot\.venv\Scripts\python.exe" -B $guard tests/test_scheduled_remediation.py::test_valid_wrapper_logs_only_in_copied_project tests/test_forward_paper_scheduler_scripts.py::test_wrapper_preview_has_fixed_mode_absolute_paths_and_no_mutation tests/test_forward_paper_scheduler_scripts.py::test_installer_default_preview_disabled_task_xml tests/test_forward_paper_scheduler_scripts.py::test_wrapper_child_invalid_config_is_blocked_without_target_writes -q -p no:cacheprovider --basetemp "$evidenceRoot\wrapper-final-tmp" --junitxml "$evidenceRoot\wrapper-final.xml" --tb=short
```

Result: **4 passed, 0 failed/errors/skips/deselections; 12.98 seconds** (terminal). New regressions were initially red before fixes: five journal/deadline/candidate invariants failed in 3.904 seconds; five source-dependency checks failed in 2.829 seconds; two provider-initialization/elapsed-time checks failed in 9.830 seconds. These development probes are retained externally. One intermediate focused run (72 passed/1 failed in 59.102 seconds) was invalidated by a concurrent source-identity change; source-bound approval rejected it. It is not claimed as final verification.

Supplemental external synthetic probes: **9 passed, 0 failed/errors/skips/deselections; 27.08 seconds** (terminal), covering reversed candle ordering with unchanged values, stale candle batch, exhausted three-attempt retry with one logical reservation, combined disabled opt-ins, invalid mode/extra config field/non-paper settings, exhausted provider budget and interrupted transport-backed settlement-anchor publication followed by repeated acquisition-free replay. These probes reuse the repository's isolated fixtures and do not modify the frozen production/test tree:

```powershell
& "$repoRoot\.venv\Scripts\python.exe" -B $guard "$evidenceRoot\supplemental_contract_tests.py" -q -p no:cacheprovider --basetemp "$evidenceRoot\supplemental-tmp" --junitxml "$evidenceRoot\supplemental.xml" --tb=short
```

Full regression command (no test selection, ignore, skip or deselection options):

```powershell
& "$repoRoot\.venv\Scripts\python.exe" -B "$evidenceRoot\run_verification.py" full-final
# Child command recorded verbatim in full-final-command.json:
& "$repoRoot\.venv\Scripts\python.exe" -B $guard -q -p no:cacheprovider --basetemp "$evidenceRoot\full-final-tmp" --junitxml "$evidenceRoot\full-final.xml" --tb=short
```

Full-suite result on 2026-11-14: **2,066 passed, 0 failures, 0 errors, 0 skips, 0 deselections; exit code 0**. Pytest terminal runtime **1,482.02 seconds (24 minutes 42 seconds)**; JUnit suite time **1,481.804 seconds**; outer evidence-driver wall time **1,485.9259261 seconds**. The 1,974-test historical baseline plus 92 new repository regressions equals 2,066. Supplemental external probes are reported separately and are not added to this suite total. All existing 271 V1/V2 engine/recovery/durability and 105 scheduler/observer/script regressions passed again as part of the unfiltered final run.

Source/test hashes captured before/after the run show **zero changes**. Exact child command, JUnit counts/duration, exit code and wall time are retained in `full-final-command.json`, `full-final-result.json`, `full-final.xml` and `full-final.log`. The full run used the supported approved test runner to avoid the earlier sandbox-only PowerShell denials, retaining the same socket/DNS/credential/mock guards. No execution-policy bypass or control change was used. This is a fresh complete run; the earlier independent 1,974 baseline is not substituted for current verification.

Fresh non-author reviewer (`/root/fresh_transport_review`) performed read-only source analysis and guarded synthetic probes. Six confirmed input-boundary/recovery/deadline/approval issues were fixed with regressions: missing request-evidence reconciliation, deadline expiry during fsync, provider candidate epoch rebinding, omitted source dependencies, SDK initialization after the final send checkpoint and wall-clock-only deadline acceptance. Final re-review found **no remaining material findings in scope**; independent synthetic deadline probes now show zero late provider submissions and zero proposals from over-deadline responses. This is scoped implementation review, not the separate independent transport audit required before any network exercise. Review notes are retained in external `fresh-review.md`.

## 16. Files changed

Existing production files changed:

- `app/research/causal_forward_paper_v2.py`: explicit validated-transport provenance/binding, quantity-free selection and independently validated observation/proposal evidence; engine-owned sizing at causal close. No settlement/ancestry/publication/recovery implementation redesign.
- `app/research/scheduled_forward_paper.py`: versioned default-off readiness, source-bound owned delegation, sanitized transport publication/counts.
- `app/research/paper_observer.py`: strict versioned read-only health schema.

New production files: `app/research/v2_transport_contract.py`, `app/research/v2_live_transport.py`, `app/research/v2_transport_controller.py`.

New tests: `tests/test_v22_transport_engine.py`, `tests/test_v2_live_transport.py`, `tests/test_v22_transport_controller.py`.

New documentation: this report. All run artifacts/review notes/manifests are outside the repository under the external evidence root above. No dependencies, credential files, enabled production configs, approval records, CLI scripts or Windows task files were added/changed.

## 17. Preservation

Before edits: 653 inventoried files, inventory SHA256 `d4848478cbe5f4f00d78232d3f735e69f770ed7f9fe9a4c5544ff8685371d3a7`. Allowed final scope: 660 files, 3 intentional existing production changes, 7 additions, **650 originals byte-identical and none removed**. The external `before.json`/`after-final.json` inventories and `preservation.json` record per-file SHA256 comparison and reject any unexpected change/addition/removal. The report's own final hash is recorded externally to avoid a circular self-hash. HEAD remains `a0789cdb5db50deece446005c40064ca5de989a4`; pre-existing dirty work was preserved, with no commit, push, history rewrite or broad cleanup.

Protected and intentionally unchanged: strategy and AI prompts/signals; risk percentages/capital assumptions/cost models; NSE manifest/calendar/session policy; broker code; shared attested Upstox and DeepSeek clients; candle row-isolation validator; legacy CSV fixes; Sessions 02–05; historical evidence; real logs/databases; previous reports; task installer/launcher/observer CLI scripts. Preservation proves byte identity, not historical-data accuracy or real-market performance.

Protected group counts (overlapping groups): **22 Session 02–05 files, 5 real log/state files, 393 historical/data/evidence files, 46 prior documents and 11 shared boundary/launcher files**. All remain byte-identical. V2's production file changed only to admit explicit validated transport commands and defer quantity to engine-owned entry sizing; settlement computation, replay verification, event ancestry, publication and recovery implementations were not changed. The complete final regression confirms their existing behavior within offline scope.

## 18. Limitations

Profitability remains unproven; synthetic accounting gains are not trading evidence. No real transport latency/reliability, real-market readiness or physical power-loss guarantee was established. Historical-data/provenance issues were not changed or resolved by this work. Closed-candle references do not establish executable order fills or independent depth timestamps. Candidate warmup/closed-candle timing and configured lifetime request caps remain operational constraints, not performance optimizations.

A consistent older local database and matching local anchor can still be jointly replaced without detection unless there is a separately trusted external witness/monotonic checkpoint. Local hashes, extra files and local review records do not solve this. Source/config review records attest local identity and operator assertion only. Remote request outcome can remain uncertain after interruption; fail-closed refusal is not provider-side exactly-once execution. Existing SDK/HTTP timeout limits and late-result rejection do not prove cancellation of a request already submitted remotely.

The fresh scoped reviewer is part of implementation verification; a separate independent transport audit and separately authorized network exercise remain required. Broker execution is disabled/unavailable in this path. No enabled production config or approval record, schedule activation, real-time paper session or causal settlement experiment is authorized by this report.

## 19. Authorization status

Implementation and guarded offline verification are complete. Fresh scoped implementation review reports no remaining material findings. No registered default forward-paper task was found by the read-only local inventory; no schedule was registered, enabled or started. No real transport/provider/broker request, real-time paper run or causal settlement experiment occurred. This report does not authorize the next phase automatically.

**TRANSPORT INTEGRATION READY FOR INDEPENDENT AUDIT. V2.2 forward paper now has an offline-verified integration path for approved live market data and AI-provider transports while broker execution remains disabled. No real transport request, scheduled production run or trading experiment was performed. Independent transport audit is required before any network-enabled forward-paper session.**
