# Sharp-reversal execution-integrity repair v1

## Scope and status

This change hardens only the isolated sharp-reversal research simulator. It does not change frozen V1, its historical artifacts, or any strategy parameters. It does not run an evaluation. Existing cached candles remain ineligible for execution-based research unless their timestamp semantics and request lineage are established against the exact file hashes.

## Timestamp evidence and limits

The local offline-pipeline documentation references the Upstox V3 historical-candle page, which specifies timestamps as interval starts: <https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/>. The local download script shows one code path that parsed API timestamps as UTC and converted them to Asia/Kolkata. That code and the provider's general documentation do not prove how the existing cache files were produced. The cache rows' explicit `+05:30` offsets and regular 09:15, 09:20, … labels are compatible with START labels, but equally cannot establish whether an upstream transformation shifted labels or whether those rows are from that V3 endpoint. There are no attached request IDs, raw responses, retrieval records, or per-cache acquisition manifests.

Accordingly, cache semantics are **UNRESOLVED**. The new `TimestampSemanticsEvidence` input is fail-closed: it must declare verified START labels, cite HTTPS provider documentation, include acquisition request IDs and a manifest hash, and bind the exact dataset SHA-256 mapping. The command accepts this as `--timestamp-evidence`; the evidence file's own hash and the session-manifest input hash are written into any new run manifest. Hashes establish byte identity, not authenticity or the truth of an attestation. The current cache has no valid evidence file; this implementation does not authorize or run an experiment against it.

Execution also requires an explicit set of verified regular-session dates. Special or unresolved sessions are not admitted through the simulator interface. The local manifest labels are the source of that set in the research runner. The session-time boundary check is an additional structural guard, not an exchange calendar or authoritative holiday validator.

## Information and execution timing

At a signal time, the event builder has the signal candle and trailing rows only; its rolling indicators are computed on ordered observations, and the market-regime proxy uses a one-session shift. It forms the signal from the completed signal candle. The earliest simulated entry is the open of the immediately following five-minute candle. A same-index/same-bar entry is rejected. The event and all holding candles must carry explicit IST offsets, be five-minute contiguous, lie on the same date, and stay within 09:15–15:25 START labels. The event's final index must remain within the candidate holding horizon.

At entry, the simulator observes the next candle's open, then checks that candle's low for a stop. If the open is below the stop, the modeled exit is the open; otherwise it is the stop. Subsequent bars are treated the same way. If no stop triggers, the exit uses the final eligible candle close, including the session's last available bar when the candidate horizon is truncated. This is a deterministic OHLC model, not proof of intrabar order, executable liquidity, queue position, or a real fill. Timestamp uncertainty in the existing caches prevents claiming that the observed bars truly represent these intervals.

The direct simulator interface requires both timestamp evidence and verified regular-session dates. It revalidates event indices, row timestamps, timezone offsets, next-bar timing, contiguous same-session holding labels, session clock boundaries, positive finite OHLC, and OHLC relationships before risk evaluation. Invalid events are rejected without opening a simulated position. Event input must be chronological; out-of-order records fail closed. There is no live broker or order placement path in this module.

## Shared risk-state repair

The simulator records each accepted trade's modeled exit label. At a new entry time, exit labels earlier than that time are removed, and the remaining count is passed as `RiskState.open_positions` to the shared risk engine. A position whose exit label equals the new entry label remains open for the gate, preventing simultaneous fills on a shared bar. A rejected event does not increment trade count or become an open position. Existing daily loss and trade-count checks remain in place.

## Costs: formulas and evidence status

The formula implementation in `app/cost_model.py` was retained and covered with independent arithmetic assertions:

- Brokerage is the lesser of 0.1% of each order's turnover and ₹20 per leg.
- STT is 0.025% on sell turnover; stamp duty is 0.003% on buy turnover.
- SEBI turnover fee is ₹10/crore on both legs; GST is 18% of brokerage, exchange and IPFT charges.
- Exchange transaction charge is fixed at 0.00322% for each leg, chosen by the existing model as the highest schedule it found for 2024–26. This is a conservative historical model assumption, not a date-specific verified fee schedule.
- IPFT is ₹0.10/lakh turnover, explicitly an estimate for the interval.
- Slippage is 5 basis points per side, applied to each side's turnover (10 bps across equal-price round-trip turnover). This is a model assumption, not a published fee or measured fill statistic.

Prior local reports cite Upstox's charges page and NSE's statutory charges page. Those citations were not re-fetched as part of this offline task, and no archived dated schedule was found in scope. Statutory/broker rates therefore remain source-referenced assumptions for the prior modeled period, not verified current or date-specific charges. The cost model's formulas and assumptions were not relaxed.

## Validation limits

Tests in this change validate software behavior and formula arithmetic on synthetic fixtures. They cannot establish historical feed provenance, candle-label semantics, fill quality, cost accuracy, profitability, or readiness for paper/live trading. No backtest, external request, acquisition, or strategy search was run.
