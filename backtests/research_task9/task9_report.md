# Task 9: Exchange Calendar and Timestamp/Source Audit

Audit date: 2026-10-18  
Workspace: `C:\Users\Priyanka\Desktop\ai-trading-bot-research`  
Mode: read-only inspection. No source row was repaired, deleted, resampled, relabelled, or overwritten.

## Scope and classifications

The audit read the copied main equity file (`data/research/extended_historical_robustness_candles.csv`, 518,570 rows), OOS equity file (`data/research/extended_historical_robustness_oos_candles.csv`, 217,350 rows), and five copied context/index CSVs (262,866 rows). It inspected every weekend row in the two equity datasets, every main-dataset timestamp outside the regular 09:15-15:30 IST clock window, the named SBIN and ITC records, all three malformed rows from the local data audit, all late context records, and all zero-volume context/index records.

**Total distinct data rows investigated: 266,784.** Of these, 3,952 exact records are in `record_findings.csv`; the other 262,832 zero-volume context/index rows are accounted for by exact per-file totals in `index_volume_summary.csv`. There are 28 separate gap events in `gap_intervals.csv`.

| Classification | Rows | Interpretation |
|---|---:|---|
| `VERIFIED_VALID` | 4 | SBIN reference/target bars pass local OHLC, time-alignment, regular-session-clock, and arithmetic checks. This does not independently prove provider-source authenticity. |
| `VERIFIED_INVALID` | 3 | Three rows violate OHLC price ordering. They remain untouched. |
| `UNRESOLVED` | 266,777 | Session windows, provider/source accuracy, or index-volume semantics are not established by the evidence retrieved. |

Classifications apply to the reviewed property, not to the truth of an entire dataset. No strategy profitability conclusion is made.

## Timestamp and session findings

Every row in both equity datasets stores an explicit `+05:30` offset. That is an explicit IST timestamp in the CSV. The five context/index files store explicit `+00:00` offsets; those represent UTC instants in the stored values. `timestamp_profiles.csv` preserves each raw value and displays its IST interpretation separately. The offset representation is established; the upstream provider’s original timestamp convention and transformation history for older context files are not.

The copied downloader code parses fetched timestamps with UTC handling and converts them to `Asia/Kolkata` before writing new files. This documents the current code path, not necessarily the provenance of historical context files. The copied validation report also names an original-checkout absolute path in its metadata; this audit read only the research-copy files, so the report’s metadata is not independent provenance evidence.

The official [NSE Market Timings page](https://www.nseindia.com/market-data/market-timings), marked updated 2025-08-04, lists regular/odd-lot equity trading from 09:15 to 15:30 and a closing session from 15:40 to 16:00. It also identifies pre-open activity before 09:15. This is the normal-session reference, not a dated timetable for every audited year. The 15:35 context labels are outside continuous trading and precede the currently listed closing session; 15:55 falls within that listed closing session. Their actual historical index-data semantics remain unresolved.

The official [NSE 2024 trading-holiday master](https://www.nseindia.com/api/holiday-master?type=trading&year=2024) identifies Saturday 2024-03-02 in the CM segment as **Special Live Trading**. It lists Diwali Laxmi Pujan on 2024-11-01 in some other segments, but the retrieved record does not supply CM Muhurat hours. It does not list 2024-05-18 as a holiday/special-session entry; that omission is not proof the exchange was closed. No primary session timetable was retrieved for 2024-01-20, 2024-05-18, 2025-02-01, or 2026-02-01.

The 168 main-file records outside the regular-session clock window are exactly 14 symbols x 12 bars on 2024-11-01, labelled 18:00 through 18:55 IST. They are plausibly Muhurat-session bars, but the exact official equity-session hours were not retrieved, so all 168 remain `UNRESOLVED` rather than being called valid or invalid.

Weekend rows also occur on 2024-01-20 (1,050 rows), 2024-03-02 (294), 2024-05-18 (294), 2025-02-01 (1,050), and in the OOS file on 2026-02-01 (1,050). The NSE calendar verifies the special-session date for March 2 only; exact in-session ranges for all five blocks remain unresolved. These are itemized in `session_assessments.csv` and `record_findings.csv`.

## Gap findings

All **28** same-date gaps are identical 95-minute intervals, from the 09:55 candle label to the 11:30 label, with 18 five-minute labels absent. This occurs for all 14 symbols on both 2024-03-02 and 2024-05-18. March 2 is independently verified as a special-live-trading date; the exact reason for the break is not verified. The repeated boundary is suggestive of a scheduled split/DR exercise, but no primary interval notice was obtained. The May 18 cause is also unresolved. No missing candle was invented or filled.

## Named suspicious records

- **SBIN, 2024-06-04 11:15 IST:** `open=815.10, high=815.10, low=769.80, close=771.55, volume=2774696, oi=0`. Relative to the immediately prior 11:10 close of 815.10, the five-minute close-to-close move is **-5.342903%**. The 15:25 session close of 781.30 versus the prior session’s 15:25 close of 909.00 is **-14.048405%**. The 11:15 candle is structurally valid and within normal trading hours (`VERIFIED_VALID` for those checks); the locally cached values were not independently reconciled to an official trade record.
- **ITC, 2025-05-28 09:15 IST:** `open=417.7, high=420.2, low=413.0, close=418.7, volume=360164505, oi=0`. The row is structurally valid and in-session, but the unusually large volume has no independent source confirmation (`UNRESOLVED`).
- **ITC, 2025-01-06 09:40, 09:45, 09:50, 09:55 IST:** each row has `open=high=low=close=455.6, volume=0, oi=0`. The local policy retains zero-volume rows; whether these represent no trades, a carried-forward price, or a provider placeholder is unverified (`UNRESOLVED`).
- **Malformed OHLC rows:** BHARTIARTL 2024-06-25 09:15 (`O=1434.00,H=1429.85,L=1422.3,C=1424.55,V=327747`); HDFCBANK same timestamp (`O=835.60,H=843.80,L=837.4,C=843.80,V=1801440`); ICICIBANK same timestamp (`O=1170.25,H=1175.00,L=1171.0,C=1172.40,V=685584`). Each violates `low <= open/close <= high`, so each is `VERIFIED_INVALID` as an OHLC tuple. The existing audit already reports them excluded; no source data was changed.
- **Zero-volume index/context candles:** all 262,866 rows across the five context/index files have `volume=0`; per-file totals and samples are in `index_volume_summary.csv`. Upstox’s published historical-candle schema describes volume generically as traded amount and OI as outstanding derivative contracts, but does not explain index-candle zero-volume semantics. These records remain `UNRESOLVED`; zero alone is not proof of invalidity.
- **Late context records:** there are **34** rows stored with `+00:00` timestamps that convert explicitly to 15:35 or 15:55 IST. They have flat OHLC and zero volume/OI. Full raw rows and interpretations are in `record_findings.csv`. Their exchange-session and vendor meaning remains `UNRESOLVED`.

## Evidence and limitations

The source/evidence table is `source_evidence.csv`. Evidence verified against authoritative sources: NSE’s 2024 calendar identifies March 2 as a CM special-live-trading date; NSE’s published normal-market hours state 09:15-15:30; and Upstox’s historical-candle documentation defines generic candle fields (the page is marked deprecated). Plausible but not independently verified: the exact scheduled reason for the repeated 09:55-11:30 breaks and the possibility that flat index values with zero volume are non-traded index candles. Unresolved: exact special-session intervals for the weekend blocks and Muhurat rows, old context-file provenance, whether ITC’s unusual records reflect provider/source errors, and index-volume semantics.

The NSE circular archive date-filter attempt returned the default 2026 listing despite 2024 query parameters and was not used as evidence. The BSE page fetched exposed current 2026 holiday tables, not historical session hours, and was not used to validate any target date. The Upstox endpoint documentation accessed is [Historical Candle Data](https://upstox.com/developer/api-documentation/get-historical-candle-data/); it is marked deprecated, gives a timestamp example with `+05:30`, and does not establish index-volume behavior.

## Integrity and limits

`input_hashes.csv` records file sizes and before/after SHA-256 hashes for the copied datasets, existing local audits/reports, docs, and downloader code. All 12 inputs were unchanged during the read-only inspection. No original checkout, historical dataset, frozen V1 strategy, threshold, risk setting, or pre-existing report was changed. No backtest, trading application, paper runner, Upstox/OpenRouter request, broker order, live trade, or live market-data request was made. Tests passing or historical results are not evidence of profitability.

## Output files

- `task9_report.md`
- `record_findings.csv`
- `gap_intervals.csv`
- `index_volume_summary.csv`
- `session_assessments.csv`
- `timestamp_profiles.csv`
- `source_evidence.csv`
- `input_hashes.csv`

**Task 9 status: NOT COMPLETE.** Primary evidence is insufficient to classify several session windows and source records. Keep those items `UNRESOLVED`; do not silently correct or use them as validated data.
