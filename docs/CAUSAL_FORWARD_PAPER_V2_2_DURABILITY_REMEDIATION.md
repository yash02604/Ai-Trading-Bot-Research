# Causal Forward Paper V2.2 — local publication durability remediation

## 1. Files changed

| File | Type | Purpose |
|---|---|---|
| app/research/causal_forward_paper_v2.py | Modified | Explicit file/directory flush ordering, verified ready publication and durable recovery cleanup |
| tests/test_causal_forward_paper_v2_durability.py | Added | Isolated synthetic interruption, durability failure, staging rejection and exact-equity regressions |
| docs/CAUSAL_FORWARD_PAPER_V2_2_DURABILITY_REMEDIATION.md | Added | Scope, ordering, verification, preservation and limitations |

Only local persistence/publication behavior changes. The existing V2 entry point, MODE, POLICY, schema, staging filename grammar, source binding and offline provenance restrictions remain. No migration or historical-ledger resumption was performed. Existing working-tree changes belong to the starting workspace and were preserved.

## 2. V2.2 durability fix

V2.1 flushed `.writing` contents, then performed two replacements without explicitly requesting persistence of the containing directory metadata. V2.2 adds `_sync_directory` and `_sync_file`, uses them at both publication boundaries, and verifies `.ready` bytes before canonical replacement. A failed flush/replace raises; the command does not return success.

On POSIX, the directory helper opens the actual parent directory with O_RDONLY and O_DIRECTORY where available, calls fsync, and closes the descriptor in finally. On Windows it opens that directory with CreateFileW(GENERIC_WRITE, sharing read/write/delete, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS), calls FlushFileBuffers, and closes the handle. Open, flush and close errors propagate. A flush failure preserves its original error after closing the handle. Unsupported operations are never silently treated as successful.

The native Windows directory-flush path succeeded on the local test filesystem, both in a pre-edit probe and an unmocked regression. A read-only handle failed the pre-edit probe with access denied; writable directory access is deliberately required. This verifies OS API acknowledgement locally, not metadata survival under physical power loss. POSIX descriptor behavior is covered through controlled fixtures on this Windows host; native POSIX execution has not been performed.

## 3. `.writing → .ready` publication

Under the existing exclusive experiment-directory lock:

1. Derive a recognized, unique owned `.writing` filename from the publication identity.
2. Create exclusively, write canonical encoded bytes, flush the stream and fsync the file.
3. Close the stream; reject an existing paired `.ready` artifact.
4. Atomically rename `.writing` to `.ready` in the same parent directory.
5. Synchronize that parent directory through `_sync_directory`.
6. Read at most expected-length-plus-one bytes and require exact expected canonical bytes.

Failure leaves diagnostic staging evidence for the existing verified recovery rules. `.writing` remains an unsealed exact-prefix artifact; `.ready` must contain the full expected bytes. Neither becomes independent financial authority.

## 4. `.ready → canonical` publication

Only after ready-directory synchronization and exact byte verification: atomically replace the canonical target; open and fsync the canonical file; synchronize its parent directory. `_atomic` returns only after these operations succeed. No missing durability operation is substituted with a successful rename or another local hash.

Canonical state visible after a failed flush remains unacknowledged. On restart, authoritative semantic replay and ancestry checks run first; a fully valid recovered anchor is flushed again, followed by directory synchronization, before recovery can return success or remove the intent. The same re-establishment applies to idempotent retries after acknowledgement loss, even if no pending intent remains visible.

## 5. Anchor publication ordering

`_apply` retains the exact ordering: verify old ledger and anchor; derive/replay the proposed events; validate exact base ancestry and monotonicity; durably publish pending intent; commit the SQLite transaction; durably publish the final anchor; re-establish anchor/quarantine/directory boundaries; unlink pending intent; synchronize the parent directory; return the result.

SQLite retains its existing default FULL synchronous transaction behavior. The anchor publication follows transaction commit and cannot precede the ancestry checks or durable intent publication. The protocol continues to rely on SQLite honoring its transaction/flush contract. A failure during anchor publication leaves the committed settlement recoverable against the retained intent. A failure before SQLite commit leaves the previous committed state; recovery never invents the uncommitted command.

`_finish_publication` is also used by `_verified`, so canonical visibility alone cannot bypass a failed earlier durability boundary. Pending cleanup is acknowledged only after a subsequent directory sync. All these operations occur under the existing process/thread lock.

## 6. Recovery matrix

The new interruption matrix runs each atomic boundary against both pending-intent and anchor publication. Pre-commit intent interruptions reconstruct the old balance 1,000, then an identical closing retry yields 1,005 once. Anchor interruptions already have the SQLite settlement: restart immediately reconstructs 1,005. This distinction is necessary: a command interrupted before durable creation cannot be manufactured on restart.

| Interruption point | Expected recovery | Financial effect |
|---|---|---|
| Before `.writing` creation | Validate canonical ledger/intent; retain old or finish committed head | One settlement after retry; already committed settlement retained |
| After creation, before write/fsync | Verify empty owned prefix, quarantine, recover | Same |
| During partial write | Verify expected prefix, sync and quarantine, recover | Same |
| Before `.writing` fsync | Verify complete unsealed prefix, quarantine, recover | Same |
| After `.writing` fsync | Quarantine verified owned writing artifact, recover | Same |
| Before ready rename | Same owned writing recovery | Same |
| After ready rename | Exact ready validation before quarantine/republication | Same |
| Before ready-directory sync | Retry verified recovery; no command success | Same |
| After ready-directory sync | Recover from full verified ready evidence | Same |
| Before canonical replacement | Recover verified ready evidence against authoritative ledger | Same |
| Immediately after replacement | Verify visible canonical head and retained intent; reflush | Same |
| Before canonical file sync | Re-establish file and directory boundaries | Same |
| After canonical file sync | Re-establish directory boundary | Same |
| Before canonical-directory sync | No success; recover and reflush verified state | Same |
| After canonical-directory sync | Complete verified intent cleanup | Same |
| After `_atomic` return / before next publication step | Pending: preserve pre-commit base; anchor: preserve committed final | Same |
| Before acknowledgement cleanup | Verify/reflush anchor and clear intent durably | Exactly one committed settlement, balance 1,005 |
| After intent unlink, before cleanup directory sync | Re-establish directory boundary, even without visible intent | Same |
| After cleanup / response lost | Idempotent identical observation, unchanged settlement identity | Same |
| Quarantine creation parent sync | Retry validated artifact handling | No new economic effect |
| Quarantine file/destination-directory/source-directory sync | Verify canonical ledger; complete or re-establish quarantine metadata boundaries | No new economic effect |

For accepted cases, repeated restart/retry checks balance, eligible equity, available cash, released reservation, net P&L, exact settlement row identity and the unchanged final anchor. Existing V2.1 tests additionally use actual child process termination at selected publication boundaries. New tests use deterministic injected exceptions and operation mocks; no host termination, OS crash or physical storage fault was attempted.

## 7. Ambiguous/corrupt artifact behavior

The V2.1 ownership grammar and single-active-artifact rule are unchanged. Corrupt writing content, truncated/corrupt ready content, foreign names, multiple writing/ready files, mixed writing plus ready, older/future/wrong-identity heads and inconsistent ancestry fail closed. Rejection leaves canonical database/anchor bytes and diagnostic artifacts intact. A valid descendant ready artifact is accepted only against the already committed SQLite chain, exact retained base and matching canonical intent; it cannot manufacture future database history.

Fully valid committed canonical state plus one recognized owned prefix/ready artifact retains the committed state and quarantines the artifact. Staging never overrides a newer anchor. An invalid authoritative ledger is rejected before directory synchronization or quarantine action.

## 8. V2.1 protections preserved

`_publication`, `_intent`, `_staging`, `_connection` and the semantic replay/economic functions retain their pre-change implementation. `_verified` only delegates its successful finalization/cleanup to `_finish_publication`; all ledger, base-ancestry, monotonicity and staging checks still precede that step.

Quarantine occurs only after authoritative ledger validation, ownership/integrity checks and the recovery decision. Its parent creation is directory-synchronized. Verified source bytes are fsynced before the move; after the move, the destination file, destination directory and source directory are synchronized. A failed quarantine operation returns no financial result. Later verified recovery re-establishes quarantine/source directory boundaries. Quarantined bytes remain inert and are never replayed as financial authority.

The unchanged V2.1 suite explicitly checks sequence 5→2 rejection, older database against a newer witness, same-sequence substitution, wrong base ancestry, reordered/substituted events, contradictory metadata, valid committed/uncommitted intent recovery and repeated restart. No integrity check was relaxed.

## 9. Economic invariants

Trading, entries, costs, replay mechanics, position reservations, settlement arithmetic and risk functions are unchanged. The new matrix compares the complete synthetic settlement row to the reference, ensuring the same identity and P&L rather than checking counts alone. Repeated recovery/retry produces one settlement, net P&L 5, balance/eligible equity/available cash 1,005 and zero reservation from initial capital 1,000.

The explicit new risk cases verify equity 900→maximum planned risk 9, 1,000→10 and realized-profit equity 1,005→10.05. Cost-inclusive risk just above each threshold is rejected; just below is accepted; restart reconstructs the same equity. The existing V2 suite additionally covers loss/profit histories, actual costs, concurrent close, duplicate/retry settlement, reservation release and daily-loss behavior. The daily-loss reference remains initial capital.

## 10. Test results

All six gates were actually rerun, in the requested order, on Windows 11, Python 3.13.2 and pytest 9.1.1. These are implementation-author reruns, not an independent audit.

| Suite | Passed | Failed | Errors | Skipped | Deselected | Duration |
|---|---:|---:|---:|---:|---:|---:|
| V2.2 focused | 78 | 0 | 0 | 0 | 0 | 98.190s |
| V2.1 recovery | 43 | 0 | 0 | 0 | 0 | 87.437s |
| V2 | 104 | 0 | 0 | 0 | 0 | 124.925s |
| V1 | 46 | 0 | 0 | 0 | 0 | 12.846s |
| Related safety | 208 | 0 | 0 | 0 | 0 | 254.857s |
| Complete offline suite | 1641 | 0 | 0 | 0 | 0 | 1168.160s |

Durations above are exact JUnit suite times. Pytest's complete-run console elapsed time was 1168.45s; the driver including interpreter startup recorded 1170.314048s. No selection expressions or deselections were used. The complete run includes the focused tests again; table rows are separate executions, not disjoint test populations.

Exact commands, expressed with the following fully specified PowerShell path variables:

```powershell
$Python = 'C:\Users\Priyanka\Desktop\ai-trading-bot-research\.venv\Scripts\python.exe'
$Temps = 'C:\Users\Priyanka\.codex\visualizations\2026\11\05\01a1acdb-980d-7dd2-b495-2691796e9d33'
$Audit = "$Temps\causal-v2_2-remediation"
& $Python -B "$Audit\run_offline.py" tests/test_causal_forward_paper_v2_durability.py -q -p no:cacheprovider --basetemp "$Temps\v22fixed" --junitxml "$Audit\v22.xml"
& $Python -B "$Audit\run_offline.py" tests/test_causal_forward_paper_v2_recovery.py -q -p no:cacheprovider --basetemp "$Temps\v22v21" --junitxml "$Audit\v21.xml"
& $Python -B "$Audit\run_offline.py" tests/test_causal_forward_paper_v2.py -q -p no:cacheprovider --basetemp "$Temps\v22v2" --junitxml "$Audit\v2.xml"
& $Python -B "$Audit\run_offline.py" tests/test_causal_forward_paper.py -q -p no:cacheprovider --basetemp "$Temps\v22v1" --junitxml "$Audit\v1.xml"
& $Python -B "$Audit\run_offline.py" tests/test_core.py tests/test_cost_model_execution_integrity.py tests/test_signal_complete_seal_integrity.py tests/test_signal_torn_seal_recovery.py tests/test_paper_session.py tests/test_execution_integrity.py tests/test_prospective_lifecycle_fixes.py -q -p no:cacheprovider --basetemp "$Temps\v22related" --junitxml "$Audit\related.xml"
& $Python -B "$Audit\run_offline.py" -q -p no:cacheprovider --basetemp "$Temps\v22full" --junitxml "$Audit\full.xml"
```

The [external evidence directory](C:/Users/Priyanka/.codex/visualizations/2026/11/05/01a1acdb-980d-7dd2-b495-2691796e9d33/causal-v2_2-remediation) retains literal argv in focused-command.json and commands.json, JUnit XML, the final focused tool-output summary, five subsequent suite logs, test-totals.json, the launcher and driver. The launcher disables dotenv before app import, clears credential-like environment variables, denies socket connect/connect_ex/sendto/create_connection and DNS lookup, disables bytecode and disables pytest cache. The existing conftest isolates real logging/state defaults. All basetemp, result, copied-ledger and staging output is external. Process-interruption children use their existing offline safeguards and inherit credential-cleared, bytecode-disabled environments.

Development history is separate from the final table: an initial 72-case run passed in 100.87s. Adding six risk fixtures initially yielded 72 passed / 6 failed / 0 errors / 0 skipped / 0 deselected in 105.21s because the chosen prices violated the existing maximum stop-distance rule before reaching risk sizing. Only the fixture entry prices were corrected; implementation/risk rules were unchanged. The final 78-case rerun above passed. The intermediate failure XML is retained as fixture-correction.xml. An earlier redirected launcher was cancelled without a test result and is not counted as verification.

## 11. Preservation/integrity verification

The pre-edit inventory matched all 632 files of the retained V2.1 ending manifest. The final inventory contains 634 files: the one authorized modified implementation and exactly two added deliverables. All other 631 pre-existing protected files match byte-for-byte.

| Check | Result |
|---|---|
| Historical baseline matched | YES — all 627 files |
| Protected evidence changed | NO |
| Sessions 02–05 changed | NO — all 22 files matched |
| Real causal state changed | NO — no real causal ledger/anchor was present in the protected inventory or opened by this work |
| Real logs changed | NO — all five log/report/paper database/WAL/SHM files matched |
| HEAD/status unexpectedly changed | NO — same HEAD; status adds only this report and the new test module |
| External request occurred | NO |
| Experiment run | NO |
| Broker execution enabled | NO |

HEAD remains a0789cdb5db50deece446005c40064ca5de989a4. The retained historical manifest SHA256 is 14f4918bec9fe8bf2a9de92280c46c2492dea25400b42954520752b9baadce58. The same 631 protected-path canonical manifest hashes to bf42650c8e1105b77725aa9d1d37b40aa3f5afa54650782b7199c4808db8668e before and after. This is comparison to retained historical evidence, not a newly invented historical baseline.

The authorized implementation SHA256 changes from 6fccaa24cd8affccca7784a1973d5a35a0e2a883afabfab9b2908027e1898b3a to f77facd4d0fa3b844a66ebdb3cd34e9b564dc7dffe150ffc03d76f8e10e68b90. The external [preservation report](C:/Users/Priyanka/.codex/visualizations/2026/11/05/01a1acdb-980d-7dd2-b495-2691796e9d33/causal-v2_2-remediation/preservation.json) retains individual real-state/log hashes, before/after inventories, unchanged HEAD/status comparison, exact changed-function scope and the task diff. The starting source copy was retained before editing; the implementation was already untracked in the starting Git status, so the task diff uses that verified copy rather than treating Git's lack of a tracked diff as preservation proof. Earlier reports, tests, audits, Sessions, budgets and historical evidence remain unchanged. No commit or migration was performed.

## 12. Durability guarantees and limitations

| Layer | Claim and limit |
|---|---|
| Process crash | Deterministic boundary tests and existing abrupt-child tests establish verified recovery/idempotency for the exercised filesystem states. Unknown, inconsistent or ambiguous surviving evidence rejects. |
| OS/filesystem | Explicit file fsync and directory flush requests are ordered before acknowledgement. Errors propagate. Actual Windows directory API success was observed locally. Other filesystems, permissions, shares or platforms may reject and remain unsupported/fail closed. Atomic rename and SQLite transaction semantics are prerequisites. |
| Host power loss | No destructive power-loss test was performed. Successful API calls alone do not prove physical persistence. Windows directory FlushFileBuffers is reported as an OS flush acknowledgement, not as a portable POSIX fsync equivalence or universal metadata-survival guarantee. |
| Hardware | Filesystem, storage driver, controller, device firmware and write cache must honor flushes/order. Device failures, dishonest caches, torn hardware writes and loss of the entire directory/storage are outside these tests. The containing filesystem and ledger directory must themselves be provisioned reliably. |
| Joint DB+anchor rollback | Replacing both with a mutually consistent older pair remains undetectable from local evidence alone. No new local hash/file solves this; a separately trusted freshness/completion witness remains a distinct future decision. |

Source binding is retained: this implementation revision changes the recorded source hash. Previously source-bound V2/V2.1 databases safely reject a mixed-source resume. There is no automatic migration, source-hash rewrite or historic settlement repair. No stored-format/schema migration was introduced.

## 13. Remaining risks

Independent review remains required. Native POSIX execution, other Windows filesystems/shares and genuine OS/power/storage failures have not been tested here. Directory-write/flush permissions are now required for durable publication; unsupported storage fails explicitly. Extra verified-read flushes have an I/O cost and are intentional after uncertain acknowledgement. Joint local database-and-anchor rollback remains unresolved without a separately trusted witness. No prospective/live/reviewed-data or broker readiness follows from offline tests.

## 14. Final verdict

**READY FOR INDEPENDENT READ-ONLY CAUSAL PAPER AUDIT ONLY**

All requested offline gates and protected-file comparisons passed. This is readiness for independent review of the tested local protocol, with the storage/platform limitations above; it is not an independent audit pass or experiment authorization.

**No experiment was run or authorized. Broker execution remains disabled. Real-time paper trading and causal settlement remain NO-GO.**
