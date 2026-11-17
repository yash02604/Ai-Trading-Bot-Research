# Legacy CSV encoding compatibility remediation

## 1. Verdict

**REMEDIATION VERIFIED** — the focused suite, six unchanged original audit probes and complete 1,869-case offline suite pass. The final preservation comparison confirms only the authorized logger edit, new test and this report. This is remediation verification, not independent post-remediation certification or trading authorization.

Scope is the LOW legacy CSV encoding regression only. The encoding policy uses the user-authorized rejection option (Option B). Existing P2 candle validation and P3 string literalization are preserved.

## 2. Root cause

The original Windows logger opened the legacy CSV in text append mode without an encoding. On the verified host (Python 3.13.2, UTF-8 mode off) that meant CP1252. The later logger opened the same path with explicit UTF-8, without examining existing bytes. Ordinary pre-upgrade and post-upgrade synthetic paper closures with reason `café` produced old `63 61 66 e9` and new `63 61 66 c3 a9` bytes in one file. UTF-8 decoding failed; CP1252 decoding returned `café` and `cafÃ©`. This was an export compatibility regression, not demonstrated financial-ledger corruption.

The independent expected-behavior failure and escaped-Unicode evidence remain unchanged externally in `v22-independent-remediation-verification/test_legacy_encoding_regression.py` and `legacy-encoding-evidence.json`. No real legacy CSV was used. The present repository regression tests the explicitly authorized safe alternative: refuse the append and preserve the old file exactly.

## 3. Chosen encoding policy

| Input state | Deterministic behavior |
|---|---|
| New file | UTF-8 without BOM; normal legacy header and one row, using the CSV writer's CRLF record terminator |
| Empty existing file | Same header-plus-row policy as a new file |
| Existing UTF-8-compatible current-format CSV | Decode all existing bytes strictly; verify the expected 23-column header and final CR/LF record boundary; append only pre-encoded UTF-8 bytes |
| UTF-8 BOM at file start | Accept using strict `utf-8-sig` decoding for inspection; preserve the BOM and every existing byte |
| Header-only file with its normal terminator | Append a row without duplicating the header |
| ASCII-only existing current-format CSV | Treat ASCII as encoding-neutral; adopt UTF-8 for appends, independent of locale |
| Existing CP1252-only bytes, including historical `café` | Raise `ValueError` before any write, requiring explicit encoding review; even an ASCII-only new row is refused |
| Invalid UTF-8 / unsupported encoding | Refuse append; do not guess, transcode, replace or discard bytes |
| Foreign/headerless file or missing final record separator | Refuse append without attempting repair |
| New Unicode outside CP1252, such as `₹` | Supported in new/compatible UTF-8 files; an incompatible existing file is still refused |
| Unpaired surrogate or otherwise unencodable row | Strict UTF-8 encoding raises `UnicodeEncodeError` before the destination is opened or created |

This is a **byte-compatibility policy**, not a detector of historical intent. Some byte sequences can be interpreted as either CP1252 or UTF-8. Passing strict UTF-8 validation does not prove which interpretation a historical producer intended; the append contract adopts the UTF-8 interpretation of compatible project-format bytes without rewriting them. Known alternative-encoding provenance requires separate review before use. No third-party detector, locale fallback, CP1252 conversion or lossy encoding is used.

The header/record-boundary checks recognize this logger's format and avoid concatenating onto an incomplete final boundary. They are not a complete CSV content-integrity audit or recovery system.

## 4. Why mixed encoding cannot occur

Under the supported exclusive-writer ownership of the legacy log path, an accepted operation concatenates strictly UTF-8-compatible prior bytes and strictly UTF-8-encoded new bytes. ASCII is a subset of UTF-8. A prior CP1252-only sequence fails validation before append. Both new and existing-file behavior are independent of the Windows code page.

The same binary append handle is used for inspection and writing, avoiding a check-close-reopen path replacement window. No existing prefix is rewritten. Binary mode also avoids platform text-mode newline translation; `csv.DictWriter` serializes into `StringIO(newline="")` using the same CRLF records as the previous `newline=""` writer.

The function does not coordinate with concurrent foreign/old-version writers or filesystem mutation. Exclusive writer ownership is required; no cross-process locking or broader logging redesign is claimed.

## 5. Formula-injection preservation

`sanitize_csv_cell_for_spreadsheet` remains unchanged. All 23 exported fields pass through it before serialization. Strings whose first effective character is `=`, `+`, `-` or `@`, after supported leading whitespace/control/format characters, retain their apostrophe prefix. Original string contents remain after that prefix.

Actual numeric values, including negative P&L, remain numeric inputs to the writer. Input dictionaries and authoritative values are not modified. Decision JSONL behavior and `PaperTrader` remain unchanged. UTF-8 compatibility checks do not bypass sanitization or reinterpret string values in the domain layer.

The existing 156 CSV security tests pass. New tests combine formula strings with `café`, `₹`, whitespace, BOM-format characters, commas, quotes and line breaks. No spreadsheet application or formula evaluation was launched; verified conclusions concern CSV bytes and decoded cell values.

## 6. Failure atomicity

The complete sanitized row is serialized and strictly encoded in memory before the destination is opened. Thus an encoding failure cannot leave even a new header-only file. Existing-file validation completes before the only `file.write` call. Failed compatibility, header or final-boundary checks leave every existing byte unchanged, including an empty file.

There is no `errors="ignore"`, `errors="replace"`, replacement-character insertion, post-write cleanup, automatic migration or historical transcoding. A non-UTF-8 file is never selectively appended using another guessed code page.

This atomicity claim is limited to serialization/encoding/preflight rejection. It is not a durable transaction or rollback guarantee after an OS write, disk-full condition, process termination or physical power loss. Existing caller error propagation is preserved; `PaperTrader.close_position` already stages its in-memory trade before invoking the logger, so this patch does not claim transactionality for caller state on logging exceptions.

## 7. Tests added

`tests/test_trade_logger_encoding.py` adds **32 cases**:

- Seven repeated CP1252-refusal cases, including ASCII, `café`, `₹` and formula-like strings, with exact byte and input-value preservation assertions.
- Six new/empty/header-only/BOM/ASCII/UTF-8 starting states, each receiving repeated ASCII, accented, non-CP1252, quoted, comma-containing, multiline and empty values.
- Nine formula-plus-Unicode interactions on an existing UTF-8 file, preserving negative numeric P&L and the original trade dictionary.
- Six invalid/unsupported/foreign/headerless/unterminated-file cases, requiring refusal with identical original bytes.
- Three unpaired-surrogate cases covering absent, empty and populated destinations, proving no header or partial row is written.
- One ordinary synthetic `PaperTrader` closure against a legacy CP1252 file, proving the production caller receives the explicit rejection and the export remains byte-identical.

The tests use only isolated synthetic logs. Existing project tests, original external audit probes and previous audit reports were not edited, skipped, xfailed or weakened.

## 8. Focused test results

All commands ran from `C:\Users\Priyanka\Desktop\ai-trading-bot-research`:

```powershell
$Python = 'C:\Users\Priyanka\Desktop\ai-trading-bot-research\.venv\Scripts\python.exe'
$Base = 'C:\Users\Priyanka\.codex\visualizations\2026\11\05\01a1acdb-980d-7dd2-b495-2691796e9d33'
$Audit = "$Base\phase1-v22-audit"
$Run = "$Base\legacy-csv-encoding-remediation"

& $Python -B "$Audit\run_offline.py" tests/test_trade_logger_encoding.py -q -p no:cacheprovider --basetemp "$Run\red-tmp" --junitxml "$Run\red.xml" --tb=short
& $Python -B "$Audit\run_offline.py" tests/test_trade_logger_encoding.py tests/test_trade_logger_csv.py tests/test_core.py tests/test_candle_validation_isolation.py -q -p no:cacheprovider --basetemp "$Run\focused-tmp" --junitxml "$Run\focused.xml" --tb=short
& $Python -B "$Audit\run_offline.py" "$Audit\test_independent_row_isolation.py" "$Audit\test_independent_correctness.py" -q -p no:cacheprovider --basetemp "$Run\original-tmp" --junitxml "$Run\original.xml" --tb=short
git diff --check -- app/trade_logger.py
```

| Run | Passed | Failed | Errors | Skipped | Deselected | JUnit seconds | Console seconds | Exit |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| New encoding matrix before fix | 16 | 16 | 0 | 0 | 0 | 4.681 | 4.69 | 1 |
| Focused encoding/security/core/candle suite | 252 | 0 | 0 | 0 | 0 | 10.472 | 10.52 | 0 |
| Unchanged original audit probes | 6 | 0 | 0 | 0 | 0 | 17.048 | 17.05 | 0 |

The pre-fix failures reproduced blind legacy append, empty-file header omission and header creation before encoding failure. No assertion was weakened after observing failures. Syntax/import behavior is exercised by these real module tests; diff whitespace verification passed.

A fresh read-only Caveman reviewer inspected the encoding function, direct caller and new tests. It reported no concrete actionable encoding, lossy conversion, encoding-failure partial-write or formula-sanitization defect within the supported single-writer use. The reviewer did not execute tests and is not presented as independent runtime certification. It identified the limitations recorded in sections 3, 4 and 6; no unrelated refactoring was performed.

The offline launcher disables dotenv loading before app imports, clears credential-like environment variables, denies outbound socket and DNS access, disables bytecode and pytest cache. Existing fixtures redirect real log/state defaults and require mocked transports. New fixtures and all run artifacts are outside real log/state paths. No broker/provider/market request or trading experiment was made. These Python guards are not an OS-wide isolation claim.

## 9. Full-suite result

The complete unfiltered offline suite was actually rerun. Final process exit, stdout, JUnit and command metadata were read and checked after completion.

| Passed | Failed | Errors | Skipped | Deselected | JUnit seconds | Console seconds | Driver seconds | Exit |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1869 | 0 | 0 | 0 | 0 | 1471.980 | 1472.40 | 1476.1128105 | 0 |

JUnit contains exactly 1,869 test cases: the prior 1,837 plus 32 new encoding cases. No prior test disappeared, was excluded, skipped or xfailed. There were no production or test edits during this final run. No `-k`, `-m` or test-path filter was used.

```powershell
& $Python -B "$Run\run_full.py"
# Capture driver executes this exact unfiltered test command:
& $Python -B "$Audit\run_offline.py" -q -p no:cacheprovider --basetemp "$Run\full-tmp" --junitxml "$Run\full.xml"
```

Exact argv/cwd/start/end/exit/wall time are retained in external `full-metadata.json`; stdout and JUnit are in `full.log` and `full.xml`. This completed run supplies the verification; previous passing runs are not substituted for it.

The completed full run passed 46 V1 causal cases, 104 V2 cases, 43 recovery cases, 78 durability cases, 55 complete-seal cases and 34 torn-seal cases. They cover current-equity risk, exact ancestry and sequence checks, quarantine, deterministic replay, exactly-once reconstruction, torn-tail/seal handling, complete-seal identity and strict duplicate-key rejection in the relevant seal readers. All 40 candle-isolation and 156 existing CSV security cases also passed. No V2.2 settlement, risk, cost, calendar or candle-validation production file changed. This logger file is not part of V2.2's source-fingerprint list, so this encoding patch does not create another V2.2 source-identity change.

## 10. Files changed

Production modification:

- `app/trade_logger.py` — pre-encode complete rows, inspect existing bytes on the append handle, refuse incompatible files, and handle empty destinations.

New test file:

- `tests/test_trade_logger_encoding.py` — the 32-case compatibility and no-loss matrix.

New documentation:

- `docs/LEGACY_CSV_ENCODING_COMPATIBILITY_REMEDIATION.md` — this report.

No other repository change is authorized by this task. The pre-existing dirty working tree was retained. External snapshots, drivers, XML and review artifacts are not repository changes. No dependency, migration, commit, push or PR was added.

## 11. Files preserved

Baseline inventory before edits contains **638 files**, manifest SHA256 `a684541782296a166c213e8f1dc8b5f499eb33aa9317c0026cc93479192dff45`, matching the independent verification's ending state. The final allowed-change comparison passed after the full run and final report edit: only `app/trade_logger.py` changed, and only the new encoding test and this report were added. The ending inventory contains 640 files. The other **637 inventoried files** match exactly.

All **22 Sessions 02–05 files** and **five real log/report/paper-database/WAL/SHM files** remain byte-identical. Prior audit/remediation reports, historical evidence and the existing P2/P3 regression files remain unchanged. HEAD remains `a0789cdb5db50deece446005c40064ca5de989a4`.

The same 637-path preservation manifest hashes to `30fe8558016fd8d97f768e94a3f011e337f86e0c5c1fcf98f96385cf2a6dd705` before and after. Source hashes:

| File | Before SHA256 | After SHA256 |
|---|---|---|
| app/trade_logger.py | d35757ec56be8938eb152aa9cfbc3d98522327a287f34c4bdbe2d22aa9f0abeb | 863e6d124865aab868fb71897d9cef9fa2ecc3b473acc570268369ff5a865013 |
| app/offline_market_data_v1.py | 29bec3b894979d1f33af04a6cb57d1c626a7ebdd88335f6f863c4d7a5c7664f7 | 29bec3b894979d1f33af04a6cb57d1c626a7ebdd88335f6f863c4d7a5c7664f7 |
| app/research/causal_forward_paper_v2.py | f77facd4d0fa3b844a66ebdb3cd34e9b564dc7dffe150ffc03d76f8e10e68b90 | f77facd4d0fa3b844a66ebdb3cd34e9b564dc7dffe150ffc03d76f8e10e68b90 |

Exact inventories and assertions are retained externally in `$Run\preservation.json`. Verification command:

```powershell
& $Python -B "$Run\verify_preservation.py"
```

The required preservation set includes Sessions 02–05, historical evidence, prior reports, real CSV/log/state files and V2.2 production sources. Existing CSV security tests, candle-isolation tests and the candle validator are intentionally unchanged. No real historical export was opened for appending or transcoding; preservation reads only inventory bytes for hashing.

## 12. Remaining limitations

- Profitability and real-market readiness remain unproven; synthetic accounting is not evidence of trading edge.
- Physical power-loss and universal filesystem/device durability remain unproven; the legacy logger did not become a transactional ledger.
- Universal spreadsheet behavior remains unverified; no viewer/formula execution was tested.
- Joint replacement of matching older local database and anchor copies remains undetectable without a separately trusted external freshness witness.
- Pre-existing source-bound ledgers may reject reopening under the prior candle-validator source change. No migration exists in the reviewed V2 path; none was implemented or attempted here.
- Incompatible legacy exports require explicit separately authorized review/migration. UTF-8-compatible bytes do not prove historical encoding intent, and concurrent foreign/old-version log writers are unsupported.

No trading experiment is authorized, broker execution remains disabled, and real-time paper trading and causal settlement remain NO-GO. Stop after this remediation and verification; no next phase starts automatically.

**LOW-severity legacy CSV encoding compatibility regression remediated and regression-tested. P2 candle isolation and P3 formula neutralization remain verified. Ready for independent read-only verification of this encoding remediation only. No trading experiment authorized. Broker execution remains disabled. Real-time paper trading and causal settlement remain NO-GO pending independent verification.**
