# Independent V2.2 audit remediation

## 1. Executive verdict

**REMEDIATION VERIFIED** — both confirmed findings are fixed, the original external audit regressions pass, the focused suite passes, and the complete offline suite passes with 1,837 cases. Final preservation verification confirms only the authorized changes. This is implementation verification, not independent post-remediation certification or trading authorization.

Scope: remediate only P2 candle-validation state isolation and P3 legacy CSV spreadsheet-formula handling. The earlier read-only authorization has been superseded for these two fixes by the user's explicit remediation request. No strategy, AI prompt, broker, capital, cost, calendar or settlement redesign is included.

## 2. Audit findings addressed

- **P2 / MEDIUM:** `app/offline_market_data_v1.py::validate_candle_records` could accept bad candles or reject good candles depending on surrounding rows. The original two audit regressions have been retained with their original test bodies and assertions in repository coverage, and the unchanged external audit file was separately rerun.
- **P3 / LOW:** `app/trade_logger.py::log_trade` emitted untrusted text directly into spreadsheet-facing CSV cells. CSV structural quoting protected delimiters but did not force literal interpretation of formula-like text.

## 3. Candle-validation root cause

The parsing loop created a fresh `reasons` list on every iteration, but retained only `(idx, row, stamp)` in `parsed_rows`. The classification loop never rebound `reasons` for the current row. It therefore reused the list left by the final parsing iteration. Later duplicate/external errors extended that same list, and output rows could share it. A final valid candle cleared the effective errors for earlier invalid candles; a final invalid candle contaminated other candles. Duplicate handling also wrote an internal list into shallow-copied row metadata, potentially aliasing caller-supplied metadata.

## 4. Candle-validation fix

Retain `(idx, row, stamp, reasons)` for every parsed row. Append duplicate errors directly to that row's retained list, and unpack that row's list during classification. Merge caller-supplied `_validation_reasons` into the independent local list as before. No validation rule, unresolved/invalid precedence, coverage calculation or duplicate policy was weakened. The production diff changes four lines.

Duplicate classification intentionally depends on whether an identical instrument/timestamp exists in the input set. The required ordering invariant applies to permutations of a fixed set; unrelated unique candles have the same result as standalone validation.

## 5. Row-order invariance evidence

`tests/test_candle_validation_isolation.py` contains 40 cases:

- The exact invalid-first and invalid-last audit reproductions.
- All 24 permutations of four uniquely timestamped candles: good, good, invalid OHLC, and negative volume. Results are compared by timestamp with independently specified classifications and exact reasons. Each result is also compared with standalone validation.
- Invalid/valid/invalid; valid/invalid/valid; all-valid; all-invalid.
- Repeated invocation, unchanged input, output-list independence and an invalid-containing call followed by a separate valid-only call.
- All six permutations of two duplicate candles and one unrelated valid candle, asserting exact local duplicate reasons and input preservation.
- Local schema, timezone and interval-alignment reasons with both row orders.

The original external two-case audit regression also passed without editing its source.

## 6. CSV root cause

`parse_ai_response` accepts a nonblank provider reason. `TradingEngine._context` copies it into `ai_reason`; `PaperTrader.close_position` merges context into the completed trade and passes it to `log_trade`. Direct legacy callers can also provide symbol, exit reason and context values. `csv.DictWriter.writerow` previously received those values unchanged. A separate spreadsheet opening the export could treat a formula-leading cell as executable spreadsheet content.

This concerns legacy CSV interpretation, not broker authority or V2.2 ledger execution. No spreadsheet formula was evaluated and no external provider was called during verification.

## 7. CSV mitigation

`sanitize_csv_cell_for_spreadsheet` handles string values at the CSV projection only. Detection skips leading Unicode whitespace and Unicode control/format characters (`Cc`/`Cf`). If the first effective character is `=`, `+`, `-` or `@`, it prefixes an ASCII apostrophe to the **original, unstripped string**. Other strings pass through unchanged. CSV quoting remains the standard library's responsibility.

All exported string cells use this boundary, because caller context can supply strings even in nominally numeric columns. Actual numbers, booleans, `None` and datetime objects pass through unchanged. A negative numeric value remains numeric; a formula-like string in a numeric-named column is still untrusted text and is escaped. Neither the input trade nor position/history/decision JSONL is mutated. Original provider text remains available in the existing internal/JSONL representations; the CSV does not gain duplicate columns.

CSV writes explicitly use UTF-8 so Unicode text can be represented faithfully. No existing export is rewritten or transcoded. No `logs/paper_trades.csv` existed in the protected baseline. Compatibility with a pre-existing non-UTF-8 CSV has not been independently established; such historical exports must be preserved and their encoding assessed before appending with this version. No historical export was opened or migrated by this task.

The verified property is the emitted cell representation, including a leading literalization marker; behavior in every spreadsheet application, import mode or later re-export is not certified.

## 8. Tests added

Two new repository files add **196 cases**: 40 candle cases and 156 CSV cases.

CSV coverage exercises all four requested formula-like strings across every legacy column; whitespace, tab, CR/LF, C0 control, nonbreaking space, BOM and zero-width-format prefixes; benign text, symbols, exchange text, empty strings, apostrophes, quotes, commas, multiline strings and Unicode; delimiter-containing formula-like text; repeated append/header behavior; negative numeric fields; preservation of nontext value types; source-dictionary immutability and original decision JSONL text.

Tests exercise real serialization into isolated synthetic temporary files. They do not launch Excel, evaluate formulas, call a provider or modify real trade logs.

## 9. Focused test results

Commands ran from `C:\Users\Priyanka\Desktop\ai-trading-bot-research` using these exact path aliases:

```powershell
$Python = 'C:\Users\Priyanka\Desktop\ai-trading-bot-research\.venv\Scripts\python.exe'
$Base = 'C:\Users\Priyanka\.codex\visualizations\2026\11\05\01a1acdb-980d-7dd2-b495-2691796e9d33'
$Audit = "$Base\phase1-v22-audit"
$Run = "$Base\v22-audit-remediation"

& $Python -B "$Audit\run_offline.py" tests/test_candle_validation_isolation.py tests/test_trade_logger_csv.py -q -p no:cacheprovider --basetemp "$Run\red-tmp" --junitxml "$Run\red.xml" --tb=short
& $Python -B "$Audit\run_offline.py" tests/test_candle_validation_isolation.py tests/test_trade_logger_csv.py -q -p no:cacheprovider --basetemp "$Run\green-tmp" --junitxml "$Run\green.xml" --tb=short
& $Python -B "$Audit\run_offline.py" tests/test_candle_validation_isolation.py tests/test_trade_logger_csv.py tests/test_offline_market_data_v1.py tests/test_core.py tests/test_causal_forward_paper.py tests/test_causal_forward_paper_v2.py tests/test_causal_forward_paper_v2_recovery.py tests/test_causal_forward_paper_v2_durability.py tests/test_signal_complete_seal_integrity.py tests/test_signal_torn_seal_recovery.py -q -p no:cacheprovider --basetemp "$Run\focused-tmp" --junitxml "$Run\focused.xml" --tb=short
& $Python -B "$Audit\run_offline.py" "$Audit\test_independent_row_isolation.py" "$Audit\test_independent_correctness.py" -q -p no:cacheprovider --basetemp "$Run\audit-probes-tmp" --junitxml "$Run\audit-probes.xml" --tb=short
git diff --check -- app/offline_market_data_v1.py app/trade_logger.py
```

| Run | Passed | Failed | Errors | Skipped | Deselected | JUnit seconds | Console seconds | Exit |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Pre-fix new regressions, initial 133 cases | 15 | 118 | 0 | 0 | 0 | 6.037 | 6.07 | 1 |
| Interim after fixes, 189 cases | 188 | 1 | 0 | 0 | 0 | 6.684 | 6.71 | 1 |
| Final focused suite | 601 | 0 | 0 | 0 | 0 | 499.733 | 499.89 | 0 |
| Unchanged external audit probes | 6 | 0 | 0 | 0 | 0 | 13.562 | 13.56 | 0 |

The interim failure was a newly authored alignment fixture: changing only `timestamp_original` made normalized timestamp fields inconsistent, so schema validation stopped before the intended alignment rule. The fixture now normalizes the actual 09:16 timestamp; the expected alignment-error assertion was retained. No legitimate regression was deleted, skipped, xfailed or loosened. Coverage was expanded from 133 to 196 cases as the complete CSV column/type checks were added.

The six external probes are the prior audit's two row regressions and four V2.2 invariant checks, not additional repository-discovery cases. The focused suite also exercises the actual native Windows directory-flush path. Diff whitespace validation succeeded.

Windows Python 3.13.2 / pytest 9.1.1. The previously inspected offline launcher disables dotenv before imports, clears credential-like environment variables, denies outbound socket and DNS access, disables bytecode and pytest cache. Existing test fixtures redirect real log/state defaults and require mocked transports. All new state, copied ledgers, XML, command metadata and test output are external beneath `$Run`; these Python safeguards do not claim OS-wide network isolation.

## 10. Full-suite result

The complete unfiltered offline suite was actually rerun and its process exit, final console output, command metadata and JUnit were read after completion.

| Passed | Failed | Errors | Skipped | Deselected | JUnit seconds | Console seconds | Driver seconds | Exit |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1837 | 0 | 0 | 0 | 0 | 1270.667 | 1270.99 | 1273.5458586 | 0 |

This is the prior 1,641 repository cases plus 196 new cases. No tests were removed, excluded, skipped or xfailed. No source or test edits occurred during this final run.

```powershell
& $Python -B "$Run\run_full.py"
# Driver executes this exact unfiltered test command and records its argv/cwd/timing:
& $Python -B "$Audit\run_offline.py" -q -p no:cacheprovider --basetemp "$Run\full-tmp" --junitxml "$Run\full.xml"
```

Artifacts: `full-command.json`, `full.log`, `full.xml` in `$Run`. The earlier 1,641-test audit run is background evidence, not a substitute for this remediation rerun. No `-k`, `-m` or test-path filter is used for the full run.

## 11. Settlement non-regression

**No V2.2 settlement production file changed.** V1/V2 causal engines, risk, cost, source-binding and publication/recovery implementation remain byte-identical to the remediation baseline.

Existing focused coverage passed for chained-ledger corruption rejection, semantic replay, derived settlement/cache validation, current-equity risk, exact ancestry and monotonic head verification, quarantine ownership and ambiguous artifacts, torn tails/seals, complete-seal identity, strict duplicate-key rejection in signal seals, `.writing -> .ready` staging and flush/fsync/rename order, interrupted recovery and repeated exactly-once reconstruction. Duplicate-key claims are confined to the tested strict readers, not every JSON reader in the application.

The unchanged external checks passed: synthetic 1,000 → 1,005 exactly once across restart/retry; committed sequence 5 → 2 rejection; failed post-commit anchor directory flush without success acknowledgement followed by exactly-once recovery; source identity mismatch rejection.

The candle-validator file is part of the existing causal source fingerprint. Its authorized edit changes that identity even though the V2.2 engine bytes do not change. A ledger created under old source hashes may fail closed on reopening. No existing ledger was reopened, migrated, normalized or rewritten, and source checks were not bypassed. This is the documented source-binding constraint, not a claim of transparent old-ledger compatibility.

## 12. Files changed

Production modifications:

- `app/offline_market_data_v1.py` — independent retained row reasons and duplicate routing.
- `app/trade_logger.py` — export-only string literalization and explicit UTF-8 serialization.

New tests:

- `tests/test_candle_validation_isolation.py`.
- `tests/test_trade_logger_csv.py`.

New documentation:

- `docs/INDEPENDENT_V2_2_AUDIT_REMEDIATION.md`.

The existing dirty working tree was preserved. External plans, snapshots, XML and driver logs are not repository changes. No dependency was added, no commit/push/PR was created, and no history was rewritten.

## 13. Files intentionally unchanged

The final baseline comparison passed after the full run and final report edit: only the two authorized production files changed and only the two test files plus this report were added. The inventory increased from 635 to 638 files. The other **633 pre-existing inventoried files** match exactly, including the prior independent audit report, **22 Sessions 02–05 files** and **five real log/report/paper-database/WAL/SHM files**. Historical evidence, acquisition manifests/raw data and experiment outputs remain unchanged.

Baseline capture before edits inventoried **635 files**, with canonical manifest SHA256 `5f0a2c1f6e56922edd996d84c8371baed83caf28b89959f79eb8c6a30a4f4ca5`. It matches the completed audit's ending inventory. The audit's protected 634-file manifest remains background evidence; this remediation intentionally changes only the two authorized production files within that inventory.

The 633-path preservation manifest, excluding only the two authorized source edits, hashes to `4adce25c3fd1bdd19882c84cc68dac602a0f8c23b8924c6a3afbbc538941df48` before and after. The older 627-file historical inventory differs only in those same two production sources; its other 625 files match. HEAD remains `a0789cdb5db50deece446005c40064ca5de989a4`. V2.2 source remains SHA256 `f77facd4d0fa3b844a66ebdb3cd34e9b564dc7dffe150ffc03d76f8e10e68b90`.

| Authorized source | Before SHA256 | After SHA256 |
|---|---|---|
| app/offline_market_data_v1.py | 535c330d7a2f3c3beea4b24ce5a88348bcadbb2f11ebe33921e306d6276af70d | 29bec3b894979d1f33af04a6cb57d1c626a7ebdd88335f6f863c4d7a5c7664f7 |
| app/trade_logger.py | 8bdaafc3d4f02e3939d0306d53113b34fd91016f5728020292dd330bb25b0a32 | d35757ec56be8938eb152aa9cfbc3d98522327a287f34c4bdbe2d22aa9f0abeb |

Exact inventory and allowed-change assertions are in external `$Run\preservation.json`. Verification command:

```powershell
& $Python -B "$Run\verify_preservation.py"
```

## 14. Remaining limitations

- Profitability remains unproven. Synthetic accounting gains are not trading evidence.
- Matching older local database and anchor copies can still conceal rollback without an independently trusted freshness witness. No new local hash/file solves that limitation, and none was added.
- Offline regression tests do not establish real-market readiness, actual provider timing, hardware/power-loss durability or universal spreadsheet-viewer behavior.
- Source-bound old ledgers require separately reviewed compatibility/migration decisions. Historical data previously classified by the defective validator requires separately scoped impact assessment; it was not silently regenerated.
- Existing non-UTF-8 legacy CSV compatibility has not been certified; historical exports were preserved.
- Broker execution remains disabled. Real-time paper trading and causal settlement experiments remain NO-GO.

The pre-patch boundary investigation and one fresh candidate review were source-only and independent of the implementer's context. Neither reviewer executed the tests. They found no concrete surviving patch defect; suggested extra coverage beyond these cases remains distinct from a proven bug. No CodeRabbit or remote review request was made, consistent with this task's local-only tooling restriction. This report does not mark the earlier canonical security scan occurrence closed or claim independent post-remediation certification.

## 15. Authorization status

Stop after remediation verification and this report. A later independent read-only remediation review requires a separate request; no next phase is automatically authorized.

**Audit findings P2 and P3 remediated and regression-tested. Ready for independent read-only remediation verification only. No experiment authorized. Broker execution remains disabled. Real-time paper trading and causal settlement remain NO-GO pending independent verification.**
