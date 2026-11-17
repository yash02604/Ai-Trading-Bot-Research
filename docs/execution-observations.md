# Paper Execution Observation Schema

The paper runner records quote observations in the separate SQLite table
`execution_observations`. Existing decisions, positions, trades, daily limits,
historical reports, and research outputs are not migrated or rewritten. The
table has application-enforced append-only triggers. An identical record is
idempotently ignored; another observation of the same signal at a different
time remains a separate row. The simulated-fill decision, open position, daily
trade counter, and required fill observation are written within one SQLite
savepoint. If any required write fails, the savepoint is rolled back and the
signal is persisted as a rejected paper entry with a sanitized failure type;
no position or trade-count increment is committed.

## Fields

| Field | Meaning |
|---|---|
| `observation_id` | SHA-256 identifier of the normalized record; primary key. |
| `signal_id`, `symbol`, `instrument_key` | V1 signal identity and configured Upstox instrument. |
| `signal_timestamp`, `entry_candle_time` | Signal-candle time and next-candle entry window. |
| `observed_at` | Local response receipt/observation time in Asia/Kolkata. |
| `provider_timestamp` | Upstox V3 `data.timestamp`, retained separately. |
| `provider_timestamp_kind` | `response_generated`; documents the endpoint timestamp semantics. |
| `source_quote_timestamp`, `source_timestamp_status` | Independent order-book source time when exposed; currently NULL and `not_exposed_by_quote_endpoint`. |
| `reference_price` | Historical next-candle open, only when available and valid. |
| `ask_vs_reference_price`, `ask_vs_reference_bps` | Observable ask minus next-candle-open reference, and that difference relative to the reference; null if either price is unavailable. This is a quote comparison, not a fill guarantee. |
| `required_quantity` | Risk-engine share quantity required for the paper entry, including rejected insufficient-depth cases when sizing was reached. |
| `bid_price`, `bid_quantity`, `ask_price`, `ask_quantity` | First valid positive-price, positive-quantity levels in the API's bid/ask depth arrays. |
| `spread_price`, `spread_bps`, `spread_available`, `spread_reason` | Ask minus bid and spread divided by midpoint; only available for valid, non-crossed two-sided depth. |
| `quote_age_ms` | Local receipt minus provider response timestamp. It is not verified source-book age. |
| `quote_valid`, `quote_reason` | Quote validity at receipt. The runner requires the requested instrument, a fresh provider timestamp and a positive ask with quantity. If a bid is present, it must be at or below the ask. |
| `paper_entry_eligible`, `eligibility_reason` | Outcome after context, duplicate, risk, quantity and timing checks. A quote that was valid at receipt can expire before the simulated fill. |
| `simulated_entry_price`, `simulated_quantity` | Populated only for an accepted simulated paper fill. |

At the simulated fill, the runner checks that the next five-minute candle is
still open and that at most five seconds have elapsed since the provider timestamp.
Upstox defines V3 `data.timestamp` as the response generation time. The age
of the individual bid and ask levels remains unknown.
[Upstox Full Market Quotes V3](https://upstox.com/developer/api-documentation/get-full-market-quote-v3/).

## Synthetic example

This is test-only data and is not a live observation or fill claim:

```json
{
  "signal_id": "synthetic-signal-1",
  "observed_at": "2026-10-12T09:25:01+05:30",
  "provider_timestamp": "2026-10-12T09:25:00+05:30",
  "provider_timestamp_kind": "response_generated",
  "source_quote_timestamp": null,
  "symbol": "TEST",
  "instrument_key": "NSE_EQ|TEST",
  "signal_timestamp": "2026-10-12T09:20:00+05:30",
  "entry_candle_time": "2026-10-12T09:25:00+05:30",
  "reference_price": 100.1,
  "ask_vs_reference_price": 0.1,
  "ask_vs_reference_bps": 9.99,
  "required_quantity": 50,
  "bid_price": 100.0,
  "bid_quantity": 40,
  "ask_price": 100.2,
  "ask_quantity": 75,
  "spread_price": 0.2,
  "spread_bps": 19.98,
  "quote_age_ms": 1000,
  "quote_valid": true,
  "paper_entry_eligible": true,
  "eligibility_reason": "simulated_paper_fill",
  "simulated_entry_price": 100.2,
  "simulated_quantity": 50
}
```

## Export and analysis

The export reads the SQLite database in read-only mode and writes a CSV plus a
summary report without modifying the database:

```powershell
python -m scripts.export_execution_observations
```

The exporter introspects the table schema. Older compatible observation
tables export later optional fields as empty values. A schema missing required
identity, timestamp, quote-validity, or eligibility fields fails with an
actionable error. The exporter never runs migrations or writes to the database.

The summary includes measured ask/reference difference and counts quote depth
that was sufficient or insufficient relative to required paper quantity when
both quantities were recorded. Rejections before the risk-sizing stage have no
required-quantity comparison.

Historical cost sensitivity is a separate offline calculation using only the
existing portfolio trade rows; its scenarios and assumptions are documented in
`backtests/execution_cost_review/execution_cost_sensitivity.md`:

```powershell
python -m scripts.execution_cost_sensitivity
```

The report separately totals brokerage, STT, exchange charges, IPFT, SEBI,
stamp duty, GST, modeled slippage, and scenario spread allowance. The saved
trade-level cost assumptions are retained; wider spreads are sensitivity
assumptions because the historical records contain no quote snapshots.

No live observation sample is claimed until a manually started paper session
collects it during an open market. The user must start that session; this task
does not start it.
