# AI-led offline trading foundation

Date: 2026-10-30

## Outcome

Implemented a separate saved-data, saved-response AI-led paper account. It accepts a complete instrument batch, independently evaluates AI proposals, selects at most one eligible long position, validates its later actual entry, monitors subsequent candles, settles modeled costs and P&L, and resumes from atomic account state. It contains no market-data fetch, live provider integration, broker executor, or live-mode switch.

## Existing components reused

- `app/research/deepseek_prospective.py`: snapshot parsing and candle-integrity checks, the cost-aware affordability/risk gate, timezone validation, and the existing directory lock.
- `app/risk_engine.py`: percentage risk limits, whole-share sizing, one-position limit, three accepted trades per day, and the daily loss check.
- `app/cost_model.py`: dated NSE cash-equity fees, taxes, and modeled slippage. Defaults are recorded in the experiment configuration.
- `app/v2/provider.py`: the `complete` request shape and `ProviderResult` contract. The new saved-response provider implements that shape without invoking the SDK.

The existing AI-led research service was inspected. Its independent-decision prompt is useful precedent, but its registered-strategy contract selects one actionable opportunity. It was preserved rather than changed to support this separate multiple-proposal experiment. The frozen quantitative candidate generator and historical provenance gate are not called by the new runner.

## Files added

| File | Purpose |
| --- | --- |
| `app/research/ai_led_offline.py` | Complete-batch validation, strict proposal parsing, deterministic ranking, account state, entry validation, monitoring, settlement, and resume. |
| `scripts/run_ai_led_offline.py` | Explicit offline CLI requiring saved snapshots, saved AI JSON, configured capital, priority, simulation clock, and output directory. |
| `tests/test_ai_led_offline.py` | Offline safety and lifecycle regression tests, with socket connections prohibited in the new tests. |
| `docs/AI_LED_OFFLINE_FOUNDATION_REPORT.md` | This implementation and verification report. |

No pre-existing source, configuration, test, or research artifact was edited during this task. Existing working-tree changes were preserved.

## Offline trading flow

1. Supply every instrument in the configured ordered universe as saved prospective-schema snapshots. Each instrument must match the existing NSE symbol-to-instrument-key mapping.
2. Validate candle integrity and point-in-time metadata using the existing loader. Require the same latest candle timestamp across instruments, completed candles, a recent contiguous 35-bar same-session window, positive recent volume, and configured minimum five-bar close-times-volume turnover. Maximum metadata/candle age is 900 seconds. The initial liquidity threshold is configurable and defaults to INR 100,000 turnover over five bars.
3. Construct a normalized market request in configured instrument order. Include only validated timestamps, identities, numeric OHLCV, and input hashes. Unknown snapshot metadata is not sent to the AI or copied to account records.
4. Evaluate the saved response using the provider-shaped offline adapter. Live provider objects and subclasses are rejected. No credentials are loaded by the new CLI.
5. Reject invalid JSON, duplicate JSON keys, extra fields, duplicate or unknown instruments, non-finite numbers, and invalid order bounds. Persist validated responses and their original-content hashes; invalid unrestricted text is replaced with a fixed redacted reason.
6. Sort proposals by descending score, then by the frozen instrument-priority list. Snapshot arrival order and proposal list order do not determine tied winners. Scores express AI preference and are not calibrated probabilities.
7. Ask RiskEngine for whole-share quantity using the smaller of configured capital and usable account cash. Apply the existing cost-aware gate with initial capital, actual quantity, entry reference, stop, and cash. Select the first eligible proposal; the cost gate never increases or independently reduces the RiskEngine quantity. Record checks for all proposals.
8. Create a pending entry for the next full five-minute boundary after the simulation decision clock. On subsequent complete batches, require that exact same-day entry candle. Recheck its opening price, proposed stop/target bounds, RiskEngine maximum quantity, costs, and account cash. Reject immediately if any actual-entry check fails; rejected entries clear pending state and do not consume accepted-trade quota.
9. Accepted entries remain open. Monitor contiguous same-session candles; honor stop gaps, stops, targets, and the validated holding horizon. Stop takes precedence if one OHLC bar touches both stop and target. Targets fill at the target reference even on favorable gaps, a conservative assumption. Force an observed session-end close when a valid 15:25 candle is supplied. Missing continuation data fails closed and preserves the previous account state.
10. Settle gross P&L less the existing round-trip fee/slippage model. Record the breakdown, net P&L, account balance, and usable cash. Accepted entries and monitored candle hashes are checked on resume so revised prices cannot silently rewrite the modeled position history.
11. Commit the manifest, inputs, decisions, position, events, and cash together in `ai_led_offline_state.json`, protected by the directory lock, checksum, temporary-file fsync, and atomic replacement. Replays do not create another decision, settlement, or offline provider evaluation.

## AI decision schema

The entire response must match these fields. No quantity, arbitrary instructions, strategy code, credentials, free-text rationale, or tool requests are accepted.

```json
{
  "schema_version": "ai-led-offline-paper-v1",
  "action": "BUY",
  "proposals": [
    {
      "symbol": "RELIANCE",
      "score": 0.8,
      "entry_price": 600.0,
      "stop_loss": 594.0,
      "target_price": 612.0,
      "holding_bars": 6
    }
  ]
}
```

These are artificial test values, not market prices or a recommendation. `NO_TRADE` requires an empty proposals list. A BUY response requires at least one proposal and at most one per configured instrument. Entry reference must equal the latest supplied close. Stops must be 0.05% to 1% below entry; targets must be above entry by at most 5%; holding horizon is 1 to 12 five-minute bars. Actual entry revalidates the same price bounds.

## Risk, capital, and account settings

Capital is configurable; INR 1,000, 5,000, and 10,000 configurations are tested. The existing 1% per-trade risk and 2% daily loss percentages remain. A proposed position must satisfy both:

```text
entry price * quantity + modeled buy-side costs <= usable account cash
(entry price - stop) * quantity + modeled round-trip stop costs <= initial capital * 1%
```

RiskEngine quantity also remains bounded by the cash-based sizing configuration. Zero shares and invalid cost inputs are rejected. Position acceptance never bypasses the existing cost gate.

Actual balance equals initial capital plus settled net P&L. Usable funding is capped at the initial capital while gains remain recorded in the balance; losses reduce usable funding. Output also reports unreserved available cash after accepted-entry cash requirements; pending positions reserve account capacity. Only accepted entries count toward the daily quota. Open or pending positions block another opportunity before provider evaluation. Rejected entries do not count as trades or losses.

The immutable configuration records initial capital, instrument identities and ordered priority, model identity, ranking policy, risk limits, cost assumptions, liquidity threshold, prompt hash, and hashes of the relevant implementation files. Resume rejects a changed configuration. Source changes therefore require a fresh experiment directory. Existing quantitative output directories are refused rather than migrated or rewritten.

## Verification

Final command, run entirely offline:

```powershell
.venv\Scripts\python.exe -B -m pytest tests/test_ai_led_offline.py tests/test_deepseek_prospective.py tests/test_deepseek_prospective_summary.py tests/test_cost_model_execution_integrity.py tests/test_core.py tests/test_deepseek_eval_v1.py tests/test_ai_v2.py tests/test_ai_v2_evaluation.py -q -p no:cacheprovider --basetemp C:\Users\Priyanka\.codex\visualizations\2026\10\30\01a18b43-af58-74b1-ad68-35a667601200\.pytest-ai-led-final-verified
```

Result: **245 passed in 37.23 seconds**, exit 0.

Coverage includes independent generation without the reversal predicate, multiple-proposal ranking, reversed submission/proposal order, configured tie priority, schema rejection, invalid/stale/incomplete data, no-trade and replay, configurable immutable capital, immediate actual-entry rejection, accepted-position blocking and settlement, costs and whole-share sizing, available cash after losses, profit reconciliation, daily quota and loss limits, stop/target/gap precedence, revised-candle refusal, malformed account reconciliation, atomic write failure, CLI operation, and refusal of non-offline providers or existing session directories.

All three new Python files passed in-memory Python `compile(..., 'exec')` checks. Each new Python file was inspected through a complete `git diff --no-index` against an empty scratch file. Targeted `git diff --no-index --check` produced no whitespace diagnostics. Git returns exit 1 for these new-file differences; an explicit trailing-whitespace scan also passed. No unrelated files were changed to address repository-wide whitespace issues.

SHA-256 comparison before and after implementation verified all **22 files in Sessions 02-05**, including their request budgets, unchanged. No Session 06 exists. No Upstox request, DeepSeek request, market-data capture, broker order, provider token consumption, or historical-live evaluation occurred.

## Remaining limitations and next step

- This is an offline foundation with saved AI outputs. It establishes execution behavior, not AI prediction quality or profitability.
- The new account supports deterministic stop, target, horizon, and observed session-end exits. It does not support discretionary AI EXIT messages, shorting, derivatives, partial fills, or multiple positions.
- OHLCV turnover and volume are coarse liquidity checks. They do not establish bid/ask spread, depth, executable volume, or real fill quality.
- Weekday/time checks are simulation checks, not an authoritative NSE holiday calendar. Saved metadata does not independently establish exchange provenance.
- Missing bars halt advancement rather than inventing fills. An unresolved position requires valid continuation data. Stops and daily limits cannot guarantee maximum realized loss through price gaps.
- Full snapshot batches and account history are retained in one atomic state file, suitable for a small research foundation. Larger replay histories will need bounded storage and an independently verified archival design.
- The checksum detects accidental corruption; it does not authenticate state against deliberate manual rewriting. External writers must not edit the account file.

The next step is a deterministic saved-data replay driver that supplies complete synchronized batches and explicit saved responses over multiple sessions, with reviewed exchange calendars and measured liquidity assumptions. Any real market-data or DeepSeek adapter requires separate authorization and validation. **Broker sandbox integration remains NO-GO** until authoritative sessions, broker funds/position reconciliation, order idempotency, partial-fill handling, and independent execution controls have been implemented and tested. This task enables offline paper research only.
