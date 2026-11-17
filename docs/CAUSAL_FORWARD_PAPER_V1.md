# CAUSAL_FORWARD_PAPER_V1 — offline implementation and audit

## Scope and interface

The new policy ID is `causal-forward-candle-v1`. The Python entry point is `app.research.causal_forward_paper.CausalForwardPaper`. It consumes one upstream-selected long BUY and explicit offline candle observations. The caller supplies the experiment capital, an empty dedicated directory, the decision ID and readiness timestamp, approved quantity, instrument key, and original proposal. No global capital cap applies.

`propose` binds the proposal and selection. `observe` consumes one explicitly identified expected slot or an explicit missing observation. `history`, `evidence` and `account` expose the journal, original hashed inputs and reconstructed account. The policy has no transport, broker, provider, scheduler or request-budget interface. Its construction is explicit. The existing AI, paper, signal-only and historic runners retain their separate entry points. This task used synthetic fixtures only.

## A. Causal entry

Use aware timestamps normalized to Asia/Kolkata. Candle labels denote five-minute interval starts. For decision readiness T, the entry candle starts at the next five-minute boundary strictly after T, including when T falls exactly on a boundary. A decision at 12:02:37 uses the entire 12:05–12:10 candle. A decision exactly at 12:05 uses 12:10–12:15. Earlier labels and skipped slots cannot establish an entry.

The submitted slot, candle label, stock and instrument key must match. The entire candle must validate, have finite numeric OHLCV and positive volume, and lie within the retained session. Let E be its exclusive end. The offline clocks must satisfy E ≤ observed_at ≤ as_of < E + five minutes. The one-slot availability limit prevents accepting a close once the following full candle has already elapsed. The simulated entry reference is its close. Acceptance is recorded at as_of after validation. Equal clock readings at completion and acceptance are allowed in that logical order. This reference is a paper convention; an executable quote at that price has not been established.

At acceptance the ledger recomputes the verified session cutoff, daily limits, position count, available cash, whole-share maximum, the original stop/target bounds, buy-side affordability, cost-inclusive stop risk, and target net gain at the actual reference close. The AI-led cost check supplies the same target-profitability test as the existing account. A nonpositive expected net gain terminally rejects the trade. Approved quantity remains fixed. An oversized or unaffordable quantity is rejected. Estimates on a rejected proposal are diagnostic only. Only OPEN consumes an entry quota and reserves cash.

## B. Monitoring

Entry-candle high and low are used only to validate its OHLC structure. Monitoring starts with the next consecutive completed slot. Every later slot must arrive in order and meet the same observation clock rule. Only post-acceptance evidence can determine an exit.

Receipt or validation can occur after the following candle has begun. In that first overlapping bar, an extreme alone cannot establish event timing relative to acceptance. If its post-acceptance close proves a stop touch and the entire bar missed the target, the conservative exit uses that close. If the close proves a target touch and the bar missed the stop, the exit pays the target. Other threshold touches produce an unresolved observation, preserve the reservation and generate no P&L. If the bar missed both thresholds, its close can count toward the horizon. Only the close contributes to confirmed excursions in this partial bar. The complete range remains an outer bound.

## C. Same-bar ambiguity and excursions

A full monitoring bar touching both thresholds closes as CLOSED_AMBIGUOUS_STOP_FIRST with same_bar_ambiguous set to true. The immutable record retains the original complete exit bar, both thresholds, quantity, times, costs and observation hashes. A reviewer can calculate a target-first upper scenario separately. The primary ledger contains one stop-first outcome.

mfe_price and mae_price are confirmed favorable (nonnegative) and adverse (nonpositive) price differences per share. mfe_upper_price and mae_lower_price retain possible whole-bar extremes where OHLC cannot order them relative to an intrabar exit or partial first bar. For an intrabar threshold exit the open and modeled exit provide the confirmed endpoints. For a known opening gap the price path ends at the opening execution. Percent excursions can be obtained by dividing by the entry reference.

## D. Gaps and exit timestamps

A stop touch exits at min(candle open, stop). A gap below the stop can exceed planned risk. A target-only touch pays the stated target even if the candle opens above it. When both thresholds appear the stop-first rule takes priority, including a favorable opening followed by a stop touch.

An opening execution records the opening timestamp. Horizon and session closes record the candle end. For an intrabar threshold exit the end is the upper time bound, labelled intrabar_time_unknown_end_bound. The record also stores both interval boundaries and the offline acceptance clock for the exit evidence.

## E. Horizon and session

The original AI holding limit of 1–12 monitored bars is enforced. With no threshold event, the policy exits at the completed close when the holding limit is reached or the current verified interval ends. Session end wins a tie in the exit reason. The retained V3 OfflineSessionCalendar supplies session bounds and entry_allowed at decision and acceptance. Entries cannot bridge breaks. Historical special sessions without affirmative execution support, Muhurat, unresolved and absent sessions fail closed. Tests cover retained sessions ending at 15:15 and 15:30.

## F. Accounting and performance record

Gross P&L is (exit reference − causal entry close) × approved quantity. The existing date-specific Indian cash-equity model is called on those unadjusted reference prices for settlement. Its itemized total includes both legs of slippage once. Net P&L is gross less that total. Entry-side affordability and target/stop projections are estimates for eligibility. A reservation limits available cash and does not debit a second fee.

The closed snapshot contains the decision ID, full scored proposal, instrument, decision readiness, quantity, accepted price and clocks, expected gross/net target P&L, monitoring count, stop/target, exit reason, prices and time semantics, gross P&L, modeled costs, net P&L, excursion bounds, ambiguity, read-only four-index down count, provenance and hashes. Accepted trade records are distinguishable from missing, disputed and rejected observations. Account balance includes realized net outcomes. As in the AI-led offline account, deployable cash is capped at initial experiment capital or current balance, whichever is lower, less outstanding reservations.

## G. Persistence and failures

One SQLite database stores immutable settings, content-addressed input bodies and sequenced lifecycle snapshots. BEGIN IMMEDIATE serializes competing writers. Every input body and its associated transitions commit in one transaction. Ordinary retries return the original slot result, including after a newer slot has committed. Each decision ID and slot pair is unique. A second ID for the same instrument and readiness instant is refused. The account clock disallows new backdated transitions. Source versions, calendar identity, risk, costs, provenance and capital bind restarts.

The normal path is PROPOSED → ENTRY_PENDING → OPEN → a single CLOSED_TARGET, CLOSED_STOP, CLOSED_AMBIGUOUS_STOP_FIRST, CLOSED_HORIZON or CLOSED_SESSION. OPEN can advance its bar count. A proposal or entry can terminally become ENTRY_REJECTED. Missing, stale, wrong-instrument or malformed canonical observations produce OBSERVATION_MISSING. A missing pending entry consumes no capital or quota. Missing evidence after OPEN leaves exposure unresolved and blocks a new position. A caller must publish an explicit missing expected slot when it cannot supply it. No price is interpolated.

A revised input for an already bound selection or slot preserves both hashes and appends INVALID_OR_CONFLICTING. The entire account is then quarantined for audit. Economic processing and computed account summaries stop. Prior snapshots remain inspectable. Audit code must inspect conflicts before aggregating a historical CLOSED record. This version has no reconciliation or automatic retry with substitute evidence. SQL triggers reject updates, deletes and replacement of existing rows. Local hashes and SQLite transactions provide integrity and crash recovery within a trusted local filesystem.

## H. Provenance

The default is UNVERIFIED_CALLER_SUPPLIED. SYNTHETIC_TEST_DATA is available explicitly for fixture ledgers. The kind is fixed when the ledger is created. A candle cannot override its provenance. Invalid or conflicting evidence is identified separately. REVIEWED_UPSTOX_CAPTURE and OFFLINE_REPLAY_FROM_REVIEWED_CAPTURE are reserved and rejected at this offline boundary. A future adapter would need independently authenticated capture receipts and verified recorded decision clocks. Caller-supplied timestamps, hashes and scores do not establish source authenticity or prove that an AI generated the proposal.

## I. Change scope

| New file | Role |
|---|---|
| `app/research/causal_forward_paper.py` | Isolated lifecycle, account, immutable evidence and journal |
| `tests/test_causal_forward_paper.py` | Synthetic lifecycle, integrity, crash, costs, clock and isolation regressions |
| `docs/CAUSAL_FORWARD_PAPER_V1.md` | Policy specification and audit results |

## J. Offline verification

| Check | Passed | Failed | Errors | Skipped |
|---|---:|---:|---:|---:|
| Final causal-policy tests | 46 | 0 | 0 | 0 |
| All pre-existing tests in the broader run | 1,365 | 0 | 0 | 0 |
| Broader run including 36 earlier causal tests | 1,401 | 0 | 0 | 0 |

The broader process collected its initial causal test version before the final isolated policy edits. All existing implementation files remained byte-identical during the run. The final causal tests were rerun against the final policy, including the receipt-overlap handling, durable conflict quarantine, immutable SQL guards and abrupt process-death recovery. The counts above deliberately distinguish those two versions. The final coverage contains 1,411 distinct tests across the existing suite and final causal test module.

The existing tests cover AI-led offline, guarded, reviewed and replay accounts, the target-profitability gate, costs and RiskEngine, pending/resumed account lifecycles, calendars, execution observations, provenance, AI_SIGNAL_ONLY, request accounting and evidence seals. Network fixtures deny external sockets. New policy tests additionally deny provider, market-data and budget dispatch. The child-process crash fixture disables dotenv, clears provider credentials and denies socket connections.

Commands were run using the project virtual environment from the repository root.

```powershell
.\.venv\Scripts\python.exe -m pytest tests -q --basetemp .pytest_cache/causal-forward-regression --junitxml "$auditPath/regression.xml"
.\.venv\Scripts\python.exe -m pytest tests/test_causal_forward_paper.py -q --basetemp .pytest_cache/causal-audit-release --junitxml "$auditPath/focused.xml"
.\.venv\Scripts\python.exe -m compileall -q app/research/causal_forward_paper.py app/research/ai_led_offline.py app/research/deepseek_prospective.py app/research/offline_session_calendar.py app/research/prospective_paper_fills.py app/cost_model.py app/risk_engine.py app/candle_integrity.py tests/test_causal_forward_paper.py
```

Compilation passed. Scoped whitespace verification used `git -c core.autocrlf=false diff --no-index --check -- /dev/null <new-file>` for each new file and passed. The existing working-tree edits predate this task. The task diff consists entirely of the three added files.

| File | Added lines | Deleted lines |
|---|---:|---:|
| app/research/causal_forward_paper.py | 400 | 0 |
| tests/test_causal_forward_paper.py | 391 | 0 |
| docs/CAUSAL_FORWARD_PAPER_V1.md | 116 | 0 |

Audit output directory used for $auditPath is [causal-policy-audit](C:/Users/Priyanka/.codex/visualizations/2026/11/05/01a1acdb-980d-7dd2-b495-2691796e9d33/causal-policy-audit). The [task diff](C:/Users/Priyanka/.codex/visualizations/2026/11/05/01a1acdb-980d-7dd2-b495-2691796e9d33/causal-policy-audit/causal-forward-v1.diff), [focused test results](C:/Users/Priyanka/.codex/visualizations/2026/11/05/01a1acdb-980d-7dd2-b495-2691796e9d33/causal-policy-audit/focused.xml), [regression results](C:/Users/Priyanka/.codex/visualizations/2026/11/05/01a1acdb-980d-7dd2-b495-2691796e9d33/causal-policy-audit/regression.xml) and [integrity manifest result](C:/Users/Priyanka/.codex/visualizations/2026/11/05/01a1acdb-980d-7dd2-b495-2691796e9d33/causal-policy-audit/integrity.json) are retained there.

## K. Protected-file integrity

Before the first project edit, a sorted relative-path-to-SHA-256 manifest captured 624 existing files. The final check recomputed each digest and also checked inventory additions. It covered app, scripts, tests, docs, data, backtests, experiments, reports, diagnostics, logs, schemas and project-root files other than the private environment file. Git internals, virtual environments, scratch directories and compiled Python caches were outside the comparison. Historical ledgers were read as bytes, never rerun.

| Protected selection | Existing files checked | Changed or missing |
|---|---:|---:|
| All backtests, including historical experiments | 263 | 0 |
| Prospective Sessions 02 / 03 / 04 / 05 | 7 / 4 / 3 / 8 | 0 |
| AI signal chain trial 20261104_02 | 21 | 0 |
| Provider smoke 01 account / diagnostic files | 8 / 5 | 0 |
| Budget data/config files within the manifest | 22 | 0 |
| Attestation files | 10 | 0 |
| Retained calendar evidence / audit files | 35 / 3 | 0 |
| Frozen experiment specification | 1 | 0 |

Selections overlap. The before and after SHA-256 of the canonical manifest over the **same 624 paths** is `d6347fcc371808c0dad7939c705fac4dc89a8da52c2659899191ff2b5486e2fe`. Only the three files in section I were added to the compared inventory. The external audit bundle contains the per-file manifests, integrity check, unified diff and JUnit results.

## L. Limitations and authority

The lifecycle trusts an upstream selection, the local clock inputs, the retained manifest and the local installation. Its hashes provide no external attestation or protection against an administrator rewriting the whole ledger. Independent experiments need separate directories. Closed-candle references do not measure venue fills, queue priority, depth, latency slippage or intrabar event order. Missing post-entry evidence deliberately prevents definitive account performance. The implementation has no reviewed capture adapter or genuine forward observations. A read-only code and evidence audit is the sole scope of this verdict. Market/provider requests, a forward experiment and broker execution remain unauthorized.

**READY_FOR_READ_ONLY_CAUSAL_PAPER_AUDIT**
