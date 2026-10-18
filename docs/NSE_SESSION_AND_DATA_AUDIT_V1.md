# NSE Capital Market session and candle audit (v1)

This is a calendar/session audit, not a strategy evaluation. It makes no claim that the local data feed is authentic, complete at source, correctly adjusted for corporate actions, or survivorship-free.

## Reproduction

From the research-copy root, run:

```powershell
.\.venv\Scripts\python.exe scripts\run_nse_session_audit.py
.\.venv\Scripts\python.exe -m pytest tests\test_nse_session_audit.py -q
```

The script reads the two historical equity CSVs and writes only under `backtests/data_validation_v1/`. It hashes inputs and emits row/session and expected-coverage findings. Existing datasets, Task 9, reports, V1, and prior outputs are inputs/read-only.

## Manifest model and limitations

`data/audits/nse_cm_session_manifest_v1.json` enumerates every calendar date from 2023-12-01 through 2026-10-01. It cites primary NSE annual Capital Market holiday circulars, later date-specific amendments, and date-specific session circulars. Weekly closure is applied only as a default rule documented with NSE’s market timings page; exceptions require explicit circular entries. Unreferenced dates fail closed. The 2026 annual holiday source is the NSE live holiday page retrieved 2026-10-19; because it is a live page, the retrieval date is part of the manifest.

Normal-market windows are represented as half-open intervals in IST. Opening/closing auctions and post-close sessions are excluded from continuous-market expected candle coverage. For a 5-minute candle, a `start` label emits times at interval start through end-minus-five; an `end` label emits start-plus-five through interval end. The current historical files do not have verified feed provenance or label semantics; the reproducible audit therefore runs both conventions and keeps the underlying provenance unresolved. No convention is silently presumed in the library API.

## Verified primary evidence included

- NSE/CMTR/54757 (2022-12-08): calendar-year 2023 CM holiday schedule.
- NSE/CMTR/59722 (2023-12-12): 2024 CM holidays; Muhurat time deferred.
- NSE/CMTR/60338 (2024-01-19): amended Jan 22, 2024 trading holiday (verified via NSE follow-on circular NSE/SURV/60342 and official exchange reference; source URL in manifest).
- NSE/MSD/60340 (2024-01-19): Jan 20, 2024 full normal-hours special Saturday live session; withdraws earlier timing notices.
- NSE/MSD/60677 (2024-02-14): Mar 2, 2024 two CM sessions 09:15–10:00 and 11:30–12:30.
- NSE/CMTR/61518 (2024-04-08): May 20, 2024 ad hoc holiday.
- NSE/MSD/61893 (2024-05-07): May 18, 2024 two CM sessions 09:15–10:00 and 11:30–12:30.
- NSE/CMTR/64628 (2024-10-19): Nov 1, 2024 Muhurat normal market 18:00–19:00.
- NSE/CMTR/65587 (2024-12-13): 2025 CM holiday calendar.
- NSE/CMTR/65729 (2024-12-23): Feb 1, 2025 Saturday full normal-hours session.
- NSE/CMTR/70319 (2025-09-22): Oct 21, 2025 Muhurat 13:45–14:45.
- NSE 2026 CM holiday page (retrieved 2026-10-19), plus NSE/CMTR/72260 (2026-01-12) ad hoc Jan 15 holiday and NSE/CMTR/72349 (2026-01-16) Feb 1 Sunday full normal-hours session.

The 2024 weekend rows in the data map to dates with session circulars; the two split sessions explain the 09:55 to 11:30 absence as a scheduled break, but do not validate individual candles. 2025-10-21 has a manifest schedule even if no copied row exists.

## Classifications

`VERIFIED_VALID` and `VERIFIED_INVALID` classify only the timestamp/session/expected-coverage tests. They cannot establish underlying trade-feed authenticity. An overall dataset finding remains `UNRESOLVED` until data provenance and the quality issues below are resolved. An absent/unverified date, invalid timestamp, or absent label convention is `UNRESOLVED`, never inferred from weekday/clock.

## Data-quality policy and unresolved issues

- **Malformed candles:** preserve source files. Structural OHLC violations are `VERIFIED_INVALID` for that tuple and must be excluded from any later analysis with row-level reason.
- **Gaps:** report every missing expected label. Known split-session breaks are expected; missing labels within a verified interval are invalid coverage. Never fill or resample gaps.
- **Duplicate labels:** mark all rows for the duplicate instrument/timestamp invalid; do not select a winner.
- **Provider provenance:** no immutable provider response, request parameters, instrument master snapshot, or end-to-end transformation provenance exists for the historical files. Calendar validation does not authenticate prices or volumes.
- **Corporate actions:** split/bonus/dividend adjustment status and adjustment factors are unverified; no automatic adjustment or cross-action comparison is endorsed.
- **Survivorship bias:** historical universe membership/delistings/constituent history is not established; the current symbol set may bias historical studies.
- **Zero volume:** retained by existing policy; interpretation (no trades, carried-forward close, index semantic, or placeholder) remains unresolved. Do not convert zero to missing or use it as proof of validity.
- **Suspicious records:** SBIN 2024-06-04 abrupt move; ITC 2025-05-28 high volume; ITC 2025-01-06 flat zero-volume candles; zero-volume index/context bars; and 15:35/15:55 IST context labels remain unresolved pending source reconciliation.
- **Conservative exclusion for any later study:** exclude unresolved dates, malformed rows, duplicate keys, out-of-session rows, incomplete interval-days, unresolved special-session records, all zero-volume rows where price-carried semantics matter, all suspicious named rows and their dependent windows, unresolved index/context bars, and any security/date affected by unverified corporate actions. Do not represent excluded rows as repaired or validated.

## Session validation versus calendar completeness

This calendar has primary references for the dates listed and year-level sources for each represented calendar year. However, the annual holiday master does not prove the absence of every unlisted emergency suspension. Any newly discovered circular requires a new manifest version. Session validity does not settle candle-label convention, vendor completeness, corporate actions, universe bias, or whether data values reflect exchange trades.
