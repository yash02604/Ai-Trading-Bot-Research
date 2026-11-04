# Provenance and exchange calendar readiness review

Date: 2026-10-30

## 1. Provenance root cause

The old provenance audit records the bytes of an earlier Upstox client. The current working client contains the subsequent structured diagnostic implementation. The mismatch is real and reproducible, rather than an unexplained hash difference.

| Evidence | SHA-256 |
| --- | --- |
| Client expected by `backtests/provenance_integrity_v1/input_hashes.json` | `59cfb331c2e1c8bece64ff0c256a7f374698b6e1692b3ea5609f7735883e5ac3` |
| Git HEAD client reconstructed with CRLF line endings | `59cfb331c2e1c8bece64ff0c256a7f374698b6e1692b3ea5609f7735883e5ac3` |
| Git HEAD client with LF line endings | `f8bd383a622c3694ca93378fde01f2eb5e728264597eb21c04f1d31876f93188` |
| Current working client, original CRLF bytes | `27ccb06e8777296edfbb2ebc56153dac291e4111d311fa424cc41813e03d9ccb` |
| Current working client normalized to LF | `e52b2b85eeb8aedeb881dd25f314e5902bbe3f29378f3956036132838daa232b` |

The committed source is available at Git commit `eafb3308c5037730dd6451b98f2d839a472e5a0f` (2026-10-18). Converting that exact blob to Windows CRLF reproduces the old audit hash. The current file has 113 CRLF lines; the historical blob has 76 lines. Normalizing both to LF still produces different hashes, proving this is not merely a line-ending issue.

The reviewed Git diff adds `UpstoxRequestError`, safe authentication/HTTP/rate-limit/transport/JSON classifications, and exception wrapping. It changes the authentication exception contract from `PermissionError` to `UpstoxRequestError`. The diff does not change the endpoint functions, retry configuration, request method, or order functionality. These changes are consistent with the diagnostic work described in the existing project history. Uncommitted working-tree changes do not independently prove their author or execution history.

Only **one of the audit's 38 input hashes** differs from the current files: `app/upstox_client.py`. The recorded historical source can be recovered exactly; the audit record itself does not need to be rewritten.

Distinctions:

- **Modified source with historical provenance:** established. The current client is not the source byte sequence pinned by the old audit.
- **Incorrect test expectation:** the assertion correctly detects current-source drift. It is a broad historical-binding precondition inside a calendar-builder preservation test, so it fails before testing the builder. Treating it as an irrelevant assertion and deleting or suppressing it would hide the mismatch.
- **Incomplete evidence:** the old audit still declares overall dataset provenance `UNRESOLVED` and historical live evaluation disabled. Recovering the old source does not establish that it produced historical candle caches or resolve their acquisition lineage.
- **Actual integrity problem:** there is no unexplained byte corruption demonstrated by this mismatch. There is a genuine incompatibility between the current source and the old audit's current-file binding. The old audit cannot attest to the updated client.

## 2. Exact calendar coverage

The current file is `data/audits/nse_cm_session_manifest_v2.json`:

- Version: `nse-cm-2023-12-01_2026-10-01-v2`, schema 2.
- Coverage: **2023-12-01 through 2026-10-01 inclusive**, in `Asia/Kolkata`.
- SHA-256: `5012edfdee4a6db80a531b9c77486448dd305aa641920a6696d9f63a9ea55cbc`.
- There are 1,036 explicit date records, all labeled VERIFIED by the local manifest: 697 REGULAR, 291 WEEKEND_CLOSED, 41 HOLIDAY, and 7 SPECIAL.

Those labels describe the retained local manifest; this task did not independently authenticate its original external sources. Its `NSE_2026` source and regular `NSE_TIMINGS` source both explicitly end their effective ranges on **2026-10-01**. There are no records covering 2026-10-02 onward. In particular, this calendar cannot authorize a session on 2026-10-30.

The existing adapter already requires timezone-aware timestamps, normalizes to Asia/Kolkata, rejects absent/unresolved dates, checks source references and effective dates, rejects holidays and closed weekends, and permits an exceptional weekend only when an explicit verified special-session entry exists. Regular and special intervals govern candle completion, entry eligibility, and observed session-end settlement. It does not infer that every weekday is open.

The seven locally recorded special dates are 2024-01-20, 2024-03-02, 2024-05-18, 2024-11-01, 2025-02-01, 2025-10-21, and 2026-02-01. Split-session gaps and short special sessions remain subject to existing candle-contiguity and 35-bar requirements.

## 3. Official calendar evidence used

The following official URLs are already referenced in the local manifest. They identify the appropriate primary-source categories; they were **not fetched or newly verified during this task**:

- [NSE exchange communication holidays](https://www.nseindia.com/resources/exchange-communication-holidays): local source `NSE_2026`, recorded effective range 2026-01-01/2026-10-01.
- [NSE market timings](https://www.nseindia.com/market-data/market-timings): local source `NSE_TIMINGS`, recorded regular CM hours 09:15-15:30, effective range ending 2026-10-01.
- [NSE/CMTR/64960](https://nsearchives.nseindia.com/content/circulars/CMTR64960.pdf): the retained correction for the 2024-11-20 trading holiday.
- [NSE/CMTR/64628](https://nsearchives.nseindia.com/content/circulars/CMTR64628.pdf): the retained 2024-11-01 Muhurat session reference.
- [NSE/MSD/60677](https://nsearchives.nseindia.com/content/circulars/MSD60677.pdf): the retained 2024-03-02 split-session reference.
- [NSE/CMTR/72260](https://nsearchives.nseindia.com/content/circulars/CMTR72260.pdf) and [NSE/CMTR/72349](https://nsearchives.nseindia.com/content/circulars/CMTR72349.pdf): date-specific 2026 amendments already referenced locally.

No retained official circular or page capture was found locally that establishes the missing post-2026-10-01 dates. The November 2024 correction metadata itself records `source_content_sha256: null` and states that its PDF was not downloaded or hashed. The presence of a URL is not a retained source body or proof of later coverage.

Extending coverage requires the applicable official CM holiday schedule, subsequent date-specific amendments and special-session announcements, and market-hour evidence for the requested dates, with retained source content and version/hash tracking. No holiday, session interval, or weekday classification was invented here.

## 4. Changes implemented

No executable correction was made. The missing official evidence triggers the task's instruction to stop rather than guess. The historical source assertion was not edited, skipped, converted to an expected failure, or bypassed.

The narrow future provenance remedy is to retain the existing audit and recovered historical-source identity, review the current diagnostic client separately, and create a new versioned source-provenance record in a fresh location. Any later separation of builder-preservation tests from historical-source binding must retain an explicit drift check. Simply replacing the old hash with the current hash would conceal the distinction and is not justified.

The calendar's existing rejection outside its verified range remains the correct behavior until new evidence is supplied. No new AI feature, strategy threshold, risk limit, cost setting, capital limit, or broker path was introduced.

## 5. Files changed

Only this new report was added:

- `docs/PROVENANCE_CALENDAR_READINESS_REVIEW.md`

No source, test, manifest, session, budget, dataset, previous report, or original provenance record was modified. The replay artifact manifest was checked: all 64 listed SHA-256 bindings match. Preservation checks cover 102 existing files, including all 22 Session 02-05 files, the old provenance audit, the replay evidence bundle, and inspected source/calendar files.

## 6. Test results

The previously excluded assertion was included in the following full, unfiltered offline command:

```powershell
.venv\Scripts\python.exe -B -m pytest tests/test_ai_led_replay.py tests/test_ai_led_offline.py tests/test_deepseek_prospective.py tests/test_deepseek_prospective_summary.py tests/test_cost_model_execution_integrity.py tests/test_core.py tests/test_deepseek_eval_v1.py tests/test_ai_v2.py tests/test_ai_v2_evaluation.py tests/test_offline_market_data_v1.py -q -p no:cacheprovider --basetemp C:\Users\Priyanka\.codex\visualizations\2026\10\30\01a18b43-af58-74b1-ad68-35a667601200\.pytest-provenance-calendar-review
```

Result: **291 passed, 1 failed in 64.59 seconds**, exit 1. No tests were deselected or marked expected-failure. The failing test is `tests/test_offline_market_data_v1.py::test_manifest_builder_preserves_v1_and_prior_artifacts`, at line 96, before the builder call. All other replay, calendar, account, prospective, core/risk, and cost tests in this command passed.

In-memory Python compilation passed for the inspected client, calendar, replay, account, risk, cost, manifest-validation, and related test modules. Targeted whitespace checks passed for the inspected client diff and this new report. No unrelated files were edited to make checks pass.

The historical-source identity was reproduced without executing the old client:

```python
blob = subprocess.run(
    ["git", "show", "HEAD:app/upstox_client.py"], capture_output=True, check=True
).stdout
historical_bytes = blob.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
# sha256(historical_bytes) == the original audit's 59cf... hash
```

No Upstox or DeepSeek request, market-data capture, broker order, Session 06, or historical-live evaluation occurred. Tests use their existing offline mocks; no credential values were printed.

## 7. Remaining failures

1. The current Upstox client does not match the historical audit's source binding. The failure remains visible and must be resolved through reviewed versioned provenance, not a rewritten historical pin.
2. Calendar evidence and effective coverage stop on 2026-10-01. No current prospective trading day can be approved using this manifest.
3. The historical dataset's original acquisition provenance remains UNRESOLVED; recovering one source file does not repair that broader evidence gap.

## 8. DeepSeek prospective readiness

**NO-GO for trustworthy current-market prospective AI paper trading.** Offline execution tests pass apart from the retained provenance assertion, but current-date session evidence and reviewed source-version provenance are incomplete. No provider call was authorized or made to compensate for these gaps.

## 9. Broker sandbox readiness

**NO-GO.** No broker executor exists in the offline path. Broker funds/position reconciliation, order idempotency, partial-fill handling, acknowledgements, and operational execution controls remain outside this task. Broker orders remain disabled and capital remains configurable.

## 10. Recommended next step

Supply retained official NSE CM calendar evidence for the intended prospective dates, including relevant amendments and special-session times. Separately review and preserve a versioned attestation of the current client alongside the recovered historical source identity. Retain all original manifests and audit outputs, then build a new calendar version and current-source audit in fresh locations and rerun the unfiltered command above.

Until that evidence is available, continue only within the existing calendar's covered offline replay dates. No current-market screening or Session 06 should start on the strength of mock performance or a substituted provenance hash.
