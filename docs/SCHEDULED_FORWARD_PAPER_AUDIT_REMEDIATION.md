# Scheduled forward-paper audit remediation

## 1. Verdict

**REMEDIATION VERIFIED.** All seven findings are corrected, the 105-case focused suite and 1,974-case complete offline suite pass, the fresh scoped reviewer reports no remaining confirmed defects, and the allowed-change preservation assertions pass. This is remediation verification, not independent post-remediation certification or permission to integrate/activate anything.

Scope is exactly the seven confirmed infrastructure findings in the external 12 November independent audit. No live transport integration, strategy/risk change, schedule registration or experiment is included. The prior audit report and implementation guide are preserved as historical records; this document describes the superseding remediation behavior.

## 2. Findings, root causes and fixes

| Finding | Root cause | Implemented correction |
|---|---|---|
| 1 MEDIUM: TEMP-dependent ownership | Canonical project identity was stored under process-specific tempfile.gettempdir(). | Retain the same native thread/OS locking primitive, using canonical project `logs/scheduled-ownership/<project-hash>.lock`. TEMP/TMP and cwd no longer select the lock location. Persistent file presence is not ownership; OS process death releases the byte/file lock without PID-based deletion. Separate canonical projects retain separate namespaces. |
| 2 LOW: old terminal summary hides unfinished attempt | Prior-date gating read mutable summary.json instead of the latest attempt journal entry. | Validate immutable begin/final records, then gate each prior day using its latest ordered begin and corresponding final. A newer orphan/unfinished attempt blocks the next day even if an older summary says completed. Same-day retry records a new linked attempt and delegates all economic recovery to existing V2. |
| 3 LOW: midnight attribution | Gate, identity, startup and shutdown independently sampled the clock. | Production captures one timestamp and passes it to preflight, identity, attempt metadata, observer and summary. The snapshot time is the captured logical invocation time, not a claim to measure elapsed runtime. |
| 4 LOW: foreign-root logs before validation | Wrapper created target logs before Python validated configuration/control identity. | Wrapper invokes explicit `--prepare-launch` before target logging. It validates exact paper config and the dedicated root without writes, then revalidates/initializes owned metadata under project ownership. Only successful CONTROL_PREPARED permits log creation. Ordinary execution subsequently acquires ownership and validates again. Failure preserves its sanitized JSON/exit status and creates no target artifacts. |
| 5 LOW: observer recursion failure | JSON byte limits did not prevent decoder RecursionError escaping either reader. | Both read boundaries catch RecursionError and return the existing sanitized UNAVAILABLE result. Size bounds, duplicate-key/schema checks and read-only behavior remain. No global recursion limit is changed. |
| 6 LOW: wrong previous attempt under non-monotonic clocks | Predecessor was selected by maximum timestamp/UUID. | Immutable scheduler-only attempt_sequence increments from the validated journal, with exact predecessor linkage across dates. Equal/rollback clocks and random UUID order cannot select the predecessor. This does not change V2 ledger sequence semantics or establish an external rollback witness. |
| 7 LOW: lost startup failure count | Production swallowed startup errors; completed synthetic duplicate branch also omitted accumulated failures. | Carry counts into `_finish` in both branches; shutdown failure increments the existing count. One later successful publication cannot erase an earlier failure. Errors remain redacted and financial behavior remains independent. |

## 3. Ownership and attempt recovery details

Ownership remains a local cooperating-process lock under the canonical project directory. There is no environment-selectable production lock directory and no alternate locking algorithm. Stale lock-file bytes remain harmless; no history is deleted to release ownership.

Synthetic harness ownership uses a dedicated sibling synthetic project namespace, so its ownership files never contaminate the dedicated V2/control root. Tests explicitly isolate default production ownership to synthetic sibling projects; native valid-wrapper tests copy the application/interpreter fixture into an external project. Real repository logs/database files are not used for ownership test effects.

`_attempt_records` validates positive integer contiguous sequences, unique attempt IDs, exact filename/day identity, predecessor chain and matching final metadata. Corrupt/contradictory records fail closed rather than being normalized. `_begin_attempt` derives the next sequence while its caller holds existing ownership. No mutable sequence counter is used as a second independent authority.

The latest prior-day attempt must have an acceptable final status. Older unmatched records remain visible; a completed same-day recovery invocation can follow them and produce its own final without rewriting any prior record. Unresolved entered exposure still blocks through existing V2 recovery/lifecycle results. No financial event, fill, settlement or reservation is manufactured to resolve scheduler metadata.

Code identity remains strict. Scheduler source changes invalidate old source-bound control metadata; no old run is migrated, rehashed, adopted or resumed to hide the change. All verification uses new synthetic roots. Existing financial V2 source binding is not weakened.

## 4. Launcher and filesystem validation

`--prepare-launch` is an explicit metadata-preparation purpose. It is mutually exclusive with read-only `--offline-preflight` and cannot accept an injected clock. It does not grant market/provider permission, instantiate V2, select a strategy or call an older paper transport. CONTROL_PREPARED means only that validated owned control metadata exists; it is not a successful trading start. Failure produces sanitized NO_GO output, exit 2, before wrapper target logs are created. Normal production execution remains NO_GO or verified closed-day NO_SESSION.

Preview remains mutation-free. Native execution still fixes FORWARD_PAPER, interpreter, cwd and native quoted arguments, preserves child status and separates timestamp/GUID stdout/stderr. Registration/enable/start logic and disabled installer XML are unchanged. No task was registered or activated.

Validation and later wrapper operations assume a trusted local filesystem/cooperating writers. Malicious concurrent path replacement, ancestor junction trust and physical power-loss durability are not newly claimed solved. Actual runner checks under ownership remain authoritative; no unsupported transport is reachable.

## 5. Tests added

`tests/test_scheduled_remediation.py` adds **36 deterministic cases** covering:

- Same/different TEMP and cwd, relative/absolute project identity, concurrent child contention, valid ownership after release, and separate projects. Existing process-death/stale-artifact regressions remain in the original suite.
- NO_GO and completed summary followed by an orphan, same-day after-cutoff retry, next-day refusal, immutable prior finals, completed next-day continuation and immediately preceding attempt linkage.
- Equal/backward clocks, repeated same-clock attempts, cross-date predecessor, invalid sequence/predecessor rejection and unchanged corrupt evidence.
- Prior-day unresolved entered exposure, retained reservation and unchanged engine bytes when the next day is blocked.
- Sunday/trading-day/holiday midnight transitions with one captured clock and consistent run, report and session attribution.
- Both deeply nested JSON readers, missing/invalid/truncated/duplicate-key data, ordinary and stale snapshots, and large valid shallow events.
- Startup-only, shutdown-only, both and neither publication failure; duplicate RECOVERY-only and RECOVERY+STOPPED failures; unchanged synthetic account values and redacted error text.
- Foreign root and normalized traversal into it, sentinel preservation/no added files, and valid deterministic logging inside a copied synthetic project.

All original 69 infrastructure cases are retained. The default ownership fixture now uses a synthetic sibling project because ownership became persistent. The native invalid-config test preserves the original exit/status/mode/request assertions against captured stdout and strengthens the filesystem assertion to no target directory, replacing the now-invalid expectation that refusal creates logs. No quantitative/economic assertion was weakened, and no test was skipped or xfailed.

## 6. Focused verification

Commands ran from `C:\Users\Priyanka\Desktop\ai-trading-bot-research`:

```powershell
$Repo = 'C:\Users\Priyanka\Desktop\ai-trading-bot-research'
$Python = "$Repo\.venv\Scripts\python.exe"
$Base = 'C:\Users\Priyanka\.codex\visualizations\2026\11\05\01a1acdb-980d-7dd2-b495-2691796e9d33'
$Guard = "$Base\phase1-v22-audit\run_offline.py"
$Run = "$Base\scheduled-remediation-20261112"
& $Python -B $Guard tests/test_scheduled_remediation.py -q -p no:cacheprovider --basetemp "$Run\red-tmp" --junitxml "$Run\red.xml" --tb=short
& $Python -B $Guard tests/test_scheduled_remediation.py -k wrapper -q -p no:cacheprovider --basetemp "$Run\red-wrapper-tmp" --junitxml "$Run\red-wrapper.xml" --tb=short
& $Python -B $Guard tests/test_scheduled_remediation.py -k duplicate_recovery -q -p no:cacheprovider --basetemp "$Run\red-duplicate-tmp" --junitxml "$Run\red-duplicate.xml" --tb=short
& $Python -B $Guard tests/test_scheduled_remediation.py tests/test_scheduled_forward_paper.py tests/test_paper_observer.py tests/test_forward_paper_scheduler_scripts.py -q -p no:cacheprovider --basetemp "$Run\focused-final-tmp" --junitxml "$Run\focused-final.xml" --tb=short
```

| Run | Passed | Failed | Errors | Skipped | Deselected | Console seconds | JUnit seconds |
|---|---:|---:|---:|---:|---:|---:|---:|
| Initial new regressions, before production patch | 14 | 17 | 0 | 0 | 0 | 28.92 | 28.907 |
| Corrected wrapper fixtures, still before patch | 1 | 2 | 0 | 0 | 28 | 11.09 | 11.089 |
| Duplicate diagnostic regressions, before final count fix | 0 | 2 | 0 | 0 | 31 | 16.15 | 16.149 |
| Final focused new + original69 | **105** | **0** | **0** | **0** | **0** | **102.48** | **102.452** |

Initial failures include the intended seven-defect reproductions and a copied-project fixture error: using PYTHONPATH for site packages let an old installed uuid package shadow stdlib. The corrected fixture uses a plain path .pth after stdlib instead. Foreign-directory assertion was clarified to report added names before trying to read directory bytes; refusal/no-write expectations stayed intact.

An intermediate `focused1.xml` run reported **97 passed / 3 failed / 0 errors / 0 skips / 0 deselections**, 155.26s console / 155.213s JUnit. Two failures exposed the test ownership fixture being inside a foreign control root; it was moved to a synthetic sibling. A retry assertion also failed in this intermediate run, which overlapped source edits and is not used as completion evidence. With source frozen, the full 105-case rerun passed, including that unchanged retry assertion. All intermediate XML and task-start source copies remain external; failures are not omitted from this record.

The inspected offline guard disables dotenv, clears credential-like environment values, denies Python socket/DNS calls and disables bytecode/cache. Existing conftest redirects real logs and requires transport mocks. Native PS tests are previews or blocked local children; Python guards are not an OS-wide network sandbox. Approved out-of-sandbox execution was used because the sandbox previously denied this interpreter/native script startup. No execution-policy bypass or security-policy change was used.

## 7. Complete offline suite

One complete unfiltered run passed **1,974 tests**, with **0 failures, 0 errors, 0 skips and 0 deselections**, exit **0**. Actual XML contains 1,974 testcase elements and no failure/error/skipped elements. This exactly matches 1,938 baseline + 36 new cases; no count mismatch, exclusion, rerun or selection filter was used. Console runtime **1,158.20 seconds**, JUnit time **1,157.997 seconds**, capture-driver wall time **1,161.3440983 seconds**. All original69 infrastructure cases and existing V2/recovery/accounting, market-data and CSV regressions are included.

Python was 3.13.2. UTC interval: `2026-11-12T17:31:58.025521+00:00` to `2026-11-12T17:51:19.371592+00:00`; India interval: **12 November, 23:01:58.025521 to 23:21:19.371592 IST**. The coordinator read process exit metadata, final console output and XML independently. Exact argv, cwd and timing remain in external full-metadata.json, full.log and full.xml. These are fresh coordinator-executed repository results, not a claim that the source reviewer independently reran the full suite.

```powershell
& $Python -B "$Run\run_full.py"
# Capture driver executes this exact unfiltered command:
& $Python -B $Guard -q -p no:cacheprovider --basetemp "$Run\full-tmp" --junitxml "$Run\full.xml"
```

## 8. Fresh read-only review

A fresh-context reviewer inspected the task-start diff for exactly the seven findings, without editing repository files. It found the completed synthetic duplicate branch's missing count propagation; its unchanged external probe failed before the fix (1 failed, 10.80s) and passed after it (1 passed, 3.66s). Repository regressions separately reproduced both one- and two-failure variants before correction.

After rereading the final patch, the reviewer reported **no remaining confirmed defects in the seven scoped remediations**. Reviewed scheduler SHA256: `fe4dc273518d3016dcd4c279ac8152eaf1a142d1246be994f61b514ea96d052e`. Evidence is `$Run/review-probes/REVIEW.md`, accounting.xml and accounting-fixed.xml. This is fresh-agent same-workspace scrutiny, not unrelated institutional certification or an independent full-suite rerun; the coordinator owns repository verification.

## 9. Files changed

Modified production/script files:

- `app/research/scheduled_forward_paper.py`
- `app/research/paper_observer.py`
- `scripts/run_scheduled_forward_paper.py`
- `scripts/run_forward_paper_scheduled.ps1`

Modified existing tests:

- `tests/test_scheduled_forward_paper.py` — ownership fixture isolation only.
- `tests/test_forward_paper_scheduler_scripts.py` — reject-before-write behavior while retaining safe status/exit assertions.

Added:

- `tests/test_scheduled_remediation.py`
- `docs/SCHEDULED_FORWARD_PAPER_AUDIT_REMEDIATION.md`

The existing dirty working tree is baseline, not this task's change. No dependency, commit, push, PR or historical rewrite was introduced. Plans, source copies, review probes and execution artifacts are external.

## 10. Preservation

Before-edit baseline contains **651 files**, canonical SHA256 `57eb17e0c1ed3f1b1a8b1974ed9cf0f200a3671053c4da13fca3b25567a4a10d`, matching the independent audit. The final allowed-change comparison passes: exactly the six authorized existing files changed, exactly the two listed additions appear, and the ending inventory contains **653 files**. All other **645** pre-existing inventoried files match. The original pre-infrastructure **640-file** inventory also remains entirely unchanged.

V2.2 settlement, risk/strategy/cost/calendar, broker boundary, candle validator, CSV logger and previous reports remain byte-identical. All **22 Sessions02-05 files** and **five real log/database artifacts** match. Protected V2.2 SHA256 remains `f77facd4d0fa3b844a66ebdb3cd34e9b564dc7dffe150ffc03d76f8e10e68b90`; Git HEAD remains `a0789cdb5db50deece446005c40064ca5de989a4`. No unrelated runtime lock/log file was added inside the real repository by tests.

External before.json/after.json and verification-results.json retain canonical final manifest SHA256, per-file allowed changes, protected hashes, exact test counts and metadata. The manifest is regenerated after this report's final edit rather than embedding its own changing hash here. Syntax/whitespace assertions over all changed/new files also pass. Verification commands:

```powershell
& $Python -B "$Base\causal-v2-independent-audit\hash_workspace.py" "$Run\after.json"
& $Python -B "$Run\verify_results.py"
```

Inventory scope is root files except .env plus app/scripts/tests/docs/data/backtests/experiments/reports/diagnostics/logs/schemas, excluding pycache/pyc. No claim is made for excluded paths. Real financial databases are not opened for preservation verification; bytes are hashed only.

## 11. Remaining limitations

Reviewed V2.2 live transport integration, real-time paper readiness, profitability, physical power-loss durability, externally trusted rollback freshness, production scheduler activation and live broker execution remain unestablished. No local scheduler sequence/hash/file fixes the consistent older database-plus-anchor rollback limitation without an independently trusted witness.

Ancestor path trust, malicious concurrent filesystem replacement and observer event-stream growth limits remain documented operational limitations from the audit; they are not silently claimed resolved. Observer liveness remains reported heartbeat freshness with process_alive=null. No unavailable confidence/rank/current-quote/drawdown measurement is invented.

## 12. Authorization status

All seven findings are remediated within the authorized scope and regression-tested. No real-time paper session, live/provider/broker request, scheduled-task registration or activation, or causal experiment was run or authorized. Production transport remains structurally absent; broker execution remains disabled. Ready for independent read-only remediation verification only. No next phase begins automatically.

**REMEDIATION VERIFIED. The scheduled forward-paper ownership, chronology, observer robustness, filesystem-validation and diagnostic-accounting findings are remediated and regression-tested. Existing paper-only, transport-block, calendar, recovery and observer read-only controls remain intact. Ready for independent read-only remediation verification only. Do not activate the schedule, integrate live transport, or start a real-time paper experiment. Broker execution remains disabled.**
