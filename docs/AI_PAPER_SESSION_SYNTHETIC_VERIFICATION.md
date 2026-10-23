# AI paper-session synthetic verification

Date: 2026-10-23
Scope: deterministic synthetic fixtures only; no cached historical data, provider calls, market-data downloads, or orders.

## Expected versus actual

| Scenario | Expected | Actual |
|---|---|---|
| BUY → HOLD → EXIT | One persistent position; next-eligible-bar fills; matching decision and position IDs; costs charged once per side | Pass: one entry fill, one matching AI exit, linked HOLD event, and exactly one realized outcome |
| Automatic stop | Stop chosen when a candle touches both stop and target | Pass: conservative stop-first exit, once |
| Automatic target | Target event closes at the bounded target; gross/net and cost legs reconcile | Pass: target exit, one outcome, net equals gross less total costs, and entry plus exit costs reconcile |
| Maximum holding period | Close on the configured completed-bar close | Pass: horizon exit on bar two at its close; report timestamp agrees with the completion event |
| Session close | Do not carry across session; close the final regular-session candle at session end | Pass: final 15:25 START candle is recorded as completed at 15:30; report and forced-close event timestamps agree |
| Stale, missing, malformed, or ambiguous inputs | Reject or block; never synthesize a candle or use a future feature | Pass: stale inputs, missing intervals, invalid OHLC, unverified timing evidence, and future feature timestamps fail closed; stop/target ambiguity uses stop-first |
| Duplicate or unmatched actions | Reject duplicate entries/exits and unmatched exits; do not double-book P&L | Pass: one fill/outcome maximum; duplicate BUY, duplicate EXIT, and exit without a filled position are rejected |
| Risk controls | Kill switch, invalid stops, trade/daily-loss limits, and open-position checks prevent entry | Pass: risk rejections produce no fill; realized loss blocks a later entry at the configured daily-loss threshold |
| Replay | Same immutable inputs produce the same run identity and event hash | Pass: repeated replay hashes match; existing output directories are not overwritten |

The evaluator exposes realized P&L only for closed positions. It does not publish an unrealized mark-to-market field; an unresolved open position at end of input blocks completion. No unrealized-P&L result is claimed.

## Defect found and repaired

The new maximum-holding test found that a trade report used the START label of the exit candle, while its audit events used the candle completion time. This could make the report imply that a completed-bar exit was known five minutes early. The report now uses the same completion timestamp as the event ledger. The session-close assertion was aligned to the verified 15:30 session end. No risk limits, strategy behavior, costs, or V1 code changed.

## Verification commands

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_ai_v2_evaluation.py -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m py_compile app/v2/evaluation.py tests/test_ai_v2_evaluation.py
git diff --check
```

Focused result: **27 passed**. Full offline suite: **298 passed**. No tests were skipped. Compilation and whitespace/diff checks are reported in the delivery summary after execution.

These tests establish deterministic behavior on the synthetic cases listed above. They do not validate real feed provenance, timestamp conventions of the cached datasets, corporate-action treatment, historical universe completeness, strategy performance, profitability, or readiness for live trading. No historical performance evaluation was run.
