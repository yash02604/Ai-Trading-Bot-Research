# Files Inspected

Reviewed the existing AI-led offline, guarded, reviewed and recovery modules; offline session calendar; execution-observation parser; RiskEngine; cost model and cost-aware eligibility gate; configuration; relevant tests; PENDING lifecycle correction report; and the pre-existing Git diff. The task-start inventory covers 495 existing project/evidence files. All prior working-tree changes were retained.

Only three files are added by this task:

- `app/research/prospective_paper_fills.py`
- `tests/test_prospective_paper_fills.py`
- `docs/PROSPECTIVE_PAPER_FILL_VALIDATOR_REPORT.md`

No existing source, tests, runner, configuration, manifest, report, dataset, session, budget or attestation is changed.

# Policy Implemented

Added pure `validate_paper_fill(...)` for `prospective-observed-ask-v1`. Explicit keyword inputs are `policy`, `timestamps`, `proposal`, `quote`, `depth_evidence`, `account`, and an already loaded `OfflineSessionCalendar`. It returns deterministic structured eligibility or rejection without committing a fill. Even an eligible result has `validation_only: true`, `taken: false`, `simulated: true`, `settlement_supported: false`, and `trade_placement_enabled: false`.

The only supported depth-evidence kind is explicitly SYNTHETIC_FIXTURE, using the `synthetic-depth-evidence-v1` schema and DEPTH_EVENT_TIME semantics. A SHA256 binds that fixture to the exact normalized quote. This binding is integrity evidence for a synthetic fixture, never proof of provider authorship or authenticated live depth. Real-provider assertions are rejected; there is no caller-controlled `verified: true` bypass. A future reviewed real-depth evidence mechanism is outside this task.

The module defines `historical-candle-open-simulation` as the separate historical policy identifier but does not retrofit that field into any existing manifest or artifact. Passing that identifier to this new validator returns UNSUPPORTED_POLICY, rather than reinterpreting historical fills.

# Causal Timing Validation

All eleven timestamps are mandatory and timezone-aware: market observation, original opportunity, provider send, provider receipt, decision readiness, earliest entry, entry expiry, quote request, depth source, quote receipt and proposed acceptance. Inputs are parsed as explicit ISO datetime values; relative strings such as `now` and `today` are rejected without reading a clock. Comparisons and returned timestamps use Asia/Kolkata.

Required ordering:

```text
market observation <= original opportunity <= provider request
< provider response <= decision readiness < quote request
<= depth source <= quote receipt <= proposed acceptance
```

The earliest entry must equal floor(original opportunity to five minutes) plus five minutes. Decision readiness must precede that boundary, and the entry quote cannot be requested before it. Expiry must equal min(earliest entry plus five minutes, verified session end minus five minutes). Proposed acceptance must precede expiry. Callers cannot extend or silently shorten/replace the schedule with an immediate fill. Empty final-session windows are rejected.

The loaded calendar validates the original opportunity and entry interval. Weekends, unsupported special sessions and absent dates fail closed. Different verified session closes are respected, including acceptance after 15:15 on the later 15:30-close fixture. A stale market-observation receipt older than the existing 900-second bound at opportunity time is rejected. This receipt check does not replace existing completed-candle freshness and quality validation; the opportunity must already have passed those checks before use.

# Indian Market Quote Rules

The normalized quote must identify the exact configured NSE equity instrument. Best bid and ask must be finite and positive, with no crossed market. Displayed bid/ask depth must be positive strict integers, excluding booleans and fractional/coerced quantities. Best-ask displayed quantity must cover the unchanged proposed quantity; the validator never walks depth or creates partial fills.

Spread is computed from bid/ask and midpoint in basis points. The caller must explicitly supply a finite nonnegative `max_spread_bps`; there is no production default. The ten-basis-point value in tests is a synthetic test parameter, not a new strategy threshold or a recommendation. `max_quote_age_seconds` must be explicitly positive and no greater than the existing five-second bound. Both receipt-time and acceptance-time ages are checked.

Quote source time must agree exactly with the timestamp supplied in the causal evidence. Missing, future, inconsistent, response-generated or ambiguous source-time evidence is rejected. The existing Upstox parser's `source_quote_timestamp=None` is explicitly tested as a rejection even when its response timestamp passes the parser's existing checks.

# Risk and Capital Validation

The account input contains capital, balance, available cash, daily loss, accepted-trade count, open-position count and `as_of`. Numeric values must be finite; counters are strict nonnegative integers. Available cash cannot exceed min(initial capital, balance). Its snapshot time must equal the proposed acceptance time. The caller remains responsible for obtaining this snapshot from verified locked account state; the pure validator does not authenticate a ledger or broker balance.

Reuses unchanged `RiskConfig`, `RiskState`, `check_trade_allowed`, and `calculate_position_size`, including one-percent trade risk, two-percent daily loss, one OPEN position and three accepted trades per day. Quantity must remain the previously selected positive whole-share quantity and cannot exceed the current RiskEngine maximum using available funding. No quantity, stop, target or holding period is adjusted to rescue eligibility. Existing AI-led price/stop/target bounds are reapplied at the observed ask.

Capital is fully configurable. Synthetic acceptance is tested at 1000, 2500, 5000, 10000 and 100000, with the maximum planned risk scaling as one percent. No application-wide 1000 limit is introduced. The existing funding policy is preserved: available funding is bounded by initial capital and current account balance.

# Cost Accounting

Reuses `_cost_aware_position_check` and its existing buy-side/round-trip fee estimators. No fee schedule or assumption was changed. Brokerage, exchange, IPFT, SEBI, STT, GST, stamp duty and five-basis-point-per-side slippage retain their original treatment.

```text
BUY reference price = observed best ask
Entry cash required = quantity * ask + modeled buy-side costs
Planned stop risk = quantity * (ask - original stop) + modeled round-trip costs
```

The ask is not inflated for slippage and then charged slippage again. Output costs are estimates only; validation does not debit cash, charge fees, create P&L or reserve capital. Tests compare estimates directly with the unchanged model and reject missing/non-finite cost components. Stop gaps and execution uncertainty may exceed estimated risk.

# Rejection Reasons

Fixed codes cover unsupported/invalid policy, malformed inputs/timestamps, causal-order failure, stale market observation, unsupported calendar/session, invalid/early/expired entry window, invalid proposal, wrong instrument, missing/ambiguous depth time, unsupported or mismatched evidence, invalid quote price/depth, stale quote, crossed market, excessive spread, insufficient ask depth, invalid price/stop/target, invalid account, daily loss/trade or open-position limits, insufficient cash, excessive RiskEngine quantity, invalid costs and cost-inclusive risk excess.

Unexpected failures return only INVALID_INPUT with fixed flags, never exception text, arbitrary provider bodies or credentials. Rejected results contain no fill, P&L or charged transaction costs. Existing PENDING expiry and terminal-event conventions are untouched; this module does not write those events or provide restart orchestration.

# Tests Added

Added 77 offline cases covering valid synthetic evidence, exact causal ordering, decision/receipt timing, entry scheduling and expiry, relative/naive timestamp rejection, source-time consistency and freshness at two times, current parser limitations, explicit spread policy, crossed/non-finite quotes, strict depth, insufficient depth, synthetic-only evidence binding, account/cash/counter freshness, quantity and stop preservation, cost-risk and affordability rejection, single-counted slippage, multiple capital amounts, calendar boundaries, deterministic repeated validation and unchanged inputs.

An explicit purity test blocks file access, current-clock access, sockets, Upstox requests, DeepSeek completion and budget reservation while validating a successful synthetic fixture. Historical replay and existing lifecycle behavior are covered by the unchanged regression suite.

# Verification Results

Focused command:

```powershell
.venv\Scripts\python.exe -B -m pytest tests/test_prospective_paper_fills.py -q -p no:cacheprovider --basetemp C:\Users\Priyanka\.codex\visualizations\2026\10\30\01a18b43-af58-74b1-ad68-35a667601200\.pytest-fill-validator-focus-verified
```

Result: **77 passed in 7.32 seconds.** Earlier focused runs had 65 passes and one fixture failure, then 75 passes before the last two cases were added. The fixture failure hit the preserved stop-distance rule before the intended affordability check; the test fixture was corrected without relaxing production validation. These counts overlap and are not additive.

Full relevant regression command, with no exclusions:

```powershell
.venv\Scripts\python.exe -B -m pytest tests/test_prospective_paper_fills.py tests/test_pending_entry_resolution.py tests/test_upstox_deadlines.py tests/test_prospective_lifecycle_fixes.py tests/test_ai_led_reviewed.py tests/test_ai_led_guarded.py tests/test_ai_led_offline.py tests/test_ai_led_replay.py tests/test_prospective_preflight.py tests/test_readiness_evidence.py tests/test_offline_market_data_v1.py tests/test_deepseek_prospective.py tests/test_deepseek_prospective_summary.py tests/test_cost_model_execution_integrity.py tests/test_core.py tests/test_deepseek_eval_v1.py tests/test_ai_v2.py tests/test_ai_v2_evaluation.py tests/test_provenance_audit.py tests/test_execution_observations.py tests/test_execution_integrity.py tests/test_paper_session.py -q -p no:cacheprovider --basetemp C:\Users\Priyanka\.codex\visualizations\2026\10\30\01a18b43-af58-74b1-ad68-35a667601200\.pytest-fill-validator-regression-final
```

Result: **676 passed in 296.48 seconds, no exclusions or remaining test failures**: all 599 prior relevant cases plus 77 new cases. This is the complete relevant offline regression selection above, not a claim that every unrelated repository test was run.

Both new Python files passed in-memory compilation with `.venv\Scripts\python.exe -B -` and `compile(path.read_bytes(), str(path), 'exec')`, without writing bytecode. All three additions passed targeted whitespace checks using `git -c core.autocrlf=false -c core.whitespace=blank-at-eol,blank-at-eof,cr-at-eol diff --no-index --check <empty-file> <new-file>`. This includes untracked additions that ordinary Git diff omits. The final addition diff was inspected; there are no existing-file edits.

SHA256 comparison confirms all **495 task-start project/evidence files unchanged**, including all **359 protected historical evidence files** from the preceding review inventory. Sessions 02-05, their budgets, source attestations, historical data and reports are preserved. No Upstox or DeepSeek request, provider token consumption, account mutation, new trading experiment, Session 06, market-data capture or broker order occurred. New test artifacts are confined to the external temporary test directory above.

# Backward Compatibility

The new module is not called by any existing runner, manifest, transport, recovery or settlement path. Existing historical candle-open replay, PENDING expiry, source identities, account commits, risk/cost limits, budgets and simulated broker-disablement remain unchanged. Existing experiments are not migrated, relabeled or modified. No old source attestation is regenerated.

# Remaining Data-Evidence Limitations

An accepted synthetic fixture demonstrates deterministic validation only, not usable real depth, predictive skill, profitability or execution readiness. Current quote response-generation time does not establish order-book freshness. No reviewed real-depth evidence verifier, authenticated transport integration or broker execution evidence is implemented. Caller-supplied account and opportunity data must eventually come from verified point-in-time state, not arbitrary claims.

# Post-Fill Settlement Requirements

Separate work is required. Record a causal, instrument-bound post-fill sequence with trustworthy source/receipt times, sequence identity and continuity/gap evidence. For long exits, use an explicit sell-side price/depth proxy and the unchanged cost convention. The interval from fill acceptance to the next full candle boundary needs post-fill observations; the containing whole candle's extrema cannot establish whether stop or target was touched after entry.

Stop/target ordering requires an adequately ordered post-fill stream. Missing/ambiguous coverage must remain unresolved or receive an explicitly reviewed conservative modeling classification; it must not become an invented observed exit. Horizon/session exits require a validated price observation at or after the applicable exit trigger, with a separately specified session-boundary policy. Retain the existing holding duration and risk thresholds rather than silently redefining them. Market-data acquisition for such evidence must remain explicitly authorized, bounded and persistently budgeted.

This validator therefore always reports `settlement_supported: false`. It never routes quote-based eligibility through historical candle-open settlement.

# Readiness Verdict

The pure offline validator is implemented for synthetic validation only. **NO-GO for real-data quote-based entries**, prospective trial enablement, broker sandbox execution or real orders. Authentic depth freshness and causal post-fill settlement remain unresolved. The module exposes no live switch that treats a synthetic fixture as provider-authenticated evidence.

# Recommended Next Step

Perform a read-only review of the new validator, schema and tests. Next specify the depth-evidence contract and post-fill observation/settlement policy before integrating any transport or account-fill commitment. Do not loosen timestamp requirements to obtain accepted real-data trades, and do not start Session 06 or a prospective trial from these synthetic results.
