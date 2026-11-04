# Bounded historical acquisition pilot — readiness record

## Current status

`data/acquisition_pilots/pilot_v1.json` specifies exactly one read-only Upstox V3 historical-candle request: RELIANCE (`NSE_EQ|INE002A01018`), 5-minute interval, 2025-02-03. The date has a `VERIFIED` regular-session entry in local NSE CM manifest v2 and expects 75 half-open START labels from 09:15 through 15:25 IST.

This is a bounded plan with a dedicated fail-closed runner at `scripts/run_acquisition_pilot_v1.py`. The runner has not been invoked and this readiness review made no network requests. The existing `app/upstox_client.py` GET helper is not a substitute for the runner's authorization, response limits, immutable raw-response, and normalization-audit controls.

## Exact source authorization and retention checks

Before the pilot can start, the account owner must confirm all of the following from their Upstox account terms/entitlements and applicable API/data terms:

1. The supplied, currently valid OAuth access token is authorized for the read-only V3 historical-candle endpoint for the configured NSE instrument and requested historical date. The local README names `UPSTOX_ACCESS_TOKEN`; no token was inspected or copied here.
2. The account and intended research use are authorized to retrieve, locally process, and retain the requested historical data, including research-only use.
3. The provider's applicable terms expressly permit retaining the exact raw response bytes locally for audit/reproducibility, and state any retention duration, deletion, redistribution, or access restrictions. If exact raw retention is not permitted or remains unclear, stop; this pilot depends on raw-byte retention.
4. Request limits and retry/rate-limit rules allow one request with zero retries. The pilot makes no retry on timeout or error.
5. A dated official instrument-master snapshot confirms the configured instrument key still maps to RELIANCE / NSE CM. The local V1 mapping is only a lead, not authoritative evidence.

Do not place the token in config, source, a command-line argument, artifacts, or logs. A later approved acquisition implementation must read it from a protected process environment or secret manager and redact request/response diagnostics. The token value is outside this task's scope.

## Output and validation contract

If separately authorized and implemented, a unique new run directory under the `outputs.root` path in the JSON will contain exact raw response bytes (content-addressed SHA-256), one immutable request manifest with sanitized parameters and retrieval time, normalized candle records, separate `VERIFIED_INVALID` and `UNRESOLVED` quarantine files, and a validation report. A hash records byte identity only; it does not prove the provider or exchange authored the data. The directory must be created exclusively; if it exists, stop rather than overwrite.

Validation must use the checked-in acquisition/candle schemas and offline validator, pin manifest v2, preserve original provider timestamp strings and offsets, normalize only explicit offsets, and record START labels. It must check schemas, 5-minute alignment, OHLC, duplicates, expected session coverage, gaps, and manifest membership. No timestamps or prices are shifted or repaired, and missing bars are never synthesized. Corporate-action adjustment status and underlying feed authenticity remain unresolved even if the response passes structural/session validation.

## Calendar and data limitations

The available local evidence confirms the specific 2024-11-20 correction in manifest v2. No additional confirmed defect was identified in the local audit evidence reviewed for this readiness task. That is not a comprehensive re-verification of other dates: the manifest itself says emergency suspensions/calendar completeness remain unresolved, and the broader NSE circular-index review is outstanding. Do not infer that a verified entry in this manifest makes the vendor feed authentic.

The old cache still has unresolved acquisition lineage, corporate-action adjustment status, and point-in-time universe/survivorship. Existing known anomalies remain quarantined per the provenance audit; this one-day pilot does not repair or validate the old cache. Upstox's documented candle semantics cannot retroactively establish old-cache timestamp semantics or adjustment status.

## Exact next action

Substantiate the five source authorization/retention items above, attach a dated official instrument-master snapshot and reviewed exact-request authorization, then independently review the existing runner and its evidence checks. Only after all gates pass may the fixed single request be run with `python -m scripts.run_acquisition_pilot_v1`; no strategy backtest authorization is included. The pilot is currently blocked. If the provider cannot permit raw response retention, it needs a separately reviewed alternative audit design.
