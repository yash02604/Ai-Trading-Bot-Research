# NSE CM timestamp and coverage audit — generated outputs

Audit date: 2026-10-19
Scope: the two historical equity candle CSVs only; no strategy code or backtests executed.
Manifest: `data/audits/nse_cm_session_manifest_v1.json`; SHA-256 is recorded in `summary.json`.

## Primary session evidence represented

Annual CM calendars reviewed for December 2023 and calendar years 2024 and 2025; the NSE 2026 CM holiday page was retrieved on 2026-10-19, with subsequent date-specific amendments applied. Refer to the manifest `sources` object for source URLs, identifiers, retrieval dates, and effective dates.

Special sessions verified from NSE circulars:

| Date | Schedule (IST) | Primary evidence |
|---|---|---|
| 2024-01-20 | 09:15–15:30 | NSE/MSD/60340, 2024-01-19; withdraws earlier proposals |
| 2024-03-02 | 09:15–10:00; 11:30–12:30 | NSE/MSD/60677, 2024-02-14 |
| 2024-05-18 | 09:15–10:00; 11:30–12:30 | NSE/MSD/61893, 2024-05-07 |
| 2024-11-01 | 18:00–19:00 | NSE/CMTR/64628, 2024-10-19 |
| 2025-02-01 | 09:15–15:30 | NSE/CMTR/65729, 2024-12-23 |
| 2025-10-21 | 13:45–14:45 | NSE/CMTR/70319, 2025-09-22 |
| 2026-02-01 | 09:15–15:30 | NSE/CMTR/72349, 2026-01-16 |

Additional verified changes: 2024-01-22 CM holiday (NSE/CMTR/60338, referenced in NSE/SURV/60342); 2024-05-20 ad hoc CM holiday (NSE/CMTR/61518); 2026-01-15 amended CM holiday (NSE/CMTR/72260). All URLs and source records are in the manifest.

## Data checks

The runner evaluates both possible five-minute timestamp-label conventions because the historical feed's label semantics are not independently proven. All row classifications below refer only to matching a primary calendar's continuous-market window under that explicit interpretation.

- Main file: 518,570 rows. Under start labels, all observed row labels fall within verified session intervals. Coverage reports 15 instrument-session gaps: all 14 symbols have no candles on 2024-11-20, and ITC is missing 09:15, 09:20, 09:25, and 09:30 on 2025-01-06.
- OOS file: 217,350 rows. Under start labels, all observed row labels fall within verified session intervals and all observed instrument-session coverage is complete.
- Under end-label interpretation, all instrument-session coverage records are incomplete; the row label counts show the end-label assumption does not align as well with the recorded 09:15–15:25 timestamps. This is a comparison, not proof of the provider's intended convention.
- Weekend and special-session rows are assessed against their date-specific intervals. Split-session breaks on March 2 and May 18, 2024 are excluded from expected normal-market candles.

Detailed row and coverage findings are the compressed CSV files in this directory. `input_hashes.json` records SHA-256 and byte sizes of the source CSVs. No input dataset was altered.

## Overall data status

**UNRESOLVED.** Calendar/session evidence does not establish candle-feed authenticity, completeness at source, historical timestamp transformation, or label convention. Task 9's malformed OHLC rows, suspicious price/volume rows, and zero-volume/context findings remain unresolved or invalid as documented there. Corporate-action adjustment and survivorship bias are also unresolved. See `docs/NSE_SESSION_AND_DATA_AUDIT_V1.md` for exclusions and handling rules. No dataset is declared validated.
