# Official NSE calendar evidence and versioned client attestation

Date: 2026-10-30

## 1. Official sources obtained

Retained 13 public official NSE downloads and four unchanged PDF members from two official NSE ZIP packages. No Upstox, DeepSeek, broker, credential, or private-data endpoint was contacted.

The annual calendar is [NSE/CMTR/71775](https://nsearchives.nseindia.com/content/circulars/CMTR71775.pdf), published 2025-12-12. [NSE/CMTR/72260](https://nsearchives.nseindia.com/content/circulars/CMTR72260.pdf), published 2026-01-12, adds the January 15 holiday. [NSE/CMTR/72349](https://nsearchives.nseindia.com/content/circulars/CMTR72349.pdf), published 2026-01-16, establishes the February 1 exceptional live session. These corroborate the already retained historical prefix.

[NSE/CMTR/76941](https://nsearchives.nseindia.com/content/circulars/CMTR76941.pdf), published 2026-10-19, supplies the November 8 Muhurat schedule deferred by the annual circular. The later [NSE/ISC/77098](https://nsearchives.nseindia.com/content/circulars/ISC77098.pdf), published 2026-10-26, adds a UCC compliance cutoff without replacing that schedule.

The amendment investigation also retained:

- [NSE/CMTR/74466 official ZIP](https://nsearchives.nseindia.com/content/circulars/CMTR74466.zip), published 2026-05-29: CAS trading modalities effective 2026-08-03, with a security-specific continuous-session cutoff.
- [NSE/CMTR/76170](https://nsearchives.nseindia.com/content/circulars/CMTR76170.pdf), published 2026-09-03: a derivatives reference-price update; it does not revise CM continuous hours.
- [NSE/CMTR/76963 official ZIP](https://nsearchives.nseindia.com/content/circulars/CMTR76963.zip), published 2026-10-16: transmits a SEBI circular dated 2026-10-15 that revises CAS timing from 2026-12-07. The original NSE wrapper and its annexure are both retained. Its separate February 2027 provisions are not applied.
- Official [holiday page](https://www.nseindia.com/resources/exchange-communication-holidays), [market-timings page](https://www.nseindia.com/market-data/market-timings), public CM holiday metadata, and public circular archive snapshots. The complete returned archive from 2025-12-12 through 2026-10-30 contains 6,301 records. Relevant CM calendar/phase notices were reviewed; mock-session notices do not make a weekend a live trading day.

The generic timing page does not adequately describe the CAS-era security-specific cutoffs, and the holiday page still contains the earlier deferred-timings note. The dated primary circulars control those details. The October 19-30 archive contains no later CMTR replacement for the Muhurat schedule; ISC77098 is a compliance supplement. This is an as-of-retrieval review, not an assurance against future amendments.

## 2. Retained evidence locations and SHA-256 hashes

All source bytes are under `data/evidence/nse_calendar_2026_20261030/`. Original downloads were written without content modification. ZIP members were read by exact member name without executing anything or extracting arbitrary paths. The original ZIP bytes are retained too.

`evidence_registry.json` records every object's official URL, circular number or explicit absence for dynamic snapshots, publication date, UTC retrieval timestamp, SHA-256, segment, applicable dates, source version, and amendment relationship. Dynamic pages/listings do not provide publication dates; their values are null with an explanatory note, rather than invented dates. Each object also has its original `<filename>.retrieval.json` receipt. Derived PDF metadata identifies the original archive/member and archive hash; validation checks that the member bytes actually match the retained ZIP.

Important final pins:

| Object | SHA-256 |
| --- | --- |
| Retained evidence registry | `7b5f72e6d804c4900d52cabc8a6822669cd0f5e04d6d18a2cf176fbae1076b59` |
| New v3 calendar | `4f0524db0b16c2dfedf354704cac7954b14af3936cbbff76f53d1848b4938d03` |
| Current-client attestation record | `7fca59d485bcf44fd4eddfef00f52062ed2f4ea9f980c7653f1361fdde9556ef` |
| Unchanged historical v2 calendar | `5012edfdee4a6db80a531b9c77486448dd305aa641920a6696d9f63a9ea55cbc` |

The source/hash table below is generated directly from the final registry. Original-byte identity, receipt hashes, UTC chronology, and extracted-member identity are checked by the offline validator. Review timestamps cannot predate the retained source retrievals, and publication dates cannot follow retrieval dates.

| Retained object (official source link) | Publication date | Retrieved UTC | SHA-256 |
| --- | --- | --- | --- |
| [CMTR71775.pdf](https://nsearchives.nseindia.com/content/circulars/CMTR71775.pdf) | 2025-12-12 | 2026-10-30T11:16:26.590105+00:00 | `aa97e0afc0ce394097f2fc62631c68e3c2e4c7c23541ed35f21d9fc06b0dcacb` |
| [CMTR72260.pdf](https://nsearchives.nseindia.com/content/circulars/CMTR72260.pdf) | 2026-01-12 | 2026-10-30T11:18:13.803965+00:00 | `c5d32a838b46d5044717b830a4793479a51a40f8e4e19d3896c65682d15b6ebc` |
| [CMTR72349.pdf](https://nsearchives.nseindia.com/content/circulars/CMTR72349.pdf) | 2026-01-16 | 2026-10-30T11:18:14.147033+00:00 | `70458f7b8126f47584d4bc402a78dde357e42dbebaa5d53cfce0c70c82dabfc3` |
| [CMTR76941.pdf](https://nsearchives.nseindia.com/content/circulars/CMTR76941.pdf) | 2026-10-19 | 2026-10-30T11:16:27.118815+00:00 | `ce94ee1c54d98eccdf3c30fa0673ae2b6a49ea4ed4a72fb83d826f4359f71d58` |
| [ISC77098.pdf](https://nsearchives.nseindia.com/content/circulars/ISC77098.pdf) | 2026-10-26 | 2026-10-30T11:22:20.951402+00:00 | `e7b67534fb7a7fe71ff0185a2c436509abf1580da676dd002df2adb78aae4b4d` |
| [nse_holidays.html](https://www.nseindia.com/resources/exchange-communication-holidays) | not supplied | 2026-10-30T11:18:15.356016+00:00 | `161caa7cfd1ed511652b7b6fef47ae915614a0419bf4a80abfe52f1f32a79a2c` |
| [nse_market_timings.html](https://www.nseindia.com/market-data/market-timings) | not supplied | 2026-10-30T11:18:16.299236+00:00 | `57112ea4c6cf6632f06e1640d2a433e1d285c8416a651eb3eb8b740831a66009` |
| [nse_holiday_master_trading.json](https://www.nseindia.com/api/holiday-master?type=trading) | not supplied | 2026-10-30T11:19:09.450144+00:00 | `933cf5dc6dd4efe3b8db8ce6ecd6ecb2582929f5e0b4faffdb88e11cf4efdfbe` |
| [nse_circulars_20261019_20261030.json](https://www.nseindia.com/api/circulars?fromDate=19-10-2026&toDate=30-10-2026) | not supplied | 2026-10-30T11:19:09.944543+00:00 | `8f0d4795d32b550e8d2b45d898edcbd21c2728c4c0b910fcba0001489acc7ff3` |
| [nse_circulars_since_annual_20251212_20261030.json](https://www.nseindia.com/api/circulars?fromDate=12-12-2025&toDate=30-10-2026) | not supplied | 2026-10-30T11:36:18.854203+00:00 | `3f4bc56ccc7a5616a4e69194164022910b203ced9ded613ac0d4687fce62877b` |
| [CMTR76170.pdf](https://nsearchives.nseindia.com/content/circulars/CMTR76170.pdf) | 2026-09-03 | 2026-10-30T11:36:37.736790+00:00 | `0df3137e0dee76bfbe2dd8afd19e992a2bc84c100b95e5c22af72f7df8c86045` |
| [CMTR74466.zip](https://nsearchives.nseindia.com/content/circulars/CMTR74466.zip) | 2026-05-29 | 2026-10-30T11:37:10.950683+00:00 | `11bbe31123cfcb3e8fbd269cc0e2a9f2eed39ca198cb1ff6d41d6bf76b3e4b9d` |
| [CMTR74466-member-0.pdf](https://nsearchives.nseindia.com/content/circulars/CMTR74466.zip) | 2026-05-29 | 2026-10-30T11:37:10.950683+00:00 | `50778523b491d582a7b367c7c7d64aabaefe0bc43eefba16b89364295c71fdbe` |
| [CMTR74466-member-2.pdf](https://nsearchives.nseindia.com/content/circulars/CMTR74466.zip) | 2026-05-29 | 2026-10-30T11:37:10.950683+00:00 | `06648b68cfe39f0ec2348e9124833963a7970dbd7000e6a0347edac34308b45f` |
| [CMTR76963.zip](https://nsearchives.nseindia.com/content/circulars/CMTR76963.zip) | 2026-10-16 | 2026-10-30T11:37:10.507857+00:00 | `efca3ac6aee4bfb755cb3a4c7fab7c9271ed614f5d04df93fcbbac6bf63840f8` |
| [CMTR76963-member-0.pdf](https://nsearchives.nseindia.com/content/circulars/CMTR76963.zip) | 2026-10-16 | 2026-10-30T11:37:10.507857+00:00 | `3bc710fad9fb245b2c7d29a33eaba8ae476bee710b3f1dad969252ef3ce02dd2` |
| [CMTR76963-member-1.pdf](https://nsearchives.nseindia.com/content/circulars/CMTR76963.zip) | 2026-10-15 | 2026-10-30T11:37:10.507857+00:00 | `b2bd9c92fdc8bd0b0076792a64cc087dd921037085ff402065acafbe3590f244` |

## 3. Calendar coverage established

New file: `data/audits/nse_cm_session_manifest_v3.json`.

Version: `nse-cm-2023-12-01_2026-12-31-v3-asof-20261030`.

It contains 1,127 explicit dates from **2023-12-01 through 2026-12-31 inclusive**, using Asia/Kolkata. There are 757 regular dates, 316 closed weekends, 46 holidays, and eight special dates. Dates outside that range fail closed. No 2027 session is generated.

The old v1/v2 files remain byte-for-byte unchanged. V3 binds its historical base to the original v2 hash and its new evidence to retained source hashes. Its extension is not based on an assumption that every weekday is open: the annual CM circular, current CM holiday list, date-specific notices and regular-session evidence are applied together.

| Requested date | Verified classification | Execution treatment |
| --- | --- | --- |
| 2026-10-02 | Holiday | Closed |
| 2026-10-20 | Holiday | Closed |
| 2026-11-08 | Special Muhurat session | Schedule retained; execution blocked as explained below |
| 2026-11-10 | Holiday | Closed |
| 2026-11-24 | Holiday | Closed |
| 2026-12-25 | Holiday | Closed |

The late amendment review found a necessary phase correction, rather than assuming 15:30 is continuous trading for every security:

- From 2026-08-03 through 2026-12-06, v3 uses **09:15-15:15** as the conservative common continuous interval across CAS and non-CAS securities. It does not enable the non-CAS-only later interval without classification evidence.
- From 2026-12-07 through 2026-12-31, the retained later amendment moves CAS to 15:30, so v3 uses the common continuous interval **09:15-15:30**.
- Auction, transition and post-close phases are not ordinary continuous trading and are not simulated.

This explicitly corrects the execution interval of 43 inherited regular dates between 2026-08-03 and 2026-10-01 in the **new v3 copy only**. Each affected entry retains its `prior_v2_normal_market_intervals`, and the manifest records the correction's date range, source and conservative treatment. No old date classification, historical source file, candle dataset or original audit was overwritten or silently reclassified. Older replay artifacts retain their original versions and hashes; new experiments must select v3 explicitly in a fresh configuration.

## 4. Special-session rules

The November 8 circular distinguishes ordinary continuous trading from other phases. V3 retains that distinction:

- Ordinary continuous trading opens at 14:20.
- CAS securities leave continuous trading at 15:15; non-CAS securities have a 15:30 continuous close.
- The special-preopen category for IPO/relisted securities has its own 15:05-15:30 normal-market interval. That does not grant generic early access to those securities.
- Pre-open, special pre-open, block deals, illiquid call auctions, CAS transition/order-entry/matching, and post-close are recorded as excluded phases.
- The later compliance notice gives a UCC cutoff of `2026-11-07T22:00:00+05:30`; no account eligibility was queried or claimed here.

The manifest records the shared 14:20-15:15 interval for reference, but sets `execution_supported: false` for this special date. The current paper engine has no verified per-instrument phase/UCC eligibility and cannot meet its unchanged 35-bar same-session requirement within that short continuous period. `entry_allowed` therefore returns false and `candle_end` rejects the date for execution. Retaining the schedule does not silently enable a phase the engine cannot model.

Regular-session simulated positions use the applicable continuous-session end. Their existing stop, costs, affordability, accepted-entry, missing-candle and overnight protections remain intact. RiskEngine, frozen strategy thresholds, sizing, capital configurability and broker disablement were not changed.

## 5. Historical client provenance

The old audit's Upstox client hash remains:

`59cfb331c2e1c8bece64ff0c256a7f374698b6e1692b3ea5609f7735883e5ac3`.

It exactly matches Git commit `eafb3308c5037730dd6451b98f2d839a472e5a0f` represented with CRLF line endings. A separately retained `historical_client.crlf.py.snapshot` reproduces those bytes. The verifier additionally checks the normalized LF hash and Git blob object identity. The original historical audit and its expected hash were not changed.

The historical audit still declares the historical candle dataset's acquisition provenance UNRESOLVED. Recovering this source version does not establish that it produced those caches and does not turn that dataset into verified live-evaluation evidence.

## 6. Current-client attestation

Separate location: `data/attestations/upstox_client_v1/`.

It contains `attestation.json`, unchanged current-source bytes in `current_client.crlf.py.snapshot`, the recovered historical snapshot, and the complete `reviewed_diff.patch`.

The working client remains unchanged at:

`27ccb06e8777296edfbb2ebc56153dac291e4111d311fa424cc41813e03d9ccb`.

The attestation records exact bytes, 113 CRLF endings, LF-normalized hash, base commit, Git blob identity, modified/uncommitted working-tree state at review, scoped review findings and reviewer-verification requirements. The complete diff was reviewed: it introduces structured authentication/HTTP/rate-limit/transport/JSON diagnostic handling. It does not change endpoint functions, request arguments, retry configuration, quantities, strategy or order placement.

`app/client_attestation.py` verifies separately pinned historical/current snapshots, the original audit hash, the diff hash, line-ending representation, review scope, and unchanged request/retry/endpoint AST invariants. Modified current source, modified historical source, altered attestation metadata, changed diff evidence or a changed original audit all fail closed.

The technical reviewer is recorded as Codex. Independent review approval, source-authentication claims and operational trading approval are explicitly false. A new hash alone is not treated as proof of authorship, review approval, dataset provenance or permission to trade. Reviewers must compare the artifacts/diff, verify the invariants and offline results, preserve the reviewed version, and re-attest later source changes.

The formerly failing preservation test now checks the original historical client bytes against the **original** audit pin and additionally requires the separately reviewed current client. The other original input bindings are still checked, and the before/after immutability assertion around the calendar builder remains. This is not an exception that skips the client, a new expected historical hash, or a rewritten audit. Dedicated tamper tests prove that changing either version is detected.

## 7. Exact files changed

Five Python files were affected: new `app/client_attestation.py`, new `app/session_manifest_v3.py`, the small v3 dispatch/execution-block addition in `app/research/offline_session_calendar.py`, the historical/current evidence split in `tests/test_offline_market_data_v1.py`, and new `tests/test_readiness_evidence.py`.

The new calendar, evidence registry/bytes/receipts, versioned attestation artifacts, and this report are additions. The full task file inventory is appended below; it includes 46 files. Original Upstox source, original v1/v2 calendars, original audit outputs, Sessions 02-05 and their budgets were preserved. Pre-existing unrelated Git changes were retained.

- `app/client_attestation.py`
- `app/research/offline_session_calendar.py`
- `app/session_manifest_v3.py`
- `data/attestations/upstox_client_v1/attestation.json`
- `data/attestations/upstox_client_v1/current_client.crlf.py.snapshot`
- `data/attestations/upstox_client_v1/historical_client.crlf.py.snapshot`
- `data/attestations/upstox_client_v1/reviewed_diff.patch`
- `data/audits/nse_cm_session_manifest_v3.json`
- `data/evidence/nse_calendar_2026_20261030/CMTR71775.pdf`
- `data/evidence/nse_calendar_2026_20261030/CMTR71775.pdf.retrieval.json`
- `data/evidence/nse_calendar_2026_20261030/CMTR72260.pdf`
- `data/evidence/nse_calendar_2026_20261030/CMTR72260.pdf.retrieval.json`
- `data/evidence/nse_calendar_2026_20261030/CMTR72349.pdf`
- `data/evidence/nse_calendar_2026_20261030/CMTR72349.pdf.retrieval.json`
- `data/evidence/nse_calendar_2026_20261030/CMTR74466-member-0.pdf`
- `data/evidence/nse_calendar_2026_20261030/CMTR74466-member-0.pdf.retrieval.json`
- `data/evidence/nse_calendar_2026_20261030/CMTR74466-member-2.pdf`
- `data/evidence/nse_calendar_2026_20261030/CMTR74466-member-2.pdf.retrieval.json`
- `data/evidence/nse_calendar_2026_20261030/CMTR74466.zip`
- `data/evidence/nse_calendar_2026_20261030/CMTR74466.zip.retrieval.json`
- `data/evidence/nse_calendar_2026_20261030/CMTR76170.pdf`
- `data/evidence/nse_calendar_2026_20261030/CMTR76170.pdf.retrieval.json`
- `data/evidence/nse_calendar_2026_20261030/CMTR76941.pdf`
- `data/evidence/nse_calendar_2026_20261030/CMTR76941.pdf.retrieval.json`
- `data/evidence/nse_calendar_2026_20261030/CMTR76963-member-0.pdf`
- `data/evidence/nse_calendar_2026_20261030/CMTR76963-member-0.pdf.retrieval.json`
- `data/evidence/nse_calendar_2026_20261030/CMTR76963-member-1.pdf`
- `data/evidence/nse_calendar_2026_20261030/CMTR76963-member-1.pdf.retrieval.json`
- `data/evidence/nse_calendar_2026_20261030/CMTR76963.zip`
- `data/evidence/nse_calendar_2026_20261030/CMTR76963.zip.retrieval.json`
- `data/evidence/nse_calendar_2026_20261030/evidence_registry.json`
- `data/evidence/nse_calendar_2026_20261030/ISC77098.pdf`
- `data/evidence/nse_calendar_2026_20261030/ISC77098.pdf.retrieval.json`
- `data/evidence/nse_calendar_2026_20261030/nse_circulars_20261019_20261030.json`
- `data/evidence/nse_calendar_2026_20261030/nse_circulars_20261019_20261030.json.retrieval.json`
- `data/evidence/nse_calendar_2026_20261030/nse_circulars_since_annual_20251212_20261030.json`
- `data/evidence/nse_calendar_2026_20261030/nse_circulars_since_annual_20251212_20261030.json.retrieval.json`
- `data/evidence/nse_calendar_2026_20261030/nse_holiday_master_trading.json`
- `data/evidence/nse_calendar_2026_20261030/nse_holiday_master_trading.json.retrieval.json`
- `data/evidence/nse_calendar_2026_20261030/nse_holidays.html`
- `data/evidence/nse_calendar_2026_20261030/nse_holidays.html.retrieval.json`
- `data/evidence/nse_calendar_2026_20261030/nse_market_timings.html`
- `data/evidence/nse_calendar_2026_20261030/nse_market_timings.html.retrieval.json`
- `docs/OFFICIAL_NSE_CALENDAR_CLIENT_ATTESTATION_REPORT.md`
- `tests/test_offline_market_data_v1.py`
- `tests/test_readiness_evidence.py`

## 8. Tests passed and failed

Final full offline command, with no exclusions or expected failures:

```powershell
.venv\Scripts\python.exe -B -m pytest tests/test_readiness_evidence.py tests/test_offline_market_data_v1.py tests/test_ai_led_replay.py tests/test_ai_led_offline.py tests/test_deepseek_prospective.py tests/test_deepseek_prospective_summary.py tests/test_cost_model_execution_integrity.py tests/test_core.py tests/test_deepseek_eval_v1.py tests/test_ai_v2.py tests/test_ai_v2_evaluation.py tests/test_provenance_audit.py -q -p no:cacheprovider --basetemp C:\Users\Priyanka\.codex\visualizations\2026\10\30\01a18b43-af58-74b1-ad68-35a667601200\.pytest-nse-attestation-verified
```

Result: **319 passed in 57.97 seconds**, exit 0. Zero failed, zero excluded. This includes the previously failing `test_manifest_builder_preserves_v1_and_prior_artifacts`, calendar tests, replay tests, prospective regressions, core/risk/cost tests and provenance tests.

The focused readiness/provenance command also passed **21 tests in 7.13 seconds** using scratch suffix `.pytest-nse-attestation-cas-focus`; those tests overlap the full run and are not an additional independent sample.

Five affected Python files passed in-memory compilation and targeted whitespace checks. Full task diffs were inspected against pre-task copies or empty files for additions because these files are untracked in the existing working tree. No-index diff exit 1 denotes content differences; whitespace diagnostics were required to be empty. No unrelated whitespace cleanup was performed.

Preservation checks verified all 97 protected original files unchanged, including all 22 Session 02-05 files, original calendar/audit records, the replay evidence bundle, and the current Upstox client. No Session 06, Upstox/DeepSeek request, market-data capture, provider-token spend, broker order or historical-live evaluation occurred. Only authorized public NSE evidence requests were made.

## 9. Remaining evidence gaps

- This is an as-of-2026-10-30 calendar. Later exchange amendments must be reviewed before future operational use; the calendar does not promise that no later notice will be issued.
- Muhurat execution remains deliberately unsupported without security-class/account eligibility evidence and sufficient valid candles. Its schedule is verified separately from execution permission.
- Dynamic official snapshots have no provided publication date. Their UTC retrieval timestamp, version, exact original bytes and explicit unknown publication date are retained.
- Source attestations establish reviewed byte identity and structural invariants, not independent authorship authentication. Exact CRLF bytes matter; a checkout transformation or subsequent source edit requires a new reviewed attestation.
- Historical dataset acquisition/adjustment/timestamp provenance remains unresolved in the untouched old audit. It is not repaired by this source-version attestation and must not be presented as authentic historical AI performance.

## 10. Readiness for prospective DeepSeek paper trading

The two technical evidence blockers are addressed for a **new, explicitly v3-configured regular-session paper experiment**: year-end calendar evidence is retained and source-verified, and historical/current client identity is separately verifiable. Offline validation is GO under those constraints.

Starting an unattended AI-led prospective run remains **NO-GO in the current saved-response-only pipeline**. The new attestation and calendar are not yet mandatory pre-request guards in a live-data/DeepSeek AI-led adapter. This task did not add that adapter, prediction features, provider authorization or requests. Existing experiments must not silently adopt the new source/calendar configuration. Muhurat execution remains blocked.

## 11. Broker sandbox readiness

**NO-GO.** No broker executor, balance fetching or order functionality was introduced. Broker funds/position reconciliation, idempotent orders, partial fills and operational execution controls remain separate work. The attestation explicitly grants no trading approval. Capital remains configurable; no INR 1,000 application maximum was added.

## 12. Recommended next step

In a separate prospective-paper integration task, require both the pinned current-client attestation and an explicitly chosen v3 calendar before any market-data/provider reservation or request. Bind their versions to a fresh AI-led experiment configuration and retain point-in-time request/response lineage. Preserve every existing RiskEngine, cost, entry, cash, quota and paper-only check. Then perform separately authorized, tightly budgeted prospective paper validation on an allowed regular session. Do not start Session 06 or enable orders as a side effect of this evidence task.
