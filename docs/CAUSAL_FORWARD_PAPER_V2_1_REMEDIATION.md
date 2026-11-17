# Causal Forward Paper V2.1 — narrow recovery remediation

## Scope

This revision addresses only the two confirmed recovery findings in the external Causal V2 audit report. The V2 entry point, policy/mode names, economic replay, current-equity definition, daily-loss basis, sizing, costs, entry/exit rules and metrics retain their existing paths. The implementation remains offline-only.

Intended changes are app/research/causal_forward_paper_v2.py, the new tests/test_causal_forward_paper_v2_recovery.py and this new report. Existing V2 tests and all earlier reports remain preserved.

The audit reviewed for this work is [AUDIT_REPORT.md](C:/Users/Priyanka/.codex/visualizations/2026/11/05/01a1acdb-980d-7dd2-b495-2691796e9d33/causal-v2-independent-audit/AUDIT_REPORT.md). Starting HEAD was a0789cdb5db50deece446005c40064ca5de989a4. Before editing, 630 existing files were hashed. Their canonical manifest SHA256 was 44ee7e41baa56974cd77474f77aca898416cf74d6a25172b6fe7c6844d984079.

## Finding 1 — committed-head monotonicity

The previous recovery branch compared head equality but could accept a final state older than the retained committed base.

The new _publication validator runs before any witness update or recovery action. It validates exact head schemas, integer sequence types, hash/configuration identities and a strictly forward sequence increase. Current COMMAND publication adds one event, while closing publication adds three. Equal, negative, backward, malformed and unsupported sequence changes are rejected.

For any committed final transaction, the verified event chain must contain the exact base at its recorded sequence. The complete database head must equal the stated final head. The retained anchor must equal either that verified base or that final head. A branch with the same sequence but a different ancestor hash is rejected. Reordered or substituted events fail normal causal replay before recovery can publish anything.

The ordinary write path also calls this validator against its verified old head and newly derived event chain before publishing intent. The restart path validates the entire committed chain, checkpoints and settlements before considering recovery metadata. No caller can use contradictory recovery metadata to lower the committed head.

Bootstrap rejects missing or empty databases when retained recovery artifacts are present, before publishing a new genesis witness. An interrupted genesis publication is recoverable only when its existing source-bound empty ledger and recognized owned genesis stage validate together.

There are three legitimate restart states.

| SQLite head | Retained anchor | Action after verification |
|---|---|---|
| Exact base | Exact base | Abandon only the uncommitted, unacknowledged intent; preserve the committed base |
| Exact final, containing exact base prefix | Exact base | Complete publication of the verified final head |
| Exact final, containing exact base prefix | Exact final | Complete acknowledgement housekeeping |

An uncommitted intent whose final transaction is absent cannot authorize a recovered final head. Only its format and forward intent are checked before it is abandoned; the verified base remains unchanged. All other combinations fail closed. Missing or contradictory committed history is never repaired by changing balances, event rows or checkpoints.

## Finding 2 — owned staging and interrupted atomic publication

_atomic now uses a two-stage publication convention.

1. Create an exclusive owned .writing file.
2. Write canonical bytes, flush and fsync them.
3. Atomically rename the file to the corresponding .ready name.
4. Atomically replace the canonical pending.json or anchor.json.

The naming convention is:

- Anchor staging uses .cfv2-a-{sequence}-{head-hash-token}-{nonce}.writing or .ready.
- Intent staging uses .cfv2-p-{base-sequence}-{base-hash-token}-{final-sequence}-{final-hash-token}-{nonce}.writing or .ready.

Hash tokens are canonical unpadded URL-safe Base64 encodings of SHA256 values. The nonce has eight lowercase hexadecimal characters. Sequence syntax and bounds are explicit. Compact tokens keep Windows staging paths shorter. Configuration identity is supplied by the source-bound ledger configuration and verified against expected payload bytes.

The name determines the exact expected canonical payload. A .ready file must contain that complete payload. An unsealed .writing file may contain an exact prefix, including an empty prefix after exclusive creation. This distinguishes an interrupted write from a corrupt prepared publication. Arbitrary bytes, oversized contents, invalid identities, foreign names and more than one active staging candidate fail closed.

Every public recovery call holds the existing cross-process directory lock. The authoritative event chain, economic replay, checkpoints, settlements, retained anchor and canonical intent are validated before a staging candidate can be moved. The staging file must describe that verified state or an uncommitted intent from that exact verified base. Staging never becomes independent authority for a missing committed transaction.

Recognized interrupted artifacts are atomically moved into .cfv2-q. Their bytes are preserved. Quarantine filenames are stable SHA256 identifiers of the original staging names with the original phase suffix. Existing destinations are never overwritten. Recovery rejects a quarantine path that is a file, symlink or junction. Quarantine contents are inert retained evidence, never replayed or consumed as publication authority.

All candidate validation completes before quarantine or anchor updates. Invalid ledger data or contradictory intent leaves the anchor and staging files untouched. Recovery quarantines the old stage before retrying publication, so interruption of recovery itself does not accumulate multiple active publication candidates. Repeated recovery preserves settlement identity, financial state and one economic effect.

The legacy unowned tmp naming convention is not accepted as trusted staging. This revision does not weaken source/configuration pins or silently migrate old stored ledgers. Tests use fresh synthetic ledgers and copies under the current source version.

## Changes and defect evidence

| File | Type | Purpose |
|---|---|---|
| app/research/causal_forward_paper_v2.py | Modified | Validate publication ancestry/monotonicity at both callers; classify and quarantine owned staging after ledger verification |
| tests/test_causal_forward_paper_v2_recovery.py | New | Defensive copied-state invariants and actual process-interruption tests |
| docs/CAUSAL_FORWARD_PAPER_V2_1_REMEDIATION.md | New | This report and preservation results |

The original audit provided the failure evidence before editing. The first 32 new regressions passed. The final test module adds malformed-head, empty-write, interrupted-recovery and quarantine-path checks.

| Property | Regression coverage |
|---|---|
| Sequence 5 cannot regress to 2 | Copied synthetic prefix against newer witness, repeated failure with unchanged anchor |
| Exact ancestry | Wrong ancestor, reordered/substituted records, wrong final/config and impossible sequences |
| No mutation on inconsistency | Anchor bytes, canonical intent and staging evidence remain unchanged on rejection |
| Normal recovery | Both uncommitted and committed publication paths, repeated restart and closing retry |
| Temporary-file interruption | Before creation, during write, after fsync, before canonical replacement and after replacement |
| Committed settlement before anchor | Actual child process stops during anchor publication; restart preserves balance 1,005 and one settlement |
| Acknowledgement loss | Identical closing observation does not add another financial effect |
| Corrupt/foreign evidence | Invalid .writing/.ready bytes, truncated .ready, unrelated files, ambiguous candidates and mismatched state |
| Verify before staging action | Invalid ledger plus otherwise recognized stage rejects without quarantine or witness mutation |
| Recovery interrupted again | A second interrupted publication is safely retained and finalized once on a later restart |
| Quarantine ownership | File/junction destinations reject; repeated recovery retains original staging bytes once |
| Bootstrap boundary | Recovery artifacts cannot reinitialize a missing/empty database; a verified interrupted genesis completes without an economic event |
| Joint local rollback | A mutually consistent older database and anchor still describe an undetectable older local state |

## Test isolation and execution

| Suite | Passed | Failed | Errors | Skipped | Deselected | Duration |
|---|---:|---:|---:|---:|---:|---:|
| V2.1 recovery plus existing V2 | 147 | 0 | 0 | 0 | 0 | 179.39s |
| Related regressions | 174 | 0 | 0 | 0 | 0 | 140.78s |

The final focused run comprises 43 new recovery cases and all 104 existing V2 cases. The related run includes all V1 causal tests plus core risk, cost-model execution integrity, complete-seal integrity and torn-seal recovery. These are 321 selected tests with zero failures, errors, skips or deselections. The full offline suite was not rerun for this narrowly scoped revision.

Commands were executed from the repository root using the project virtual environment. <audit> denotes C:/Users/Priyanka/.codex/visualizations/2026/11/05/01a1acdb-980d-7dd2-b495-2691796e9d33/causal-v2_1-remediation and <visualizations> denotes its parent.

```text
.venv/Scripts/python.exe -B <audit>/run_offline.py tests/test_causal_forward_paper_v2_recovery.py tests/test_causal_forward_paper_v2.py -q -p no:cacheprovider --basetemp <visualizations>/v21z --junitxml <audit>/focused-final.xml
.venv/Scripts/python.exe -B <audit>/run_offline.py tests/test_causal_forward_paper.py tests/test_core.py tests/test_cost_model_execution_integrity.py tests/test_signal_complete_seal_integrity.py tests/test_signal_torn_seal_recovery.py -q -p no:cacheprovider --basetemp <visualizations>/v21r --junitxml <audit>/related.xml
```

JUnit wall durations are shown above. The exact per-suite attributes, class counts and logs are retained in the external audit directory. No -k, -m, skip or deselection mechanism was used. Earlier intermediate passing runs are retained separately and are excluded from the final counts.

The [task diff](C:/Users/Priyanka/.codex/visualizations/2026/11/05/01a1acdb-980d-7dd2-b495-2691796e9d33/causal-v2_1-remediation/recovery-v2_1.diff), [test totals](C:/Users/Priyanka/.codex/visualizations/2026/11/05/01a1acdb-980d-7dd2-b495-2691796e9d33/causal-v2_1-remediation/test-totals.json), JUnit results and captured logs provide the verification record.

All runs use external basetemp and JUnit/log output. The launcher disables dotenv, clears credential-like environment variables, denies outbound socket connection/send paths, disables Python bytecode writing and disables the pytest cache provider. Process-interruption children explicitly deny sockets and inherit bytecode-disabled, credential-cleared environments. There is no market/provider acquisition or real/reviewed settlement.

These are actual reruns by the implementation author. They do not substitute for an independent peer audit. The full offline suite is not claimed as rerun for this narrow revision; the focused and nearest affected regression gates are reported individually.

## Preservation and hashes

| Check | Result |
|---|---|
| Starting / final inventory | 630 / 632 files |
| Authorized pre-existing modification | app/research/causal_forward_paper_v2.py only |
| New files | Recovery test module and this report only |
| Other pre-existing protected files unchanged | 629 |
| Independently retained original baseline | All 627 files matched |
| Sessions 02–05 | 22 files unchanged |
| Real logs/paper database/WAL/SHM | 5 files unchanged |
| HEAD | Unchanged |
| Git status | Starting state plus exactly the two new untracked files |
| Scoped whitespace | Passed |
| External requests / experiment / broker execution | NO / NO / NO |

The before/after canonical SHA256 for the same 629 protected paths is 7e1e0ac5877998eb40ce8a812669f633be13fff0b8b39a5e3028ff4a2f1b9de7. The retained original 627-file manifest has SHA256 14f4918bec9fe8bf2a9de92280c46c2492dea25400b42954520752b9baadce58. Historical preservation was verified against that pre-existing retained baseline, not inferred solely from a new snapshot.

The pre-change untracked V2 source was recovered byte-for-byte from its retained creation diff and checked against the starting source hash to produce the task-specific diff. Existing working-tree edits were preserved. No commit, historical regeneration or old-ledger repair occurred. The private environment file was outside the manifest and was never edited.

The external [preservation report](C:/Users/Priyanka/.codex/visualizations/2026/11/05/01a1acdb-980d-7dd2-b495-2691796e9d33/causal-v2_1-remediation/preservation.json), before/after manifests, HEAD/status snapshots, test results and task diff retain the exact evidence.

The authorized implementation file must have a new source hash. Every other pre-existing file is protected. The original retained 627-file historical baseline is also checked independently of the new starting snapshot. Source-hash binding continues to reject silent mixed-version resumption. No prior ledger, audit report, Session 02–05, historical output, request budget, attestation or calendar evidence is upgraded or rewritten.

## Remaining limits

A consistent older database and local anchor can still be jointly replaced without local detection. A separately trusted completion witness is required to claim protection against that condition. The staging names and additional local validation do not solve joint rollback or provide external source authentication.

The protocol relies on SQLite and the filesystem honoring transaction, fsync and atomic-replace semantics. Tests cover abrupt process death and interruption at the specified boundaries, not every destructive hardware/power-loss failure. Quarantined bytes are retained for inspection and never accepted as financial authority. A caller with administrative control over all local evidence remains outside the authenticated trust boundary.

## Readiness

**READY FOR INDEPENDENT READ-ONLY CAUSAL PAPER AUDIT ONLY**, subject to the stated storage trust boundary and independent review.

No independent audit pass, prospective readiness, live/reviewed settlement readiness or broker authorization is claimed.

No experiment was run or authorized. Broker execution remains disabled. Real-time paper trading and causal settlement remain NO-GO.
