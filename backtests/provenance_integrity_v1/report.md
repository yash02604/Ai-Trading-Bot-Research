# Historical market data provenance and integrity audit

Audit is deterministic over the source files listed in input_hashes.json. It does not call any market-data provider and does not run a strategy evaluation. Dataset integrity remains **UNRESOLVED**.

## Exact evaluation-file binding

The intended historical evaluator input is `data/research/extended_historical_robustness_candles.csv`. Current byte count: **47032720**. Pinned SHA-256: **`30924b7f53e295de813b859bc81998e196d9ee33cde64ee83d94585911100065`**. Binding status: **VERIFIED**. The audit stores the first observed hash in `dataset_binding.json`; subsequent runs compare current bytes against that record and mark a path/hash mismatch **INVALIDATED** without refreshing the pin. This establishes byte identity only, not source authenticity or price correctness.

## Historical-live decision

**NOT PERMITTED.** The byte binding is verified, but the dataset provenance status remains UNRESOLVED and ten provenance findings remain unresolved. The evaluator gate requires both verified overall provenance and a verified exact-file binding. The hash does not substitute for missing acquisition lineage, adjustment metadata, or source validation.

Current structural checks on that exact file: 518570 rows; 0 duplicate instrument/timestamp rows; 3 invalid OHLC rows; 4 zero-volume rows; 0 negative-volume rows; 14 symbols with internally one-to-one symbol/instrument-key mapping: **True**. Existing malformed records remain unchanged.

## Pipeline and provenance

The current code has an Upstox V3 historical-candle request path and normalizes explicit UTC timestamps to Asia/Kolkata before cache writes. The audited caches have no raw response archive, retrieval manifest, request/response hashes, or per-file API version. The current OOS downloader writes under scripts/data/research, while the audited cache resides under data/research. Therefore the current code is not proof of the historical files’ origin.

NSE circular NSE/CMTR/64960 confirms 2024-11-20 as a Capital Market trading holiday; this date-specific amendment was missing from the preserved session manifest. [NSE circular NSE/CMTR/64960](https://nsearchives.nseindia.com/content/circulars/CMTR64960.pdf).

Current Upstox V2 and V3 documentation describes candle labels as interval starts: [V2 historical candles](https://upstox.com/developer/api-documentation/get-historical-candle-data/) and [V3 historical candles](https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/). This supports start labels as the most defensible API-level interpretation, not a verified convention for these specific caches. Prior session audit found start-label coverage aligned better than end-label coverage.

## Timestamp, adjustments, and universe

Main and OOS equity labels are recorded with +05:30 offsets. Context data mixes +00:00 and +05:30; explicit offsets normalize to comparable instants, but their ingestion paths are unknown. No source evidence establishes corporate-action adjustment status. Cached schemas omit adjustment metadata; no adjustment factors were applied by the loader. A current separate [Upstox corporate actions endpoint](https://upstox.com/developer/api-documentation/get-corporate-actions/) was not called and does not resolve cache adjustment status. The universe is a hard-coded 14-symbol basket with no point-in-time inclusion history or delisted/renamed/merged security map. Survivorship bias is unresolved.

## Findings

| Finding | Audit status | Resolution classification | Evidence summary | Evidence still needed |
| --- | --- | --- | --- | --- |
| `pipeline_provenance` | `UNRESOLVED` | `UNRESOLVED` | app/upstox_client.py constructs current Upstox V3 historical-candle requests; download scripts convert timestamps and write CSV caches. | Original request/response archive, retrieval time and parameters, API version, normalization record, and per-file manifest. |
| `exact_eval_candle_hash_binding` | `VERIFIED_VALID` | `VERIFIED` | Pinned SHA-256: 30924b7f53e295de813b859bc81998e196d9ee33cde64ee83d94585911100065. | No additional evidence is required for this narrow structural finding; its stated limitation still applies. |
| `timestamp_semantics` | `UNRESOLVED` | `UNRESOLVED` | Upstox V2 and V3 documentation describes candle timestamps as interval starts: https://upstox.com/developer/api-documentation/get-historical-candle-data/ and https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/. | Raw provider response or a dataset-specific export contract linked to this file. |
| `corporate_action_adjustment` | `UNRESOLVED` | `UNRESOLVED` | Cached CSV schema contains timestamp and OHLCV/OI fields but no adjustment flag or corporate-action metadata. | Provider adjustment contract or authoritative action history and explicit adjustment state for these rows. |
| `universe_history` | `UNRESOLVED` | `UNRESOLVED` | strategy_backtest.py defines a fixed 14-symbol basket with instrument keys. | Dated point-in-time constituent/membership and security-identity history. |
| `malformed_ohlc` | `VERIFIED_INVALID` | `VERIFIED` | Task 9 identifies three OHLC-order violations on 2024-06-25 09:15 IST. | No additional evidence is required for this narrow structural finding; its stated limitation still applies. |
| `duplicate_candles` | `VERIFIED_VALID` | `VERIFIED` | Checked 518570 main-dataset rows using the instrument_key plus timestamp key. | No additional evidence is required for this narrow structural finding; its stated limitation still applies. |
| `symbol_instrument_consistency` | `VERIFIED_VALID` | `VERIFIED` | Checked 14 symbols and 14 instrument keys in the evaluation file. | No additional evidence is required for this narrow structural finding; its stated limitation still applies. |
| `sbin_move` | `UNRESOLVED` | `UNRESOLVED` | Task 9 records 11:15 IST low 769.80 and close 771.55 after 11:10 close 815.10, an abrupt 5-minute move. | Source-authenticated SBIN candles and relevant corporate-action/event evidence for 2024-06-04. |
| `itc_volume_spike` | `UNRESOLVED` | `UNRESOLVED` | Task 9 flags the 09:15 IST bar volume of 360,164,505 as suspicious relative to adjacent records. | Independent source-authenticated ITC volume observation or raw response for 2025-05-28. |
| `itc_flat_zero_volume` | `UNRESOLVED` | `UNRESOLVED` | Task 9 identifies four consecutive 09:40-09:55 IST flat OHLC bars with zero volume and zero OI. | Feed semantics or raw source evidence distinguishing no-trade bars from carried/placeholder values. |
| `itc_missing_opening` | `VERIFIED_INVALID` | `VERIFIED` | Session coverage audit against the verified NSE session manifest marks 09:15, 09:20, 09:25 and 09:30 IST absent; first available bar is 09:35. | No additional evidence is required for this narrow structural finding; its stated limitation still applies. |
| `full_day_gap` | `VERIFIED_VALID` | `VERIFIED` | The NSE CM circular NSE/CMTR/64960 dated 2024-11-08 amends the holiday calendar and declares 2024-11-20 a trading holiday: https://nsearchives.nseindia.com/content/circulars/CMTR64960.pdf. | No additional evidence is required for this narrow structural finding; its stated limitation still applies. |
| `manifest_2024_11_20` | `VERIFIED_INVALID` | `VERIFIED` | Manifest entry currently says REGULAR for 2024-11-20. | No additional evidence is required for this narrow structural finding; its stated limitation still applies. |
| `split_session_gaps` | `VERIFIED_VALID` | `VERIFIED` | Task 9 records 28 95-minute gaps (14 symbols on each date). | No additional evidence is required for this narrow structural finding; its stated limitation still applies. |
| `context_zero_volume` | `UNRESOLVED` | `UNRESOLVED` | Task 9 reports zero volume for all context/index rows; schema/data source semantics for index volumes are undocumented in this cache. | Provider documentation and per-instrument source metadata defining context/index volume semantics. |
| `late_context_timestamps` | `UNRESOLVED` | `UNRESOLVED` | Task 9 identifies 34 records at 15:35 or 15:55 IST with flat OHLC and zero volume/OI. | Dataset-specific timestamp convention and source records explaining the 15:35/15:55 context rows. |
| `context_timestamp_offsets` | `UNRESOLVED` | `UNRESOLVED` | Task 9 timestamp profile shows UTC and +05:30 offsets within NIFTY50 and India VIX context files. | Per-file/per-request acquisition lineage showing whether mixed offsets are equivalent source labels or separate ingestion paths. |

The ten prior unresolved findings remain unresolved where local files prove only the observed values, not their source, meaning, or cause. The exact candle hash is separately VERIFIED as a byte binding; this does not promote overall dataset provenance.

### pipeline_provenance — UNRESOLVED

acquisition and normalization lineage. Do not treat the cached records as independently source-authenticated. Retain existing bytes and require future acquisition manifests containing endpoint/version, instrument key, dates, interval, retrieval time, response hash, and normalization steps. Limitation: Current code describes a possible/current pipeline but cannot prove it produced these older cache files; OOS output-path mismatch further breaks lineage.

### exact_eval_candle_hash_binding — VERIFIED_VALID

Exact historical-evaluation candle file byte binding. Historical-live preflight must compare the exact candle file against this retained hash; do not refresh the pin after a mismatch. Limitation: SHA-256 establishes byte identity only; it does not establish source authenticity, completeness, price correctness, or adjustment treatment.

### timestamp_semantics — UNRESOLVED

timestamp labels and timezone. For future reproducible research, preserve source timestamps and offsets; provisional start-label analysis may be shown only as an assumption, with the end-label alternative retained as a sensitivity check. Limitation: The dataset-specific timestamp convention is unverified without raw acquisition responses or an authoritative dataset export specification.

### corporate_action_adjustment — UNRESOLVED

split, bonus, dividend and other adjustment status. Exclude securities and dates affected by an unverified corporate action from price-return evaluation until raw/adjusted status and adjustment factors are reconciled; never retrospectively rescale silently. Limitation: Upstox currently exposes a separate corporate-actions endpoint, but it was not called and does not establish whether these cached candles are adjusted: https://upstox.com/developer/api-documentation/get-corporate-actions/.

### universe_history — UNRESOLVED

equity universe and survivorship. Limit conclusions to the stated fixed basket and its available history. Do not generalize to the historical NSE universe; require point-in-time membership and corporate-action/security identity mapping for broader claims. Limitation: Absence of membership records does not prove survivorship bias exists or does not exist; it remains unresolved.

### malformed_ohlc — VERIFIED_INVALID

structurally inconsistent OHLC records. Exclude the three invalid rows from any derived price calculations, preserve originals, and record each exclusion; do not repair values. Limitation: Structural invalidity is verified from the stored values; this does not identify the cause or validate other records.

### duplicate_candles — VERIFIED_VALID

Duplicate instrument/timestamp records in the evaluation candle file. Exclude verified duplicate records from derived calculations and retain the original file unchanged. Limitation: A zero duplicate count establishes only structural uniqueness in the currently hashed file.

### symbol_instrument_consistency — VERIFIED_VALID

Within-file symbol and instrument-key consistency. Use this only as an internal consistency check; require dated official instrument-master evidence for authoritative mapping. Limitation: Internal consistency does not prove that the instrument keys were correct at acquisition time or that the historical universe was unbiased.

### sbin_move — UNRESOLVED

SBIN abrupt 5-minute move on 2024-06-04. Quarantine the affected SBIN session from price-return research until reconciled against a source-authenticated record and corporate-action history. Limitation: Could reflect a genuine market move, a data error, or an unrecorded action; no cause can be established locally.

### itc_volume_spike — UNRESOLVED

ITC volume spike on 2025-05-28. Quarantine the bar and affected session from volume-dependent analysis pending independent reconciliation. Limitation: Large opening volume may be legitimate; local context alone cannot determine authenticity.

### itc_flat_zero_volume — UNRESOLVED

ITC flat zero-volume bars on 2025-01-06. Quarantine these four bars; do not interpret as confirmed no-trade bars or fill/interpolate them. Limitation: No feed semantics establish whether zero-volume flat bars are no-trade records, carried values, or placeholders.

### itc_missing_opening — VERIFIED_INVALID

ITC missing opening intervals on 2025-01-06. Keep the gap as an explicit missing-data event; exclude affected opening-window decisions; never synthesize candles. Limitation: Coverage invalidity is verified under documented start-label convention. The reason (source outage, omission, or other) remains unresolved.

### full_day_gap — VERIFIED_VALID

No bars on the 2024-11-20 NSE trading holiday. Treat absence of bars as expected holiday coverage; do not label this a dataset gap or generate expected candle rows. Limitation: This verifies the official session schedule and expected absence only; it does not authenticate feed contents on other dates.

### manifest_2024_11_20 — VERIFIED_INVALID

Session manifest omitted the amended 2024-11-20 holiday. For this provenance audit, override the prior manifest classification using the date-specific NSE circular and record the discrepancy. Correct the manifest only in a separately approved session-audit revision. Limitation: This task preserves prior session-audit artifacts; the original manifest remains unchanged and must not be used as authoritative for this date.

### split_session_gaps — VERIFIED_VALID

Long gaps on 2024-03-02 and 2024-05-18. Do not classify the expected scheduled break as missing data; continue validating bars inside each scheduled segment independently. Limitation: This verifies calendar schedule compatibility, not the authenticity or completeness of the retained bars.

### context_zero_volume — UNRESOLVED

Market-context/index zero-volume semantics. Do not use these volume fields as liquidity evidence; retain context price bars only where calendar, timestamp, and provenance checks independently pass. Limitation: A zero index volume may be expected for an index instrument, but the feed’s semantics and data correctness are not established.

### late_context_timestamps — UNRESOLVED

Late market-context timestamps. Exclude late labels from regular-session alignment pending source-specific timestamp semantics; do not shift timestamps. Limitation: Could be post-close updates, placeholders, or malformed records; no raw response proves which.

### context_timestamp_offsets — UNRESOLVED

Mixed timestamp offsets in market-context files. Parse only explicit offsets, preserve original timestamp strings, normalize to IST for comparison, and mark naive/ambiguous records unresolved. Limitation: The mixed offsets may be valid equivalent instants or reflect different ingestion paths; per-record request provenance is absent.

## Conservative inclusion/exclusion policy

Preserve source files unchanged. Exclude VERIFIED_INVALID rows from derived price calculations and log each exclusion. Keep UNRESOLVED records in a separate quarantine classification; do not relabel them invalid. Never fill gaps, shift labels, normalize suspicious values, or silently drop records. Exclude unexplained gaps and the affected ITC opening window from any future signal window crossing them. Quarantine SBIN 2024-06-04, ITC 2025-05-28 opening volume, and ITC 2025-01-06 flat/zero-volume bars pending independent reconciliation. Do not use context/index zero-volume fields as liquidity measures. Corporate-action-affected prices and universe-wide claims remain ineligible until evidence is supplied. The special-session split breaks on 2024-03-02 and 2024-05-18 are expected schedule gaps under the manifest, but bar authenticity remains unverified.

## Reproduction and limitations

From the research-copy root run: `python scripts/run_provenance_integrity_audit.py` to regenerate the JSON/CSV/report outputs, and `python -m pytest tests/test_provenance_audit.py -q` for the focused helper tests. The runner hashes every listed input and reports missing expected files. Structural checks do not establish feed authenticity. No backtests were run.

Input file hashes and byte counts are in input_hashes.json; machine-readable classifications and per-finding evidence, affected records, handling, and limitations are in findings.json and findings.csv.
