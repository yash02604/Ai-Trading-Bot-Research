# AI-led V2 research design and data policy

Status: implementation interface only; no V2 backtest, provider call, or runner has been run. No candidate is approved for performance claims.

## Decision and control boundaries

`app.v2` is separate from V1. `DecisionRequest` carries an aware signal timestamp, explicit data cutoff, a finite per-symbol feature map with a timestamp for every map, an eligible universe, registered strategy IDs, and current paper positions. The prompt is versioned (`ai-led-v2.1`) and prohibits future outcomes and invented data. The strict response contract (`ai-decision-v2.1`) requires regime, a complete non-duplicated ranking of eligible symbols, an allowlisted action and strategy, concise rationale/evidence, and bounded order parameters. A self-reported confidence is recorded as uncalibrated and is never used by Python for sizing.

`DecisionService` sends only the supplied research context to an injected provider. OpenRouter is an optional adapter, requires explicit API-key injection, and never logs the key or raw provider error. It records requested and returned model IDs, flags substitutions, and defaults to HOLD on provider errors, substitutions, invalid JSON, unsupported fields, or invalid decisions. No provider call was made during Stage B.

`SafetyGate` independently rechecks timestamp freshness, future data, session validity, kill switch, duplicate signal IDs, registry membership, action/position consistency, stop/target/horizon bounds, whole-share risk sizing, allocation, open positions, daily loss, and trades per day. It has no provider authority. Production must supply an authoritative exchange-session validator; `regular_weekday_session` is only a clock helper and does not know NSE holidays or special sessions. The default registry exposes the frozen V1 candidate as a non-paper-enabled reference and registers no new validated strategy.

`V2ResearchController` writes an append-only SQLite decision record before any optional simulated fill. `PaperOnlyExecutor` has no broker interface; it records simulated entry/exit slippage, estimated transaction costs and net outcome. The ledger rejects updates/deletes via SQLite triggers and rejects credential-like payload keys. The controller is a callable research interface; it does not start itself, fetch market data, or connect to a broker.

## Versions and risk defaults

- Contract: `ai-decision-v2.1`
- Prompt: `ai-led-v2.1`
- Configuration: `ai-v2-risk-2026-10-18.1`
- Default max risk per trade: 1% of current capital
- Default max allocation: 25% of current capital
- Default daily loss limit: 2% of day-start capital
- Default maximum positions/trades per day: 1 / 3
- Default stop distance: 0.05%–1.00%; target distance at most 5%; holding horizon 1–12 bars
- Default feature freshness: 300 seconds
- Paper slippage and statutory charge assumptions reuse `app.cost_model`; these remain estimates, not verified fills or contract-note fees.

The registry starts with `frozen_reversal_v1` marked `FROZEN_BASELINE`, paper-disabled. New candidates must be explicitly registered and remain `RESEARCH_ONLY` unless separately reviewed; registering does not mean the candidate is validated. No strategy parameters were tuned.

## Data handling prerequisites for any later evaluation

Existing data are reused historical samples, not a V2 holdout. Keep the CSVs unchanged and apply exclusions through an evaluation manifest. Until primary session evidence is attached, exclude whole affected dates from signal generation, training, scoring, and context joins: 2024-01-20, 2024-03-02, 2024-05-18, 2024-11-01, 2025-02-01, and 2026-02-01. This avoids treating unknown weekend/special-session/Muhurat intervals as ordinary continuous trading. Do not fill the 28 95-minute gaps; because all occur on the two special dates above, exclude those dates unless exact exchange schedule evidence and a reviewed session-aware method are added.

Exclude the three structurally invalid OHLC rows already identified by the existing data audit (BHARTIARTL, HDFCBANK, ICICIBANK at 2024-06-25 09:15 IST), and invalidate any feature/signal/holding window that depends on those records or crosses their resulting gap. Exclude the anomalous SBIN 2024-06-04 11:15 record and any dependent feature/trade window pending source reconciliation. Exclude ITC 2025-05-28 09:15 from volume-dependent features pending confirmation of its unusually large volume; exclude ITC 2025-01-06 09:40–09:55 flat zero-volume rows from price/indicator windows pending source semantics. Never synthesize replacement bars.

All index/context volume fields are zero and their meaning is unresolved; do not use them as a feature. Exclude context timestamps mapping to 15:35 or 15:55 IST unless a source-specific session interpretation is independently verified. Normalize explicit offsets to UTC instants and join only backward/as-of at or before the decision cutoff; never silently reinterpret stored timestamps. The existing candle file contains three invalid rows, four zero-volume equity rows, and unresolved unusual observations documented in `backtests/research_task9/`.

No independent holdout exists: 2023-12-01–2025-12-01 is heavily reused; 2025-12-02–2026-10-01 was evaluated in Stage 9/10. The latter is not available for V2 selection or a V2 validation claim. Stage D needs a prospectively collected or independently sourced chronological holdout frozen before results are observed, with a written manifest and matching symbol universe/cost assumptions. Avoid any Stage D until a later approval.

## Stage B completion limits

This implementation is not evaluated for profitability. AI historical replay remains unavailable because point-in-time prompts, outputs, and exact model versions were not recorded. The existing market data's corporate actions, survivorship status, session provenance, and actual spread/impact remain unresolved. Model failures and substitutions are represented in records, but real provider latency/token observations will exist only when a separately approved paper workflow calls a provider.
