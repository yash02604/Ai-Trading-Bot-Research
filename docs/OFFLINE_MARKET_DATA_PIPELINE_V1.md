# Offline historical market-data pipeline v1

## Scope and current state

This is an offline foundation only. It contains versioned record schemas, deterministic normalization and validation helpers, and a new NSE CM session manifest v2. It contains no HTTP client, network acquisition command, live runner, or strategy evaluation. No historical acquisition provenance has been reconstructed by these files.

The acquisition manifest is a future record format, not evidence that a request was made. The API credential, authorization headers, and secret query parameters are prohibited. Raw response retention is configurable and must remain disabled until the applicable provider terms and retention permissions are reviewed. A hash proves byte identity for the bytes hashed; it does not prove source authenticity, completeness, or correctness.

## Acquisition manifest schema v1

The JSON Schema is schemas/acquisition-manifest-v1.schema.json. Each immutable record includes request ID, provider/API version, sanitized HTTPS endpoint, non-secret request parameters, UTC retrieval timestamp, instrument and available instrument-master metadata, requested range and interval, response/retry metadata, parser/normalizer/session manifest versions, and SHA-256 for raw response and normalized output. Failed requests may have null response hashes; successful records require both.

Secrets are removed by key-name before construction and endpoint query strings are omitted. Authorization headers are not an allowed field. Do not log a token to explain that it was redacted.

## Normalized candle schema v1

The JSON Schema is schemas/candle-record-v1.schema.json. Preserve the original provider timestamp and offset and record both normalized UTC and Asia/Kolkata values. V3 interval labels are recorded as START. Source request ID and source row index preserve lineage. OHLC, provider volume, optional OI, adjustment state, and validation classification/reasons are represented explicitly.

Upstox V3 documentation specifies a candle start timestamp and describes volume as the amount traded in the interval. It describes OI as outstanding derivative contracts; zero OI on an equity/index instrument is not evidence about trade volume. The provider documents these fields, but that is not independent verification of any observation. The documentation reviewed does not establish whether historical equity prices are corporate-action adjusted. Adjustment status therefore remains UNRESOLVED until provider terms or evidence establish otherwise.

## Conservative handling

Only explicit-offset timestamps are normalized. Naive timestamps fail schema validation. START labels are checked against half-open session intervals from a verified manifest; no timestamp is shifted. OHLC, finite values, nonnegative provider volume/OI, interval alignment, duplicate instrument/timestamp keys, and missing labels are reported. Invalid rows remain present and receive VERIFIED_INVALID plus reasons. Rows requiring unavailable/untrusted session evidence remain UNRESOLVED. Missing candles are reported, never synthesized. Suspicious prices and volumes are never repaired automatically.

## Session manifest v2

data/audits/nse_cm_session_manifest_v2.json is built from v1 by a deterministic script that refuses to overwrite an existing target and refuses a changed v1 source hash. It adds NSE circular NSE/CMTR/64960 and changes only 2024-11-20 to a verified CM holiday with no normal-market intervals. V1 remains unchanged. A broader circular-index review across all represented dates remains outstanding; this one amendment does not validate other dates.

## Future acquisition gate

This task does not implement an enabled network path. Any future downloader must be separately approved, configured for an explicitly authorized source, respect provider retrieval limits/terms, write raw content-addressed objects only where retention is permitted, create append-only request manifests, write normalized output to a new run directory, and keep credentials outside artifacts and logs. No Upstox or OpenRouter requests were made in this task.
## Hash and immutability semantics

The raw-response hash is SHA-256 over response-body bytes supplied to the offline hashing function. The normalized-output hash is SHA-256 over UTF-8 canonical JSON produced with sorted object keys, compact separators, and non-finite values forbidden. A caller must record the parser and normalizer versions alongside both hashes. Content hashes make byte changes detectable; they do not attest who served the bytes or whether the bytes represent exchange truth.

Acquisition manifest records are stored individually under a filename derived from the request ID. Repeating an identical record is idempotent; reusing an ID for different content fails. The helper does not perform acquisition or enable raw-response persistence. Retaining raw content remains a separate configurable decision and requires documented provider-terms review.

The historical provenance audit pins the first observed SHA-256 and byte count for `data/research/extended_historical_robustness_candles.csv` in `backtests/provenance_integrity_v1/dataset_binding.json`. Later audit runs compare the exact bytes and path against that pin; a mismatch is recorded as `INVALIDATED` and does not refresh the binding. The historical evaluator preflight also requires the audit summary to report a verified binding and compares the supplied candle file to the pinned hash. This establishes byte identity only. It does not establish when, how, or from whom the cached candles were acquired, or whether the values are complete, authentic, or corporate-action adjusted.

## Reviewed source references

Upstox V3 candle reference: https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/

Upstox corporate actions reference: https://upstox.com/developer/api-documentation/get-corporate-actions/

NSE amendment: NSE/CMTR/64960, issued 2024-11-08, effective 2024-11-20: https://nsearchives.nseindia.com/content/circulars/CMTR64960.pdf

The NSE source was reviewed through the official archive but not downloaded; its source-content hash is therefore null in v2. No raw market response was fetched during this task.
