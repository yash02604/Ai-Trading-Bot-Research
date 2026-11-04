# Acquisition Pilot V1 Execution Gate

This note records the preflight review for `data/acquisition_pilots/pilot_v1.json`. The implementation is restricted to that single pilot and does not itself authorize a request.

## Fixed pilot scope

- Instrument: RELIANCE, `NSE_EQ|INE002A01018` (NSE CM).
- Date and interval: 2025-02-03, five-minute candles labelled `START`, IST.
- Request/response limits: one HTTP GET, zero retries, at most 75 candle rows, and 2 MiB response body.
- Endpoint: `https://api.upstox.com/v3/historical-candle/{instrument_key}/minutes/{interval}/{to_date}/{from_date}`.
- Output root: `data/acquisition_pilots/runs/upstox-v3-reliance-2025-02-03-5m-v1/`.
- Output artifacts: content-addressed raw response, sanitized request manifest, normalized candles, separate invalid/unresolved quarantines, and a validation report beneath that root.

## Preflight findings

| Requirement | Research-copy evidence | Result |
| --- | --- | --- |
| Read-only V3 endpoint authorization and account entitlement | `docs/ACQUISITION_PILOT_READINESS_V1.md` requires the account owner to confirm the current token entitlement and endpoint authorization. No owner approval or terms evidence is present. | `UNVERIFIED` |
| Intended historical research use | Readiness document requires owner confirmation; supporting terms or entitlement evidence is absent. | `UNVERIFIED` |
| Exact raw-response retention and duration | Pilot JSON says `retention_permission_confirmed: false`; readiness document says to stop if exact raw retention is not permitted or remains unclear. | `UNVERIFIED` |
| Applicable request limits | Local runner is limited to one request and zero retries, but no verified account/API rate-limit evidence is attached. | `UNVERIFIED` |
| RELIANCE key in a dated official instrument-master snapshot | `complete.json` contains a matching RELIANCE/NSE mapping, but its dated official provenance is not established in this copy. It is not accepted as the required snapshot. | `UNVERIFIED` |
| Session date and 75 expected START labels | Pinned NSE CM manifest v2 marks 2025-02-03 as `VERIFIED` regular, 09:15-15:30 IST. The offline validator supports 75 five-minute START labels. | `VERIFIED` locally for calendar validation only |
| Explicit execution authorization | Pilot status remains `BLOCKED_PENDING_SOURCE_AUTHORIZATION_AND_RETENTION_REVIEW`; no exact-request approval attestation exists. | `NOT AUTHORIZED` |

No credentials were inspected or copied. The runner reads `UPSTOX_ACCESS_TOKEN` only from the protected process environment after all preflight checks pass. It never loads `.env` files.

## Fail-closed runner

After prerequisites are documented and execution is explicitly approved, invoke the runner with the isolated venv:

```powershell
./.venv/Scripts/python.exe -m scripts.run_acquisition_pilot_v1
```

Before a request can be made, an owner-reviewed `data/acquisition_pilots/execution_authorization_v1.json` must be supplied using `schemas/acquisition-pilot-authorization-v1.schema.json`. It must cite evidence for source authorization, research use, raw-byte retention and duration, request limits, and the exact single-request authorization. The dated official instrument-master snapshot must be stored in the research copy and its SHA-256 and RELIANCE mapping must match the attestation. Attestation booleans alone do not establish provider terms. Never put credentials in the attestation.

The runner accepts only the fixed pilot configuration. It makes at most one GET with zero retries, rejects redirects, caps the response at 2 MiB and 75 candles, writes only to a new pilot-specific run directory, and refuses an existing output path. Raw response retention is enabled only after the provider-terms attestation passes.

## Current blocker

Required external account authorization, intended-use permission, raw-retention permission, applicable request-limit evidence, and a dated official instrument-master snapshot are absent or unverified. The pilot remains blocked. Do not invoke the runner until each prerequisite is substantiated and the one-request execution is explicitly authorized.
