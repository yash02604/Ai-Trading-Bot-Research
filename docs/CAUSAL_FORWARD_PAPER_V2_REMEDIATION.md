# Causal Forward Paper V2 remediation

## 1. Files changed

| File | Type | Purpose |
|---|---|---|
| app/research/causal_forward_paper_v2.py | New | Isolated V2 authoritative input chain, independent replay, derived checkpoints, exactly-once settlement, head anchoring and current-equity trade risk |
| tests/test_causal_forward_paper_v2.py | New | Copy-only tamper attacks, replay/accounting, risk, balance proof and interrupted publication regressions |
| docs/CAUSAL_FORWARD_PAPER_V2_REMEDIATION.md | New | This report |

The V2 entry point is CausalForwardPaperV2. It requires an empty dedicated directory and accepts only UNVERIFIED_CALLER_SUPPLIED or SYNTHETIC_TEST_DATA provenance. It never upgrades a caller to reviewed capture. Existing V1 modes and saved evidence retain their source bindings.

## 2. Audit defects fixed

| Finding | Severity | Root cause | Fix | Test evidence |
|---|---|---|---|---|
| Missing exit rewrites settlement | HIGH | Individually hashed rows lacked a committed extent and complete causal replay | Complete sequence/hash chain plus a separately persisted committed head; deterministic replay expects exact event roles, checkpoints and settlements | Missing exit, settlement, entry, middle/final records, truncation, reordered and substituted events fail closed |
| Duplicate closure/P&L | HIGH | Row identity was not bound to economic identity and arbitrary closed snapshots were aggregated | Event IDs bind command and role; settlement IDs bind trade, entry and exit; one settlement per trade; replay independently derives the expected set | Identical winner/loss retries, restart, concurrent retries, copied events with new row IDs and duplicate settlement rows |
| Rehashed fabricated snapshots | HIGH | Checksums were treated as economic validation | Checkpoints are compared byte-for-byte with replayed input-derived state; event output and settlement payloads must equal freshly derived values | Rehashed balances, equity, reservation, loss, status, quantity, prices, costs, timestamps and counts are rejected |
| Initial-capital 1% risk | HIGH | Entry checked stop risk against original experiment capital | Entry uses current reconstructed realized equity for RiskEngine and cost-inclusive stop risk; available cash separately subtracts reservations | Original audit reproduction now rejects; capital/loss/profit/restart and boundary matrices |

## 3. Authoritative ledger design

V2 persists canonical causal inputs in COMMAND events. Proposal acceptance and each completed observation are explicit commands. The replay engine reuses the V1 causal candle validation, calendar, conservative exits, delayed-acceptance rules and immutable in-memory lifecycle. It substitutes current-equity entry risk only.

A command has a deterministic identity over its operation and canonical arguments. An identical observation retry excludes its later retry clock from identity, retaining the originally accepted clock. Every event binds sequence, previous hash, deterministic event ID, event kind, trade ID and canonical payload in its hash. A closing observation generates COMMAND, EXIT and SETTLEMENT events in that order, committed together. Proposal/entry/horizon progress remains represented in the replayed lifecycle and checkpoint history.

Every reopen and public economic read reconstructs the entire sequence from the authoritative command inputs. Expected event roles, row identities, hashes, checkpoints and the settlement table must exactly match. Extra, deleted or reordered records fail validation. Self-consistent economic snapshots cannot override the replay.

anchor.json binds the committed sequence and head hash to the configuration. This independent extent detects tail deletion even if remaining per-event hashes are unchanged. It is a local completion witness, with the filesystem trust limits described in section 12.

## 4. Exactly-once settlement

The entry identity binds proposal hash, accepted entry observation hash, acceptance time, price and quantity. The exit identity binds that entry, final causal observation hash, exit time and exit price. The deterministic settlement ID binds policy, trade, entry identity and exit identity. Settlement includes gross P&L, complete retained cost components, net P&L and settlement clock.

The SQL settlement table has unique settlement and trade identities. More importantly, replay computes exactly one expected settlement per economic trade and compares every row. A copied closure with a fresh database row identity or an appended duplicate cannot generate a second balance change. There is no caller-facing arbitrary apply-P&L method.

Identical command retries return their previously derived result with zero new events or settlements. A contradictory observed slot follows the inherited quarantine behavior. Account/performance access fails on quarantine.

## 5. Semantic snapshot verification

Checkpoints are derived caches. Replay recomputes the returned trade, complete lifecycle history, balance, eligible equity, available cash, reservation, open-position count, accepted quota, realized daily loss, settled identities and performance totals. The checkpoint's hash is checked against that exact expected body. A rehash of fabricated cache data cannot make it valid.

The chronological input path enforces aware clocks, completed expected candle slots, observation availability, retained session eligibility, acceptance ordering and consecutive monitoring. Exit clocks are derived by the existing conservative mechanics. Settlement is sequenced after exit and records the observation acceptance clock. Expected settlement identities, quantity, exit economics and costs are reconstructed from the proposal and observations, rather than taken from the stored output.

The authoritative in-memory lifecycle is PROPOSED → ENTRY_PENDING → OPEN → one CLOSED_* result, with inherited ENTRY_REJECTED, OBSERVATION_MISSING and INVALID_OR_CONFLICTING alternatives. A durable closed lifecycle cannot reopen by deleting only its terminal event.

## 6. Current eligible equity definition

Let I be configured initial experiment capital, and S be the verified unique settled trades.

- Realized P&L = fsum(net_pnl for each trade in S).
- Reconstructed balance B = I + realized P&L.
- Current eligible equity E = max(0, B).
- Active reservation R = sum(entry cash requirements for verified entries that have no valid settlement).
- Available/unreserved cash A = max(0, B − R).

The reservation includes the existing buy-side affordability estimate. It is not a second realized cost debit. Pending proposals reserve no cash. Entries reserve cash only on valid OPEN. Unresolved entered exposure keeps its reservation and open-position status. Reserved capital is subtracted once for affordability and is not double-subtracted from total known equity. Unobserved mark-to-market P&L is never invented.

V2 explicitly adopts the user's preferred reconstructed-equity rule. Realized profits increase E and available cash; realized losses reduce both. This supersedes the V1 initial-allocation cap only in V2. A one-position risk gate separately prevents another trade while exposure remains open or unresolved. All these values are reconstructed identically after restart.

## 7. 1% and 2% risk behavior

Maximum planned per-trade loss is 1% × E at actual causal acceptance. The existing cost-inclusive calculation uses (entry − stop) × approved quantity plus modeled round-trip costs at the stop. Whole-share sizing must fit available cash, the current-equity risk limit and the original price/stop/target bounds. A stop gap can exceed planned risk.

Examples are 1,000 equity → 10 planned risk; 900 equity after realized losses → 9; and 1,005 equity after realized gains → 10.05. The audited case with balance 994.777230478 and stop risk 9.99 now rejects against an allowance of approximately 9.94777230478. The boundary test uses the existing strict comparison: equal is allowed, above is rejected, with no added tolerance that could widen risk.

The daily-loss definition retains V1's 2% of initial experiment capital. It sums absolute negative realized NET outcomes for the exit date, without offset from winners. It is rebuilt from replay, resets by date, and blocks at or above the limit. Open risk and reservations do not become realized daily loss. They are governed separately by the position and affordability checks. Changing the trade-risk base does not silently alter this daily reference.

## 8. Balance reconstruction example

A deterministic one-share synthetic trade starts with 1,000, enters at 600 and exits at a fixed target chosen so the existing cost model produces net P&L of 5 within binary floating-point resolution. The replayed balance and stored checkpoint both equal 1,005 exactly. The result is based on one causal entry, one exit and one deterministic settlement.

The fixture's exact binary-floating-point outputs are as follows.

| Field | Value |
|---|---:|
| Entry reference | 600 |
| Quantity | 1 |
| Exit reference | 607.2429196598708 |
| Gross P&L | 7.242919659870836 |
| Modeled costs | 2.242919659870809 |
| Net P&L | 5.000000000000027 |
| Reconstructed balance | 1005.0 |
| Eligible equity | 1005.0 |
| Available cash after closure | 1005.0 |
| Unique settlements | 1 |

The tiny floating-point residual in net P&L is retained, rather than rounding stored economics to fit an expected result. Adding it to the initial balance yields exactly 1,005 under the existing float-account convention.

The test independently reopens the ledger and repeats the closing observation. Replayed equity, retry equity and restart equity all remain 1,005, and the settlement table still has one record. A copied closure cannot manufacture 1,010. Invalid copies fail closed rather than exposing a different balance.

## 9. Crash/restart behavior

A cross-process directory lock serializes V2 operations. The publication protocol is as follows.

1. Replay and verify the currently anchored state.
2. Derive the next complete event batch, checkpoint and settlement effect.
3. Durably publish pending.json with the base and final head.
4. Commit the SQLite event/checkpoint/settlement batch in one transaction.
5. Atomically publish the final anchor and remove the pending intent.
6. Return the derived result.

On restart, all SQLite content is replayed before pending publication is resolved. If an intent exists but SQLite remains at its verified base, the unacknowledged operation is abandoned and may be retried. If the complete replay-verified final transaction exists with the old anchor, recovery advances the anchor. A matching final anchor makes a leftover intent removable. Any other mismatch fails closed. Without a pending intent, the verified database head must equal the committed anchor.

Tests interrupt before/after intent publication, after command/exit/settlement insertion, after checkpoint insertion, after the settlement row, before/after final anchor publication and after a committed result loses its acknowledgement. Separate tests cover proposal and entry/reservation interruption. An os._exit child verifies SQLite recovery with exit and settlement staged but uncommitted.

Reservation and release are derived effects of the same atomic entry/closure batches. There is no durable window where P&L was separately applied without its settlement or reservation was independently released. Initialization or storage corruption that cannot satisfy the publication protocol fails closed.

## 10. Test results

| Suite | Passed | Failed | Skipped | Deselected |
|---|---:|---:|---:|---:|
| Final Causal V2 focused | 104 | 0 | 0 | 0 |
| Existing Causal V1 | 46 | 0 | 0 | 0 |
| Related persistence/accounting/cost/calendar/safety | 187 | 0 | 0 | 0 |
| Complete default-discovery offline suite | 1,520 | 0 | 0 | 0 |

Errors were zero in all final JUnit files. The final full run lasted 1,005.57 seconds. Subset rows overlap the full total.

The first implementation iteration had 16 equity-matrix harness failures because the helper observed decision D after proposing a decision named next. That helper was corrected. The final V2 suite was rerun on the final source and all 104 cases passed. Initial and final logs are retained separately. No failing case was skipped or deselected.

Testing progressed from the new defect-focused cases, to existing V1, to related safety checks, and finally the entire default-discovery offline suite. The related selection covered test_core, cost-model execution integrity/audit, candle integrity, NSE session audit, pending entry resolution, prospective lifecycle fixes, execution integrity, provenance audit and complete signal-seal integrity. The full run also covered all monitor, observation, request-accounting, seal-recovery, AI-led and AI_SIGNAL_ONLY regressions, plus Stage 11's five offline unit tests. It executed tests only, with synthetic/mocked data.

The external run_offline.py launcher disabled dotenv before imports, cleared credential-like process environment variables and denied outbound socket connection/send paths. Existing test fixtures isolated persistent logs and monitor state. Each run used its own external basetemp. The process-death fixture explicitly disabled credentials and sockets in its child.

Key commands from the project root, through the existing virtual environment, were as follows.

- run_offline.py tests/test_causal_forward_paper_v2.py -q -p no:cacheprovider --basetemp <audit>/focused-final --junitxml <audit>/focused-final.xml
- run_offline.py tests/test_causal_forward_paper.py -q -p no:cacheprovider --basetemp <audit>/v1 --junitxml <audit>/v1.xml
- run_offline.py <the ten related test modules listed above> -q -p no:cacheprovider --basetemp <audit>/related --junitxml <audit>/related.xml
- run_offline.py -q -p no:cacheprovider --basetemp <audit>/full --junitxml <audit>/full.xml

compileall passed for V2, its tests and reused V1/risk/cost/calendar helpers. Scoped git diff --no-index --check passed for each new file. No existing test file was edited.

Audit artifacts, test JUnit XML/logs, the [V2 diff](C:/Users/Priyanka/.codex/visualizations/2026/11/05/01a1acdb-980d-7dd2-b495-2691796e9d33/causal-v2-remediation/v2.diff), [test totals](C:/Users/Priyanka/.codex/visualizations/2026/11/05/01a1acdb-980d-7dd2-b495-2691796e9d33/causal-v2-remediation/test-totals.json), and [balance proof](C:/Users/Priyanka/.codex/visualizations/2026/11/05/01a1acdb-980d-7dd2-b495-2691796e9d33/causal-v2-remediation/balance-proof.json) are retained under the external causal-v2-remediation directory.

## 11. Integrity verification

| Check | Result |
|---|---|
| External network requests | NO |
| Broker execution enabled | NO |
| Prospective experiment run | NO |
| Protected evidence changed | NO |
| Real paper state changed | NO |
| Real logs changed | NO |

Starting HEAD was a0789cdb5db50deece446005c40064ca5de989a4 and matched the final HEAD. All 627 starting inventory paths matched their exact SHA256 values. The canonical manifest hash over those same paths before and after is 14f4918bec9fe8bf2a9de92280c46c2492dea25400b42954520752b9baadce58. This includes the real decision log, paper database/WAL/SHM, previous V1 and audit documentation in the project, historical artifacts, Sessions 02–05, _02 evidence, provider-smoke files, budgets, attestations and retained calendars.

The compared inventory grew from 627 to 630 solely through the three additions in section 1. Git status matched the starting status plus exactly those three untracked files. All pre-existing working-tree changes were preserved. No commits, restoration or old-ledger repair occurred. Bytecode caches and external synthetic fixtures were outside the protected inventory. The private .env was outside the manifest and was never edited.

The [integrity result](C:/Users/Priyanka/.codex/visualizations/2026/11/05/01a1acdb-980d-7dd2-b495-2691796e9d33/causal-v2-remediation/integrity.json), before/after manifests and HEAD/status records preserve the exact comparison.

## 12. Remaining risks

The committed-head witness is local. It detects ledger mutation relative to its retained anchor, but an administrator replacing the database, anchor and every input with an older mutually consistent copy cannot be authenticated by an offline local implementation. Preserving the anchor in independently trusted storage is a prerequisite to stronger adversarial guarantees. This remediation does not add an external service or key-management mechanism.

Filesystem fsync/atomic replace and SQLite durability depend on the underlying operating system and storage honoring those operations. Tests cover process death, transaction rollback and acknowledgement loss, not destructive power-loss hardware faults. Full replay has linear cost per validation and cumulative cost for a long ledger. This isolated research policy prioritizes complete verification over caching authoritative state.

Reviewed capture integration and independently authenticated market/decision clocks remain absent. Reference candle closes and modeled costs are paper conventions. Unresolved exposure has no fabricated liquidation value. Binary floating-point values follow existing cost/account behavior; no rate, slippage or rounding convention was changed. A source/configuration change intentionally prevents silent resumption under a different policy.

## 13. Final verdict

**READY FOR INDEPENDENT READ-ONLY CAUSAL PAPER AUDIT ONLY**

**No experiment authorized. Broker execution remains disabled.**
