# Existing Components Reused

Inspected the existing execution-observation parser, strict prospective fill validator, AI-led offline/guarded/reviewed/recovery/replay flows, NSE calendar, RiskEngine, cost model, tests and existing Git diff. Reused the existing canonical `_digest` helper and loaded `OfflineSessionCalendar` instead of creating another hashing convention or calendar. The existing strict parser and fill validator are unchanged.

The companion normalization is necessary because an observation needs immutable multi-level depth, capture ordinals, connection identity and control markers. The existing quote parser flattens quotes for a different workflow and accepts/coerces some quantity forms that this capture contract rejects. No shared parser, execution engine, storage layer or live entry point was refactored.

# Files Changed

Only three additions:

- `app/research/market_observation_replay.py`
- `tests/test_market_observation_replay.py`
- `docs/OFFLINE_MARKET_OBSERVATION_CAUSAL_REPLAY_REPORT.md`

No existing source, configuration, report, dataset, session, budget, manifest or attestation is modified. No live acquisition command, broker interface, recorder daemon or streaming engine is added.

# Observation Schema

`nse-market-observation-v1` is a frozen dataclass whose nested depth/timestamp collections are tuples. It contains provider, explicit synthetic or recorded-unauthenticated evidence kind, REST/WEBSOCKET transport, NSE/EQ segment identity, instrument key, connection identifier, global local sequence, local UTC receipt time, event kind, original recognized provider timestamp values and their semantics, bid/ask levels and quantities, available circuit bounds, asserted provider phase, source-freshness classification, original payload SHA256 and canonical envelope SHA256.

Supported kinds are SNAPSHOT, UPDATE, GAP, DISCONNECT, RECONNECT and HEARTBEAT. Controls are connection-wide with no invented instrument or price. Global local sequence identifies each normalized observation across all instruments/connections in one capture; splitting a multi-instrument message requires distinct local ordinals, with the shared original payload hash retained. It is not an exchange sequence number.

`normalize_observation(raw_payload, *, transport, instrument_key, connection_id, local_sequence, received_at, kind, evidence_kind, freshness_status)` accepts original JSON bytes. REST fixtures use full-quote-shaped snapshots; WebSocket-like fixtures use decoded full-feed JSON. The latter are not raw Protobuf frames and do not claim to implement every official feed variant. Provider phase fields in these fixtures are explicit normalization inputs; no real market-info/CAS adapter is supplied.

Known REST timestamps are `timestamp` and `last_trade_time`; known WebSocket-like fields are `currentTs` and `ltt`. Unknown extra timestamp fields are not reinterpreted as exchange times. Raw bytes are hashed, not embedded in returned records. A future authorized recorder must preserve those original bytes separately if later raw-to-normalized verification is required.

Depth is normalized to finite positive prices and strict whole-share quantities. Decimal quantities and booleans are rejected; integer strings support decoded int64 fields. Levels sort deterministically by price, duplicate price levels are rejected, crossed books are rejected, and explicit zero-price/zero-quantity placeholders are omitted. One-sided/empty books remain observations, never proof of executable liquidity. Invalid identity, duplicate JSON keys, malformed values and future provider times fail closed with fixed error codes.

# Timestamp and Freshness Handling

Receipt time is explicit, timezone-aware and normalized to UTC. Relative and naive times are rejected; neither function reads the current clock. Original recognized provider values are retained separately from parsed comparison times. REST response generation, last trade time and feed time with unresolved semantics remain distinct. Nothing substitutes those values for an exchange book timestamp.

The schema reserves the conceptual VERIFIED/UNKNOWN/INVALID classifications, but normalization and replay currently accept only UNKNOWN and INVALID. A caller's VERIFIED claim is rejected because no reviewed depth-verification contract exists. INVALID is a conservative caller-supplied quarantine label, not an execution authorization. UNKNOWN remains UNKNOWN through snapshots, updates, heartbeats and reconnects. Malformed/impossible timing is rejected rather than being repaired or silently assigned a new timestamp.

Envelope/prefix hashes establish local byte/record integrity and reproducibility, not provider authorship, exchange completeness or resistance to coordinated rewriting of all local evidence.

# Offline Replay Implementation

`replay_observations(observations, *, as_of, calendar, reference_time=None)` returns immutable views and a reproducible hash chain, with `trades_created: 0`. No account, provider, transport or budget operation is available. Inputs are an explicit recorded list/tuple, not a live iterator or callback.

Ordering is by global recorded local sequence, never by trade/provider timestamps. Reordered submissions yield the same unique observation prefix. Exact duplicate envelopes are skipped and counted; conflicting observations sharing an ordinal are rejected. Missing ordinals require an explicit GAP marker. Receipt-clock regression is rejected instead of being hidden by chronological resorting. Provider generation/feed timestamps are conservatively checked for regression per connection/instrument/field; last-trade time is deliberately not treated as a book clock.

Replay stops at the first receipt beyond `as_of`, before inspecting that future observation's prices or integrity hash. Each visible prefix hash depends only on prior visible observations, calendar identity and optional reference timestamp. Tests demonstrate that corruption of a future price does not affect an earlier prefix, but is rejected when it becomes visible. Future metadata is needed to order the supplied recorded sequence; no future market values are used in a view.

An initial UPDATE cannot establish a book. GAP/DISCONNECT clear all observational books. A reconnect requires a new connection ID and a recorded interruption; updates/heartbeats cannot restore books until a new instrument snapshot arrives. Used connection IDs cannot be recycled. Heartbeats preserve the old book's original receipt time and never renew its freshness. A global gap invalidates every instrument; one instrument's later snapshot does not restore another instrument's book.

`post_reference` means only local receipt strictly after the supplied reference time. It is not a claim that an exchange event occurred after a fill. `displayed_depth_covers` checks top-level quantity only, not freshness, session permission or fill/exit eligibility. The replay emits no fill, stop/target trigger, costs, P&L, performance report or settlement outcome.

# NSE Market Handling

Only configured NSE equity cash identities are normalized. The loaded verified calendar determines continuous-session intervals. Observations outside supported intervals are retained in the evidence view but quarantined from observational books; no universal 15:15/15:30 assumption is introduced. Tests include November 2's earlier close, December 7's later close, weekends, unsupported special sessions and absent coverage. Provider phase assertions can restrict calendar permission, never expand it. Auction/closed assertions clear that instrument's book and require another snapshot before updates can re-establish it. Books cannot carry across verified session intervals.

REST circuit bounds are retained when present and validated against displayed prices. One-sided circuit books and insufficient displayed exit depth do not create pretend liquidations. An observed downward jump after a reference is preserved as data without filling at an unobserved stop price or charging a realized loss. Whole-candle extrema are not used at all in this replay.

No US-market assumptions, fractional shares, account allocation or capital maximum is introduced. Capital is intentionally absent from an observation-only replay; tests confirm replay is independent of capital while unchanged RiskEngine/cost helpers continue to operate at 1000, 2500, 10000 and 100000. Existing one-percent trade-risk/two-percent daily-loss limits, funding policy, Indian fee schedules and slippage-cost convention remain untouched. They must be reapplied by any separately reviewed execution policy.

# Tests and Verification

Added 46 offline cases covering immutable envelope/hash behavior, REST and WebSocket-like snapshots, updates without snapshots, missing/ambiguous timestamps, no freshness promotion, invalid quarantine, reordered/duplicate/conflicting events, future-price isolation, explicit gaps, disconnect/reconnect, heartbeat behavior, local/provider clock regression, malformed/crossed/one-sided books, insufficient exit depth, pre/post-reference price jumps, session/phase boundaries, independent multi-stock books, capital independence, original timestamp preservation and rejection of naive/relative clocks. A purity test blocks file, clock, socket, Upstox, DeepSeek, budget and account-commit operations.

Initial focused run: 35 passed, one failed because a corrupted non-finite envelope was classified as generic replay input rather than observation integrity. The integrity boundary was corrected; additional edge cases were added. Final focused command:

```powershell
.venv\Scripts\python.exe -B -m pytest tests/test_market_observation_replay.py -q -p no:cacheprovider --basetemp C:\Users\Priyanka\.codex\visualizations\2026\10\30\01a18b43-af58-74b1-ad68-35a667601200\.pytest-market-observation-focus-final
```

Result: **46 passed in 3.55 seconds.** Counts overlap with the full run and must not be added together.

Full relevant regression command, without exclusions:

```powershell
.venv\Scripts\python.exe -B -m pytest tests/test_market_observation_replay.py tests/test_prospective_paper_fills.py tests/test_pending_entry_resolution.py tests/test_upstox_deadlines.py tests/test_prospective_lifecycle_fixes.py tests/test_ai_led_reviewed.py tests/test_ai_led_guarded.py tests/test_ai_led_offline.py tests/test_ai_led_replay.py tests/test_prospective_preflight.py tests/test_readiness_evidence.py tests/test_offline_market_data_v1.py tests/test_deepseek_prospective.py tests/test_deepseek_prospective_summary.py tests/test_cost_model_execution_integrity.py tests/test_core.py tests/test_deepseek_eval_v1.py tests/test_ai_v2.py tests/test_ai_v2_evaluation.py tests/test_provenance_audit.py tests/test_execution_observations.py tests/test_execution_integrity.py tests/test_paper_session.py -q -p no:cacheprovider --basetemp C:\Users\Priyanka\.codex\visualizations\2026\10\30\01a18b43-af58-74b1-ad68-35a667601200\.pytest-market-observation-regression-final
```

Result: **722 passed in 282.77 seconds, without exclusions or remaining test failures**: all 676 previous relevant cases plus 46 new cases. This is the full relevant offline selection listed above, not a claim that every unrelated repository test was executed.

Both new Python files passed in-memory compilation using `.venv\Scripts\python.exe -B -` and `compile(path.read_bytes(), str(path), 'exec')`. All three additions passed targeted whitespace checks using `git -c core.autocrlf=false -c core.whitespace=blank-at-eol,blank-at-eof,cr-at-eol diff --no-index --check <empty-file> <new-file>`. The final addition diff was inspected, including untracked files omitted by ordinary Git diff.

SHA256 checks confirm all **498 existing task-start files unchanged**, including the strict prospective fill validator and all **359 protected historical evidence files**. Sessions 02-05, budgets, reports, source attestations and historical replay inputs are preserved. The pre-existing tracked Git diff is unchanged. No network/Upstox/DeepSeek request, provider-token consumption, broker order, live capture, Session 06 or real-data trial occurred. Regression artifacts were written only in external temporary test directories; no existing trading experiment was altered.

# Remaining Data Limitations

This records observed input, not a complete exchange event stream. Local ordinal continuity does not establish upstream delivery completeness. Source depth freshness remains UNKNOWN unless quarantined INVALID; no live evidence verifier is provided. Hashes cannot prove that normalized values were truly published by Upstox. One-sided books and connection liveness do not prove tradability. Whole-minute/five-minute candles cannot reconstruct a post-entry intrabar path. Rejection/quarantine of uncertain data must remain visible rather than becoming fabricated trades or repaired prices.

# Genuine AI Signal Evaluation Readiness

Existing reviewed request/receipt and response-seal patterns can later record genuine prospective decisions independently of fills. A future explicitly authorized signal-only record should bind the observation prefix hash, visible event IDs, universe, calendar/source/prompt versions, exact request hash, requested and returned model, send/receipt/validation-ready timestamps, response hash and validated proposal. The record must say whether it is a genuine prospective response, synthetic mock or recorded-but-unauthenticated replay. Local receipt time is not an independent timestamp signature.

Define evaluation horizons, missing-data exclusions and outcome metrics before collection, and keep eligibility/no-fill reasons separate from signal quality. Future observations may label subsequent outcomes only, never change a prior prompt, candidate set or proposal. Do not regenerate decisions today on old data and call them historical AI evidence, tune against those outcomes and report the same sample as validation, or infer predictive profitability from mock decisions. No signal-only provider runner or DeepSeek call is added here; acquisition and provider-budget authorization remain separate future work.

# Observed-Feed Paper Simulation Readiness

**NO-GO for real-time quote-based paper execution or broker execution.** The strict `prospective-observed-ask-v1` validator remains byte-for-byte unchanged, synthetic-only and separate from this replay. UNKNOWN observations cannot become VERIFIED fill evidence through replay.

A later separately versioned settlement contract must bind an accepted simulated entry and its exact acceptance time to trustworthy subsequent bid-side observations, source/receipt semantics, quantities, trigger rules, capture continuity, calendar/phase and acquisition budgets. It must distinguish trigger time, observed executable proxy and commitment time; preserve spread plus single-counted slippage and Indian charges; and handle gaps, insufficient depth, simultaneous/ambiguous triggers, stop gaps, horizon and session exits explicitly. Pre-fill extrema cannot produce post-fill outcomes. Missing coverage must remain unresolved or receive a separately justified modeling label; it must not create a broker-confirmed or observed fill claim.

# Recommended Next Step

Review this envelope and prefix contract read-only. Resolve provider timestamp/snapshot/completeness semantics and specify the separate post-fill observation/settlement contract before adding an authorized capture adapter. Continue synthetic/offline replay tests in the meantime. Do not start Session 06, connect to a market feed, evaluate DeepSeek or enable trades because these tests pass.
