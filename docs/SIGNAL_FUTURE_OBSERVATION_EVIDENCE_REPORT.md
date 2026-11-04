# Current Signal Fix Verification

Reviewed the actual signal/evaluation sources and tests, guarded transports, persistent budgets, calendar/attestation checks and quote observation/replay infrastructure. Exact signal prompt text/hash checks remain enforced, including immutable resume. Reconciliation still derives expected instrument outcomes from predictions and reports pending/missing/invalid/ambiguous outcomes, genuine NO_TRADE and rate denominators.

The new CLI regression exposed equal numeric configuration values with different canonical bytes (`1000` versus `1000.0`). Resume now returns the original integrity-checked saved configuration after equality validation, preserving its original hash/seals rather than reconstructing an equal but byte-different configuration. No saved manifest is rewritten.

# Future Observation Evidence Design

`signal_observations.py` reuses `_ReviewedRun.fetch`, the same bounded GET client, mandatory preflight, durable reservations/send markers, receipts, immutable writers, candle validators and append-only seals. It introduces no HTTP client, provider client, scheduler or polling loop. The existing quote/depth observation normalizer is not used to manufacture candle or depth freshness; that module and the strict fill validator remain unchanged.

The signal manifest freezes the future acquisition policy and new source identity before any prediction request. Each expected capture identity binds the manifest digest, immutable prediction digest/ID, configured NSE_EQ symbol/key, purpose and exact forecast/capture windows. Valid BUY and NO_TRADE predictions both require observations for the complete configured universe.

Successful capture retains canonical decoded provider JSON in an immutable envelope, including original candle start strings and supported timestamp fields exactly as supplied. Unknown response fields and nonnumeric candle values fail closed before persistence. The receipt hash binds that payload to the existing transport send; a separately hashed normalized snapshot preserves the numerical candles. The observation record binds both artifacts, attestation version/hash, verified calendar identity, reservation, send/receipt/local observation times, interval, validation status and freshness/session checks. Its seal binds the record digest and exact saved-artifact bytes.

Provider fields remain unauthenticated. Local clocks do not authenticate exchange event time. Decoded JSON is not original HTTP wire-byte evidence.

# Provenance States

- `REVIEWED_UPSTOX_CAPTURE`: complete future observations validated against the locally recorded guarded acquisition chain.
- `OFFLINE_REPLAY_FROM_REVIEWED_CAPTURE`: an experiment summary reconstructed exclusively from that chain.
- `UNVERIFIED_CALLER_SUPPLIED`: the separate numeric evaluator; a caller-supplied source label cannot upgrade it.
- `SYNTHETIC_TEST_DATA`: reserved terminology for fixtures; mocked HTTP/SDK tests are technical simulations, never genuine performance evidence.
- `INVALID_OR_CONFLICTING`: failed/incomplete capture evidence, or an integrity conflict that blocks replay.
- `NO_VALIDATED_FUTURE_OBSERVATIONS`: no completed reviewed observations are available; no genuine coverage is claimed.

These are local provenance assertions, not signatures from Upstox or an independent witness. Tests deliberately exercise guarded-path plumbing with mocks in external temporary directories; their reviewed-path assertions are not genuine market captures.

# Files Changed

- `app/research/ai_signal_only.py`: frozen future policy/source identity, sufficient remaining-session gate and preserved canonical configuration on resume.
- `app/research/ai_led_reviewed.py`: an explicitly authorized candle-only follow-up purpose, original transport reuse and receipt-bound payload retention.
- New `app/research/signal_observations.py`: expected jobs, capture envelope/seals, chain verification and complete experiment replay.
- New `scripts/run_ai_signal_followup.py`: offline replay by default; capture requires separate opt-ins. No provider or broker authorization flags exist.
- New `tests/test_signal_observation_chain.py` and this report.

The numeric evaluator, baseline, original CLI, Upstox client, strict fill validator, quote replay module and old reports remain unchanged.

# Evaluation Binding

The experiment replay API accepts an experiment directory and cutoff, not caller-selected decisions, prices or evaluation objects. It verifies every saved intent/request/prediction, original provider response seals and request-budget inventory. A deleted final signal can be recovered from its sealed original response; withholding an intent leaves a conflicting request inventory.

For each future artifact, replay recomputes its expected job, checks instrument/experiment/prediction/window identity, compares original transport and payload hashes, reconstructs normalization and validation, and verifies its independent seal. Unknown/duplicate artifacts or conflicting bytes block replay. Completed outcomes cannot come from manually substituted numeric bodies.

Missing artifacts and capture failures remain in expected evaluation coverage. A capture failure is explicit in observation bindings and contributes no positive/negative outcome. Existing sealed captures are reused; spent requests without a finished artifact fail closed. Interrupted publication/sealing is not silently retried or regenerated. Offline evaluation can still report missing coverage where no conflicting artifact exists.

# Selection-Bias Protection

The manifest predeclares a fixed six-bar window starting at the first full five-minute boundary after decision readiness. Exactly one intraday snapshot per valid prediction and configured instrument is scheduled after the horizon, including NO_TRADE and unselected instruments. Acquisition is allowed from horizon end + 5 seconds until strictly before horizon end + 300 seconds. No replacement reservation, alternative window, refund or automatic experiment is created after failure.

Initial prediction preflight conservatively leaves 45 minutes from its current five-minute boundary through the supported session end, allowing both the forecast and guarded follow-up acquisition. Under the retained 09:15–15:15 interval, the last eligible initial boundary is 14:30. Unsupported special sessions remain blocked.

The operator can fail to run the declared follow-up; that leaves missing coverage, not a removed prediction. Coherent malicious rewriting/deletion of all locally controlled ledgers cannot be detected by unsigned local hashes and is outside this evidence guarantee.

# Future Observation Budget Requirements

For one NSE equity with quotes disabled: **one initial candle reservation + one future candle reservation = two lifetime market-data reservations**. One future intraday response can contain all six completed forecast bars; six separate requests are unnecessary. Existing transport retries allow up to three physical attempts per logical reservation, within deadlines and repeated preflight checks. Thus the bounded trial has at most six physical market-data attempts, plus one DeepSeek attempt with zero SDK retries.

The provider cap remains exactly one. Follow-up never reserves or calls a provider. No quotes, account balances, positions, fills, fees, execution P&L or broker operations are introduced. Capital is immutable positive configurable metadata; ₹1,000 is not an application maximum. A cap chosen too low is never increased on resume.

# Tests and Verification

Clean focused run: **93 passed in 182.48 seconds**, without exclusions. This includes 62 prior signal/accounting cases and 31 new chain cases. Coverage includes mocked guarded capture, supplied/synthetic separation, tampering, wrong identities/windows, missing bars, acquisition failure, duplicates, restart/interruption, fixed denominators, policy changes, session limits, direct transport isolation, CLI defaults and canonical configuration preservation.

Full relevant regression: **815 passed in 507.40 seconds**, without exclusions, skips or failures. This is the original 24-file regression selection plus both accounting and observation-chain modules: all 784 previous cases plus 31 new cases. It is not a claim that unrelated repository tests were run.

All five changed/new Python files compile in memory. All six task files pass targeted CRLF-aware whitespace checks. The final diff includes untracked implementation files omitted by ordinary Git diff. All **387 pre-existing documentation/data/backtest/report files** match their task-start SHA-256 inventory, including protected history, Sessions 02–05, budgets and attestations. Of 507 inventoried existing files, only the two intended signal/transport integration files changed; the other four task files are new.

Tests disable `.env` loading, use mock credentials, block sockets and keep temporary artifacts outside protected repository evidence. No real API call, genuine experiment or broker order is performed.

# Remaining Limitations

This verifies a locally asserted reviewed software path, not independent provider authorship, exchange event-time accuracy or an adversarial filesystem. Genuine future acquisition has not been exercised. Current credentials, provider availability, live latency and complete future candle availability are unverified.

Capture is a manual one-shot follow-up, not a background service. An interrupted or failed spent request may leave a permanently missing outcome. Earlier source-bound experiments remain incompatible with changed sources; none is migrated. Calendar verification uses retained local evidence rather than new online amendment checks. Depth freshness stays UNKNOWN; no executable return or calibrated predictive skill is established.

# Final Verdict for One Bounded Signal-Only Trial

**GO** for the proposed single, bounded, prediction-only trial using locally asserted reviewed capture and offline replay. Full regression and preservation checks passed. This verdict is technical readiness only, not authorization to send requests. Independent source/time authentication is not claimed. No paper or broker execution approval follows.

# Exact Proposed Trial and Follow-Up Commands

Proposal only; neither command is executed. Use a fresh directory on the locally supported 4 November 2026 session, after separate authorization and credential confirmation. Submit the prediction around **12:10:10 IST**, with decision readiness before **12:15 IST**. Its window is then **12:15–12:45 IST**. Run the follow-up around **12:45:10 IST**, within the enforced **12:45:05–12:50:00 IST** half-open acquisition window. If the initial decision occurs in another five-minute bucket, derive the schedule from its saved readiness rather than using these example times.

```powershell
$repo = 'C:\Users\Priyanka\Desktop\ai-trading-bot-research'
$out = "$repo\backtests\ai_signal_chain_trial_20261104_01"
if (Test-Path -LiteralPath $out) { throw 'Use a completely new output directory.' }
$env:PAPER_TRADING = 'true'
$env:DEEPSEEK_MODEL = 'deepseek-flash'
& "$repo\.venv\Scripts\python.exe" -B "$repo\scripts\run_ai_led_prospective_paper.py" `
  --output $out --instrument-priority RELIANCE --paper-capital 1000 `
  --max-provider-requests 1 --max-market-data-requests 2 `
  --signal-only --enable-signal-only `
  --allow-market-data-requests --allow-provider-requests
```

In the same PowerShell session, at the declared follow-up time:

```powershell
& "$repo\.venv\Scripts\python.exe" -B "$repo\scripts\run_ai_signal_followup.py" `
  --output $out --instrument-priority RELIANCE --paper-capital 1000 `
  --max-provider-requests 1 --max-market-data-requests 2 `
  --enable-signal-only --capture-future --enable-signal-future `
  --allow-market-data-requests
```

For later offline replay, omit the three capture/request flags (`--capture-future`, `--enable-signal-future`, `--allow-market-data-requests`) and supply an explicit aware `--as-of` cutoff. Replay does not need API credentials. The follow-up command automatically prints the verified, reconciled summary.

# Paper Execution Status

Disabled for this workflow. Real-time fill-quality and causal execution readiness remain NO-GO.

# Broker Execution Status

Disabled and unauthorized. No broker functionality was added or used.
